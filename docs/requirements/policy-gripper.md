# PolicyArm binary gripper requirements
Status: baseline v1 2026-11-02 (task brief)

## Goal
Let policy callers open and close the vendor gripper after PolicyArm's vendor Home.
Physical-arm behaviour is unverified; verification is desk-only.

## Requirements
| ID | Type | Statement | Check |
|---|---|---|---|
| PG01 | SHALL | Open and close after connect reach the simulated vendor targets and return the existing move result. | test: `tests_extra/test_policy_arm_gripper.py::PolicyArmGripperTests::test_open_close_and_stream_resume` |
| PG02 | SHALL NOT | Gripper calls send commands when disconnected or either vendor Home flag is cleared. | test: `tests_extra/test_policy_arm_gripper.py::PolicyArmGripperTests::test_disconnected_and_unhomed_refuse_without_commands` |
| PG03 | SHALL | Gripper messages match the existing recorded DLL fixture's commands, endpoints and ramp tolerance. | test: `tests_extra/test_policy_arm_gripper.py::PolicyArmGripperTests::test_packets_match_the_recorded_dll` |
| PG04 | SHALL | End the joint stream before the gripper call and resume it only after success, preserving joint targets, speed and wrist configuration. | test: `tests_extra/test_policy_arm_gripper.py` |
| PG05 | SHALL NOT | Verification opens real USB, moves hardware or records new DLL data. | review: new tests use only SimulatedScorbot and mocks; existing number-only fixture reused |

## Out of scope
- Force control, intermediate widths, simultaneous joint/gripper motion and LeRobot plugin schema changes.
- Real USB access and physical-arm validation (unverified).

## Assumptions
- Existing vendor gripper and stream stop/start checks remain authoritative.
- PolicyArm calls are sequential, as with its existing act/close interface.

## Open questions
- None for desk implementation; physical-arm behaviour remains unverified.

## Change log
- 2026-11-02 Baseline from the authorized task brief.
