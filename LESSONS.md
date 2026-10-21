# Lessons

One dated line per lesson: what worked or failed, and the evidence (command, test or file). Keep it under about 30 lines; delete lines that stop being true.

- 2026-10-18: the gate suite (`pytest -n 2 tests`) takes about 4.5 min, so a Stop hook cannot run it; use `ruff check .` plus `compileall` for a quick check. Evidence: 1011 passed in 260 s on the bootstrap run.
- 2026-10-20: layered gates (env switch, flags, typed word) plus 17 lab docs blocked the first lab run more than they protected it; the owner had them removed. The vendor tools now need only `--online`; logs default to `logs/`. Evidence: `docs/lab/RUN_THE_ARM.md`, `tests_extra/test_usbc_*.py`.
- 2026-10-20: the vendor DLL refuses Initialization (`init_refused`, both builds, simulated or not) when the axis INIs (`ER4Ax*.ini`, `ROB_4u.ini`) are not beside it; with them, simulation inits but its Home ends in 561. The fake-pipe tree also needs the pipe listening before the DLL starts. Then `usbc_server` on the lab build ran Home, two `move_by`, both gripper calls, Speed and an absolute `move_to` in one session, ending with Control off and close (desk run 2026-10-20). A 10 mm step read back as 9,996 xyz units, so the xyz array uses the Teach unit in the DLL's own model.
- 2026-10-20: `sed -i` over many files in Git Bash is slow and its escaping silently matched nothing; a one-pass Python rewrite worked. Evidence: the docs/archive path rewrite.
- 2026-10-21: the arm-path tests (119, Stack 1 only) take about 17 s with `pytest -q -n 0 <files>`; the same set through `-k "usbc or arm_api"` with xdist took 39 s because it collects all of tests_extra and starts workers, and the full suite takes 3 to 6 min. Use the explicit file list. Evidence: `CLAUDE.md` "Daily check for the arm path".
