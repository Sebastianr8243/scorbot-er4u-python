# Physical joint calibration data

The [supervised bench procedure](ARM_CONTROL_BENCH.md) establishes USB feedback, homing and small relative jogs first. No published count-to-angle scale or travel range is applied as calibration. Determine the physical angle and safe region from independent measurements on this arm. Homed shoulder and elbow angles are not assumed to be zero.

## Before measuring

- **Home must repeat first.** From the same confirmed start pose, record the raw counts after several supervised homes and compare the spread. Homing assumes that start pose; it is not home-from-anywhere.
- **One joint at a time**, inside a physically confirmed clear region, with small jogs in both directions so backlash and direction differences show. Never sweep toward a mechanical stop to find a limit.
- **The requested angle is a command, not a measurement.** Only an independent reference (protractor, digital angle gauge) counts. Do not copy values from the manuals, a vendor display or the inherited software scales into a calibration file.
- **The two wrist encoders are motor counts.** Neither is pitch or roll by itself; the wrist needs its own two-motor experiment and stays gated until then. The gripper is its own experiment too.
- Keep the raw bench logs with every measurement CSV, so each calibration value can be traced to an experiment.

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

Set the intended software limits **within your measured clear region**, leaving at least 2.5 degrees between each limit and the most extreme measured angle. For example, if you actually measured a safe base range beyond -10 to +10 degrees:

```json
{"base": {"soft_min_deg": -7, "soft_max_deg": 7}}
```

Save that as `limits.json`, then run:

```powershell
.\.venv\Scripts\python.exe .\scripts\fit_calibration.py --measurements .\measurements\arm-1.csv --limits .\measurements\limits.json --robot-id lab-er4u-1 --output .\calibration\lab-er4u-1.json
```

The fitter never connects to USB and refuses to overwrite an existing output. The JSON records robot ID, CSV hash, fit and verification counts, measured limits, home repeatability, and directional bias. Keep the source CSV and bench logs with it. Review actual repeated target-versus-achieved results before loading the calibration for supervised `move_joint` use.

## Manual priors and bounds

`scorbot/nominal.py` holds the ER-4u manual's values (#100343 Rev. B, pp. 4-6), each with its source. They are **nominal, from the manual, not measured**, and serve only as priors and sanity bounds:

- **Soft-limit span.** `load_calibration` rejects a joint whose `soft_max_deg - soft_min_deg` exceeds the manual travel: base 310°, shoulder 165° (+130/−35), elbow 260° (±130). Only the span is checked. The manual does not define where 0° is or which way is positive, and this code's home angle comes from your protractor, so the manual's signed limits cannot be compared with calibrated angles.
- **Counts per degree.** Expected scale = encoder counts per motor revolution × gear ratio / 360. The ER-4u manual gives two ratios for motors 1-3 (127.1:1 in the spec table, 127.7:1 in the parts list) and no wrist joint ratio. It gives neither encoder counts per revolution nor the decode mode, so counts per degree cannot be computed from the manual and must be measured. Intelitek's controller parameter files give vendor defaults of 141.89 counts/° (base), 113.51 (shoulder, elbow) and 27.90 (per wrist motor), consistent with an 80 counts/motor-rev encoder, a 127.7:1 gearbox and a final 5:1 (base) or 4:1 (shoulder, elbow) gear stage. `scripts/fit_calibration.py` warns when a fit is more than 5% off these defaults. They are priors, not measurements; the legacy wrist-pitch scale (33.8) disagrees with them.
- **Fit check.** After fitting, `fit_calibration.py` prints each joint's counts per degree and the implied counts per motor revolution. It prints a `WARNING` (and still writes the file) when the fitted magnitude differs from the legacy `motion_profile.COUNTS_PER_DEGREE` by more than 10%. Record these lines with the calibration; across joints and arms they test the ~400 counts/revolution hypothesis. The sign of the fitted scale is not compared.

## Count wrap and wrist

Raw counts are unsigned 16-bit values. The fitter uses the shortest modular difference to the home count and rejects the exactly ambiguous half-range case. Its measured region must stay small enough that the actual path from home cannot cross half the counter range; if it can, collect a continuous trace and extend the unwrapping model before using those rows.

Base, shoulder, and elbow use one motor count each. Pitch and roll share two wrist motors, so the one-axis fitter and `move_joint` do not accept them. Calibrate the wrist as a two-input mapping after recording physical pitch and roll in both motor-direction combinations.
