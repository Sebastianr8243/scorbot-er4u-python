# Queued output review requirements
Status: baseline v1 2026-11-04 (user's five review findings)

## Goal
Prevent a queued digital write after a stop, fault or stale queue feedback.
Keep jaw and recording claims tied to the count-frame code and desk evidence.

## Requirements
| ID | Type | Statement | Check |
|---|---|---|---|
| QO01 | SHALL NOT | Send output message 44 after operator stop, pendant Abort, emergency feedback or a session fault during its queue wait. | test: `tests/test_io_outputs.py::QueuedOutputRobotTests::test_interrupt_during_wait_never_sends_the_output` and `QueuedOutputTests::test_feedback_interrupts_never_send_the_output` |
| QO02 | SHALL NOT | Treat repeated or backward echoed IDs as drain progress; a wait without progress shall fail within the existing 5 s deadline without message 44. | test: `tests/test_io_outputs.py::QueuedOutputTests::test_stale_echo_never_drains_the_queue`, `test_advanced_is_a_small_forward_step_on_the_ring`, `test_stale_echo_fails_at_the_source_with_nothing_sent` |
| QO06 | SHALL | Refuse a queued write, sending nothing and latching no fault, when a stop was requested before it started; the stop is used up. | test: `tests/test_io_outputs.py::QueuedOutputRobotTests::test_a_pending_stop_refuses_the_queued_write_once` |
| QO03 | SHALL | Read raw gripper 0x800CB1 as 3250 DLL counts and (65 percent, 46 mm). | test: `tests_extra/test_policy_arm_gripper.py::PolicyArmGripperTests::test_jaw_reads_the_dll_count_from_raw_positive_feedback` |
| QO04 | SHALL | Bound recorded queue wait packet counts and elapsed time using the precision supported by the recordings. | test: `tests/test_io_outputs.py::QueuedOutputTests::test_matches_the_dlls_recorded_wait` |
| QO05 | SHALL | State which full-stroke recordings match exactly and which differ, with physical-arm behavior marked unverified. | test: `tests/test_vendor_gripper.py::Planner::test_the_ramp_is_the_dlls_s_curve`; review: `docs/protocol/DLL_COVERAGE.md` |

## Out of scope
- USB access, hardware motion, dependencies, public signature changes, merging.

## Assumptions
- Desk recordings describe software behavior, not physical output-line or jaw behavior.
- Message IDs use the existing 1..255 ring.

## Open questions
None.

## Change log
- 2026-11-04 Baseline transcribed from the user's approved review-fix brief.
- 2026-11-04 QO06 added (a pending stop refuses the queued write, like the gripper); QO02 checks extended; pushing allowed by the mission-control brief.
