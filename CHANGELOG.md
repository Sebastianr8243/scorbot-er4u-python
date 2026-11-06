# Changelog

## Unreleased (simulator vendor orders, 2026-11-06)

- Simulator vendor joint moves now follow all five arm setpoints on order 23; gripper fault behavior applies only to packets selecting the gripper motor, so digital and analog output writes leave every encoder count unchanged.

## Unreleased (issue #199, 2026-11-06)

- #199: `python -m scorbot.bridge stop` prints ok only when the server confirms the stop (new `Arm.stop_confirmed`, 3 s wait); a busy server or no reply is a failure with exit 1. `stop()` is unchanged (fire and forget, used by Ctrl+C). `scorbot doctor` now requires Python 3.12, like `preflight`. Fake server only; never run on the arm.

## Unreleased (issue #197, 2026-11-06)

- #197: the guided lab session (`python -m scorbot.lab`) and `scripts/bring_up.py` home with the vendor Home only. Before, the default session ran the legacy `home()`, which leaves the gripper unhomed, so every jog after it was refused and the session ended failed. Removed `LabSession(vendor_home=...)`, `--vendor-home` (no doc or script still passed it, so it is not kept as a no-op) and `bring_up.py --legacy-home` (nothing on the lab path needs it). `Scorbot.home()` and `openScorbot/setHome.py` stay for other callers. The docs' SDK snippet now shows `vendor_home`.

## Unreleased (issue #190, 2026-11-05)

- #190 cut B, one limit source: the velocity jog window (`scorbot/manual_jog.py`) is the DLL vendor limit only; the source-model `motion_window` / `pose_limit_excess` guard (`_edge`) is gone, so a velocity jog may go to the vendor edge (elbow with the wrist at home: 7373 counts, was 5194). Every other motion path already used `nominal.vendor_limit_problem`. `source_model` limit helpers stay as reference data with no motion caller. Simulator only; never run on the arm.

## Unreleased (issue #189, 2026-11-05)

- #189 cut A, one home: deleted the inch home (`Scorbot.home_inch`, `scorbot/inch_home.py`), the pre-home move (`Scorbot.pre_home_jog`) and the park (`Scorbot.park_at_home`) with their helpers, `--inch-home` and `--coupled-pre-home` in `scorbot.lab`, `bench_joint.py`, `bench_stream.py` and `policy_loop.py`, `LabSession(inch_home=, coupled_pre_home=)` and the lab session's pre-home and park prompts, requirements R16 and R24, and the tests of those. `Scorbot.home()` (legacy) and `openScorbot/setHome.py` stay as the simulator test fixture until the follow-up. Vendor Home and the lab-day path are unchanged. Never run on the arm.

## Unreleased (issue #188, 2026-11-05)

- #188: removed the recording viewers: `python -m scorbot.session` (replay, list, export, compare, plot, view), `scorbot analyze session`, `session/analysis.py`, `plot.py`, `rerun_view.py`, `examples/make_synthetic_session.py`, the `plot` and `viz` extras and matplotlib from `dev`, and the tests of that code. Kept: `SessionWriter`, `BestEffortRecorder`, `schemas.py`, `notes.py` (the writer creates `notes.md`) and `replay.py` (the camera stream check and lab tests read sessions with it). Foxglove opens the `.mcap` directly. No hardware code touched.

## Unreleased (issue #185, 2026-11-05)

- #185: safe shutdown after any failure (SD01-SD07, `docs/requirements/safe-shutdown.md`). A USB error in a legacy jog, the gripper clamp or the stream close attempts motors-off once, reports result 1 and keeps the worker alive; `motors_off` and the gripper cleanup try every exchange even after a failure; any latched fault queues one motors-off, the emergency input also cancels orders already queued (no motors-on after an emergency), `disable()` works while faulted, and Ctrl+C during `connect` after motors-on attempts motors-off. The legacy jog impact stop is unchanged. Fake endpoints only; never run on the arm, physical motor state unverified.

## Unreleased (issue #183, 2026-11-05)

- #183: `test_timed_jog_call_order` counts sends against a simulated clock instead of a 100 ms wall-clock window, fixing a random failure on busy PCs. Test-only; fake DLL only, never run on the arm.

## Unreleased (issue #172, 2026-11-05)

- #172: stream stop rows name the first stop cause, including Abort, caller exceptions and disconnect; mapped the end/fault tests for ST11-ST13, ST15 and ST18. Simulator and fake endpoints only.

## Unreleased (issue #171, 2026-11-05)

- #171: ST09 setpoint steps round toward zero and stay within the current speed limit per period; finished trajectories reach their rounded endpoint within that limit. Added test coverage for ST07-ST10 and a strict velocity assertion in the core tests. Simulator and core tests only; never run on the arm.

## Unreleased (issue #166, 2026-11-05)

- One-command lab day: G adds supervised velocity jogs of base, shoulder and elbow at manual speed 5 for 0.3 s each way (JOG per axis); declining GO skips and continues. One dated `lab-day.jsonl` indexes step results and logs for `lab_summary.py --sheet`, including signed jog counts and skips. Simulator checked only; velocity jogs unverified on the arm.

## Unreleased (branch fix-stream-usb-173-175, 2026-11-05)

