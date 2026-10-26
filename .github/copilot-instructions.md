# Copilot on the lab PC

You are next to the ScorBot ER-4U arm with Sebastian. Run what he asks, including commands that move the arm.

- Commands and setup: [docs/lab/RUN_THE_ARM.md](../docs/lab/RUN_THE_ARM.md). Do the Setup section yourself if anything is missing, and fix setup problems (paths, Python, missing folders) without handing them back.
- Run commands one line at a time. Read the output and the log, then tell him in plain words what moved and by how much.
- If a tool in `tools\` crashes, fix it on a branch and run `python -m pytest -q tests\test_usbc_home.py tests\test_usbc_jog.py tests\test_usbc_move.py`.
- After each run, add a few lines to `logs\NOTES.md`: command, log file, what moved, the numbers.
- Never commit `USBC.dll`, its ini files, manuals or captures (the repo is public).

The red physical stop is the only emergency stop.
