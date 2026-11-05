# LeRobot simulator teleoperation requirements
Status: baseline v1 2026-11-04 (task brief)

## Goal
Let an operator record a local simulator demo with all five joints and the gripper on letter keys.
Show the observed jaw opening in both twin cameras; physical-arm behaviour is unverified.

## Requirements
| ID | Type | Statement | Check |
|---|---|---|---|
| LT01 | SHALL | Step base with a/d, shoulder with s/w, elbow with k/i, pitch with t/g and roll with l/j, positive/negative respectively, in either letter case. | test: `tests_extra/test_lerobot_scorbot_plugin.py::TeleopMapTests::test_each_joint_key_steps_exactly_its_motor_in_either_case` |
| LT02 | SHALL | Move each held joint by at most one configured step per call; duplicate keys count once and opposite keys cancel. | test: `tests_extra/test_lerobot_scorbot_plugin.py::TeleopMapTests::test_duplicate_joint_keys_still_move_at_most_one_step`; test: `tests_extra/test_lerobot_scorbot_plugin.py::TeleopMapTests::test_opposite_keys_cancel` |
| LT03 | SHALL | Open with o (100 percent), close with c (0 percent), and hold when neither or both are held, without mutating the input target. | test: `tests_extra/test_lerobot_scorbot_plugin.py::TeleopMapTests::test_the_gripper_keys_open_close_and_hold` |
| LT04 | SHALL NOT | Use right/left arrows, Esc, n, r or q for motion; unbound and invalid keys hold the target. | test: `tests_extra/test_lerobot_scorbot_plugin.py::TeleopMapTests::test_unbound_record_and_invalid_keys_hold_without_mutating_the_target` |
| LT05 | SHALL | Read held keys through LeRobot's keyboard listener and hold the last action when it stops. | test: `tests_extra/test_lerobot_scorbot_plugin.py::WithLerobotTests::test_keys_teleop_maps_lerobots_keyboard_action_to_joint_targets` |
| LT06 | SHALL | Map 0..100 percent jaw opening linearly to positive slide travel, clamp outside that interval and reject None and NaN. | test: `tests_extra/test_mujoco_twin.py::JawOpeningTests::test_half_open_gripper_has_half_jaw_travel`; test: `tests_extra/test_mujoco_twin.py::JawOpeningTests::test_jaw_opening_clamps_above_one_hundred_percent`; test: `tests_extra/test_mujoco_twin.py::JawOpeningTests::test_jaw_opening_clamps_below_zero_percent`; test: `tests_extra/test_mujoco_twin.py::JawOpeningTests::test_none_jaw_opening_raises`; test: `tests_extra/test_mujoco_twin.py::JawOpeningTests::test_nan_jaw_opening_raises` |
| LT07 | SHALL | Keep the five arm joints separate from two jaw joints; opened jaw centres are 48 mm apart and closed plates nearly touch. | test: `tests_extra/test_mujoco_twin.py::JawOpeningTests::test_jaw_joints_are_separate_from_the_five_arm_joints`; test: `tests_extra/test_mujoco_twin.py::MujocoTwinTests::test_the_jaws_close_and_open_to_the_old_fixed_plate_separation`; review: jaw geometry in `scorbot/mujoco_twin.py` leaves a 1 mm closed gap |
| LT08 | SHALL | Hold commanded jaw positions within 0.1 mm when physics steps for 3 s. | test: `tests_extra/test_mujoco_twin.py::MujocoTwinTests::test_the_arm_holds_a_commanded_pose` |
| LT09 | SHALL | Pose the twin arm and jaw under one lock from the same gripper-inclusive observation before rendering. | test: `tests_extra/test_lerobot_scorbot_plugin.py::SimDriveTests::test_the_twin_receives_counts_and_gripper_from_the_same_observation`; test: `tests_extra/test_lerobot_scorbot_plugin.py::TwinCameraTests::test_the_twin_render_shows_the_jaw_at_the_observed_gripper_pos`; review: `TwinScene.set_counts` holds its lock for both updates |
| LT10 | SHALL | Document Windows setup, the key table, global key capture, a local two-episode 20 s recording at 10 fps with two 320x240 cameras and 5 s resets, dataset reload and simulator replay. | review: `plugins/lerobot_robot_scorbot/README.md`, "Record your first sim demo", against the task brief and installed LeRobot CLI config |
| LT11 | SHALL NOT | Verification open real USB, move hardware, add a dependency or upload a dataset. | review: allowed test commands use simulator controllers and mocks; README recording uses `simulate=true` and `push_to_hub=false`; package dependencies unchanged |

## Out of scope
- Velocity jog (#133), gamepad control, physical-arm direction checks and real USB access.
- Block contact in twin camera frames (the arm is posed without physics steps).
- Changes to PolicyArm, legacy code or the step-1 test specifications.

## Assumptions
- The separate LeRobot venv supplies its existing keyboard listener, pynput and MuJoCo.
- PolicyArm remains responsible for joint travel limits and binary, blocking gripper commands.
- Twin geometry is nominal and unmeasured; its jaw travel is a visual approximation.

## Open questions
- Gamepad support needs a new dependency; none is added here.
- The `TypeError` comparing `MagicMock` with `int` from a leaked legacy thread in `tests/test_home_stop.py`, seen in the #146 CI log, is outside this scope.
- No person has tried these keys under `lerobot-record`; which way the real arm turns remains unverified.

## Change log
- 2026-11-04 Baseline from the authorized issue #143 step-2 task brief and step-1 tests.
