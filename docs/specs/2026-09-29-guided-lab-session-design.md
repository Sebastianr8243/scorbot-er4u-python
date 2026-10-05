# Guided lab session: design

> **Built.** Kept as the record of a decision; the code may have moved on since. The status line below is as written at the time.

Date: 2026-09-29. Status: approved in conversation, awaiting written-spec review.

## Goal

Make testing and moving the arm understandable for the operator, and build
the reusable core that later front ends (student window app, keyboard or
gamepad teleop, LeRobot plugin) will share.

Pain points it fixes, from the first bench visit (G1):
- **A. Setup:** too many flags and labels to type; unclear which script runs when.
- **B. Prompts:** unclear what to check and what to type (`HOME`, `HOME_OK`, `MOVE`, LED keys).
- **D. Moving:** one 1 degree jog per run, then disconnect, reconnect and re-home.

Success: an operator who has read one page can run connect, idle check,
home and several small jogs in one session without typing a flag, and the
review prints a readable per-jog table.

## Non-goals

No new motion capability: no wrist, gripper, Cartesian, coordinated or
streamed motion, no jogging before home, no larger steps. No window app, no
teleop device, no LeRobot code. `examples/bench_joint.py` and
`examples/record_raw_state.py` are not changed; they stay as the procedure
G1 used.

## Safety rules carried over

- Every motion goes through `Scorbot` gates (`home`, `jog_joint`), the fault
  latch and the SDK's 5 degree ceiling. The session adds a stricter 1 degree
  ceiling per jog, same as `bench_joint.py`.
- Only base, shoulder and elbow are offered.
- The expected LED state is never shown before the operator answers.
  Required LED checks (after connect, after enable) end the session on a
  mismatch or `unsure`.
- A typo in a typed confirmation declines; it never moves.
- Real and simulated runs differ only in `Scorbot` versus `SimulatedScorbot`
  and the `data_source` label. Simulated runs write under `rehearsal/` by default.
- JSONL is written before the matching MCAP recorder call; outputs are
  created with exclusive open and never overwritten.
- `disable()` is not called an emergency stop anywhere. The screen names the
  physical stop as the stop.
- New: a **net travel cap per joint** of 10 degrees from the home pose per
  session, in legacy software degrees. Jogs that would exceed it are refused
  before anything is queued. The legacy scale is unmeasured, so this cap is a
  bound on commands, not on physical angle.

## Architecture

```
python -m scorbot.lab [--simulate] [--profile PATH] [--logs DIR]
        |
scorbot/lab/terminal.py   TerminalOperator: prints, reads keys (msvcrt or input)
        |  Operator protocol (confirm, choose, text, show, status)
scorbot/lab/session.py    LabSession: steps, arming, gates, logging. No print/input.
scorbot/lab/profile.py    LabProfile: saved lab identity (JSON)
        |
Scorbot / SimulatedScorbot    JSONL (primary)    SessionWriter via BestEffortRecorder (MCAP)
```

`scorbot/lab/` must not import `examples/`, must not import `usb` at import
time, and must keep `import scorbot` free of `usb` (existing test).

### Operator protocol

The engine talks to the person only through this interface, so tests,
the terminal and later front ends are interchangeable.

| Method | Returns | Used for |
|---|---|---|
| `confirm(prompt, expected) -> bool` | True only if the typed text equals `expected` (case-insensitive, trimmed) | `HOME`, `ARM`, first move `BASE -1` |
| `choose(prompt, options: dict[str, str]) -> str` | the value for the key pressed; `"unsure"` after 3 invalid keys or end of input | LED checks, observations, jog-loop keys |
| `text(prompt) -> str` | free text, may be empty | pose and observation notes |
| `show(message, level)` | none | `info`, `warn`, `alarm` (alarm lines are marked `!!!`) |
| `status(line: StatusLine)` | none | redraw the status line before each prompt |

`StatusLine` fields: data source (`REAL`/`SIMULATED`), homed, armed, joint,
step, speed, fault text or `none`.

### Profile

