# Lessons

One dated line per lesson: what worked or failed, and the evidence (command, test or file). Keep it under about 30 lines; delete lines that stop being true.

- 2026-10-18: the gate suite (`pytest -n 2 tests`) takes about 4.5 min, so a Stop hook cannot run it; use `ruff check .` plus `compileall` for a quick check. Evidence: 1011 passed in 260 s on the bootstrap run.
- 2026-10-20: layered gates (env switch, flags, typed word) plus 17 lab docs blocked the first lab run more than they protected it; the owner had them removed. The vendor tools now need only `--online`; logs default to `logs/`. Evidence: `docs/lab/RUN_THE_ARM.md`, `tests_extra/test_usbc_*.py`.
- 2026-10-20: `sed -i` over many files in Git Bash is slow and its escaping silently matched nothing; a one-pass Python rewrite worked. Evidence: the docs/archive path rewrite.
