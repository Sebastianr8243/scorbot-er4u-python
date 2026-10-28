# scripts

Helper scripts. None of them opens USB or commands the arm.

| Script | Purpose |
|---|---|
| `lab_check.py` | Read-only checks before a lab run: vendor files and `ER4Ax*.ini` beside the DLL, 32-bit Python, controller listed with the Intelitek driver (not Zadig's), SCORBASE closed, server port free, working box measured, no vendor files tracked by git. Prints PASS, WARN or FAIL per line; exits 1 on any FAIL. The USB, driver and Python checks have only run on a PC without the arm |
| `summarize_log.py` | Prints a `logs/server-*.jsonl` as one row per command (time, op, ok, refused with the reason, DLL code, or no reply; server seconds) and totals. Says when the log ends mid-line. Standard library only. Reports what was asked and answered, not whether the arm physically moved |
| `check_docs.py` | Fails on broken documentation: names or tool references that do not belong in the repo, control characters in Markdown, a mistyped venv path, duplicate requirement IDs, links to missing files. Runs in CI and as a pre-commit hook |
| `setup_windows.ps1` | Creates `.venv`, installs `.[dev]`, runs `pip check` and the tests. No USB access, no driver change |

Usage: [docs/lab/RUN_THE_ARM.md](../docs/lab/RUN_THE_ARM.md), section 1.