`LabProfile(robot_id, arm_label, controller_label, driver, operator, speed=10)`
stored as JSON at `--profile` (default `lab.json` in the current folder).
First run asks each field; later runs show it and ask `[Enter] keep, [c]
change`. Validation: every field non-empty; the example placeholders the
bench script rejects (`arm nameplate`, `your initials`, and the rest of
`bench_joint._EXAMPLE_VALUES`) are rejected; `speed` is an int 1-20. The
file is written only after the operator confirms it, with an atomic
replace; it is a settings file, not evidence.

## Session flow

| Step | Screen | Operator input | Log rows |
|---|---|---|---|
| 1 Profile | profile summary | Enter keep / c change | `profile` |
| 2 Checklist | four items: base clamped and 70 cm clear; physical stop located and tested; teach pendant on Auto or unplugged and Intelitek software closed; arm in the known start pose | Enter per item; `n` on any item ends the session as declined | `checklist` |
| 3 Connect | preflight results (real only), then connect | none | `session`, `connected` |
| 4 LED after connect | "look at the controller front panel" | MOTORS y/n/u, POWER g/o/f/u (required: off, green) | `led_observation`, `led_mismatch`, `led_gate_failed` |
| 5 Idle | 5 samples at 1 Hz; counts and "stable" or "changing" | none | `idle_sample` |
| 6 Home | banner `HOME NEEDED`; asks for the start pose note | type `HOME`; then LED after enable (required: lit, green); then "describe what moved" text; then "did home look right and is travel clear?" y/n | `enabled`, `home_complete`, `home_observation`, `home_ok` or `operator_declined` |
| 7 Jog loop | status line and key help | see below | see below |
| 8 Finish | disable, LED after disable (warn only), summary table, log review line | LED keys | `disabled`, `summary` |

Declining at any step ends the session with exit code 3; a failed required
check or any exception ends it with exit code 1 and a `session_failed` row;
a completed session exits 0. Ctrl-C, SIGTERM and SIGBREAK behave as today
(`termination_as_interrupt` semantics, reimplemented in `scorbot/lab`).

### Jog loop

Keys (SCORBASE layout, one step per press, never hold-to-move):

| Key | Action |
|---|---|
| `1` / `q` | base + / - |
| `2` / `w` | shoulder + / - |
| `3` / `e` | elbow + / - |
| `s` | cycle step: 1.0, 0.5 degrees |
| `a` | arm: shows "path clear, hand on the stop?" then requires typing `ARM` |
| `d` | disarm |
| `?` | key help |
| `x` | finish |

"+" and "-" are the legacy software direction; the physical direction is
unverified, so every jog asks what moved.

Rules:
- Jog keys do nothing while disarmed except print "DISARMED: press a".
- The first arming of a session also asks for a reference landmark (free
  text, for example "the door"), used by every direction question; it is
  logged in the `armed` row.
- The first jog of a given (joint, sign, step) since arming shows the plan
  (`preview_jog`: motor count deltas, increments, the legacy scale warning)
  and requires typing the move, for example `BASE -1` or `ELBOW +0.5`. The
  same key again repeats that move with one key press after a one-line
  plan echo. A different joint, sign or step needs a typed confirmation again.
- Disarm on: `d`; an unknown key; more than 60 s since the last key when the
  next key arrives (that key is ignored); any exception from a jog; a refused
  jog. After a fault the session is latched: the loop ends and Finish runs.
- Before each jog: refuse if the net travel cap would be exceeded.
- After each jog, the observation comes before the numbers: "Which way did
  it move?" `t` toward the reference landmark (named once per session), `a`
  away, `n` none, `u` unsure; "Did any other joint move?" y/n/u; optional
  note. Then the planned and measured count change are shown.

Per jog `n` (1-based), rows: `jog_preview` (n, joint, sign, step, plan),
`jog_confirmed` (typed or repeat), `before_jog` (state), `after_jog`
(state), `jog_observation` (direction, other_joint_moved, note),
`jog_result` (planned deltas, measured deltas via `signed_count_delta`).
Also `armed`, `disarmed` (reason), `jog_refused` (reason).

