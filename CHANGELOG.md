# Changelog

## Merged to stack2, 2026-11-01 to 2026-11-04

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
