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
- [ ] **Rehearse the whole visit at a desk with the simulated robot.** Use a rehearsal folder, **not** `logs\`, so rehearsal files never mix with lab evidence:
  ```powershell
  .\.venv\Scripts\python.exe .\examples\record_raw_state.py --output .\rehearsal\idle-01.jsonl --robot-id lab-er4u-1 --arm-label x --controller-label x --driver none --operator XX --pose-note rehearsal --seconds 2 --simulate --acknowledge-connect-handshake
  .\.venv\Scripts\python.exe .\examples\bench_joint.py --output .\rehearsal\base-first-01.jsonl --robot-id lab-er4u-1 --arm-label x --controller-label x --driver none --operator XX --start-pose-note rehearsal --joint base --delta 1 --simulate --acknowledge-supervised-motion
  .\.venv\Scripts\python.exe .\scripts\review_lab_logs.py --idle .\rehearsal\idle-01.jsonl --bench .\rehearsal\base-first-01.jsonl
  ```
  Everyone practises the prompts (`HOME`, `HOME_OK`, `MOVE`) and the review. Every output says **SIMULATED**.
- [ ] Commit to record: `git rev-parse HEAD` → ______________________
- [ ] Printed: this checklist, one observation sheet per planned run, and a photo or sketch of the ScorBot-software home start pose.

## B. At the bench, before Python connects

- [ ] ScorBot software and the old GUI are **closed**. Only one program talks to the controller.
- [ ] Arm label ____________, controller label ____________, USB driver ____________
- [ ] The base is secured, and the **whole** possible arm path is clear of people, cables, and objects.
- [ ] The stop operator presses the emergency stop and releases it once, so everyone knows where it is and that it works.
- [ ] Everyone knows: **connecting briefly energises the motors.** `disable()` is **not** an emergency stop.
- [ ] Preflight passes: `.\.venv\Scripts\python.exe -m scorbot.preflight`. If it fails, stop here.
- [ ] The arm is in the documented start pose, and a photo was taken. Photo file: ____________

## C. Idle capture (no motion requested)

- [ ] Run `record_raw_state.py` with a **new** output name, e.g. `logs\idle-01.jsonl` (see bench guide §3).
- [ ] Run `scripts\review_lab_logs.py --idle ...`. Packet indices increase, there is no fault, and counts are steady at rest.
- [ ] **Stop and review if anything looks wrong.** Homing waits until the idle log is understood.

## D. One home and one base jog

- [ ] Choose `--delta 1` or `--delta -1` from **visible clearance**, not from an assumed "positive" direction. Chosen: ______
- [ ] Run `bench_joint.py --joint base --speed 10` with a new output name (see bench guide §4).
- [ ] At the `HOME` prompt, the stop operator confirms they are ready and the start pose matches the photo.
- [ ] Watch the entire home search. Type `HOME_OK` **only** if it looked right. Otherwise decline, and the run ends.
- [ ] Read the printed plan (joint, signed target counts, increments) aloud before typing `MOVE`.
- [ ] Answer the post-jog prompts honestly. "Not sure" is a valid answer.
- [ ] Run `review_lab_logs.py --idle ... --bench ...`. An exit code of 0 only means the log can be read. It does **not** mean the motion was safe.

## E. Stop immediately if any of these happen

- The arm moves when no motion was requested, or a joint other than the one requested moves.
- The direction disagrees with the plan, or the count change disagrees with the observed motion.
- There is a timeout, a stale response, an error, or the motor state is uncertain.
- Anyone asks to stop.

After a stop: press the physical stop. Don't retry the command in this session. Photograph the pose, and write down what happened while it's fresh.

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
| **Observed direction** (a lab reference, e.g. "toward the door", and about how far) | |
| **Other joints moved** (yes / no) | |
| Controller LEDs or sounds | |
| **Python returned normally** (yes / error text) | |
| **How run ended** | |
| Log review: planned vs observed counts | |
| **Discrepancies** (between this sheet and the log; "none" if none) | |
| **Reviewed by** (name / date, filled in later) | |
