# Lessons

One dated line per lesson: what worked or failed, and the evidence (command, test or file). Keep it under about 30 lines; delete lines that stop being true.

- 2026-10-18: the gate suite (`pytest -n 2 tests`) takes about 4.5 min, so a Stop hook cannot run it; use `ruff check .` plus `compileall` for a quick check. Evidence: 1011 passed in 260 s on the bootstrap run.