- #175: a failed write or partial read on the stream's opening message uses the existing stop and motors-off recovery, restores the last complete reply, and keeps the command worker alive without reusing the opening packet ID. Tested with fake endpoints only; never run on the arm.
- #173: stream and vendor-home recovery return the last packet ID consumed when motors-off fails, including failed writes, partial reads and sequence-byte wraparound. Clean motors-off messages and return values stay unchanged. Tested with fake endpoints only; never run on the arm.

## Unreleased (branch stream-usb-error-169, 2026-11-04)

- #169 / ST14: a USB error mid-stream ends setpoints, attempts stop and motors-off with bounded failures, and reports a latched fault with physical-stop guidance while keeping the command worker alive. Tested with fake endpoints only; never run on the arm.

## Merged to stack2, 2026-11-01 to 2026-11-04

- #170: streams wait for rest and start within 20 counts of home on all five arm motors; only a clean close permits a one-use resume near its final pose for gripper handoffs, with start and refusal rows logged. Tested on the simulator and fakes only.
Desk only: nothing below ran on the arm. Newest first.

- #164 `scorbot.lab`: the guided lab session requires explicit `--simulate` or `--online`; no mode refuses before setup, and real connections require `--online` because connecting energises the motors.
- #161 CI: a pull request that changes only `docs/` or `*.md` passes the four required unit jobs without installing or running the tests; the Ubuntu 3.13 cell still runs `check_docs.py` and the two tests that read Markdown.
- #159 `scorbot` and `python -m scorbot`: a read-only setup doctor with text/JSON output and short commands for existing Stack 2 tools. Arguments and safety gates stay unchanged; Ctrl+C waits for child cleanup. `scorbot record` prints the separate LeRobot simulation command.
- #155 Docs: the lab DLL's count-to-angle and home-offset math checked against `scorbot/vendor_model.py`, ending in the bench measurement list for calibration.
- #154 Docs: `docs/protocol/DLL_ERROR_CODES.md`, every error code the lab DLL raises, and whether Stack 2 handles it.
- #153 Docs: the remaining partial rows in `DLL_COVERAGE.md` settled against the lab DLL.
- #151 Test fix: `RealArmCleanupTests` patches only the plugin's `atexit`, so ruckig's own exit hook no longer makes it flaky (#146).
- #150 Teleop: all five joints and the gripper on letter keys, clear of `lerobot-record`'s arrows; the twin's jaw follows `gripper.pos`.
- #149 Docs: byte-level trace of the lab DLL's velocity jog (`EnterManual`, `MoveManual`, `CloseManual`) for #133.
- #147 Faster CI: `tests_extra` runs once, one install, pip cache, sysmon coverage on Python 3.13.
- #145 Legacy path: golden byte tests for 43 legacy flows, then one jog implementation instead of four (#130).
- #142 Legacy jog: on a count jump, stop at the last trusted counts instead of raising with the move open (#140).
- #141 Faster tests: fake clocks instead of real 60-75 s waits; no product code changed (#135).
- #139 Count frame: a corrupt top byte in a reply never becomes a setpoint, on the SDK stream or the legacy path (#138).
- #137 DLL gaps: `Time`, jaw ramp and `GetJaw`, and queued digital output match the lab `USBC.dll` (#129).
- #136 `lab_summary.py --sheet`: fills the lab record sheet from the logs, row by row.
- #132 Count frame: every count is the controller count over its full 24-bit range.
- #131 Count frame: every legacy setpoint carries the controller count, fixing one-count errors around zero.

## Unreleased (branch move-timing, 2026-10-22)

- `usbc_server` replies to a move carry `timing_s` (read, define, teach, move, settle) and to a gripper call (call, settle); `Position.timing` exposes it and `arm_latency.py` prints the median per step. No change to what the arm does. Tested against the fake DLL only; not run on the arm.

## Unreleased (branch episode-recorder, 2026-10-22)

- `examples/record_episode.py`: key-driven teleoperation that logs two cameras, the arm state and each command to `episodes/<time>/` as plain files; `--fake` rehearses with no hardware under `rehearsal/`. Details in its docstring. Not yet run on the arm or with real cameras; nothing reads the episodes yet.

## Unreleased (branch box-check, 2026-10-22)

- `usbc_server`: with a box that `arm_characterise.py` measured (`"measured": true` in `calibration/arm.json`), `move_by` is refused when the step ends further outside the box than it starts (a step back toward the box is allowed). No box, or a box grown only by `teach`: the 50 mm cap only, so teach-then-characterise still works. Tested against the fake DLL only; not run on the arm.

## Unreleased (branch arm-latency, 2026-10-22)

- `examples/arm_latency.py`: times `where`, `move_by` and the gripper through the arm server (calls per second). Not yet run on the arm.

## Unreleased (branch release-cleanup, 2026-10-22)

- README rewritten around the arm API (`scorbot.bridge.Arm`); the old text is `docs/STACK2_SDK.md`.
- `docs/API.md`: every call, unit, refusal and error.
- `scorbot/` marked experimental except `bridge.py`; `README.md (in git history)` indexes the archive.
- `bridge.py`: type hints and docstrings; `XYZ_UNITS_PER_MM` comment cites the 2026-10-20 reading (9,996 units for a 10 mm step on the fake controller).
- Tests for the `Arm` client against a stand-in server.

## 2026-10-20

- Vendor Home ran on the real arm (134 s). Arm server and Python API merged (PR #29), not yet run on the arm.
