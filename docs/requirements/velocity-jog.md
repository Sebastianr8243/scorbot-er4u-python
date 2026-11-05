# Velocity jog requirements
Status: baseline v1 2026-11-04 (issue #133, approved by Sebastian through mission control)

## Goal
`Scorbot.start_velocity_jog(joint, speed, *, deadline_s=1.0)` sends what the lab `USBC.dll`'s
`EnterManual(0)`, `MoveManual(axis, v)` and `CloseManual` send (`docs/protocol/VENDOR_MANUAL_MOVE_TRACE.md`
section 4), in the controller count frame (`count-frame.md`), with a dead-man. It is the base for a
pendant-like teleop. Checks run offline against the generator, the simulator and the desk fake. Nothing
here has been run on the arm: every physical claim is **unverified**.

## Requirements
| ID | Type | Statement | Check |
|---|---|---|---|
| VJ01 | SHALL | The per-axis step is `slot` = trunc(float32(`Manual` x \|v\|/100 / 1000) x `NoEnc90` / (pi/2)), `Manual` = `Manual_1` for v < 0 and `Manual_2` for v > 0, from the lab axis files: base -145/145, -12770; shoulder 145/-145, -10216; elbow 160/-160, 10216. Base at +50 gives -589. | test: `tests/test_vendor_manual.py::Planner::test_slot_matches_the_dll_formula` |
| VJ02 | SHALL | Message k (from the first message, k = 0) carries start + trunc(`slot` x s((k + 1) x 0.024)) on the jogged slot, s the lab DLL's S-curve (0.3 s jerk-limited ramp to a slope of 10/3 per second); every other slot holds the start. | test: `Planner::test_the_trace_table_speed_50` (the trace's k = 0 to 85 table, exact) |
| VJ03 | SHALL | Every recorded `MoveManual`/`CloseManual` jog in `tests/fixtures/dll_lab_manual.json` is reproduced exactly: the same commands at the same message index and the same setpoint change on every message, given the recorded close message as input. | test: `Planner::test_every_recorded_jog_matches_exactly` |
| VJ04 | SHALL | The first message is `47` only when the jog starts settled (`settled=True`); with `settled=False` the first message is a `0D` with the same setpoint and no `47` is sent. The SDK starts a jog only after its settle check, so it always passes `settled=True`. | test: `Planner::test_no_47_when_not_settled` (unverified case) |
| VJ05 | SHALL | A close (stop, deadline, keep-alive, window, 30 s) starts on the message after the request: past t = 0.3 s a 0.3 s ramp down that ends 0.5 x `slot` past the setpoint at the request; at or before 0.3 s the reflected profile (end 2t, final s = 2 s(t)). Then `4F 3F 53`, `73 20`, `42 20 00 00`, each carrying the last setpoint, and no `47`. | test: `Planner::test_close_after_the_ramp`, `Planner::test_close_during_the_ramp_is_reflected` (unverified case), `Simulated::test_stop_ramps_down_and_returns_the_state` |
| VJ06 | SHALL | A keep-alive of 300 messages, re-armed to 300 by `renew()`, ends the jog with the same close when it runs out. | test: `Planner::test_keep_alive_runs_out_after_300_messages`, `Planner::test_renew_rearms_300_messages` (unverified cases) |
| VJ07 | SHALL | Ours: the jog also closes when `renew()` was not called for `deadline_s` (0.1 to 3 s), after 30 s in all, or before the setpoint plus the remaining ramp down would leave the window. | test: `Planner::test_deadline_without_renew_closes`, `Planner::test_renew_keeps_it_going`, `Planner::test_30_s_cap`, `Planner::test_window_includes_the_ramp_down`, `Planner::test_at_the_edge_sends_nothing`, `Planner::test_speed_and_axis_are_checked`; `Simulated::test_the_deadline_ends_it_and_renew_then_says_so` |
| VJ08 | SHALL | A new speed or sign during a jog changes nothing (the DLL ignores it); a speed change is `stop` and a new jog. | test: `Planner::test_every_recorded_jog_matches_exactly` (the "renewed, then -40 and +20 re-sent" jog) |
| VJ09 | SHALL | A stop request or the pendant Abort holds the last reply's counts and sends `47`, `4F 3F 53`, `73 20`, `42 20 00 00` (legacy `stopMov`), and `stop()` raises `MotionStopped` with no fault. An emergency input, an impact flag (also during the close or the stop), or a setpoint more than `LEAD_LIMIT_COUNTS` from the measured count sends the same and faults with the motors switched off. | test: `Planner::test_a_stop_request_holds_the_measured_counts_with_the_legacy_stop`, `Planner::test_an_emergency_stops_then_faults`, `Planner::test_an_impact_while_closing_or_stopping_still_closes_then_faults`, `Planner::test_an_arm_that_does_not_follow_is_a_fault`, `Planner::test_speed_and_axis_are_checked`; `Simulated::test_request_stop_and_the_pendant_abort_end_it_without_a_fault`, `Simulated::test_refusals`, `Simulated::test_refused_during_another_motion`, `Simulated::test_a_stop_asked_before_refuses_once`, `Simulated::test_disconnect_ends_an_open_jog` |
| VJ10 | SHALL | The jog needs vendor Home, like `jog_joint`; its window is the narrower of the source-model window and the vendor limits (`nominal.vendor_limit_problem` on `signed_encoder_counts`, CT14), measured from the worker's first reply. A pose already past a limit may move back, never further out. | test: `Simulated::test_the_joint_limit_ends_it`, `Simulated::test_the_vendor_limit_ends_it`, `Simulated::test_refused_before_vendor_home`, `Simulated::test_past_a_vendor_limit_can_only_move_back`, `Simulated::test_needs_enabled_and_homed` |
| VJ11 | SHALL | No count-frame offsets: the window and the simulator use controller counts as they are (no +1 or -1 on the positive side, CT12, CT13). | test: `Simulated::test_the_window_is_measured_from_where_the_worker_starts` with start counts on both sides of zero |
| VJ12 | SHALL | The real `Scorbot` on the desk fake sends the DLL's commands for a jog. | test: `DeskWire::test_the_real_scorbot_sends_the_dlls_commands` |
| VJ13 | SHALL NOT | The jog changes any legacy flow's messages: the #130 golden tests pass unchanged. | test: `tests/test_legacy_golden.py`; review: `git diff origin/stack2 -- tests/test_legacy_golden.py` is empty |
| VJ14 | SHALL NOT | This work opens USB, moves hardware, adds dependencies, or puts more than 5 lines of decompiled code or any vendor binary in the repo. | review: final diff and commands |

