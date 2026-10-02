# Guided lab session

One command runs a whole bench session: profile, checklist, connect, LED
checks, idle, home, and several small jogs. Rehearse first; the rehearsal
uses no USB and no robot.

    .\.venv\Scripts\python.exe -m scorbot.lab --simulate     # rehearsal, logs in rehearsal\
    .\.venv\Scripts\python.exe -m scorbot.lab                # real arm, logs in logs\

Add `--rehearse-motors-dropped` to the rehearsal to practise the controller
cutting motor power by itself right after homing.

The rehearsal models the arm (`scorbot.simulated.REHEARSAL_PROFILE`): homing
takes about 3 s and presses each home switch in the legacy order (shoulder,
elbow, pitch, roll, base), readings wobble by one count at rest, and
before every LED question a `SIMULATED panel:` line shows what the modeled
front panel would look like. On the real arm there is no such line: look at
the controller. All of it is modeled from the manuals and other projects,
not measured.

**Connecting energises the motors.** Someone stands at the physical stop the
whole time. Software disarm and disable are not emergency stops.

## Steps

| Step | What you do |
|---|---|
| Profile | First time: type the robot id, labels, driver, initials. Later: Enter keeps them |
| Checklist | Enter or `y` for each item; `n` ends the session, nothing moves |
| LED checks | Look at the controller: MOTORS `y` lit / `n` off / `u` unsure; POWER `g` / `o` / `f` / `u`. Answer what you see; the expected state is not shown first |
| Idle | Hands off for 5 s |
| Home | Describe the start pose, type `HOME`, answer the LEDs, watch the search, describe it, `y` if it looked right |
| Jog | Press `a`, name a landmark once, answer the LEDs, type `ARM`. Then keys below. Every arming asks for the LEDs again: anything but MOTORS lit and POWER green ends the session |
| Finish | `x`: motors off, LED check, summary and `LOG CHECK` line |

The controller can turn the motors off by itself (e-stop, over-current,
communication time-out) and nothing the software reads shows it (SAFETY_CASE
HZ-21). The MOTORS LED is the only sign, which is why every arming asks for it.

**After a failure:** do not retry in the same session. Connecting a new session
turns the motors on at whatever pose the arm is in, so do not reconnect until
the arm is back in the known start pose, per the lab procedure.

Whether the arm holds its pose with motors off is unverified (no brake or holding spec in the manuals, SAFETY_CASE HZ-23). Keep hands and objects clear below the arm on finish, on any stop, and after the physical stop.

## Jog keys

| Key | Action |
|---|---|
| `1` / `q` | base + / - |
| `2` / `w` | shoulder + / - |
| `3` / `e` | elbow + / - |
| `s` | step size 1 or 0.5 degree |
| `a` / `d` | arm / disarm |
| `b` | back to start: every joint returns to where it was after homing |
| `m` | mark the current pose as P1..P9 (this session only) |
| `g` | go to a mark: choose 1-9 |
| `?` | help |
| `x` | finish |

One press is one step; nothing moves while a key is held. The first move of a
kind asks you to type it (for example `BASE -1`); pressing the same key again
repeats it. Unknown keys, 60 s without a key, a declined confirmation or any
error disarm. Each joint can move at most 10 degrees from home per session
(legacy scale, not measured). After each move you say which way it went and
whether anything else moved, before the numbers are shown.

`b` and `g` show the whole move first and need one typed confirmation
(`BACK`, `GOTO P2`), and the session must be armed. Joints move one at a time:
elbow, then shoulder, then base. **Any key during the move stops it after the
current step** and disarms; that is a software pause, the physical stop is the
stop. If the terminal cannot read keys during the move (not a console,
or no key-press support), the session says so and only the physical stop stops
it; on a real session such a move is refused (`plan_refused`). Keys pressed
during the last step are discarded and counted in the log. At the end you say
whether the arm is there, and the count difference from the target is logged. Back to start is not a re-home.

Before every step (and again after a typed confirmation) the counts are compared with the last step. If any motor
moved more than 20 counts with nothing commanded (probably noise, a push or sagging; unverified), the
step is refused and the session disarms: finish and home again in a new
session. Marks are never kept between sessions.

## Teleop and episodes

For recording demonstrations. Arm first (`a`, LED check, type `ARM`), then
press `t`.

| Key | In teleop |
|---|---|
| `1/q 2/w 3/e` | One step of the current size (1 or 0.5 degree), at once, no questions |
| `r` | Start an episode / stop it. Stopping asks `Task done?`: `y` keeps it as completed, `n` discards it (logged as aborted, never exported). The first `r` asks for the task (e.g. `reach left block`) |
| `n` | New task text (only with no episode open) |
| `t` | Leave teleop; the arm stays armed |
| `?` | Help |
| `x` | Finish the session (an open episode is aborted) |
| anything else | Disarms |

- **One press, one step.** Holding a key gives one step, not a stream: after
  each step the tool waits until the key is physically released *and* no key
  repeat has arrived for longer than this PC's keyboard repeat settings allow
  (about 0.6 s on default settings), and throws away the repeats Windows typed
  meanwhile. Release and press again for the
  next step.
- The same limits as the rest of the session: base, shoulder and elbow only,
  the 10 degree cap from home, the drift check, the fault latch. A refusal
  disarms and leaves teleop; re-arm with `a`.
- Teleop disarms after 15 s without a key (60 s elsewhere).
- An episode that ends any way other than `r` (disarm, fault, `x`, camera
  problem) is recorded as `aborted` with the reason. Only `completed`
  episodes are meant for datasets.
- Teleop needs a Windows console window (single keys and key-release
  detection); elsewhere it is refused.

**Camera.** Add `--camera 0` (webcam index) to record video for the whole
session into `camera-main.mcap`, or `--camera fake` with `--simulate` for a
rehearsal. An episode starts only while the camera is writing frames, and is
aborted if the camera stalls or fails; the arm stays armed, because the
camera records data and is not your view of the arm. If the camera cannot
open, the session continues without video. Check a webcam first with
`python -m scorbot.camera check`.

## Logs and review

Logs go to `logs\<date>-<robot>-session-NN.jsonl` (never overwritten) with a
`.controller.jsonl` and an MCAP session in `logs\sessions\`. Review:

    .\.venv\Scripts\python.exe scripts\review_lab_logs.py --session logs\<file>.jsonl

`LOG CHECK: 0 problems` means the log is complete and consistent, not that
the motion was safe or accurate. The older `examples\bench_joint.py` and
`examples\record_raw_state.py` still work as the fallback procedure.
