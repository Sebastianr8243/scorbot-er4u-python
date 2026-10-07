# Agent runbook for a lab session

Written 2026-10-06. For an AI agent on the lab laptop that helps a human operator. Nothing here has been validated on the arm. Companion to [LAB_DAY_CARD.md](LAB_DAY_CARD.md) (the commands) and [ACCEPTANCE_RUN.md](ACCEPTANCE_RUN.md) (what to measure).

## Agent rules

1. **The human runs every motion command.** The agent may run the no-arm checks in P and read logs. It never runs a command that connects or moves the arm without `--simulate`, never types `HOME`, `MOVE` or `STREAM` for the operator, and never retries a faulted command. The operator's hand stays near the physical stop. Ctrl-C and the software stop are not an emergency stop.
2. **Logs are evidence, chat is not.** A claim about the arm ("it moved up", "homing works") counts only if a log from this PC shows it. Cite the file name and the row.
3. **Stop and report** (do not suggest a workaround) on: any fault, `session_failed`, MOTORS LED disagreeing with the software, motion in a joint that was not commanded, a counts change the model says is wrong, a result outside a pass criterion below.
4. **New `--output` name every run.** Files are never overwritten. Put real runs in `logs\`, simulated ones in `rehearsal\`.
5. **Never edit** `openScorbot/` packet code or the `WRITE`/`READ` sleeps, and do not change a limit, during a session. Limits change at a desk, with the evidence open.

## P. Pre-tests (no arm, agent may run these)

| # | Command | Pass |
|---|---|---|
| P1 | `python -m compileall -q scorbot openScorbot scripts examples tests` | no output |
| P2 | `python -m unittest discover -s tests` | `OK` (some tests skip when an optional package is missing; no failures) |
| P3 | `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows_usb_check.ps1` | `PASS USB 09F1:0007` |
| P4 | `python -m scorbot.lab --simulate --profile rehearsal\lab.json --logs rehearsal` (operator drives it) | session runs end to end, every output says SIMULATED |
| P5 | `python scripts\review_lab_logs.py --idle rehearsal\idle-01.jsonl --bench rehearsal\base-01.jsonl` after the rehearsal commands in the root `CLAUDE.md` | no problems reported |

If P3 fails, stop: nothing else can run.

## Homing without a start pose: `--inch-home` (added 2026-10-06, never run on the arm)

It is an option of `bench_joint.py`, `bench_stream.py` and the keyboard session (`python -m scorbot.lab --inch-home`). The keyboard session without it uses the legacy home, which failed on the arm on 2026-10-06 (shoulder rose a few degrees, stopped, noise, error code 1).

`bench_joint.py ... --inch-home` homes shoulder, elbow and base by inching each joint a degree at a time to its switch (each way from where it started, up to 30, 30 and 100 degrees for the shoulder, elbow and base, then the vendor's offset). **Start the arm within a few tens of degrees of its home pose** (shoulder up, not folded down): the elbow and wrist motors are coupled to the shoulder, this code moves one motor at a time, and a long sweep from a folded pose is the likely reason the legacy home failed (see the project log, 2026-10-06), from any pose. Same typed words (`HOME`, `HOME_OK`). Operator's hand at the physical stop, whole arm path clear; it homes all three joints every time (there is no single-joint option, because a partial home would be recorded as a full one); the jog that follows is the base (`--joint base --delta 1`) first. If a joint's switch is missed it can be driven into its stop. The wrist is not homed: the operator sets it by eye first. If it faults it says which joint and why, switches the motors off and leaves no home: keep the log and report. Design: `docs/protocol/VENDOR_HOMING_TRACE.md`, `docs/project/PROJECT_LOG.md` (2026-10-06).

## Homing precondition (why the 2026-10-06 run failed)

`base-first-02`: motors enabled, `home()` ran about 4 s, then `Legacy controller returned error code 1` (a joint error word at or above 40: the search hit resistance before its switch). No jog was sent. The legacy search only works from the homing start pose.

Before any `HOME`, the agent asks the operator, and writes the answers in the run's notes:

- [ ] SCORBASE was used to home the arm, then closed completely. The pose it left is the start pose (ARM_CONTROL_BENCH.md line 21).
- [ ] The arm has not been moved by hand or by any tool since.
- [ ] The whole arm path is clear; someone is at the physical stop.
- [ ] `home_switch_bits` in the idle recording (A) is below 32.

If homing faults again, the agent asks for the terminal lines (`Joint limit reached` or `Joint did not respond`) and a photo of the pose, saves them next to the log, and stops. Do not retry in the same session.

## Real steps, in order

Each step has one pass criterion. Fail means: keep the log, stop, report.

| Step | Command source | Pass criterion, read from the log |
|---|---|---|
| A idle | LAB_DAY_CARD A | `review_lab_logs.py --idle` clean, `home_switch_bits` < 32, LEDs MOTORS off / POWER green |
| B base jog 1 degree | LAB_DAY_CARD B | `home_done` row, then `jog` row; `encoder_counts` base changes by about 142 (model: 141.89 per degree); other joints within 10 counts |
| C stop trial | LAB_DAY_CARD C | the printed line is one of the four in the card; any is a result |
| D shoulder jog, phone level | LAB_DAY_CARD D | forearm angle before and after written in the notes |
| E stream trial, one motor | LAB_DAY_CARD E | `Trial result: PASSED`, lead under 284 counts, other motors at most 10 counts |
| F gripper, empty jaws | LAB_DAY_CARD F | both lines say `full travel` |
| G idle with e-stop press | LAB_DAY_CARD G | `vendor_check.py` shows whether bit 0 follows the press |

Counts differences always go through `scorbot.calibration.signed_count_delta`, never plain subtraction.

## What the agent writes after each step

One block in the session notes: step, log file name, pass or fail with the numbers, what the operator saw, anything unexpected. No interpretation beyond the criterion.

## When a limit may be changed

At a desk, never in the lab. A limit moves only when the notes hold: direction confirmed, scale within about 5 percent (counts per degree compared with the model's 141.89 base, 113.51 shoulder and elbow), a clean stream trial for that joint, and the reason is written in `docs/project/PROJECT_LOG.md`. The plan for the travel cap is [2026-10-06-travel-cap-widening-plan.md](../specs/2026-10-06-travel-cap-widening-plan.md).
