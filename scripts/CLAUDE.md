# scripts/

| Script | Purpose |
|---|---|
| `lab_check.py` | Read-only checks before a lab run: vendor files and `ER4Ax*.ini` beside the DLL, 32-bit Python, controller listed with the Intelitek driver (not Zadig's), SCORBASE closed, server port free, working box measured, no vendor files tracked by git. Prints PASS / WARN / FAIL per line, exit 1 on any FAIL. Never opens the DLL or the arm. The USB, driver and Python checks have only run on a PC without the arm |
| `setup_windows.ps1` | Creates `.venv`, installs `.[dev]`, runs `pip check` and the tests. No USB access, no driver change |

Scripts here never open USB or command the arm. Root rules in [../CLAUDE.md](../CLAUDE.md) apply.
