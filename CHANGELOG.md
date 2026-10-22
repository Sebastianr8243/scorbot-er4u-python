# Changelog

## Unreleased (branch episode-recorder, 2026-10-22)

- `examples/record_episode.py`: key-driven teleoperation that logs two cameras, the arm state and each command to `episodes/<time>/` as plain files (`--fake` rehearses with no hardware). Each step's row is written before the arm moves, then an outcome line (ok, refused, failed, interrupted); keys pressed during a move are discarded except q, which still ends the episode; `meta.json` records `source` (real or simulated) and `--fake` writes under `rehearsal/`; Windows special keys (Insert, Delete, End) no longer become commands. Not yet run on the arm or with real cameras. Nothing in the repo reads the episodes yet; a converter to a training format comes after the format is checked against the real library.

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
