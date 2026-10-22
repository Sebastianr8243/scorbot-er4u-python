# Changelog

## Unreleased (branch box-check, 2026-10-22)

- `usbc_server`: with a box file, `move_by` is refused when the step ends further outside the box than it starts (a step back toward the box is allowed). No box file: the 50 mm cap only, as before. Tested against the fake DLL only; not run on the arm.

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
