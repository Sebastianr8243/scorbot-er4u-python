# ER-4U supervised arm-control bench

This runbook creates evidence for this particular arm. It does not certify an emergency stop, soft limits, or autonomous operation. Use the physical emergency button if motion or motor state is uncertain. The Python `disable()` command is queued behind the legacy worker.

## Sources and distinctions

| Source | What it establishes | What it does not establish |
| --- | --- | --- |
| [ER-4U robot manual](https://downloads.intelitek.com/Manuals/Robotics/ER-4u/ER_4u_B.pdf), Rev B | Five home switches, 20-slot encoder disks, published travel, and two-motor differential wrist | Controller counts per degree or this arm's measured zero and limits |
| [Controller-USB manual](https://downloads.intelitek.com/Manuals/Robotics/ER-4u/Controller-USB-H.pdf), Rev H | Emergency switch and documented motor-off on USB timeout | Verified stop latency and this controller's behavior with the Python driver |
| [SCORBASE manual](https://downloads.intelitek.com/Manuals/Robotics/ER-4u/Scorbase_USB_I.pdf), Rev I | Search Home versus Go Home; vendor angle and count displays | Independent physical angle measurements |
| [ER-4U data sheet](https://www.intelitek.com/resources/pdf/35-1005-8600_DS_HW_SCORBOT-ER4u_Ver%20K.pdf), Rev K | Another published operating range | Safe limits for this unit |

The ER-4U manual's ratio table lists 127.1:1 for motors 1-3, while its parts list lists 127.7:1. The data sheet gives 158 degrees for shoulder travel, while the manual's -35 to +130 values total 165 degrees. These are reference values, not calibration constants. The repo's bundled `references/manual_scorbot.pdf` is labeled ER 4pc.

## 1. Prepare one session

Install the Python environment and run `python -m scorbot.preflight`. Secure the base, clear the full travel path, identify the *legacy homing start pose*, and keep an operator at the controller emergency button. Confirm the controller and arm labels, bound USB driver, and repository revision. Do not run SCORBASE and Python at the same time. The legacy connection handshake briefly commands motors on before requesting disable.

Start with [record_raw_state.py](../examples/record_raw_state.py) and review the idle log. A state sample now includes `packet_index` and `host_monotonic_ns`; the SDK waits for a recent complete USB response. A changing packet index proves a new host USB read, not a new physical pose.

## 2. Record one supervised movement

Run this on the robot PC, one joint and direction at a time. Review the command and pose before typing HOME or MOVE.

```powershell
.\.venv\Scripts\python.exe .\examples\bench_joint.py --output .\logs\base-plus-01.jsonl --robot-id lab-er4u-1 --arm-label "arm nameplate" --controller-label "controller nameplate" --driver "bound USB driver" --start-pose-note "operator-confirmed legacy pose" --joint base --delta 1 --speed 10 --acknowledge-supervised-motion
```

The script refuses to overwrite its bench or controller log. It records session metadata, raw state and switch bits before and after homing and the jog, command results, monotonic and UTC timestamps, and your observation. Make a new file for each direction. Begin with a physically clear base direction; progress to other joints only after inspecting prior results. No script in this repo presses the physical emergency stop.

Record three or more supervised homes from the same confirmed start pose on separate runs and compare home counts. A completed Python `home()` reflects the legacy search command and a fresh response; switch polarity and mechanical repeatability still need operator verification. **Go Home is a position move, not a switch search.**

## 3. Characterize faults and the controller stop

Record controller motor LED state at connect, enable, disable, and disconnect. With a qualified operator and a lab-approved procedure, observe the physical emergency button and the controller response to communication loss. Do not intentionally disconnect USB during motion as a casual software test. Document what happened, the time to stop if it can be measured, and how the controller was reset. Until observed, the manual's communication-failure shutdown remains a claim about the controller, not a verified property of this Python session.

If homing, jogging, or feedback times out, stop the session. The SDK latches a fault, invalidates home, and rejects further movement. Use the physical stop whenever motor state is uncertain. Do not retry a faulted motion.

## 4. Physical calibration

Use [the measurement guide](CALIBRATION_START.md). Each base/shoulder/elbow row needs an independent measured joint angle, not the requested jog angle or the SCORBASE display. A protractor or suitable digital angle gauge is enough to begin. Keep measurements in a small, clear region. The supplied fitter requires repeated homes, both movement directions, separate fit and verification points, and repeated measured moves. It writes a validated calibration only when those checks pass. The resulting soft limits must be inside the measured region.

SCORBASE can be run in a separate session to record its displayed counts and joint angles as `vendor_reference`. This can help diagnose sign or scale mismatch, but it does not replace independent angle measurements. Switching USB drivers between applications may be necessary and must follow the lab's driver procedure.

## 5. Enabling absolute angles

Pass the physical robot ID and the validated JSON file to `Scorbot(robot_id="lab-er4u-1", calibration_path="calibration/lab-er4u-1.json")`. After an accepted home, `get_joint_angles()` reports only calibrated joints. `move_joint("base", target_degrees)` allows a single bounded step within measured soft limits and reads back the result. It refuses a target more than `max_jog_degrees` from the current calibrated reading. A target error over 2 degrees faults the session. This is still supervised motion, not a trajectory or safety-rated stop.

Wrist pitch and roll need a two-motor calibration: the ER-4U manual says opposite directions produce pitch and the same direction produces roll. Neither wrist motor's encoder count alone is a wrist angle. The gripper needs a separate study. Cartesian and model-issued movement remain unavailable.
