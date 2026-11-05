# Streaming requirements
Status: baseline v1 2026-11-04 (issue #167; owner decisions given 2026-11-04 through mission control)

## Goal
Stack 2 streaming: a caller (a LeRobot/VLA policy, a teleop) sends a new joint target many times a
second while the arm moves, and the arm follows within fixed limits. `Scorbot.start_stream` returns a
`Stream`; `StreamCore` (`scorbot/streaming.py`) plans one jerk-limited step per period with Ruckig, and
the legacy worker sends it as order 21 (`STREAM` in `openScorbot/libcomm.py`, `stream_targets`).
`PolicyArm` and the LeRobot plugin are built on it.

**This is ours, not the vendor's.** The lab `USBC.dll` has no moving-target mode: it plans point moves
(`MoveJoint`) and the pendant-style manual jog (`EnterManual`/`MoveManual`/`CloseManual`,
velocity jog, PR #156, open). The message
format, the stop sequence and the limits below come from the legacy path and the vendor INI files;
the follower itself has no outside reference.

Most of this is built and tested on the simulator and fakes. What the next issue builds, from the
**to build** checks in the table:
- **New behaviour:** ST01 (near-home start), ST02 (gripper resume, pending Q1), ST05 (at-rest wait).
- **Built, test missing:** ST12 (pendant Abort mid-stream), ST16 (idle hold after the stream).
- **Partly covered, one test to add:** ST03 (default bounds equal the vendor window), ST13 (motors-off for
  every fault cause), ST18 (log rows for the ST01 and ST05 refusals).
- **Covered by existing tests:** ST04, ST06 to ST11, ST14, ST15, ST17, ST19, ST20.

Nothing here has run on the arm: every physical claim is **unverified** until a bench log.

## Owner decisions (2026-11-04)
1. **Near home:** "streaming starts only with every joint at home (0 counts from home, within a small
   tolerance you propose and justify)." Requirement ST01; tolerance below.
2. **Shoulder travel:** "as far as the hardware and the Intelitek manual allow, i.e. the existing vendor
   limits (`nominal.vendor_limit_problem`, the windows in `docs/protocol/VENDOR_DLL_PROTOCOL.md` 11.5),
   no extra software cap." Requirement ST03.
3. **BACKLOG 20** is already fixed: every fault goes through `_latch_fault`. Nothing to decide.

### Near-home tolerance: 20 counts per motor
At most `limits.DRIFT_COUNTS` (20) counts from home on base, shoulder, elbow and both wrist motors,
read from the last reply before the start.
- A legacy jog or move ends once the joint is within 20 counts of its target (the `libcomm.py` settle
  loop). So an arm that a supervised move just brought back to home passes, and a tighter band would
  refuse arms our own moves leave behind.
- In joint terms, 20 counts is small: 0.14 degrees on the base (141.9 counts per degree), 0.18 on the
  shoulder and elbow (10216 counts per 90 degrees), 0.7 on wrist pitch (27.9 per motor degree). It is
  also well under one default stream step at full speed (156 counts) and under the 2 degree lead limit.
- It is 10 times the 2-count rest band (`limits.STABLE_COUNTS`), so encoder noise at rest never refuses
  a start.
- The gripper is not a streamed joint and is left out (Q2).

## Requirements
| ID | Type | Statement | Check |
|---|---|---|---|
| ST01 | SHALL | Decision 1, verbatim: "Near home: streaming starts only with every joint at home (0 counts from home, within a small tolerance you propose and justify)." Tolerance: 20 counts (above). `start_stream` is refused, with nothing queued and a `stream_refused_not_home` row naming each motor and its counts, unless base, shoulder, elbow, `wrist_motor_1` and `wrist_motor_2` are each within `limits.DRIFT_COUNTS` (20) counts of home. This applies on a wrist stream and on a three-motor stream (the held wrist counts too). | **to build**: `SdkStreamTests::test_a_stream_starts_only_within_20_counts_of_home` (each motor at 19, 20 and 21 counts, both signs, wrist and no-wrist streams); refusal leaves the command queue empty |
| ST02 | SHALL | **Pending Q1.** The PolicyArm gripper handoff (a stream ended by `close`, a vendor gripper move, then the stream started again in the same session with no stop, fault, disconnect or reconnect in between) is exempt from ST01, because it resumes from the held setpoint of a stream that itself started near home. Every other start obeys ST01. | **to build**: `test_policy_arm_gripper.py::test_gripper_resume_away_from_home_restarts_the_stream`; `test_a_stop_or_fault_clears_the_resume_exemption` |
| ST03 | SHALL | Decision 2, verbatim: "Shoulder travel: as far as the hardware and the Intelitek manual allow, i.e. the existing vendor limits (`nominal.vendor_limit_problem`, the windows in `docs/protocol/VENDOR_DLL_PROTOCOL.md` 11.5), no extra software cap." By default (`travel_cap_deg=limits.TRAVEL_CAP_DEG`, which is `None`) every streamed motor's bounds are the vendor encoder window (`nominal.VENDOR_ENCODER_SOFT_LIMITS` minus this session's home), and every target and every setpoint also passes `nominal.vendor_limit_problem` (the joint windows, 11.5). No smaller default bound exists on any joint. An explicit positive `travel_cap_deg` is the caller's own extra bound. | test: `tests/test_one_limit.py::test_default_cap_is_none`, `test_stream_optional_cap_has_no_ceiling_and_keeps_vendor_windows`, `tests_extra/test_streaming.py::SdkStreamTests::test_r4_the_shoulder_cannot_be_streamed_past_its_joint_limit`; **to build**: `SdkStreamTests::test_the_default_shoulder_bounds_are_the_vendor_window_exactly` (bounds equal the window minus home on both sides) |
| ST04 | SHALL | Start conditions besides ST01: connected, enabled, legacy home done and vendor Home done this session (`_gripper_homed`), pendant in Auto, no pending stop, no other stream, no session fault, and every streamed motor within `MAX_COUNTS_FROM_HOME` of home. Any failure refuses with nothing queued. | test: `SdkStreamTests::test_r12_streaming_needs_enable_and_home_and_queues_nothing_otherwise`, `test_a_pending_stop_refuses_the_stream_before_anything_is_sent`, `tests/test_teach_mode.py` (stream refused in teach mode), `tests/test_count_frame.py::HomeReachTests::test_a_stream_refuses_a_motor_past_the_home_reach_and_names_it` |
| ST05 | SHALL | At rest before the start: the arm counts as at rest after `STOP_SETTLE_QUIET_PAIRS` (3) reply pairs in a row move at most `STABLE_COUNTS` (2) counts on every motor, the rule `_settled_after_stop` already uses. If it does not come to rest within `STOP_SETTLE_TIMEOUT_S` (8 s), the start is refused with nothing queued. | **to build**: `SdkStreamTests::test_a_stream_waits_for_rest_and_refuses_an_arm_still_moving` (simulator counts drifting 5 counts per reply, then still) |
| ST06 | SHALL | Vendor-limit window: a stream never starts on a pose outside the window or past a joint limit (ST01 already rules that out, because home is inside). A target outside is refused and changes nothing; a planned setpoint outside faults the stream before it is sent. There is no "move back" mode in a stream; a pose past a limit comes back with a jog (`one-limit.md`). | test: `test_streaming_core.py::StreamCoreTests::test_r4_target_outside_the_travel_cap_is_refused_and_changes_nothing`, `test_r4_a_stream_cannot_start_on_a_pose_that_breaks_the_check`, `test_r4_r6_a_setpoint_on_the_way_that_breaks_the_check_faults_the_stream`, `SdkStreamTests::test_r4_the_elbow_cannot_be_streamed_past_its_pose_limits`, `test_r4_the_pose_check_uses_the_measured_wrist_not_an_assumed_home`, `WristReversalTests::test_no_setpoint_puts_pitch_or_roll_past_the_cap` |
| ST07 | SHALL | One setpoint per period: `period_s` 0.024 s by default (the vendor planner period), 0.01 to 0.05 s accepted. Setpoints are never closer together than the period; a slow loop is not caught up (the arm then moves slower than planned, never faster). | test: `SdkStreamTests::test_steps_are_never_closer_together_than_the_planned_period`, `StreamCoreTests::test_settings_are_validated` |
| ST08 | SHALL | Target rate: the caller may send targets at any rate; the newest target replaces the old one. If no target arrives for `hold_timeout_s` (0.5 s by default, 0.05 to 2 s accepted), the stream slows to rest within its limits and holds there (`holding`). It never extrapolates or drifts. A new target resumes tracking. | test: `StreamCoreTests::test_r7_no_fresh_target_slows_to_rest_and_holds`, `SdkStreamTests::test_r7_arm_holds_when_targets_stop_arriving`, `StreamCoreTests::test_r1_a_new_target_mid_move_bends_the_motion_without_stopping`; bench: shoulder and elbow counts stay within 20 of the held setpoint for 10 s after targets stop |
| ST09 | SHALL | Max step per message: no setpoint differs from the one before by more than `max_velocity x period_s` counts per motor. With the default speed (`speed_fraction` 0.25 of the vendor MaxSpeed of 6500 counts/s) that is 39 counts at 24 ms; at most 156 at full speed. Acceleration stays within the vendor peak (MaxAccel / 1.5) and jerk within the prior. A speed change never lowers acceleration or jerk mid-stream. | test: `StreamCoreTests::test_r2_every_step_respects_speed_acceleration_and_jerk`, `test_acceleration_never_passes_the_vendors_max_accel`, `test_slowing_down_mid_acceleration_never_passes_the_vendors_max_speed`, `test_slowing_down_then_finishing_or_while_braking_stays_under_max_speed` |
| ST10 | SHALL | Max lead: before each setpoint is sent, setpoint minus measured count is at most `lead_limit_deg` (2 degrees by default, at most `MAX_JOG_DEG`, 5) per motor; on a wrist stream, |m1| + |m2| lead is at most the pitch-plus-roll limit. Past it, the stream faults: no setpoint is sent and the motors go off (ST13). | test: `StreamCoreTests::test_r3_r6_an_arm_that_does_not_follow_stops_the_stream_with_a_fault`, `test_r11_a_lagging_arm_is_tracked_with_a_bounded_error`, `SdkStreamTests::test_r3_r6_arm_that_does_not_follow_latches_a_fault`, `test_a_stalled_wrist_faults_on_pitch_or_roll_lead_not_on_the_doubled_motor_limit` |
| ST11 | SHALL | Stop request: `Stream.stop()`, `Stream.request_stop()` or `Scorbot.request_stop()` ends the stream on the next step with the legacy stop sequence (`stopMov`) on the measured position, with no fault and a `stream_stopped` row. | test: `StreamCoreTests::test_r5_stop_takes_effect_on_the_next_step`, `LegacyStreamTests::test_r5_stop_ends_with_the_stop_sequence_on_the_measured_position`, `SdkStreamTests::test_r5_stop_ends_the_stream_short_of_the_target_without_a_fault`, `test_r5_request_stop_on_the_robot_stops_a_stream` |
| ST12 | SHALL | Pendant Abort during a stream is the same stop as ST11 (LEDGER PL64, PL67), and while the key stays held, no new stream starts. Teach mode switched on mid-stream is the same stop too. | test: `LegacyStreamTests::test_teach_mode_mid_stream_sends_exactly_the_normal_stop_bytes`; **to build**: `SdkStreamTests::test_pendant_abort_mid_stream_stops_without_a_fault` and `test_abort_held_refuses_a_new_stream` (simulator reply byte 3 = 0x21) |
| ST13 | SHALL | Emergency input, an impact (error word above `IMPACT_DETECT_COUNTS`, vendor error 201, or the collision flag on a streamed motor), a lead past ST10, a setpoint leaving the bounds, a corrupt top byte, a setpoint past the 24-bit counter range, a session fault from elsewhere, or a trajectory-generator failure: each faults the stream. No further setpoint is sent, the stop sequence ends the order, the session latches through `_latch_fault`, and motors-off (order 16) is queued, whether or not the caller ever ends the stream. | test: `StreamCoreTests::test_r6_controller_error_word_and_emergency_bit_fault`, `SdkStreamTests::test_r6_emergency_bit_faults_by_default_without_the_caller_ending_the_stream`, `test_r6_controller_error_word_latches_a_fault`, `test_r6_collision_flag_on_a_streamed_motor_latches_a_fault`, `test_r6_collision_flag_of_a_motor_not_streamed_is_ignored`, `test_one_reply_with_a_corrupt_top_byte_sends_no_setpoint_and_names_the_motor`, `test_r6_a_fault_latches_the_session_even_if_the_caller_never_ends_the_stream`, `test_a_session_fault_from_elsewhere_stops_a_running_stream`, `SetpointConversionTests::test_the_end_of_the_counter_range_is_a_fault_not_a_wrap`; `abort-emergency.md` AB03; **to build**: `SdkStreamTests::test_every_stream_fault_queues_motors_off` (one case per cause listed) |
| ST14 | SHALL | USB error mid-stream (a failed write or read), handled as `vendor_home_run` already does: `stream_targets` catches it, sends no further setpoint, sends the stop sequence and then motors-off if the link still answers (at most `IO_ABORT_TRIES` failures, then the link counts as dead), reports a failure without killing the command worker, and the session latches a fault saying motor state is unverified and the physical stop may be needed. | test: `tests/test_stream_usb_error.py::LegacyStreamTests::test_a_usb_error_mid_stream_stops_and_switches_the_motors_off`, `LegacyStreamTests::test_a_dead_link_gives_up_after_the_abort_tries`, `LegacyStreamTests::test_execute_survives_a_usb_error_mid_stream_and_takes_the_next_order`, `SdkStreamTests::test_a_usb_error_mid_stream_latches_and_the_worker_survives` |
| ST15 | SHALL | A stream that does not end in time, an interrupt while it ends, an exception in the caller's `with` block, or a disconnect with the stream open ends it with the stop sequence, or faults the session if the worker does not answer. | test: `SdkStreamTests::test_r6_a_stream_that_does_not_end_in_time_is_stopped_not_left_running`, `test_an_interrupt_while_a_stream_is_ending_faults_the_session`, `test_an_exception_in_the_callers_block_stops_the_stream`, `test_disconnect_ends_a_stream_left_open` |
| ST16 | SHALL | Idle hold after the stream (the #133 review finding 1, for streams): after `close`, the idle messages hold the last stream setpoint; after a stop (ST11, ST12), they hold the counts at the stop. Neither follows the encoders. After a fault, motors go off and the idle messages follow the encoders. Today this comes from `STREAM` being in `HOLD_ORDERS` and from `closeMov` / `_stop_with_hold` setting `ultimo_hold`. No stream test pins it yet. | **to build**: `LegacyStreamTests::test_after_close_the_idle_messages_hold_the_last_setpoint` and `test_after_a_stop_the_idle_messages_hold_the_stop_counts`, each with the simulated counts drifting after the end (the hold must not follow them); `test_after_a_fault_no_hold_is_left`; bench: shoulder held 10 s after `close`, counts within 20 of the last setpoint |
| ST17 | SHALL | While a stream is active, every other motion command is refused; only the stream's own `set_target`, `set_speed`, `close` and `stop`, and the stop paths, act. Wrist and gripper setpoint fields not streamed echo the measured position. | test: `SdkStreamTests::test_r12_other_commands_are_refused_while_a_stream_is_active`, `LegacyStreamTests::test_r9_wrist_and_gripper_fields_always_echo_the_measured_position`, `test_five_setpoints_fill_the_wrist_fields_and_leave_the_gripper_measured` |
| ST18 | SHALL | Logged, in the session JSONL: `stream_start` (every setting, the start counts from home, the state); every refusal, including ST01 and ST05; one step record per period (time, action, state, target, commanded, measured, lead; up to 20000, with dropped steps counted); `stream_trace` with the USB packets; and one of `stream_complete`, `stream_stopped` (with the reason) or `stream_fault`. A record that cannot be written faults the session. | test: `SdkStreamTests::test_r10_the_log_holds_target_command_and_measurement_for_every_step`, `test_a_stream_whose_record_cannot_be_written_faults_the_session`, `test_a_failed_log_write_cannot_stop_a_timed_out_stream_from_faulting`, `test_a_stop_request_after_the_stream_has_ended_is_logged_not_dropped`; **to build**: the ST01 and ST05 refusal rows in their tests |
| ST19 | SHALL NOT | Streaming changes any legacy flow's bytes: the #130 golden tests pass unchanged. | test: `tests/test_legacy_golden.py`; review: `git diff origin/stack2 -- tests/fixtures/legacy_golden` is empty |
| ST20 | SHALL NOT | The build opens USB, moves hardware, or adds a dependency (Ruckig stays the optional `planning` extra). | review: final diff and the commands run; tests under the `conftest.py` USB guard |

## Open questions (with my pick)
- **Q1. Gripper resume (ST02).** PolicyArm ends the stream for each vendor gripper move and starts it
  again (`policy_arm.py` `_start_stream`). Read strictly, ST01 refuses that restart whenever the policy
  grips away from home, which is nearly always. **Pick:** exempt only that same-session resume (ST02):
  it resumes from a held setpoint of a stream that started near home. Anything that ends the session's
  trust in where the arm is (stop, fault, disconnect) clears the exemption.
- **Q2. Is the gripper a "joint" for ST01?** **Pick:** no. The gripper is not streamed, has its own
  vendor path, and vendor Home leaves it closed.
- **Q3. Every start, or the first after vendor Home?** **Pick:** every start (ST01), apart from Q1. An
  episode therefore begins at home. Returning there between episodes is a supervised vendor joint move
  to 0, which the LeRobot reset can call. That is a later issue.
- **Q4. Should a long `holding` end the stream?** **Pick:** no. Holding and the idle hold after `close`
  are the same setpoint, so ending changes nothing at the arm. The policy loop decides when to close.
- **Q5. At-rest rule (ST05).** **Pick:** reuse the stop-settle rule (2 counts, 3 quiet pairs, 8 s) rather
  than adding a new band.
- **Q6. USB error (ST14): try the stop over a failing link?** **Pick:** yes, as `vendor_home_run` does:
  the stop sequence and motors-off, at most `IO_ABORT_TRIES` failures, never raising out of the worker.
  The alternative, latch only, leaves the motors enabled with the last setpoint until the controller's own
  communication time-out (which the manual says switches the motors off; unverified).

## Callers and ST01
| Caller | Start pose | ST01 |
|---|---|---|
| `examples/bench_stream.py` | Homes, then refuses unless every motor is within 5 counts of home | Satisfies it |
| `examples/policy_loop.py` | Homes, then starts one stream | Satisfies it |
| `scripts/bring_up.py` (and `scripts/lab_day.py`, which runs it) | Jogs +1 then -1 degree, checks it is back within 20 counts of the state read after home, then streams | Satisfies it while that state is at home. The build checks against home, not that state, so the two 20-count bands cannot add up to 40 |
| `PolicyArm.connect` (and the LeRobot plugin) | Starts the stream after vendor Home | Satisfies it right after Home; a later connect away from home is refused |
| `PolicyArm` gripper handoff | Restarts the stream wherever the gripper move left the arm | Needs Q1 (ST02) |

## Not modelled, unverified
- Every limit and period is a prior: the 24 ms period, the 0.5 s hold timeout, 25 percent of MaxSpeed,
  the 2 degree lead limit, and counts per second inferred from the DLL (`VENDOR_MAX_SPEED_COUNTS_S`).
- How the controller follows a setpoint that moves every 24 ms. The legacy jog sends one target; the
  stream reuses its message (`mov_comm(1)`) with a new setpoint each period. No recording of either
  stack shows this on the arm.
- What the controller does when idle packets stop after a USB error (ST14): the manual's communication
  time-out is said to switch the motors off. We do not test it on purpose; the physical stop is
  authoritative.
- Whether the idle hold (ST16) holds the shoulder and elbow against gravity on the real arm.
- The reply the lead check uses is one loop old, plus the pacing wait (`Stream._pace`).
- Signs and zero of each joint after vendor Home: lab steps G and X in `lab/LAB_CHECKLIST.md`.
- Bench checks first: one motor, about a degree, supervised; record gap and lead from `stream_trace`
  in [`lab/LAB_RECORD_SHEET.md`](../lab/LAB_RECORD_SHEET.md).

## Sources
- Code read on `stack2` 4d0dd51: `scorbot/streaming.py`, `scorbot/stream_control.py`,
  `scorbot/policy_arm.py`, `scorbot/limits.py`, `openScorbot/libcomm.py` (`stream_targets`,
  `HOLD_ORDERS`, `vendor_home_run`'s USB-error handling), `openScorbot/libdef.py` (`closeMov`; `stopMov`
  goes through `_stop_with_hold`), `scripts/bring_up.py`, `examples/bench_stream.py`,
  `examples/policy_loop.py`.
- Earlier streaming requirements R1 to R12: `specs/2026-10-04-streaming-driver-requirements.md (in git history)`;
  the test names above keep those numbers.
- Related: [one-limit](one-limit.md), [vendor-limits](vendor-limits.md),
  [abort-emergency](abort-emergency.md), [count-frame](count-frame.md),
  [policy-gripper](policy-gripper.md).

## Change log
- 2026-11-04 Baseline from issue #167 with owner decisions on near-home and shoulder-travel decisions.
