# Idle hold fault fixes requirements
Status: baseline v1 2026-11-03 (authorized task brief)

## Goal
Prevent stale or faulted idle setpoints and queued motion after an idle impact.
Checks run offline against fake endpoints; physical-arm behavior remains unverified.

## Requirements
| ID | Type | Statement | Check |
|---|---|---|---|
| IH01 | SHALL | Idle impact cancels pending motion, rejects it before execution, and queues one disable without taking the motion lock on sync. | test: `tests/test_impact_detect.py::IdleImpactTests::test_idle_impact_rejects_a_jog_that_passed_the_fault_check` |
| IH02 | SHALL | Disconnect and connection startup clear SDK and legacy hold caches before new handshake or idle packets. | test: `tests/test_idle_hold.py::RobotIdleHoldTests::test_reconnect_clears_holds_before_the_handshake_and_workers`, `test_disconnect_clears_all_hold_caches_even_without_a_device`; `LegacyIdleHoldTests::test_exit_clears_the_worker_hold_callback` |
| IH03 | SHALL | Settle exhaustion stops at measured counts, retains result 2 and never holds the failed target. | test: `tests/test_idle_hold.py::LegacyIdleHoldTests::test_settle_exhaustion_stops_at_measured_counts_and_keeps_result_2` |
| IH04 | SHALL | Both calibration errors latch the session and faulted idle callbacks return no hold. | test: `tests/test_idle_hold.py::RobotIdleHoldTests::test_jog_calibration_error_latches_the_fault_and_drops_holds`, `test_angle_calibration_error_latches_the_fault_and_drops_holds`, `test_faulted_idle_setpoint_ignores_a_late_hold` |
| IH05 | SHALL NOT | This work opens USB, moves hardware, uses the network, adds dependencies, changes setHome.py, commits or pushes. | review: fake endpoint tests, commands and final diff |

## Out of scope
- Hardware validation, protocol changes and unrelated cleanup.

## Assumptions
- Existing fake endpoints model the packet fields needed by these regressions.

## Open questions
- None.

## Change log
- 2026-11-03 Baseline from the authorized four-bug task brief.

## Focused verification (2026-11-03)
Each regression was run and observed failing before its production fix.
The combined idle-hold and impact suites passed: 27 passed, 40 subtests passed.

| ID | Result | Evidence |
|---|---|---|
| IH01 | pass | Both queue orderings ran with real sync and command threads; no jog dispatch and one disable. |
| IH02 | pass | EXIT callback, disconnected-instance cleanup and two fake-link connection cycles. |
| IH03 | pass | Base, shoulder, elbow and wrist exhaustion retained only result 2 and held measured counts. |
| IH04 | pass | Both calibration entry points and a late hold after a latch returned no faulted setpoint. |
| IH05 | pass | Fake endpoints only; no network, hardware, dependency, excluded-file or git publication actions. |
