# G1 lab card: first bounded motion

Keep this card open on the robot PC or phone. The [bench procedure](ARM_CONTROL_BENCH.md) has the commands and explanations. The observation sheet below is optional; the scripts save the required answers in the log.

**Goal:** one supervised idle capture, then at most one small base jog in this run if the idle review is clear. An unresolved MOTORS LED mismatch stops the visit before homing. This visit does not validate calibration, limits, or software stop behavior.

## People and stop

- Assign the three roles used for this first motion check: one person at the physical stop watching the arm, one at the keyboard, and one recording what moves. The observation sheet is optional because the script also saves the answers.
- Close Intelitek software and the old GUI. Secure the base, clear the arm's possible path, and keep the controller LEDs visible.
- Before connecting, the stop operator points to the physical stop and presses/releases it once under the lab's procedure. A Python `disable()` or Ctrl-C is not an emergency stop. Connecting briefly energizes the motors.

## 1. Check the PC once

- Do [Windows setup](../START_HERE_WINDOWS.md) and one [offline `--simulate` rehearsal](../CLAUDE.md#commands) for this checkout; either can be done before lab day. Do not redo them for each visit. If rehearsing on the robot PC, unplug or power off the controller first. Run the [read-only USB check](../START_HERE_WINDOWS.md#3-check-that-windows-and-python-see-the-controller) at the bench. Stop before a live connection if Python preflight fails.
- Use the known starting pose from the prior ScorBot-software home; keep a phone photo or sketch. Do not use a copied or hand-edited `openScorbot\data.json`; ask for help if its origin is unclear.

## 2. Idle capture

- Run the prompted [idle command](ARM_CONTROL_BENCH.md#3-capture-idle-responses-first) with the real arm, controller, driver, operator, and pose values. Use a new output name.
- Read the **MOTORS** and **POWER** labels on the controller, then answer the LED prompts: MOTORS `y` lit / `n` off / `u` unsure; POWER `g` green / `o` orange / `f` flashing / `u` unsure.
- Run the log review. If the LED check, capture, or review reports a problem, stop before homing and keep the logs. If `home_switch_bits` is 32 or more, do not home; the SDK also refuses it. Stable encoder counts and software `enabled=false` do not prove motor power is off.

## 3. One supervised home and base jog

- Only after the idle result is understood, choose `--delta 1` or `--delta -1` from visible clearance and run the [bench command](ARM_CONTROL_BENCH.md#4-one-supervised-home-and-base-jog) with a new output name.
- At each pause, the stop operator says **clear, hand on stop** before the keyboard operator types. Type `HOME` only from the known start pose. Watch the full home search; type `HOME_OK` only if it looked right. Read the jog plan together; type `MOVE` only if the path is clear. Any other answer declines that step.
- Answer the LED prompts from the controller front panel. A contradictory or unsure required answer ends the run before idle sampling or homing. During motion, the recorder watches the arm. After the jog, the recorder says what moved before reading the count results; the keyboard operator enters the direction, indicators, and any issue in the script prompts.
- Review the bench log before another command. A zero review exit code means the log is readable; it does not prove the motion was safe or accurate.

## Stop and keep evidence

If motion is unexpected, MOTORS is lit when software says disabled, POWER turns orange or flashes during a run, a response times out, or anyone asks to stop: **use the physical stop and end this session.** Do not retry a faulted command. Photograph or describe the pose, then copy the whole `logs\` folder to a second location, including `.controller.jsonl` and `sessions\`.

---

## Optional observation sheet (one per run)

Use this if a third person wants paper notes. The scripts already save the
prompt answers. The **bold** rows also match `notes.md` in that run's session
folder (`logs\sessions\<id>\notes.md`); copy them there later if you use the
sheet. `python -m scorbot.session list logs\sessions` shows which notes are
still incomplete. Yes/no rows take yes, no, n/a or not sure. *How run ended*
takes normal return, declined prompt, emergency stop, error, or other: <what happened>.

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
