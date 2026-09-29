# G1 lab checklist: first bounded motion

**Goal of this visit (gate G1):** a supervised idle capture, a known start pose, and **one** small base jog. It must have fresh before and after controller responses, an observed physical direction, and a normal return from Python. That is all. Success here doesn't show calibration, safe limits, or stop behaviour.

The full procedure with every command is in [ARM_CONTROL_BENCH.md](ARM_CONTROL_BENCH.md). Print this page and the observation sheet below, and tick the boxes as you go. **Every number on this page is provisional.**

Roles. Write names, and don't let one person hold two roles:

- **Stop operator** (hand near the physical emergency stop, eyes on the arm): ____________
- **Keyboard operator** (types commands and prompts): ____________
- **Recorder** (fills in the observation sheet): ____________

## A. Before leaving for the lab

- [ ] The lab code includes the `libdef.py` fix (commit `55e6ecb` or later). GitHub `main` before that fix **crashes on connect**.
- [ ] On the robot PC, or on a laptop with the same setup, these pass:
  - `.\.venv\Scripts\python.exe -m unittest discover -s tests`
  - `.\.venv\Scripts\python.exe examples\preview_jog.py --joint base --delta 1`, which is offline and prints the plan only
- [ ] **Rehearse the whole visit at a desk with the simulated robot.** If rehearsing on the robot PC, unplug or power off the controller first: a command pasted without `--simulate` is a real connect, and the handshake energises the motors. Use a rehearsal folder, **not** `logs\`, so rehearsal files never mix with lab evidence:
  ```powershell
  .\.venv\Scripts\python.exe .\examples\record_raw_state.py --output .\rehearsal\idle-01.jsonl --robot-id lab-er4u-1 --arm-label x --controller-label x --driver none --operator XX --pose-note rehearsal --seconds 2 --simulate --acknowledge-connect-handshake
  .\.venv\Scripts\python.exe .\examples\bench_joint.py --output .\rehearsal\base-first-01.jsonl --robot-id lab-er4u-1 --arm-label x --controller-label x --driver none --operator XX --start-pose-note rehearsal --joint base --delta 1 --simulate --acknowledge-supervised-motion
  .\.venv\Scripts\python.exe .\scripts\review_lab_logs.py --idle .\rehearsal\idle-01.jsonl --bench .\rehearsal\base-first-01.jsonl
  ```
  Everyone practises the prompts (`HOME`, `HOME_OK`, `MOVE`, and the one-key LED checks) and the review. Every output says **SIMULATED**.
- [ ] Commit to record: `git rev-parse HEAD` → ______________________
- [ ] Printed: this checklist, one observation sheet per planned run, and a photo or sketch of the ScorBot-software home start pose.

## B. At the bench, before Python connects

- [ ] ScorBot software and the old GUI are **closed**. Only one program talks to the controller.
- [ ] Arm label ____________, controller label ____________, USB driver ____________
- [ ] The base is secured, and the **whole** possible arm path is clear of people, cables, and objects.
- [ ] The stop operator presses the emergency stop and releases it once, so everyone knows where it is and that it works.
- [ ] Everyone knows: **connecting briefly energises the motors.** `disable()` is **not** an emergency stop.
- [ ] The recorder can see the controller's front panel. The green **MOTORS** LED is the only independent evidence of motor power; the SDK's `enabled` is command history. **POWER**: green = communicating with the PC, orange = not communicating, flashing = USB timeout ([hardware reference](HARDWARE_REFERENCE.md)). Before connecting: POWER ______ MOTORS ______
- [ ] `openScorbot\data.json` was freshly created on this PC by the step A checks, not copied from elsewhere or hand-edited. The legacy code creates it once from the defaults in `conf.py` and never overwrites it, so an old copy silently replaces the packet timing and limits. If unsure, delete it; the next run recreates the defaults.
- [ ] Preflight passes: `.\.venv\Scripts\python.exe -m scorbot.preflight`. If it fails, stop here.
- [ ] Optional: the recorder opens a second terminal **in the repository root** with the read-only live view, using the same output path as the run: `.\.venv\Scripts\python.exe -m scripts.watch_lab_log .\logs\base-first-01.jsonl`. It only reads the log files and cannot command the arm. Closing it does not affect the run.
- [ ] The arm is in the documented start pose, and a photo was taken. Photo file: ____________

## C. Idle capture (no motion requested)

- [ ] Run `record_raw_state.py` with a **new** output name, e.g. `logs\idle-01.jsonl` (see bench guide §3). It asks for the LEDs after connect and after exit.
- [ ] Run `scripts\review_lab_logs.py --idle ...`. Packet indices increase, there is no fault, and counts are steady at rest.
- [ ] `home_switch_bits` at the start pose is below 32, and record which bits are set: ______. The legacy switch decoder (`libdef.get_switch`) misreads byte 5 when any bit ≥ 32 is set: the shoulder search would miss its switch and the elbow, pitch and roll would be treated as already home. **If it is 32 or more, do not home today.** `home()` also refuses such a byte before sending anything.
- [ ] **Stop and review if anything looks wrong.** Homing waits until the idle log is understood.

## D. One home and one base jog

**Pause points (spoken, everyone stops):** ⏸ before `HOME`, ⏸ before `MOVE`. At each one the keyboard operator reads the step aloud and the stop operator answers "clear, hand on stop" before anything is typed. During motion, all eyes are on the arm and the LEDs, not on a screen.

- [ ] Choose `--delta 1` or `--delta -1` from **visible clearance**, not from an assumed "positive" direction. Chosen: ______
- [ ] Run `bench_joint.py --joint base --speed 10` with a new output name (see bench guide §4).
- [ ] At the `HOME` prompt, the stop operator confirms they are ready and the start pose matches the photo.
- [ ] Watch the entire home search. Type `HOME_OK` **only** if it looked right. Otherwise decline, and the run ends.
- [ ] ⏸ Read the printed plan (joint, sign, signed target counts) aloud; the stop operator repeats the joint and direction back before `MOVE` is typed.
- [ ] **Record blind:** right after the jog, the recorder writes the observed direction (against a lab landmark), whether any other joint moved, and the LEDs on the sheet **before** looking at the plan, the terminal or the live view. The stop operator says what they saw first; then the keyboard operator types it. "Not sure" is a valid answer.
- [ ] At each `LED check` (after connect, `enable`, the jog, `disable`) the recorder reads the front panel aloud and the keyboard operator types one key: MOTORS `y`/`n`/`u`, POWER `g`/`o`/`f`/`u`. The script does not say what it expects. A `!!! WARNING` means the answer contradicts the software: stop and check. A contradictory or unsure answer after connect ends either script before sampling or motion; after `enable`, it ends the bench run before homing. Later checks still warn and require operator action.
- [ ] Run `review_lab_logs.py --idle ... --bench ...`. An exit code of 0 only means the log can be read. It does **not** mean the motion was safe.
- [ ] Declining at `HOME`, `HOME_OK`, or `MOVE` is the procedure working: the script disconnects, prints "Run ended by the operator", exits with code 3 and logs `operator_declined`, with no alarm. A required LED check that is contradictory or unsure records `session_failed`; the context attempts disconnect, but this is not proof of motor-off. Use the physical stop if motor state is uncertain.

## E. Stop immediately if any of these happen

- The arm moves when no motion was requested, or a joint other than the one requested moves.
- The direction disagrees with the plan, or the count change disagrees with the observed motion.
- There is a timeout, a stale response, an error, or the motor state is uncertain.
- The terminal prints `*** USB ... worker stopped ...`, even while waiting at a prompt. The controller is no longer receiving packets.
- The MOTORS LED is on when the software says motors are disabled, or POWER turns orange or flashes during a run.
- Anyone asks to stop.

Ctrl-C, a Python timeout, or closing the console window does **not** stop a move in progress. The disable is queued behind the current motion, and closing the window can skip it entirely. Use the physical stop.

After a stop: press the physical stop. Don't retry the command in this session. After the e-stop is released the controller stays in control-off (MOTORS LED off) until a new control-on, and the SDK no longer knows the motor state, so any further motion needs a **new Python session** after the logs are reviewed. Photograph the pose, and write down what happened while it's fresh.

## F. Before any second movement

- [ ] Logs are reviewed and the observation sheet agrees with them.
- [ ] Back to the known start pose. A new run and a new output file.
- [ ] Only then try the opposite base direction. Shoulder and elbow come after base, one direction per run, ≤ 1° each.

## G. Before leaving the lab

- [ ] Copy the whole `logs\` folder to a second location. It holds every `*.jsonl`, its `*.controller.jsonl` companion, and `logs\sessions\` (one MCAP recording per run, each with a `notes.md`). Copy the photos and the signed sheets too.
- [ ] Record whether G1 passed. If any run ended on the emergency stop or with a fault, G1 has **not** passed.

---

## Observation sheet (one per run)

The **bold** rows use the same labels as the `notes.md` file in that run's
session folder (`logs\sessions\<id>\notes.md`). Afterwards, copy each bold
answer onto its line in `notes.md`. `python -m scorbot.session list logs\sessions`
then shows which sheets are still incomplete. Yes/no rows take yes, no, n/a
or not sure. *How run ended* takes normal return, declined prompt, emergency
stop, error, or other: <what happened>.

| Field | Entry |
|---|---|
| Run file (`logs\...jsonl`) | |
| **Date / time** | |
| **Stop operator** | |
| **Recorder** | |
| **E-stop tested before start** (yes / no) | |
| **Start pose matches photo** (yes / no, and how it differs) | |
| Joint / requested delta / speed | base / ____ / ____ |
| Home search: what moved, in what order, anything odd | |
| Typed `HOME_OK`? If not, why | |
| Planned count change (from the printed plan) | |
| **Observed direction** (a lab reference, e.g. "toward the door") | |
| Approximate displacement | |
| **Other joints moved** (yes / no) | |
| POWER / MOTORS LEDs (warnings, changes between prompts, and state after exit) | |
| Controller sounds | |
| **Python returned normally** (yes / error text) | |
| **How run ended** (normal return / declined prompt / emergency stop / error) | |
| Log review: planned vs observed counts | |
| **Discrepancies** (between this sheet and the log; "none" if none) | |
| **Reviewed by** (name / date, filled in later) | |
