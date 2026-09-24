# Physical joint calibration data

The [supervised bench procedure](ARM_CONTROL_BENCH.md) establishes USB feedback, homing and small relative jogs first. No count-to-angle scale or safe limit from a manual is automatically applied. The ER-4U manual gives a 20-slot encoder disk, but does not establish how many counts this Python response reports per motor revolution. Homed shoulder and elbow angles are not necessarily zero.

## Measurement files

Collect base, shoulder and elbow one at a time with a protractor or digital angle gauge. Start in a confirmed clear region and measure each joint's **actual physical angle**. Use a separate CSV file for each calibration attempt and keep the original bench JSONL logs.

```csv
robot_id,joint,role,approach,reference_source,encoder_count,measured_angle_deg,requested_target_deg
lab-er4u-1,base,home,,physical,1000,0,
lab-er4u-1,base,home,,physical,1001,0,
lab-er4u-1,base,home,,physical,999,0,
lab-er4u-1,base,fit,-,physical,900,-10,
lab-er4u-1,base,fit,+,physical,950,-5,
lab-er4u-1,base,fit,+,physical,1050,5,
lab-er4u-1,base,fit,-,physical,1100,10,
lab-er4u-1,base,verify,-,physical,925,-7.5,
lab-er4u-1,base,verify,+,physical,1025,2.5,
lab-er4u-1,base,verify,-,physical,1075,7.5,
lab-er4u-1,base,move_verify,-,physical,950,-5,-5
lab-er4u-1,base,move_verify,+,physical,1050,5,5
lab-er4u-1,base,move_verify,-,physical,975,-2.5,-2.5
```

**These numbers are synthetic format examples, not ER-4U calibration data.** Do not use them to command the robot. `home` rows are separate supervised home trials; they anchor the physical home angle and encoder count. `fit` rows estimate count scale. `verify` rows are held out from fitting. `move_verify` rows document the physically measured ending angle and intended target after a small legacy jog; record both approach directions. `reference_source` must be `physical`. Put SCORBASE displays in a separate vendor-reference file, not this CSV.

Each joint needs at least 3 home, 4 fit, 3 verify and 3 move-verification rows, with both directions in all non-home groups. The tool rejects ambiguous 16-bit half-range differences, home spread over 50 counts or 2 degrees, holdout or motion error over 2 degrees, and a calibration span under 2 degrees.

Set the intended software limits **within your measured clear region**, leaving at least 0.5 degree between each limit and the most extreme measured angle. For example, if you actually measured a safe base range beyond -10 to +10 degrees:

```json
{"base": {"soft_min_deg": -7, "soft_max_deg": 7}}
```

Save that as `limits.json`, then run:

```powershell
.\.venv\Scripts\python.exe .\scripts\fit_calibration.py --measurements .\measurements\arm-1.csv --limits .\measurements\limits.json --robot-id lab-er4u-1 --output .\calibration\lab-er4u-1.json
```

The fitter never connects to USB and refuses to overwrite an existing output. The JSON records robot ID, CSV hash, fit and verification counts, measured limits, home repeatability, and directional bias. Keep the source CSV and bench logs with it. Review actual repeated target-versus-achieved results before loading the calibration for supervised `move_joint` use.

## Count wrap and wrist

Raw counts are unsigned 16-bit values. The fitter uses the shortest modular difference to the home count and rejects the exactly ambiguous half-range case. Its measured region must stay small enough that the actual path from home cannot cross half the counter range; if it can, collect a continuous trace and extend the unwrapping model before using those rows.

Base, shoulder, and elbow use one motor count each. Pitch and roll share two wrist motors, so the one-axis fitter and `move_joint` do not accept them. Calibrate the wrist as a two-input mapping after recording physical pitch and roll in both motor-direction combinations.
