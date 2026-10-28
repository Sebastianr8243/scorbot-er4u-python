# ScorBot ER-4U Python control

Python control for the Intelitek ScorBot ER-4U arm (original Controller-USB). A script talks to a small local server that holds one homed session with Intelitek's own `USBC.dll`:

```python
from scorbot.bridge import Arm          # FakeArm has the same calls and needs no arm

with Arm() as arm:
    arm.move_by(dz=20)                  # mm
    arm.gripper(open=False)
    print(arm.where())
```

**The red physical stop is the only emergency stop.** Nothing in this repo replaces it.

## Try it with no hardware

```powershell
python -m pip install -e ".[dev]"
python -c "from scorbot.bridge import FakeArm; a = FakeArm(); a.move_by(dz=20); print(a.where())"
python -m pytest -n 2 tests          # about 1 minute, never opens USB or moves the arm
```

## Run the real arm

Lab PC only. It needs Intelitek's `USBC.dll` (not in this repo) and a 32-bit Python for the server. One page covers setup, commands and errors: **[docs/lab/RUN_THE_ARM.md](docs/lab/RUN_THE_ARM.md)**. Every call, unit and error: **[docs/API.md](docs/API.md)**.

## What works, and how sure we are

Full table with evidence levels: [docs/STATUS.md](docs/STATUS.md).

| | Status |
|---|---|
| Vendor Home through the DLL | Ran on the real arm, 2026-10-20 (once) |
| Arm server and Python API (`move_by`, `move_to`, gripper, speed) | Tested, and run against the DLL on a fake controller. Not yet run on the arm |
| Physical mm scale of moves | Unmeasured. `examples/arm_characterise.py` measures it |
| Pick and place (`examples/pick_place.py`) | Written, not yet run on the arm |
| Our own USB protocol (`scorbot.Scorbot`, "Stack 2") | Experimental. Never moved the arm; its one home attempt failed. See [docs/STACK2_SDK.md](docs/STACK2_SDK.md) |

Hardware claims are "unverified" until a bench log backs them.

## Layout

| Path | What it is |
|---|---|
| `scorbot/bridge.py` | The Python API (`Arm`, `FakeArm`), standard library only |
| `tools/usbc_server/` | The 32-bit server that holds the DLL session |
| `tools/usbc_home`, `usbc_jog`, `usbc_move` | One-shot lab tools through the DLL |
| `tools/usbc_fake/` | The DLL against a fake controller (no arm) |
| `examples/` | `arm_characterise.py`, `pick_place.py`, and offline previews |
| `scorbot/` (rest), `openScorbot/` | Stack 2 and the original GPL OpenScorbot code (experimental, reference) |
| `scripts/bring_up.py` | Stack 2 desk rehearsal (`--simulate`) and supervised lab ladder; [runbook](docs/lab/BRING_UP_RUNBOOK.md) |
| `docs/` | Index in [docs/README.md](docs/README.md); protocol and manual notes |
| `tests/`, `tests_extra/` | `unittest` suites, no hardware |

## Rules

- Never commit Intelitek files: `USBC.dll`, its ini files, manuals, captures. The repo is public.
- Tests and CI never open USB or move the arm.
- Contributor rules: [CONTRIBUTING.md](CONTRIBUTING.md).

## Original project

OpenScorbot was developed by José Luis Pérez Pérez and Yolanda M. Gimeno Rodríguez at the University of La Laguna ([original repo](https://github.com/tidus747/openScorbot)). This repository retains the GPL-3.0 license.
