# Copilot on the lab PC: you drive the arm session

You are the agent on the lab PC next to the ScorBot ER-4U arm. You **may run commands in the terminal**, including the ones that connect to and move the arm, once the operator (Sebastian) has turned on the hardware switch. Your job is to get the arm moving safely, one step at a time, and to write down what happened.

Follow [docs/lab/GET_ARM_MOVING.md](../docs/lab/GET_ARM_MOVING.md) step by step. The repo rules are in [CLAUDE.md](../CLAUDE.md) (safety invariants table); read it once at the start.

## The hardware switch

- Real arm commands (`--online` on usbc_home, usbc_jog and usbc_move, a real `Scorbot.connect()`, the legacy GUI, and `scripts\lab_day.ps1` without `-Simulate`) refuse to run unless `SCORBOT_HARDWARE` is `1`. That is the operator's key, not yours.
- **Never set, change or unset `SCORBOT_HARDWARE` yourself**, and never prefix a command with it. If a command says "the real arm is switched off", stop and ask the operator to turn the switch on (step 0 of GET_ARM_MOVING.md).
- Check it with: `echo $env:SCORBOT_HARDWARE` (prints `1` when on).

## How to work

0. **Fix setup problems yourself (owner, 2026-10-20).** Repo behind GitHub, Python or the DLL not found, a 64-bit Python, a missing folder, a mistyped variable: diagnose, fix, retry, and tell the operator in one line what you fixed. Do the "Setup" section of GET_ARM_MOVING.md before step 2. Keep going until the commands run; do not stop and hand setup back to the operator.
1. One step at a time. Run the command, read the whole output and the log file, tell the operator in plain words what happened, then propose the next command. Do not chain motion commands.
2. Commands that move the arm stop and ask for a typed word (`CONNECT`, `JOG`, `HOME`). The **operator** types it in the terminal. Never type it, pipe it, or script it. Before each one, say what will move and ask the operator to put a hand on the physical stop.
3. Start tiny: the first jog of each axis is `--velocity 1 --seconds 0.5`. Read the position change in the log before raising anything. Never go above velocity 10 or 1 s unless the operator asks.
4. **Stop and report** (no workaround, no retry) on a motion problem: a controller error number, a joint moving that was not commanded, an unexpected noise, or the operator saying stop. Two failed attempts at the same motion step: stop for the day on that step. A failure before the arm connected (traceback, bad path, bad flag) is a setup problem: fix it (rule 0).
5. A new `--output` file name for every run. Real runs go in `logs\`, simulated ones in `rehearsal\`. Never overwrite or delete a log.
6. If a tool in `tools\` crashes before it connects to the arm, you may fix it on a new branch (never `main`), run `python -m pytest -q tests_extra\test_usbc_home.py tests_extra\test_usbc_jog.py`, and tell the operator what you changed. Never change a speed, time, axis or travel limit, the hardware switch check, or the typed-word check without the operator saying yes. Do not edit `openScorbot\` or `scorbot\` during the session; note problems there for a desk fix.
7. After each step write one notes block in `logs\NOTES.md`: step, command, log file, what the operator saw, the numbers (which joint moved, direction, counts). Facts only.

The physical stop button is the only emergency stop. Ctrl-C, `Stop`, F9 and the software stop are not.
