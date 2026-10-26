# Changelog

## Unreleased (2026-10-25)

- 2026-10-26: the server now logs every reply it sends (`reply` event: id, ok, code, error, timing, xyz); `scripts/summarize_log.py` prints a server log as a table of commands and outcomes; `teleop.py` prints a status line after every key. Requirements: `docs/requirements/run-log.md`. Desk-tested on the fake library; not run on the arm.
- 2026-10-26: `Arm.send` now waits for a reply in 0.2 s slices. A read blocked for the whole timeout is not woken by Ctrl+C on Windows, so Ctrl+C during a move did not send `stop` until the move had finished; now it does (desk test, no arm).
- 2026-10-26: `REQUIREMENTS.md` baseline approved; `STATUS.md` brought up to date; README gained the layers diagram and a reviewer path.
- `main` is now only the vendor-DLL path. Our own USB protocol (`Scorbot`, the simulator, sessions, `openScorbot/`), the disassembly notes, the archive and their tests moved to the `stack2` branch. Tests went from 1,431 to 148; `uv.lock` dropped mcap, pyusb, ruckig and the other Stack 2 dependencies.
- `scripts/lab_check.py`: read-only checks before a lab run.
- `docs/PLAN.md` (goal, unknowns, steps), `docs/DESK_FINDINGS.md` (what the vendor DLL asks for) and `tools/usbc_fake/fake_oracle.py` (the 44-move desk run behind it).
- `FakeArm.move_to` now restores pitch and roll when given.
- `wrist_by` (server op, `Arm.wrist_by`, `python -m scorbot.bridge wrist`): relative wrist pitch and roll steps; `teleop.py --wrist-step` turns on the t/g and y/h keys. Tested on the fake library and end to end on the vendor DLL against the fake controller; not run on the arm.
- `examples/teleop.py`: keyboard control, one `move_by` per key press, no cameras. The key reader moved from `record_episode.py` to `scorbot/keys.py`. Not yet run on the arm.
- `usbc_server` `ping` reports `simulated`; `record_episode.py` labels an episode `real` only when the server says it is not simulating.
- `REQUIREMENTS.md` is now the arm API requirements (was `docs/requirements/arm-api.md`).

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
- `scorbot/` marked experimental except `bridge.py`; `docs/archive/README.md` indexes the archive.
- `bridge.py`: type hints and docstrings; `XYZ_UNITS_PER_MM` comment cites the 2026-10-20 reading (9,996 units for a 10 mm step on the fake controller).
- Tests for the `Arm` client against a stand-in server.

## 2026-10-20

- Vendor Home ran on the real arm (134 s). Arm server and Python API merged (PR #29), not yet run on the arm.
