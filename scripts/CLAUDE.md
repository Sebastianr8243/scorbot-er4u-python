# scripts/

| Script | Purpose |
|---|---|
| `lab_check.py` | Read-only checks before a lab run: vendor files and `ER4Ax*.ini` beside the DLL, 32-bit Python, controller listed with the Intelitek driver (not Zadig's), SCORBASE closed, server port free, working box measured, no vendor files tracked by git. Prints PASS / WARN / FAIL per line, exit 1 on any FAIL. Never opens the DLL or the arm. The USB, driver and Python checks have only run on a PC without the arm |
| `summarize_log.py` | Reads a `logs/server-*.jsonl` and prints one row per command (time, op, ok / refused with reason / dll code N / no reply, server seconds) and totals; says when the log ends mid-line. Read-only, standard library only. Pairs each command with the next `reply` event, skipping the server's own step events. It reports what was asked and answered, never whether the arm physically moved |
| `setup_windows.ps1` | Creates `.venv`, installs `.[dev]`, runs `pip check` and the tests. No USB access, no driver change |

Scripts here never open USB or command the arm. Root rules in [../CLAUDE.md](../CLAUDE.md) apply.
