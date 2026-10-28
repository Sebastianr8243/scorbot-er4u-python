# Changelog

Newest first. Nothing below has run on the arm except the vendor Home (see [docs/STATUS.md](docs/STATUS.md)).

## 2026-10-28

### Added
- `scripts/check_docs.py`: a documentation check that runs in CI and as a pre-commit hook. It fails on names or tool references that do not belong in the repository, control characters in Markdown, a mistyped venv path, duplicate requirement IDs and links to missing files.
- `examples/lerobot_install_check.py`: runs the LeRobot adapter against an installed `lerobot` on `FakeArm` (passed on 0.6.1). Opt-in, not part of CI.
- `README.md` files for `scorbot/`, `examples/`, `scripts/` and `tests/`.

### Changed
- Documentation reorganised: one how-to ([RUN_THE_ARM.md](docs/lab/RUN_THE_ARM.md)), one reference ([API.md](docs/API.md)), one status and roadmap page ([STATUS.md](docs/STATUS.md), which replaces `PLAN.md`). `REQUIREMENTS.md` moved to `docs/requirements/arm-api.md`. The run-log requirement IDs are now `LG01` to `LG06`.
- The lab run sheet has one first-move procedure, a third fault check (Ctrl+C during a move), and the correct `.venv` path.

### Removed
- Local coding-agent notes are no longer tracked; they are in `.gitignore`.

## 2026-10-27

### Added
- The server answers a second client that connects while it is serving one with a `busy` line and closes the connection; `Arm` raises it as an `ArmError`. Requirements: [lab-prep-polish](docs/requirements/lab-prep-polish.md).
- `docs/lab/LAB_RUN_SHEET.md`: a hazard table, a pass limit and an outside check for every step, two fault checks and a sign-off.
- `plugins/lerobot_robot_scorbot_stack1`: a LeRobot-style adapter with a safety layer, a dataset writer and `examples/sim_demo.py`. Requirements: [stack1-lerobot-adapter](docs/requirements/stack1-lerobot-adapter.md).

### Changed
- After an arm error ends `teleop.py`, it says the arm may have moved and to read `where`.
- `scorbot.keys.getch` waits in 0.05 s sleeps instead of one blocking call, because a blocking `getwch` is not woken by Ctrl+C on Windows.

## 2026-10-26

### Added
- The server logs every reply it sends (`reply` event: id, ok, code, error, timing, xyz). `scripts/summarize_log.py` prints a server log as a table of commands and outcomes. `teleop.py` prints a status line after every key. Requirements: [run-log](docs/requirements/run-log.md).

### Changed
- `Arm.send` waits for a reply in 0.2 s slices. A read blocked for the whole timeout is not woken by Ctrl+C on Windows, so Ctrl+C during a move did not send `stop` until the move had finished.
- The requirements baseline was approved and `STATUS.md` brought up to date.

## 2026-10-25

### Added
- `wrist_by` (server op, `Arm.wrist_by`, `python -m scorbot.bridge wrist`): relative wrist pitch and roll steps. `teleop.py --wrist-step` turns on the `t`/`g` and `y`/`h` keys.
- `examples/teleop.py`: keyboard control, one `move_by` per key press. The key reader moved from `record_episode.py` to `scorbot/keys.py`.
- `docs/DESK_FINDINGS.md` and `tools/usbc_fake/fake_oracle.py`: what the vendor DLL asks for, from a 44-move run against the fake controller.
- `scripts/lab_check.py`: read-only checks before a lab run.
- `FakeArm.move_to` restores pitch and roll when given.
- `usbc_server` `ping` reports `simulated`; `record_episode.py` labels an episode `real` only when the server is not simulating.

### Removed
- `main` is now only the vendor-DLL path. The own-USB-protocol code (`Scorbot`, the simulator, sessions), the disassembly notes and the archive moved to the `stack2` branch. The suite went from 1,431 tests to 148, and `uv.lock` dropped the Stack 2 dependencies.

## 2026-10-22

### Added
- `examples/record_episode.py`: key-driven teleoperation that logs two cameras, the arm state and each command to `episodes/<time>/`; `--fake` rehearses with no hardware under `rehearsal/`.
- `examples/arm_latency.py`: times `where`, `move_by` and the gripper through the server.
- Replies to a move carry `timing_s` (read, define, teach, move, settle) and gripper calls carry (call, settle); `Position.timing` exposes it.
- `docs/API.md`: every call, unit, refusal and error.

### Changed
- With a box that `arm_characterise.py` measured, `move_by` is refused when a step ends further outside the box than it starts. A step back toward the box is allowed. With no box, or one grown only by `teach`, only the 50 mm cap applies.
- The README was rewritten around the arm API (`scorbot.bridge.Arm`).

## 2026-10-20

### Added
- Vendor Home ran on the real arm (134 s).
- The arm server and the Python API.