## Logs and review

- JSONL at `<logs>/<YYYYMMDD>-<robot_id>-session-<NN>.jsonl`, `NN` the first
  unused two-digit number, plus `<stem>.controller.jsonl` from the SDK and an
  MCAP session under `<logs>/sessions/`. Default `<logs>`: `logs/` for real
  runs, `rehearsal/` with `--simulate`.
- First row `session` has `schema_version: 2`, `kind: "lab_session"`, the
  profile, `data_source`, `software_commit`, `motion_source_sha256`,
  `controller_event_log`, `mcap_session`.
- `scripts/review_lab_logs.py --session PATH [--json]` prints: a header with
  REAL or SIMULATED; LED checks; a table with one row per jog (joint, step,
  typed or repeat, planned counts, measured counts, difference, operator
  direction, other joint moved); faults and declines; then
  `LOG CHECK: N problems (not a safety verdict)`. `--json` prints the machine
  form. Problems: a missing `after_jog` for a started jog, measured change
  of a joint that was not jogged beyond 20 counts, measured sign opposite to
  the plan, missing required LED rows, any `session_failed`.
- `scripts/watch_lab_log.py` shows the new rows; `jog_refused` and
  `led_gate_failed` are alarms.

## Terminal front end

- Standard library only: `input()` for typed text; `msvcrt.getwch()` for
  single keys on Windows when stdin is a TTY; otherwise `input()` with the
  first character. Arrow keys are not used.
- The status line is printed before every prompt (no cursor control).
  Colour only when stdout is a TTY and `NO_COLOR` is unset; alarm lines
  always start with `!!!` so they read without colour.
- Help is printed on entering the jog loop and on `?`.

## Testing

- `tests/test_lab_session.py`, all with `SimulatedScorbot` and a scripted
  operator (a test double recording every prompt), no terminal, no USB:
  - full session: profile kept, checklist, LEDs, idle, home, arm, first jog
    typed, same jog repeated with one key, different joint typed, finish; exit 0
    and the expected rows in order.
  - a wrong typed move declines that jog and queues nothing.
  - unknown key and 60 s idle (injected clock) disarm; jog keys while
    disarmed queue nothing.
  - the travel cap refuses the jog that would exceed 10 degrees.
  - LED `unsure` after connect ends with exit 1 before homing.
  - an injected fault during a jog latches, ends the loop, runs Finish.
  - checklist `n` and a declined `HOME` exit 3.
  - no step above 1 degree is offered or accepted; wrist and gripper keys do
    nothing.
- `tests/test_lab_profile.py`: first-run creation, keep, change, rejected
  placeholders, bad speed, atomic write.
- `tests/test_lab_log_review.py`: `--session` table, problem counting, `--json`.
- Terminal: key decoding and the non-TTY fallback with patched `msvcrt` and stdin.
- Smoke: `python -m scorbot.lab --simulate` with piped input in a temporary
  folder, then the review exits 0.

## Docs

- New `docs/lab/LAB_SESSION.md`: one page, the flow table, keys, what each
  answer means, where logs go.
- `docs/lab/ARM_CONTROL_BENCH.md` and `START_HERE_WINDOWS.md`: point to the
  guided session as the normal way; the bench scripts stay as the fallback.
- `docs/design/OPERATOR_UX.md`: mark backlog items 1-4 and 6 as addressed by this
  design where they apply.
- `CLAUDE.md` Commands: the guided-session command. `scorbot/CLAUDE.md`: a
  `scorbot/lab/` entry (engine has no print/input; gates stay in `Scorbot`).

## Risks

- `msvcrt.getwch` in the VS Code terminal is expected to work (ConPTY) but is
  untested; the `input()` fallback covers it.
- Two ways to run a bench session exist for a while; docs name the guided
  session as the default and the scripts as the fallback.
- The travel cap uses the unmeasured legacy scale; it limits commands, and
  the physical stop remains the authority.
