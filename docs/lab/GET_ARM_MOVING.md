# Get the arm moving: agent script and copy-paste commands

Written 2026-10-15, updated 2026-10-19 (hardware switch; the agent may now run the commands). For an AI agent on the lab PC (GitHub Copilot in VS Code reads [.github/copilot-instructions.md](../../.github/copilot-instructions.md) automatically), working with the operator (Sebastian). **Nothing here has been run on the arm.** Capture and hand-sweep detail is in [VISIT_A_AGENT_CHECKLIST.md](VISIT_A_AGENT_CHECKLIST.md); where [AGENT_RUNBOOK.md](AGENT_RUNBOOK.md) says the human runs every motion command, this file's rules replace that for this run.

## The idea

Do not send our own USB bytes. Replay what SCORBASE's manual procedure does, by calling Intelitek's own `USBC.dll`, so the controller gets exactly the packets it expects:

1. `tools/usbc_home` runs the vendor's Home (the same Home SCORBASE runs).
2. `tools/usbc_jog` runs the vendor's manual jog (`EnterManual`, `MoveManual` every 300 ms, `CloseManual`) on one axis for a bounded time. Spec: [vendor-dll-jog plan](../specs/2026-10-13-vendor-dll-jog-plan.md).

Both need the 32-bit Python (the DLL is 32-bit). `<bin>` below is the SCORBASE `BIN` folder copy that holds `USBC.dll`, `USBC.INI`, `ER4CONF.INI`, `Par\`, with the 32-bit embeddable Python copied next to the DLL (see Visit A checklist step 1).

## Agent rules for this run

- **The agent may run every command here, including `--online`, once the operator has turned on the hardware switch (step 0).** Changed 2026-10-19 by the owner. The agent never sets, changes or unsets `SCORBOT_HARDWARE` itself and never types a confirmation word (`CONNECT`, `JOG`, `HOME`): each `--online` command waits for the operator to type it in the terminal. That typed word is the operator's go for that one move. Before each one the agent says what will move and asks for a hand on the physical stop.
- If a command says "the real arm is switched off", the switch is not on in this terminal: stop and ask the operator to do step 0.
- One person's hand stays on the physical stop for every `--online` run. `Stop` and F9 are not emergency stops.
- Close SCORBASE completely first: only one program may talk to the controller.
- Go one step at a time. Do not move to the next step until the operator pastes the output and the agent has read it. If an exit code is not 0, an error number appears, or anything moves unexpectedly: stop, keep the log, tell the operator.
- First motion is velocity 1 for 0.5 s on one axis. Read the logged position delta before raising the velocity or trying another axis. Never go above velocity 10 or 1 s in this visit without the operator asking.
- Axis numbers (0 base, 1 shoulder, 2 elbow) come from the vendor demo and have never been confirmed on the arm. Step 5 finds out which joint moves.
- Do not commit Intelitek files (`USBC.dll`, inis, manuals) or captures. Logs stay in `logs\`.

## Commands (PowerShell, from the repo root)

Set these once. Replace the two values.

```powershell
$bin = "C:\scratch\bin"
$op  = "SR"
$py  = "$bin\python.exe"
```

### Step 0. Turn on the hardware switch (operator only)

Real arm commands refuse to run unless `SCORBOT_HARDWARE` is `1` in the terminal that runs them. Copilot runs commands in VS Code's own terminals, so the switch has to be on when VS Code starts. The operator closes every VS Code window, then in a normal PowerShell window:

```powershell
cd <repo>
$env:SCORBOT_HARDWARE = "1"
code .
```

Every terminal in that VS Code window, including Copilot's, now has the switch on. Check in the VS Code terminal: `echo $env:SCORBOT_HARDWARE` prints `1`. **To turn it off, close VS Code.** It does not stay on after that (it is not saved anywhere). Run step 1 before or after; the tests clear the switch for themselves.

### Step 1. Desk check (no arm, no power)

```powershell
.venv\Scripts\python.exe -m pytest -n 2 tests tests_extra -q
```

Expect all passed (1339 passed, 1 skipped on 2026-10-19).

### Step 2. Probe and simulate (no arm needed, controller can be off)

```powershell
& $py tools\usbc_home\usbc_home.py --dll-dir $bin --probe
& $py tools\usbc_home\usbc_home.py --dll-dir $bin --simulate --output rehearsal\usbc_sim_01.jsonl
& $py tools\usbc_jog\usbc_jog.py --dll-dir $bin --probe
& $py tools\usbc_jog\usbc_jog.py --dll-dir $bin --simulate --axis 0 --output rehearsal\jog_sim_01.jsonl
```

Agent: the probe prints the DLL SHA-256. If it is not in `KNOWN_DLL_SHA256` (`tools\usbc_home\usbc_home.py`), tell the operator it is a third build; `--allow-unknown-dll` is then a deliberate choice, not a default.

### Step 3. Hand sweep gate (SCORBASE, Control Off)

Do Visit A checklist step 3. The wrist roll switch must stay on for about 800 counts or more. If narrower, do not run Home; tell the operator.

### Step 4. Connect only, no motion (arm powered, hand on the stop, SCORBASE closed)

```powershell
& $py tools\usbc_home\usbc_home.py --dll-dir $bin --online --no-home --operator $op --acknowledge-vendor-home --output logs\usbc_nohome_01.jsonl
```

Operator types `CONNECT`. This energises the motors and then turns control off. Pass: exit code 0, clean shutdown.

### Step 5. First jog, base, smallest possible

```powershell
& $py tools\usbc_jog\usbc_jog.py --dll-dir $bin --online --acknowledge-vendor-jog --operator $op --axis 0 --velocity 1 --seconds 0.5 --output logs\jog_axis0_v1_01.jsonl
```

Operator types `JOG`. Agent reads the logged before, after and difference of the three position arrays and tells the operator: which joint moved, which direction, how many counts. Write it down. If nothing moved, raise velocity to 2, then 5, one run each, but only after a run that exited 0 and logged a `position_delta` (a failed position read exits 4 and logs no delta). If the wrong joint moved, the axis table is wrong: stop and tell the owner.

Then repeat for the other two axes, each starting at velocity 1:

```powershell
& $py tools\usbc_jog\usbc_jog.py --dll-dir $bin --online --acknowledge-vendor-jog --operator $op --axis 1 --velocity 1 --seconds 0.5 --output logs\jog_axis1_v1_01.jsonl
& $py tools\usbc_jog\usbc_jog.py --dll-dir $bin --online --acknowledge-vendor-jog --operator $op --axis 2 --velocity 1 --seconds 0.5 --output logs\jog_axis2_v1_01.jsonl
```

A shoulder jog also swings the elbow; that is expected. The reverse direction is the same command with a negative velocity (`--velocity -1`), to put the joint back.

### Step 6. Vendor Home (only if step 3 passed, step 5 behaved, the arm is within a few tens of degrees of home, jaws empty, wrist path clear)

```powershell
& $py tools\usbc_home\usbc_home.py --dll-dir $bin --online --operator $op --acknowledge-vendor-home --output logs\usbc_home_01.jsonl
```

Operator types `HOME`, and presses `S` only if something looks wrong. Pass: callbacks 1 to 5 then `0x40`, exit code 0. Note the time it took and whether the gripper and wrist pitch moved.

### Step 7. Jog after Home

Repeat step 5 commands with new log names (`..._02.jsonl`), then a small bounded move you choose, for example:

```powershell
& $py tools\usbc_jog\usbc_jog.py --dll-dir $bin --online --acknowledge-vendor-jog --operator $op --axis 0 --velocity 5 --seconds 1 --output logs\jog_axis0_v5_01.jsonl
```

### Step 8. Wrap up

```powershell
Copy-Item logs <stick>\logs_backup -Recurse
```

Then the operator closes VS Code: that turns the hardware switch off.

Agent: write one notes block per step in `logs\NOTES.md` (step, log file, what the operator saw, the numbers, anything unexpected), no interpretation beyond what the file shows. Then record the axis table result in `docs/specs/2026-10-13-vendor-dll-jog-plan.md` and `docs/protocol/BRING_UP_SEQUENCE.md`.

## If it goes wrong

| Symptom | Do |
|---|---|
| Error 903 | Control is off. Re-run; the tool turns it on. |
| Error 911 | A move is still running. Wait, then re-run. |
| Error 561 or a home timeout | Probably the wrist roll switch is too narrow. Stop; tell the owner. |
| Another program has the controller | Close SCORBASE and anything else, power-cycle the controller, retry. |
| Two failed fixes | Stop; ask the owner for a Codex diagnosis (`codex:codex-rescue`). |

## Not covered

Wrist and gripper jogs, several axes at once, XYZ manual mode, and any of our own `scorbot/` motion code. Those stay off until the vendor path is proven on the arm.
