# First ER-4U lab visit: small movements and evidence

> The normal way to run a bench session is now the guided session: [LAB_SESSION.md](LAB_SESSION.md). This page documents the older per-script procedure, which still works as a fallback.

Use the tested branch or ZIP you intend to run on the robot PC. This visit checks Python communication, homing, and one small joint movement at a time. It does not establish safe joint limits or calibrated physical angles. No manufacturer value or vendor display is used as calibration data.

Keep the [G1 lab checklist and observation sheet](G1_LAB_CHECKLIST.md) open or printed during the visit.

## 1. Prepare the robot PC

Follow [Start here on the robot PC](../START_HERE_WINDOWS.md) once to get Python and `.venv` working. Git is optional. If setup already passed in this checkout, do not reinstall everything for each visit. From the repository root, run the read-only USB check:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows_usb_check.ps1
```

The check lists the Windows device and asks Python to enumerate it; it does not start a controller session or move the arm. If Python preflight fails, stop before any live connection and keep the error output. Record the arm, controller, driver, operator, and pose in the prompts below. The logs also include a source fingerprint when the checkout came from a ZIP. Do not change a working USB driver merely for this visit.

## 2. Prepare the physical workspace

Close ScorBot software and the old GUI before Python connects. Reproduce the physical start pose from your successful ScorBot-software home; a photo or sketch is useful. Secure the base, clear the entire possible arm path, and keep an operator at the physical emergency stop.

**Connecting is not passive:** the inherited handshake briefly sends motor-on packets before the Python adapter requests disable. The queued `disable()` call cannot act as an emergency stop. If the arm moves unexpectedly or motor state is uncertain, use the physical stop and end the session.

## 3. Capture idle responses first

Use a new filename. Enter short, consistent IDs you can recognize later. A sticker or nameplate helps, but if one is missing, use your own short ID; enter `unknown` for a driver you cannot identify. Describe the pose in one sentence. Do not copy example words into the log:

```powershell
$robotId = Read-Host 'Short ID for this arm'
$armLabel = Read-Host 'Arm sticker/nameplate, or short ID'
$controllerLabel = Read-Host 'Controller sticker/nameplate, or short ID'
$usbDriver = Read-Host 'Windows USB driver, or unknown'
$operator = Read-Host 'Operator initials'
$poseNote = Read-Host 'Starting pose in one sentence'
.\.venv\Scripts\python.exe .\examples\record_raw_state.py --output .\logs\idle-01.jsonl --robot-id $robotId --arm-label $armLabel --controller-label $controllerLabel --driver $usbDriver --operator $operator --pose-note $poseNote --seconds 10 --hz 2 --acknowledge-connect-handshake
.\.venv\Scripts\python.exe .\scripts\review_lab_logs.py --idle .\logs\idle-01.jsonl
```

Keep `idle-01.jsonl` and `idle-01.controller.jsonl`. Review increasing packet indices, decoded sign fields, fault status, motor status, and the displayed count range at rest. A count range is an observation, not an angle or an automatic safety pass. If capture or review reports a problem, stop before homing.

## 4. One supervised home and base jog

Choose `--delta 1` or `--delta -1` based on **visible physical clearance**, not an assumed positive direction. Use a new output filename for each run. Re-enter the label variables above if this is a new PowerShell session:

```powershell
$startPoseNote = Read-Host 'Starting pose in one sentence'
.\.venv\Scripts\python.exe .\examples\bench_joint.py --output .\logs\base-first-01.jsonl --robot-id $robotId --arm-label $armLabel --controller-label $controllerLabel --driver $usbDriver --operator $operator --start-pose-note $startPoseNote --joint base --delta 1 --speed 10 --acknowledge-supervised-motion
```

The script asks you to confirm the start pose before `HOME`. Watch the complete home search. It then records your description and requires `HOME_OK` before preparing any jog. If the result looks wrong, decline the prompt and end the run. Before `MOVE`, review the printed plan: joint, signed motor-count target, and integer increment sequence. The preview describes requested controller setpoints; it cannot confirm the actual movement.

**LED prompt keys:** Look at the controller's labeled LEDs and enter what you see. For MOTORS, use `y` lit, `n` off, or `u` unsure. For POWER, use `g` green, `o` orange, `f` flashing, or `u` unsure. The script asks after connect and exit during idle capture, and after connect, enable, jog, and disable during the bench run.

An unsure or contradictory answer after connect ends either script before sampling or motion. In the bench run, it also ends before homing if the after-enable LEDs are unsure or contradictory. Later warnings are saved for review. If motor state is uncertain, use the physical stop; Python cleanup does not prove motor power is off.

After the jog, record observed direction and approximate movement, whether any other joint moved, other controller indicators or sounds, and any issue. Preserve both the bench JSONL and its `.controller.jsonl` companion. Then review:

```powershell
.\.venv\Scripts\python.exe .\scripts\review_lab_logs.py --idle .\logs\idle-01.jsonl --bench .\logs\base-first-01.jsonl
```

The review prints planned and observed count changes for every motor, missing observations, and packet problems. **An exit code of zero means the log is structurally reviewable, not that the motion was physically safe or accurate.** Examine the actual physical observations before another command.

If the first base trial behaves as expected, reproduce the known start pose and use a new run for the opposite base direction. Then consider shoulder and elbow, one direction at a time, at no more than 1° per run. Each run performs a new home search. Stop at the first unexpected physical motion, sign/count disagreement, stale response, timeout, or uncertain motor state. Do not retry a faulted command during this session.

## 5. After the visit

Keep the original logs, source commit/fingerprint, start-pose photo, and notes. Compare repeated home counts, fresh packet timing, commanded versus observed count changes, and physical direction for each joint. Do not infer physical degrees from the inherited software scale.

The next milestone is independent angle measurement using a protractor or digital angle gauge. Collect several physical points approached from both directions plus separate verification points; fit per-arm scale, zero, and backlash; set software limits only inside the measured clear region. See [physical calibration](PHYSICAL_CALIBRATION.md). Absolute angle control follows verified measurements. Wrist jogs remain blocked in the Python adapter until both wrist motors and physical pitch/roll are checked.

ScorBot-software values may be recorded separately for comparison, but they are not physical ground truth. Do not run ScorBot software and Python against the controller at the same time.
