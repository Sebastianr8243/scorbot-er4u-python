# DLL gaps requirements (Time, jaw parity, queued digital output)
Status: baseline v1 2026-11-04 (issue #129, authorized task brief)

## Goal
Move the open `DLL_COVERAGE.md` build-order rows to **match**, each proved against the lab `USBC.dll` (sha256 `6118465d...`) by static analysis and a byte test, or leave them open with the blocking evidence.
Checks run offline against the simulator, the desk wire fake and recordings of the DLL on the pipe fake; physical-arm behavior remains unverified.

## Requirements
| ID | Type | Statement | Check |
|---|---|---|---|
| DG01 | SHALL | `move_joint_vendor(total_ms=...)` runs the DLL's `Time`: the ramp lasts `total_ms` and a time below the full-speed duration is refused (the DLL's 909) with nothing sent. | test: `tests/test_vendor_move.py::Planner::test_a_time_below_the_full_speed_duration_is_what_the_dll_refuses`, `tests/test_vendor_move.py::Wiring::test_fixture_messages_and_consecutive_targets` (LEDGER PL97, PL99) |
| DG02 | SHALL | Every gripper move (open, close, `move_gripper_mm`) sends the DLL's setpoint on every message: the joint-move duration at speed factor 1.0 with the gripper's `MaxSpeed` 15000 and `MaxAccel` 50000, sampled once per 24 ms tick from `4F 20 53` on, for full strokes and short moves. | test: `tests/test_vendor_gripper.py::Planner::test_the_ramp_is_the_dlls_s_curve`, `test_the_duration_is_the_dlls_for_the_gripper`, `tests/test_vendor_gripper.py::Desk::test_post_home_open_and_close_send_the_dlls_messages` (PL100) |
| DG03 | SHALL | A jaw readback gives the DLL's `GetJaw` pair: whole percent (truncated) and mm (rounded) from the gripper count, 0 below 0. | test: `tests/test_vendor_gripper.py::Planner::test_get_jaw_is_whole_percent_and_rounded_mm`, `tests_extra/test_policy_arm_gripper.py::PolicyArmGripperTests::test_jaw_is_get_jaws_whole_percent_and_mm` (PL101) |
| DG04 | SHALL | `set_digital_output(n, on, immediate=False)` sends no `47`, holds the setpoints for 4 rounds of 10 replies plus 100 ms each, then sends `44` with the whole word; `immediate=True` (the default) is unchanged. | test: `tests/test_io_outputs.py::QueuedOutputTests::test_matches_the_dlls_recorded_wait`, `test_immediate_still_sends_clear_then_the_word`, `tests/test_io_outputs.py::QueuedOutputRobotTests::test_queued_write_sends_no_clear_and_holds_before_the_word` (PL102) |
| DG05 | SHALL | Every new behavior that sends bytes is marked unverified on the arm in its docstring and its LEDGER/coverage row. | review: `scorbot/io.py`, `scorbot/vendor_gripper.py`, PL100-PL102, `DLL_COVERAGE.md` rows |
| DG06 | SHALL NOT | This work opens a USB device, moves the arm, puts DLL code or raw recordings in the repo (no more than 5 decompiled lines quoted), or changes `openScorbot/libdef.py`, `openScorbot/libcomm.py` or `scorbot/state.py`. | review: final diff; the desk run used `pipe_server.py`, whose guard refuses when a ScorBot USB device is present |

## Out of scope
- `ShowTorque` / `CloseTorque` callback lifecycle: it needs the idle loop in `openScorbot/libcomm.py`, owned by another worker.
- `GetTPInfo` key text, Cartesian moves, bench checks on the arm.

## Assumptions
- The pipe fake answers like the controller in the fields these checks read (counts, echo); its pacing (about 10.9 ms per reply in the 2026-11-04 run) is not the controller's.

## Open questions
- Whether the controller follows the shorter gripper ramps and the queued `44` as the fake does (bench).

## Change log
- 2026-11-04 Baseline from issue #129.
