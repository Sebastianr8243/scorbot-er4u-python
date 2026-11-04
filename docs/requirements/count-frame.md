# Count frame requirements
Status: baseline v1 2026-11-04 (issue #128, approved through mission control)

## Goal
Every position the legacy path sends or reads uses one count frame: the controller count
`raw24 - 0x7FFFFF`, carried in each OUT setpoint slot as a signed 32-bit number
(`docs/protocol/VENDOR_DLL_PROTOCOL.md` sections 3 and 6). #127 did this for the stop and the enable
snapshot (`libdef.get_hold`). Checks run offline against fake endpoints; the arm is unverified.

The old echo wrote the reply's low word and sign word. Read as signed 32-bit that is always one count
low, on both sides of zero: sign byte 128 is count `L + 1` and was sent as `L`; sign byte 127 is count
`L - 65535` and was sent as `L - 65536` (checked for every L, both signs).

## Requirements
| ID | Type | Statement | Check |
|---|---|---|---|
| CF01 | SHALL | After a stop, every message of the next jog (open, steps, settle, close) carries each joint that does not move at the count it reported, and the moving joint's first step is the count plus the first increment, for counts on both sides of zero, every legacy mover and both directions. | test: `tests/test_count_frame.py::CountFrameTests::test_after_a_stop_the_jog_keeps_every_joint_where_it_was` |
| CF02 | SHALL | Every motor-on message (`libcomm.motors_on`, 7 messages) carries the reported counts, including 0, +/-1, -65535, 65535 and 65536. | test: `tests/test_count_frame.py::CountFrameTests::test_every_motor_on_message_carries_the_reported_counts` |
| CF03 | SHALL | `closeMov`'s last message and the idle hold it leaves are the start count plus the whole jog for the moving joints and the reported count for the others; a jog that crosses zero moves by exactly its increments (`suma`/`resta` wrap the low word by 65536). | test: `tests/test_count_frame.py::CountFrameTests::test_the_jog_ends_holding_the_start_plus_the_jog` |
| CF04 | SHALL | `scorbot/transport/codec.py` builds the same bytes as the legacy functions (`encode_count` = `libdef.countField`, `step_count` = `suma`/`resta`), and every region the lab DLL wrote, read as signed 32-bit counts, is one the codec writes back unchanged. | test: `tests/test_transport_codec.py` (golden and property tests), `tests/test_desk_byte_check.py::DeskByteCheckTests::test_every_region_the_dll_wrote_is_one_the_codec_builds` |
| CF05 | SHALL NOT | This work opens USB, moves hardware, touches the `lab-2026-11-04` tag, adds dependencies, or changes `moveXYZ.py`/`setHome.py`. | review: fake endpoint tests, commands and final diff |

## Out of scope
- The 24-bit range and `decode_state` (item 4 of #128): the stacked PR B.
- `setpointField` (stream setpoints) stays in the old decode until PR B changes it with `decode_state`,
  since the stream reads one and sends the other.
- `moveXYZ.py` and `setHome.py` build their moving fields with `detrans` and the sign word, so those
  fields stay one count low; their echoed fields use `get_encoder` and are fixed.

## Assumptions
- The controller reads each slot as a signed 32-bit count. Source: the DLL disassembly
  (VENDOR_DLL_PROTOCOL.md sections 3 and 6). `test_desk_byte_check` only shows the codec is consistent
  with that reading of the lab DLL's regions (the fixture does not record what the fake reported), not
  that the reading is true. Unverified on the arm.
- Raw `0x80FFFF` holds as 65536 (chosen in #128).

## Open questions
- None.

## Change log
- 2026-11-04 Baseline from issue #128 (PR A: items 1-3).
