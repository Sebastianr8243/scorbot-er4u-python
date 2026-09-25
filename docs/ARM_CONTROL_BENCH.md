# First ER-4U lab visit: small movements and evidence

Use the `feat/arm-control-bench-calibration` branch of [this repository](https://github.com/Sebastianr8243/scorbot-er4u-python/pull/1). This visit checks Python communication, homing, and one small joint movement at a time. It does not establish safe joint limits or calibrated physical angles. No manufacturer value or vendor display is used as calibration data.

Print the one-page [G1 lab checklist and observation sheet](G1_LAB_CHECKLIST.md) and tick it off during the visit.

## 1. Prepare the robot PC

From the repository root in PowerShell:

```powershell
git fetch origin
git switch feat/arm-control-bench-calibration
git pull
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[windows]"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m scorbot.preflight
git rev-parse HEAD
```

If the PC has no Git, download the ZIP from the **feature branch**, not the default branch, using the branch selector on the repository page. Run the install, tests, and preflight commands from the extracted folder. The logs include a SHA-256 fingerprint of the motion source even when a ZIP has no Git metadata.

The tests never command USB. Preflight only enumerates the controller; stop if it fails. Record the PC, Python version, USB driver, arm and controller labels, operator initials, and source commit or ZIP date. Do not change a working USB driver merely for this visit.

## 2. Prepare the physical workspace

Close ScorBot software and the old GUI before Python connects. Reproduce the physical start pose from your successful ScorBot-software home; a photo or sketch is useful. Secure the base, clear the entire possible arm path, and keep an operator at the physical emergency stop.

**Connecting is not passive:** the inherited handshake briefly sends motor-on packets before the Python adapter requests disable. The queued `disable()` call cannot act as an emergency stop. If the arm moves unexpectedly or motor state is uncertain, use the physical stop and end the session.

## 3. Capture idle responses first

Use a new filename and your actual labels:

```powershell
.\.venv\Scripts\python.exe .\examples\record_raw_state.py --output .\logs\idle-01.jsonl --robot-id lab-er4u-1 --arm-label "arm nameplate" --controller-label "controller nameplate" --driver "current Windows driver" --operator "your initials" --pose-note "photo/sketch of known start pose" --seconds 10 --hz 2 --acknowledge-connect-handshake
.\.venv\Scripts\python.exe .\scripts\review_lab_logs.py --idle .\logs\idle-01.jsonl
```

Keep `idle-01.jsonl` and `idle-01.controller.jsonl`. Review increasing packet indices, decoded sign fields, fault status, motor status, and the displayed count range at rest. A count range is an observation, not an angle or an automatic safety pass. If capture or review reports a problem, stop before homing.

## 4. One supervised home and base jog

Choose `--delta 1` or `--delta -1` based on **visible physical clearance**, not an assumed positive direction. Use a new output filename for each run:

```powershell
.\.venv\Scripts\python.exe .\examples\bench_joint.py --output .\logs\base-first-01.jsonl --robot-id lab-er4u-1 --arm-label "arm nameplate" --controller-label "controller nameplate" --driver "current Windows driver" --operator "your initials" --start-pose-note "same known pose as idle capture" --joint base --delta 1 --speed 10 --acknowledge-supervised-motion
```

The script asks you to confirm the start pose before `HOME`. Watch the complete home search. It then records your description and requires `HOME_OK` before preparing any jog. If the result looks wrong, decline the prompt and end the run. Before `MOVE`, review the printed plan: joint, signed motor-count target, and integer increment sequence. The preview describes requested controller setpoints; it cannot confirm the actual movement.

After the jog, record observed direction and approximate movement, whether any other joint moved, controller indicators, and any issue. Preserve both the bench JSONL and its `.controller.jsonl` companion. Then review:

```powershell
.\.venv\Scripts\python.exe .\scripts\review_lab_logs.py --idle .\logs\idle-01.jsonl --bench .\logs\base-first-01.jsonl
```

The review prints planned and observed count changes for every motor, missing observations, and packet problems. **An exit code of zero means the log is structurally reviewable, not that the motion was physically safe or accurate.** Examine the actual physical observations before another command.

If the first base trial behaves as expected, reproduce the known start pose and use a new run for the opposite base direction. Then consider shoulder and elbow, one direction at a time, at no more than 1° per run. Each run performs a new home search. Stop at the first unexpected physical motion, sign/count disagreement, stale response, timeout, or uncertain motor state. Do not retry a faulted command during this session.

## 5. After the visit

Keep the original logs, source commit/fingerprint, start-pose photo, and notes. Compare repeated home counts, fresh packet timing, commanded versus observed count changes, and physical direction for each joint. Do not infer physical degrees from the inherited software scale.

The next milestone is independent angle measurement using a protractor or digital angle gauge. Collect several physical points approached from both directions plus separate verification points; fit per-arm scale, zero, and backlash; set software limits only inside the measured clear region. See [physical calibration](PHYSICAL_CALIBRATION.md). Absolute angle control follows verified measurements. Wrist jogs remain blocked in the Python adapter until both wrist motors and physical pitch/roll are checked.

ScorBot-software values may be recorded separately for comparison, but they are not physical ground truth. Do not run ScorBot software and Python against the controller at the same time.
