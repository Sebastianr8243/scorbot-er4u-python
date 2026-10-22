# Changelog

## Unreleased (branch move-timing, 2026-10-22)

- `usbc_server` replies to a move carry `timing_s` (read, define, teach, move, settle) and to a gripper call (call, settle); `Position.timing` exposes it and `arm_latency.py` prints the median per step. No change to what the arm does. Tested against the fake DLL only; not run on the arm.

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