## Not modelled, unverified
- The manual queue depth (`ManualBuffers` 4 in `ER4CONF.INI`, 5 by default; which file the lab loads is
  unchecked) and its once-per-process doubling. Stack 2 paces one message per reply.
- The `47` gate is read as "settled" (trace I); the reply has no following error, so the SDK's own settle
  check stands in for it.
- The start is the first reply's measured count; the DLL starts from its commanded setpoint. They agree when
  the idle hold echoes the measured counts.
- Axes 3 to 5 (the wrist takes another path in the DLL), the XYZ mode (type 1), the gripper path.
- Close during the ramp, keep-alive expiry and the no-`47` case follow the trace's reading of the code; no
  recording covers them.

## Test evidence
- The fixture's `steps` are message 0 minus the start, then each message minus the one before, without
  the last (always 0). At base +50 they begin -1, -4, -9, whose running sums -1, -5, -14 are the trace
  table's k = 0..2: the recording and the table agree, and the first message (`47`) carries s(0.024).
- Both `1.5708` and `math.pi/2` reproduce every fixture difference with the S-curve. Tests choose `1.5708`, the constant identified in the lab disassembly.
- For fixture matching, `c` is the trace profile's zero-based close request message: 186 for the
  renewed base -20 jog; 266 for the one-call 3 s base -20 jog; 70 for base -5, -20, -50, +50,
  shoulder -20 and elbow +20; 71 for base -10 and -100.
  These are profile alignment values, not a verified physical call-to-message timing.
- Keep-alive off-by-one is **unverified**: send messages k=0..299, then start ramp down at k=300.
  A renew after k=199 permits k=200..499, then starts ramp down at k=500. The clock is frozen in
  these checks so the wall-clock deadline cannot hide keep-alive expiry.

## Change log
- 2026-11-04 Baseline from issue #133 and the lab-build trace (#149).
- 2026-11-04 Brief 1: executable Planner/Simulated/DeskWire check names, exact trace and fixture checks.
