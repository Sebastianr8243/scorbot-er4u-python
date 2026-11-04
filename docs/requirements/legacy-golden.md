# Legacy golden messages requirements
Status: baseline v1 2026-11-04 (issue #130, approved through mission control)

## Goal
Pin every message the legacy path (`openScorbot/`) sends for its main flows, byte for byte and in
order, before cleaning up its duplicated code, so the cleanup provably changes nothing on the wire.
The flows run on one thread against a fake arm (`tests/test_legacy_golden.py`); nothing is opened and
nothing moves. A golden says what the code sends, not what the controller accepts (unverified on the arm).

"No behavior change" means, for every pinned flow: the same OUT packets in the same order (all 64 bytes),
the same result codes on the result queue, the same sequence byte handed back, the same media left in the
read queue, the same idle hold (`libdef.ultimo_hold`) and the same write/read sleeps in the same order.

## Requirements
| ID | Type | Statement | Check |
|---|---|---|---|
| LG01 | SHALL | Connect (`libsync.msg_start`: handshake groups 1-3 with their waits), motors on (`libcomm.motors_on`), motors off (`libcomm.motors_off`) and disconnect (order EXIT: `motors_off` then `scorbotoff`) each send their golden messages. | test: `tests/test_legacy_golden.py::LegacyGoldenTests::test_every_flow_sends_its_golden_messages` (flows `connect`, `motors_on`, `motors_off`, `disconnect`) |
| LG02 | SHALL | Every jog order 4-13, both directions of every joint and all four wrist orders, sends its golden messages to a normal close (`closeMov`), including a base jog that crosses zero and a slow jog. | same test, flows `jog_4` .. `jog_13`, `jog_5_slow` |
| LG03 | SHALL | A stop request mid-ramp ends each kind of jog (base, shoulder, elbow, wrist) with its golden stop messages and result 14. | same test, flows `jog_4_stop`, `jog_7_stop`, `jog_9_stop`, `jog_12_stop`; result checked in `test_the_goldens_cover_what_they_claim` |
| LG04 | SHALL | A fault ends the jog with the golden stop and result 1: each joint's own collision bit (base 0x01, shoulder 0x02, elbow 0x04, wrist 0x18), the impact error word of base, elbow and wrist motor 2, an impact during the settle loop; another joint's collision bit does not stop the jog. | same test, flows `jog_*_collision`, `jog_*_impact*`, `jog_6_other_bit` |
| LG05 | SHALL | The settle loop closes a jog whose motors end within 20 counts of the target and gives up with result 2 after 100 more messages otherwise (an arm that does not follow, or stops 21 counts short), for a single-motor joint and the wrist. | same test, flows `jog_5_no_settle`, `jog_10_no_settle`, `jog_5_short_20`, `jog_5_short_21`, `jog_12_short_20`, `jog_12_short_21` |
| LG06 | SHALL | A jog whose joint starts outside the sign-byte range sends only its open message and the stop, with result 1 (CT17). | same test, flow `jog_6_refused` |
| LG07 | SHALL | Gripper open (14), close (15) and a stop request during the ramp send their golden messages. | same test, flows `gripper_open`, `gripper_close`, `gripper_stop` |
| LG08 | SHALL | A stream (`stream_targets`) of three-joint and five-motor setpoints, a stopped stream and an empty stream send their golden messages. | same test, flows `stream`, `stream_wrist`, `stream_stop`, `stream_empty` |
| LG09 | SHALL | A vendor home that fails sends what its source asked for, then the legacy motors-off, and reports result 1. The full vendor home is pinned by its own fixture (every non-idle command and the sequence byte across the wrap). | same test, flow `vendor_home_failed`; `tests/test_vendor_home_libcomm.py::VendorHomeRunTests::test_a_whole_home_through_the_legacy_loop` |
| LG10 | SHALL | Every flow has exactly one fixture, every pinned packet is 64 bytes, and the fixtures show the endings they claim (no empty recordings). | `test_every_flow_has_a_fixture_and_every_fixture_a_flow`, `test_the_goldens_cover_what_they_claim` |
| LG11 | SHALL NOT | A cleanup of the legacy code changes any fixture. A deliberate byte change re-records only the flows it lists, in its own PR. | review: the cleanup commits of the PR touch no file under `tests/fixtures/legacy_golden/` |

## Not covered
- Timing beyond the sleep values (the sleeps are recorded, not waited), the sync thread's idle `0D`
  loop (`libsync.syncro`) and the order dispatch in `libcomm.execute`; `test_idle_hold.py`,
  `test_pause_marker.py` and `test_desk_byte_check.py` cover those.
- Legacy homing (`setHome.py`, order 18) and XYZ moves (`moveXYZ.py`, order 19): not pinned here, so
  this cleanup does not change them.
- What is printed to stdout and logged.

## Change log
- 2026-11-04 Baseline from issue #130.
