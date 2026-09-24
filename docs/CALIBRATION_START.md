# ER-4U calibration: first measurements

This is the starting point for calibrating **your physical arm**. The Python adapter reports timestamped raw controller responses and offers small relative jogs after homing. It has no verified joint zeros, encoder scale, direction, backlash, joint limits, tool position, or accuracy. Do not copy the legacy constants in `openScorbot/conf.py` into a calibration file as if they had been measured on your robot.

The [Intelitek ER-4u manual](https://downloads.intelitek.com/Manuals/Robotics/ER-4u/ER_4u_B.pdf) describes five home switches and a differential wrist driven by motors 4 and 5. The two wrist encoder values are **motor** counts; neither is a wrist pitch or roll angle by itself. The controller's home-switch bits also need verification on this unit.

## 1. Collect an idle raw-state record

Finish [Windows setup](../START_HERE_WINDOWS.md) and the USB preflight in the [bench runbook](WINDOWS_BENCH_RUN.md) first. Secure the robot base, clear the workspace, keep the physical emergency stop within reach, and have an operator at the arm. Close Intelitek software and the old GUI before Python uses USB. The Python connection handshake briefly sends motor-on packets before requesting motor-disable, even though this script requests no motion.

From the repository root in PowerShell:

```powershell
.\.venv\Scripts\python.exe .\examples\record_raw_state.py --robot-id lab-er4u-1 --pose-note "initial stationary pose" --seconds 10 --output .\logs\idle-01.jsonl --acknowledge-connect-handshake
```

The script checks that Python, USB libraries, and controller `09F1:0007` are visible, then saves approximately 20 samples and a separate controller event log. It refuses to overwrite either file. `logs/idle-01.jsonl` starts with session metadata; each following line contains `host_monotonic_ns` and the raw `RobotState`. `logs/idle-01.controller.jsonl` contains controller events. Keep both files and note the date, controller label, driver, arm pose, and anything the arm did. If the arm moves unexpectedly or the motor state is uncertain, use the physical stop and end the session.

**Interpretation limits:** `encoder_counts` are unsigned 16-bit raw values, not degrees. `home_switch_bits` is an undecoded controller byte. The UTC timestamp is assigned by the host when `get_state()` decodes a copied response. `packet_index` and `host_monotonic_ns` indicate a successful recent host USB read; repeated identical counts can still be normal at rest. The `homed` flag reflects Python command history, not an independent switch check.

## 2. Establish a repeatable home

Before commanding homing, independently identify the required physical start pose from your controller/robot documentation and an experienced operator. The inherited homing code assumes that pose and searches shoulder, elbow, wrist pitch, wrist roll, then base; it cannot safely be treated as home-from-anywhere. First verify which switch bit changes for each axis and whether it is active high or low. Then, under supervision, record raw counts after several successful homes from the same confirmed start pose. Compare count spread and any switch or error changes. Do not automate repeat homing until the first supervised run and shutdown behavior are understood.

## 3. Measure each joint's encoder mapping

Once homing and one small jog have been verified, perform one supervised joint experiment at a time within a **physically confirmed clear region**. Record raw motor counts before and after each small positive and negative jog. Measure the **actual** joint angle with an independent reference such as a suitable angle gauge; the requested jog angle is only a command, not ground truth. Take more than one point in both directions to expose backlash and direction differences. Do not sweep toward a mechanical stop to discover limits.

For each experiment, preserve the raw log plus: robot ID, home attempt, joint, requested jog, speed, independent before/after angle, raw counts before/after, observed direction, switch bits, controller errors, and any stop or fault. A useful row schema is:

```text
run_id,joint,requested_deg,measured_before_deg,measured_after_deg,base_before,base_after,shoulder_before,shoulder_after,elbow_before,elbow_after,wrist_motor_1_before,wrist_motor_1_after,wrist_motor_2_before,wrist_motor_2_after,switch_bits_before,switch_bits_after,notes
```

Fit signed count change against **measured** angle change. Handle 16-bit counter wrap only when successive samples are close enough to determine the correct direction; a large jump or missing packet makes the wrap ambiguous. For wrist pitch and roll, use both wrist motor deltas and fit a two-axis mapping from measured pitch and roll changes. Check reverse-direction data separately for backlash. Keep the gripper as its own calibration experiment.

## 4. Build calibrated control after the data is credible

Use those measurements to define home offsets, count-to-angle mapping, direction, repeatability, and conservative software limits inside known physical clearance. Then verify commanded motion against an independent angle measurement at multiple points and speeds. Later measure link geometry and tool center point for Cartesian moves, and camera-to-robot transforms before any vision-language-action control. Save calibration values with robot ID, date, method, data-file references, and uncertainty so each value can be traced back to an experiment.

Use the [supervised bench procedure](ARM_CONTROL_BENCH.md) for one home and jog at a time. When independent angle measurements are available, use the [physical calibration guide](PHYSICAL_CALIBRATION.md) and `scripts/fit_calibration.py`. Keep the original logs and measurement CSV with the resulting per-arm calibration.
