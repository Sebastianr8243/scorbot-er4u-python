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
| CT01 | SHALL | After a stop, every message of the next jog (open, steps, settle, close) carries each joint that does not move at the count it reported, and the moving joint's first step is the count plus the first increment, for counts on both sides of zero, every legacy mover and both directions. | test: `tests/test_count_frame.py::CountFrameTests::test_after_a_stop_the_jog_keeps_every_joint_where_it_was` |
| CT02 | SHALL | Every motor-on message (`libcomm.motors_on`, 7 messages) carries the reported counts, including 0, +/-1, -65535, 65535 and 65536. | test: `tests/test_count_frame.py::CountFrameTests::test_every_motor_on_message_carries_the_reported_counts` |
| CT03 | SHALL | `closeMov`'s last message and the idle hold it leaves are the start count plus the whole jog for the moving joints and the reported count for the others; a jog that crosses zero moves by exactly its increments (`suma`/`resta` wrap the low word by 65536). | test: `tests/test_count_frame.py::CountFrameTests::test_the_jog_ends_holding_the_start_plus_the_jog` |
| CT04 | SHALL | `scorbot/transport/codec.py` builds the same bytes as the legacy functions (`encode_count` = `libdef.countField`, `step_count` = `suma`/`resta`), and every region the lab DLL wrote, read as signed 32-bit counts, is one the codec writes back unchanged. | test: `tests/test_transport_codec.py` (golden and property tests), `tests/test_desk_byte_check.py::DeskByteCheckTests::test_every_region_the_dll_wrote_is_one_the_codec_builds` |
| CT06 | SHALL | `decode_state` reads every 24-bit raw value as the count raw24 - 0x7FFFFF, from -0x7FFFFF (raw 0x000000) to 0x800000 (raw 0xFFFFFF); it refuses no sign byte (the top byte). | test: `tests/test_count_frame.py::CountRangeTests::test_decode_state_reads_every_24_bit_count`; `tests/test_legacy_properties.py::StateCodecProperties`; `tests/test_lab_log_review.py::LabLogReviewTests::test_a_count_past_65535_is_not_an_invalid_sign_byte` |
| CT07 | SHALL | Any count in that range, read from the controller and sent back as a hold or echo (`get_hold`, `get_encoder`, `stopMov`, `motors_on`, the codec), is the same count, across 0, +/-65535/65536 and the 24-bit ends; the idle path never raises on a top byte by itself (only on one that jumps, CT16). | test: `tests/test_count_frame.py::LegacyCountRangeTests::test_holds_and_echoes_round_trip_every_24_bit_count`, `test_the_codec_writes_every_24_bit_count` |
| CT08 | SHALL | A stream setpoint is the count it read: `setpointField` writes the controller count as signed 32-bit over the 24-bit range and refuses beyond it; the stream's own range check uses the same range. | test: `tests/test_count_frame.py::LegacyCountRangeTests::test_stream_setpoints_send_the_count_they_read`; `tests_extra/test_streaming.py` |
| CT09 | SHALL | `calibration.signed_count_delta` gives the exact difference of two low words for counts less than 32767 apart, across every top-byte change (modulus 65536). | test: `tests/test_count_frame.py::CountRangeTests::test_count_deltas_are_exact_across_every_top_byte`; `tests/test_legacy_properties.py::SignedCountDeltaProperties` |
| CT10 | SHALL | A legacy jog that leaves -65535..65536 keeps counting: `suma`/`resta` change the sign word only across zero, so the target never jumps 65536. | test: `tests/test_count_frame.py::CountFrameTests::test_a_jog_past_the_old_range_keeps_counting`; `tests/test_legacy_properties.py::LegacyKnownBugs` |
| CT11 | SHALL | The SDK refuses, before sending, a legacy jog whose moving joint starts outside -65535..65536 (the legacy jog keeps it as a 128/127 pair, `get_signo`). | test: `tests/test_count_frame.py::CountRangeTests::test_a_legacy_jog_must_start_inside_the_sign_byte_range` |
| CT12 | SHALL | The simulator reports counts as raw24 = count + 0x7FFFFF over the whole 24-bit range, and its gripper follows setpoints as counts. | test: `tests/test_count_frame.py::CountRangeTests::test_the_simulator_reports_every_24_bit_count` |
| CT13 | SHALL | `RobotState.encoder_counts` is the low 16 bits of the controller count (`signed_encoder_counts & 0xFFFF`), so `signed_count_delta(encoder_counts, 0)` is the DLL's count for counts within 32766 of zero, on both sides. Until 2026-11-04 it was the raw low word, one less. | test: `tests/test_reply_decode_matches_dll.py::ReplyDecodeMatchesDll::test_legacy_path_reads_the_dlls_count` (failed on CI before the fix: -1 != 0 on every DLL fixture row); `tests/test_count_frame.py::CountRangeTests::test_decode_state_reads_every_24_bit_count` |
| CT14 | SHALL | Past 32766 counts from home a low word aliases by 65536, so: the jog's vendor-limit check reads the absolute count from `signed_encoder_counts` (exact over 24 bits); a stream refuses to start, naming the motor, when any motor it reads is more than `calibration.MAX_COUNTS_FROM_HOME` (32766) from home; and the vendor limits never let the SDK command a motor past that reach. | test: `tests/test_count_frame.py::HomeReachTests` |
| CT15 | SHALL | A stream refuses, with no setpoint sent, any reply whose 24-bit count for a streamed motor, less home, is not the delta its low word gives (`measured`): one corrupt top byte moves the count by 65536 while the low word stays. The fault latches the session and names the motor, the top byte and both counts. | test: `tests_extra/test_streaming.py::SetpointConversionTests::test_a_corrupt_top_byte_is_a_fault_naming_the_motor_not_a_setpoint`, `SdkStreamTests::test_one_reply_with_a_corrupt_top_byte_sends_no_setpoint_and_names_the_motor` |
| CT16 | SHALL | On the legacy path every reply (`libdef.check`) is judged against the last count of each joint the guard trusted: a count may move only as far as its low word moves (the shortest way). A reply that fails is not trusted, and `get_hold`/`get_encoder` (the stop, the enable snapshot, the idle echo, the jogs, the stream's held joints) raise `CountJumpError` naming the motor instead of building a setpoint from it. A new connection (`libsync.msg_start`) and the vendor home (its `48` re-zeroes the counters) start the guard afresh. | test: `tests/test_count_frame.py::CorruptTopByteTests` |
| CT17 | SHALL | A legacy jog (`libcomm.move_hips`, `move_shoulder`, `move_elbow`, `move_wrist`) whose moving joint cannot be kept as a 128/127 pair (`get_signo`) after its open message was sent sends the stop at the counts it read, reports failure 1 and hands the media back, instead of raising with the movement open. | test: `tests/test_count_frame.py::CorruptTopByteTests::test_a_jog_whose_joint_is_past_the_legacy_pair_stops_cleanly` |
| CT18 | SHALL | `codec.step_count` takes back every target it returns, past 0..65535 too, as `suma`/`resta` keep counting there; the controller count before and after must be in the 24-bit range. | test: `tests/test_transport_codec.py::CodecContractTests::test_step_count_takes_back_every_target_it_returns`, `test_step_count_rejects_bad_state` |
| CT19 | SHALL | `load_calibration` refuses a `home_tolerance_counts` of 0 and says why: a file recorded before 2026-11-04 is one count off today's reading (CT13); re-fit it. | test: `tests/test_calibration_load.py::LoadTests::test_a_home_tolerance_of_zero_is_refused_with_the_reason` |
| CT05 | SHALL NOT | This work opens USB, moves hardware, touches the `lab-2026-11-04` tag, adds dependencies, or changes `moveXYZ.py`/`setHome.py`. | review: fake endpoint tests, commands and final diff |

## Out of scope
- `moveXYZ.py` and `setHome.py` build their moving fields with `detrans` and the sign word, so those
  fields stay one count low (and `detrans` cannot write a low word past 65535); their echoed fields
  use `get_encoder` and are fixed.
- `libcomm.clamp` (the gripper) still calls `get_signo` after its first messages; it already sends the
  gripper-off messages before passing the error on. CT11 refuses an out-of-range jog in the SDK first;
  CT17 makes the four arm jogs stop cleanly for a direct legacy caller (the GUI).

## Checks with 65535 wrap logic (CT09 and after)
| Check | Status |
|---|---|
| `calibration.signed_count_delta` | Fixed: modulus 65536 (CT09). Every caller passes low words and stays as it is: `scripts/bring_up.py` (3), `homing.py`, `joint_move.py`, `streaming.py`, `stream_control.py`, `robot.py`, `policy_arm.py`, `gripper.py`, `lab/session.py`, `session/analysis.py`, `scripts/fit_calibration.py`, `home_trace_report.py`, `hand_sweep_report.py`, `lab_day.py`. Exact for moves under 32767 counts between the two samples. |
| `streaming.py` absolute setpoint range | Fixed: the 24-bit range (CT08). |
| `robot.preview_jog` starting counts | Fixed to -65535..65536 (CT11): the legacy jog pair's range, not the controller's. |
| `simulated.py` count range | Fixed: 24-bit (CT12). |
| `codec.step_count` input 0..65535 | Fixed: takes any target it returns (CT18). |
| `motion_profile.count_distance` (modulus 65535) | Fixed: modulus 65536, so it agrees with `signed_count_delta` (`tests/test_settle_seam.py`). `openScorbot/motion_profile.py` is outside #128's files (flagged for w3). |
| `calibration` `home_count` 0..65535, `session/analysis.py` and `fit_calibration.py` 0..65535 | Unchanged: they hold low words, which are 0..65535 by definition. |
| `scripts/home_trace_report.py` error-word fold at 65500 | Unchanged: an error word, not a count. |
| `signed_count_delta(home, 0)` as an absolute count (`policy_arm.py`, `stream_control.py`, `scripts/lab_day.py`) | Unchanged and now exact: `encoder_counts` is the count's low word (CT13). `robot.py`'s jog vendor check reads `signed_encoder_counts` instead (CT14). |
| `scripts/review_lab_logs.py` sign bytes other than 127/128 flagged invalid | Fixed: any top byte is a count (test: `tests/test_lab_log_review.py::LabLogReviewTests::test_a_count_past_65535_is_not_an_invalid_sign_byte`). |
| `scripts/usb_trace.py` `setpoint_stats` | Unchanged and now exact: it compares a slot's low 16 bits with `encoder_counts`, both the count's low word (CT13); a slot past +/-65535 is counted under "unexpected sign words". |
| `scripts/build_calibration_csv.py` 0..65535 | Unchanged: it checks low words. |
| `libdef.getError` fold at 65500 | Unchanged: an error word, not a count. |
| `libdef.conversorAngEnc`, `libdef.joint` (65535 wraps with `LIM_SUP`) | Unchanged: GUI-only angle display and reference, not sent; outside the count functions this issue names. |
| `setHome.py`, `moveXYZ.py` `get_signo` on their moving joints | Unchanged: a legacy home or XYZ move with such a joint outside -65535..65536 still raises in the worker; neither file is in #128's list. |

## Low words recorded before 2026-11-04
Logs and calibration files written before CT13 hold the raw low word, one less than the count's.
Deltas between two samples of one log are unaffected. A stored calibration `home_count` compared with
a new reading (`validate_home`) is one count off, inside any `home_tolerance_counts` of 1 or more
(a tolerance of 0 is refused, CT19);
`scripts/build_calibration_csv.py` and `scripts/review_lab_logs.py` read deltas only.

## Known limits
- `wrist_motor_2` (vendor EncLimit +/-1,000,000): within the vendor limits it stays under about 21500
  counts from vendor zero (CT14's test checks every pose past 32766 is refused), so the SDK never
  commands it out of reach. If it gets there another way (the pendant), a stream refuses to start and
  names it; a jog's vendor check reads it exactly; a jog's own delta is read between two samples.
  This relies on vendor Home's zero being the controller's count zero, as the vendor-limit code
  already does.
- A corrupt top byte is no longer a decode error: any top byte is a legal count. `decode_state` stays
  stateless; the stream (CT15) and the legacy echoes (CT16) check the count against the low word
  instead. A refusal on the legacy path ends the worker that hit it (the SDK latches the fault and
  says so); what the controller does when messages stop is unverified (the manual says its
  communication time-out switches the motors off). A corrupt low word is not caught by either check.
- CT15 cannot tell a corrupt reply from a motor that has gone past 32766 counts from home while
  streaming (the low word aliases); both are faults, and the message says so.
- `scorbot/vendor_home.py` (its stop holds the measured position) and `scorbot/gripper.py` read a
  reply's 24-bit count without this check; not changed here.

## Assumptions
- The controller reads each slot as a signed 32-bit count. Source: the DLL disassembly
  (VENDOR_DLL_PROTOCOL.md sections 3 and 6). `test_desk_byte_check` only shows the codec is consistent
  with that reading of the lab DLL's regions (the fixture does not record what the fake reported), not
  that the reading is true. Unverified on the arm.
- Raw `0x80FFFF` holds as 65536 (chosen in #128).

## Change log
- 2026-11-04 Baseline from issue #128 (PR A: items 1-3).
- 2026-11-04 CT06-CT12 for item 4, the 24-bit range (PR B).
- 2026-11-04 CT13 (`encoder_counts` is the count's low word; CI found the DLL fixture one off) and
  CT14 (the home reach), fix round from mission control.
- 2026-11-04 CT15-CT19 from the post-merge review of #132 (issue #138): the corrupt-top-byte checks on
  both paths, the legacy jog's clean stop, `step_count`'s input range and the tolerance-0 refusal.
