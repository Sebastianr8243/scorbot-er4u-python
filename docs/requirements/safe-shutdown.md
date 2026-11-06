# Safe shutdown after any failure
Status: baseline v1 2026-11-05 (issue #185; code approved by Sebastian through mission control, systems review R1-R6)

## Goal
Whenever something goes wrong while the motors may be on, the software attempts motors-off once, tries every
exchange of the off group even if one fails, and never turns the motors back on after a fault. `disable()` works
while faulted. Desk checks on fake endpoints only. Physical motor state stays unverified; the physical stop
is authoritative.

## Requirements
| ID | Type | Statement | Check |
|---|---|---|---|
| SD01 | SHALL | A USB error inside any legacy jog (orders 4-13) attempts motors-off once, restores the last complete reply and media, reports result 1 and keeps the command worker alive. | test: `tests/test_safe_shutdown.py::LegacyOrderErrorTests::test_a_usb_error_in_every_jog_order_switches_the_motors_off` |
| SD02 | SHALL | A USB error in the gripper clamp (orders 14, 15) ends the same way: motors-off attempted once, result 1, worker alive. | test: `tests/test_safe_shutdown.py::LegacyOrderErrorTests::test_a_usb_error_in_the_clamp_switches_the_motors_off` |
| SD03 | SHALL | A fault latched by the session (worker death, command timeout, emergency input, impact, any other `_latch_fault`) while the link is alive queues exactly one motors-off, and `disable()` then succeeds instead of being refused. | test: `tests/test_safe_shutdown.py::SessionFaultTests::test_every_latched_fault_queues_one_motors_off_and_disable_still_works` |
| SD04 | SHALL NOT | After an emergency input or any latched fault, no queued or in-flight order other than 16 and EXIT runs: an order 17 already queued is answered with result 1 and sends no motors-on. | test: `tests/test_safe_shutdown.py::SessionFaultTests::test_an_order_17_queued_before_an_emergency_never_turns_the_motors_on` |
| SD05 | SHALL | A USB error while the stream closes (`stopMov`/`closeMov`) attempts motors-off once, reports result 1 and keeps the worker alive. | test: `tests/test_safe_shutdown.py::LegacyOrderErrorTests::test_a_usb_error_while_the_stream_closes_switches_the_motors_off` |
| SD06 | SHALL | `motors_off` attempts every exchange of the off group even if one fails, then reports the failure (`MotorsOffError`, with the last packet ID consumed). Gripper `_clamp_off` likewise tries both exchanges. | test: `tests/test_safe_shutdown.py::OffGroupTests::test_motors_off_tries_every_exchange_after_a_failure`; `tests/test_safe_shutdown.py::OffGroupTests::test_clamp_off_tries_both_exchanges_after_a_failure` |
| SD07 | SHALL | A Ctrl+C (`KeyboardInterrupt`) during `connect` after the handshake sent motors-on attempts motors-off and the usual cleanup, then re-raises. | test: `tests/test_safe_shutdown.py::ConnectInterruptTests::test_ctrl_c_after_motors_on_attempts_motors_off_and_cleans_up` |
| SD08 | SHALL NOT | The legacy jog's impact stop (`stopMov` then hold) changes: LF01 stays as it is. | test: `tests/test_software_stop.py` and `tests/test_legacy_golden.py` pass unchanged |
| SD09 | SHALL NOT | This work opens a real device, runs `--online`, moves the arm, adds dependencies or touches `docs/lab/`, `scripts/check_docs.py` or other requirement docs. | review: final diff; tests use fake endpoints only |

## Review map
R1 = SD01, SD03 (dead worker) | R2 = SD04 | R3 = SD03 | R4 = SD05 | R5 = SD06 (SD02 for the clamp) | R6 = SD07.

## Out of scope
- Real hardware, new protocol bytes, a way to clear a latched fault, the velocity jog guard (R9).

## Assumptions
- A dead sync or command worker cannot send anything; the fix keeps the worker from dying (SD01, SD02, SD05) and the banner still says to use the physical stop.
- The sequence byte (packet byte 0) of the last written packet is the last ID consumed.

## Open questions
- None.

## Change log
- 2026-11-05 Baseline from issue #185.
