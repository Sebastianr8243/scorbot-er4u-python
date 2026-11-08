# Vendor Home pacing requirements
Status: baseline v1 2026-11-07 (authorized by the home-pacing task brief; SOURCE_AUDIT section 4 item 1)

## Goal
While the vendor Home (order 22) runs, keep the controller's queue near the vendor's `HomingBuffers` (20) the way
the vendor DLL does (PL03): after a reply that shows 20 or more messages in flight, wait half a `PCPeriod` before
the next message instead of the legacy 5 ms. `MAX_LAG` 60 stays as a second guard. Verify on desk fakes only;
physical-arm behavior is unverified.

"In flight" (the lag) is the last ID sent minus the ID the reply echoes, on the 1..255 ring (`pacing.echo_lag`).

## Requirements
| ID | Type | Statement | Check |
|---|---|---|---|
| HP01 | SHALL | During the vendor Home, after a reply whose lag is at least `pacing.HOMING_BUFFERS` (20), the worker shall wait `pacing.next_read_delay(lag, 20, 0.016)` (8 ms) before asking for the next message, instead of the legacy `READ` (5 ms). | test: `pytest tests/test_vendor_home_libcomm.py -k draining_fake`; `pytest tests/test_vendor_home_robot.py -k records_the_session_home` (the Home asks for it) |
| HP02 | SHALL | Below the limit the wait shall stay the legacy `READ`: pacing never sends faster than today (the vendor waits 1 ms there; ours keeps 5 ms on purpose). | test: `pytest tests/test_vendor_home_libcomm.py -k below_the_limit` |
| HP03 | SHALL | Against a fake controller that drains one message every 16 ms, the whole Home shall complete with the lag never above 20 (without pacing it passes 20 and the Home ends on `ERR_LAG`). | test: `pytest tests/test_vendor_home_libcomm.py -k draining_fake` |
| HP04 | SHALL | In that paced run the non-`0D` command sequence shall still equal the lab DLL trace (PL10); `tests/test_vendor_home.py` passes unchanged. | test: same run asserts `non_nop(sent) == FIXTURE["steps"]`; `pytest tests/test_vendor_home.py` |
| HP05 | SHALL | Pacing shall never slow a stop: once the source is aborting, interrupted, or a stop or cancel is set, the wait is the legacy `READ`, and the stop is checked after the wait, not before it. Worst case a stop is seen one 8 ms wait late (5 ms today). | test: `pytest tests/test_vendor_home_libcomm.py -k stop_is_not_held_back` |
| HP06 | SHALL | Orders that share the loop (gripper, outputs, torque, manual, joint move) and a Home source built without `queue_limit` shall keep the exact legacy `set_msg(..., WRITE, READ)` timing. | test: `pytest tests/test_vendor_home_libcomm.py -k "without_pace or legacy_delays"` |
| HP07 | SHALL | `MAX_LAG` 60 shall stay as a second guard: a controller that drains slower than one message per 16 ms still grows the lag, and the Home still ends on `ERR_LAG` past 60. | test: `pytest tests/test_vendor_home_libcomm.py -k falls_behind`; `pytest tests/test_vendor_home_source.py -k lag_that_grows` (unchanged) |
| HP08 | SHALL NOT | Verification shall open a real USB device or move the arm. | script: the full pytest command runs under `conftest.py`'s USB guard; the new tests use fake endpoints and a fake clock |

## Out of scope
- Holding messages back (not sending while "waiting"): the 2026-10-05 stream attempt did that and the reply it
  needed never changed (`scorbot/README.md`). This is a rate limit like the vendor's, not a hard cap.
- The vendor's 1 ms fast wait, the stream's pacing (`start_stream(adaptive_pacing=True)`), and the
  `ManualBuffers` / `Buffers` limits for the other orders.

## Assumptions
- The controller drains one message per `PCPeriod` (16 ms, PL05) and byte 0 echoes the message it is on (PL06,
  PL07). Both unverified while homing (PL92). If it drains slower, the lag still grows and `MAX_LAG` catches it.
- At 20 in flight a stop queues behind about 20 messages (about 320 ms at 16 ms each) instead of up to 60
  (about 1 s). Unverified on the arm.

## Open questions
- Whether the real controller's lag while homing is near 20 at all (PL92, lab).

## Change log
- 2026-11-07 Baseline from the task brief.
