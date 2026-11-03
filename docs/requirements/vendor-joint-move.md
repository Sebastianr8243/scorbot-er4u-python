# Vendor joint move requirements
Status: baseline v1 2026-11-03 (task brief)

## Goal
Expose the recorded vendor joint profile as an explicit Stack 2 opt-in. All checks run on desk models, with no arm or USB device.

## Requirements
| ID | Type | Statement | Check |
|---|---|---|---|
| VJM01 | SHALL | Send the recorded Time(3000) and multi-axis setpoints in order, then hold the target, with unused slots zero and no normal start/end commands. | test: `tests/test_vendor_move.py::Wiring::test_fixture_messages_and_consecutive_targets` |
| VJM02 | SHALL | Start at vendor home joints, then at the previous completed target rather than encoder readings. | test: `tests/test_vendor_move.py::Wiring::test_fixture_messages_and_consecutive_targets` |
| VJM03 | SHALL NOT | Send motion without a connection, enabled motors, an idle stream, a fresh vendor Home and a duration at least the acceleration bound, or outside the existing joint limits. | test: `tests/test_vendor_move.py::Wiring::test_refusals_send_nothing` |
| VJM04 | SHALL | Invalidate the commanded target on other motion or interruption; require a fresh vendor Home. | test: `tests/test_vendor_move.py::Wiring::test_other_motion_invalidates_until_home` |
| VJM05 | SHALL | Honor stop requests and worker reply/fault checks using the existing generator runner. | test: `tests/test_vendor_move.py::Wiring::test_stop_holds_and_switches_off` and `test_faults_latch` |
| VJM06 | SHALL NOT | Change default wire messages or connect to hardware in tests. | test: `tests/test_desk_byte_check.py`; script: full task verify command exits 0 |

## Out of scope
- Speed-percent duration decoding, physical-arm validation and a second worker loop.

## Assumptions
- Vendor joint radians and the source model's toolbox degrees use opposite shoulder, elbow and pitch signs.
- Message pacing on a desk model gives no physical-controller timing evidence.

## Open questions
None for this desk-only wiring.

## Change log
- 2026-11-03 Initial baseline from the requested task and checks.

## Verification (2026-11-03)
| Requirement | Result | Evidence |
|---|---|---|
| VJM01, VJM02 | pass | Real-worker desk tests matched the recorded messages, consecutive targets and idle holds. |
| VJM03 | pass | Refusal tests assert no command call, no queued motion and no changed wire setpoints. |
| VJM04 | pass | Jog, gripper, legacy command and stream tests refuse the next vendor move until another Home. |
| VJM05 | pass | Stop holds/off and following-fault tests passed; the existing source reply/lag checks remain in use. |
| VJM06 | pass | Full requested verify command exited 0: 1776 passed, 14 skipped, 12970 subtests passed; docs ok. The unchanged desk byte check passed. |

The full suite reported one warning in the existing mocked home-stop worker exit (a sequence comparison with a test double). Physical-arm behavior and controller pacing remain unverified.
