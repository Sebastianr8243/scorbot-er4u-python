# Run the arm

Purpose: set up a lab PC, start the arm server, and move the ER-4U through Intelitek's own `USBC.dll` (the same calls SCORBASE makes).

**WARNING:** someone keeps a hand near the red physical stop whenever the arm can move. The red stop is the only emergency stop. `Stop` in the server and `Arm.stop()` are not emergency stops.

## Before you start

- A lab PC with the arm connected over USB, and the folder holding `USBC.dll`, `USBC.INI`, `ER4CONF.INI` and `Par\`.
- SCORBASE closed. Only one program can talk to the controller.
- Teach pendant on Auto (or the bypass plug in).
- PowerShell, started in the repository root. Paste **one line at a time**: pasting several lines at once can crash PSReadLine.

Order of the day, with expected numbers and stop conditions: [LAB_RUN_SHEET.md](LAB_RUN_SHEET.md).

## 1. Set up the PC (once)

1. Update the repository: `git pull`.
2. Find the folder with the vendor files. If it is only in the SCORBASE install, copy that whole `BIN` folder to `C:\scratch\bin` (never into the repository):
   ```powershell
   Get-ChildItem "C:\Program Files (x86)", "C:\Program Files" -Filter USBC.dll -Recurse -ErrorAction SilentlyContinue
   ```
3. The DLL is 32-bit, so it needs a 32-bit Python next to it. If `C:\scratch\bin\python.exe` is missing:
   ```powershell
   Invoke-WebRequest https://www.python.org/ftp/python/3.13.5/python-3.13.5-embed-win32.zip -OutFile $env:TEMP\py32.zip
   Expand-Archive $env:TEMP\py32.zip -DestinationPath C:\scratch\bin -Force
   ```
4. Create the 64-bit environment `.venv` with exact versions from `uv.lock`: `uv sync --locked --extra dev`. Without `uv`, run `powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1` (it installs without pinned versions). Then set the variables (in every new terminal):
   ```powershell
   $bin = "C:\scratch\bin"
   $py = "$bin\python.exe"
   $vpy = ".\.venv\Scripts\python.exe"
   & $py -c "import struct; print(struct.calcsize('P') * 8)"
   ```
   The last command must print `32`.
5. Check the PC (read-only; it needs the `.venv` from step 4): `& $vpy scripts\lab_check.py --bin $bin`. Fix every FAIL line before you go on.

Result: `lab_check.py` prints no FAIL line.

## 2. Check the DLL and the connection (vendor tools)

Optional checks before the server. They use the DLL directly, without the Python API, and are not the first-move procedure (that is section 3). The vendor Home here is the same Home the server runs when it starts. Logs go to `logs\<tool>-<time>.jsonl` by themselves (`rehearsal\` when simulated); add `--output <file>` to name one.

```powershell
# the DLL loads (no arm)
& $py tools\usbc_home\usbc_home.py --dll-dir $bin --probe

# connect, motors on then off, no motion
& $py tools\usbc_home\usbc_home.py --dll-dir $bin --online --no-home

# jog one axis: 0 base, 1 shoulder, 2 elbow (numbers not yet confirmed on the arm)
# velocity 1 to 100 (percent of top speed, sign = direction), seconds up to 5
& $py tools\usbc_jog\usbc_jog.py --dll-dir $bin --online --axis 0 --velocity 5 --seconds 1

# vendor Home (closes the gripper and moves the wrist too)
& $py tools\usbc_home\usbc_home.py --dll-dir $bin --online

# small relative move in x, y or z, up to 20 mm (needs a Home first)
& $py tools\usbc_move\usbc_move.py --dll-dir $bin --online --axis x --mm 5
```

Press `s` during a run to stop it early. After a jog, the log's `position_delta` says which joint moved and by how many counts; write the axis mapping in `logs\NOTES.md`.

`--simulate` runs the DLL's own simulation (no arm). It needs the axis ini files (`ER4Ax*.ini`, `ROB_4u.ini`) beside the DLL or it refuses (`init_refused`), and its Home always ends in error 561 (no switches). Use it to check the connection and relative moves; `tools/usbc_fake` rehearses a full session.

## 3. Start the server and use the Python API

The server keeps one DLL session open (it runs the vendor Home once, when it starts) and takes commands from scripts. You need two terminals, both started in the repository root: the server and the scripts both read `calibration\arm.json` relative to where they started.

**Terminal 1** (32-bit; leave it running; `s` stops a move; `quit` from a script ends the session):
```powershell
& $py tools\usbc_server\usbc_server.py --dll-dir $bin --online
```
Wait for the `serving` line. Home takes about 135 s on the arm.

**Terminal 2** (the repository's 64-bit Python, the `.venv` from section 1):
```powershell
& $vpy -m scorbot.bridge where            # position: x y z mm, pitch, roll, encoder counts
& $vpy -m scorbot.bridge speed 20         # percent; start slow
& $vpy -m scorbot.bridge by 10 0 0        # relative move in mm (x y z), at most 50 per axis
& $vpy -m scorbot.bridge close            # gripper (open or close)
& $vpy -m scorbot.bridge home             # vendor Home again (about 135 s; clear the arm first)
& $vpy -m scorbot.bridge teach above_a    # save this spot by name
& $vpy -m scorbot.bridge go above_a       # move back to it (needs the box, below)
```

The **first moves on the real arm** follow the checklist in [LAB_RUN_SHEET.md](LAB_RUN_SHEET.md), steps 1 to 4. Do not improvise a different first move.

In a script:

```python
from scorbot.bridge import Arm

with Arm() as arm:
    arm.move_by(dz=20)
```

A script that ends leaves the server homed for the next one. Every call, unit and error: [../API.md](../API.md).

## 4. Keyboard control, measurement and pick-and-place

Run these after the first moves on the checklist have passed.

```powershell
& $vpy examples\teleop.py --step-mm 5          # asks for `go`; d/a, w/s, r/f move x, y, z; o/c the gripper; q ends
& $vpy examples\teleop.py --step-mm 5 --wrist-step 200   # adds t/g (pitch) and y/h (roll)
& $vpy -m scorbot.bridge wrist 200 0           # one wrist step
& $vpy examples\arm_latency.py --speed 20      # commands per second; moves up and down along z; type go
& $vpy examples\arm_characterise.py            # frame, scale, repeatability, grid (mat under the arm)
& $vpy examples\pick_place.py --runs 10        # needs above_a, at_a, above_b, at_b taught
```

The wrist unit is probably 0.001 degree (unverified), so 200 units is about 0.2 degree: watch the first press. Run `arm_latency.py` before `arm_characterise.py`; the second writes a box only about 20 mm tall in z, which then refuses the latency steps.

### The working box

The box is the region of the workspace the arm has been driven in under supervision. `logs\` and `calibration\` are created the first time a tool needs them. `move_to` and `go` are refused until `calibration\arm.json` holds a box. `arm_characterise.py` writes it from the points it reached, and `teach` grows it to every taught spot. Ctrl+C in terminal 2 stops a move that is running (the server calls Stop when its client goes away).

### Read the log afterwards

`& $vpy scripts\summarize_log.py logs\server-<time>.jsonl` prints one row per command: what was asked, ok, refused with the reason, or a DLL error. It reports what was asked and answered, not whether the arm moved. `teleop.py` also prints a status line after every key.

## 5. Several arms (unverified: only one arm has been run)

One PC per arm. Each PC has its own USB cable, its own `usbc_server` and its own calibration folder, so nothing is shared between arms.

1. On each PC, `git pull` and do section 1.
2. Pick the arm's name, for example `arm3`. The port is the server's `--port` (default 8765; separate PCs can all use the default. Two arms on one PC need 8765, 8766, and so on).
3. Start the server with that arm's box: `& $py tools\usbc_server\usbc_server.py --dll-dir $bin --online --port 8765 --box calibration\arm3\arm.json`.
4. Give the scripts the same folder: `--calibration-dir calibration\arm3` before the command on `python -m scorbot.bridge` (for example `& $vpy -m scorbot.bridge --calibration-dir calibration\arm3 teach above_a`) and on `examples\arm_characterise.py`. Server and scripts must use the same box file, or their checks differ. Never copy one arm's `arm.json` to another: the box is measured per arm.
5. In the LeRobot adapter ([plugins/lerobot_robot_scorbot_stack1](../../plugins/lerobot_robot_scorbot_stack1/README.md)) set `arm_id="arm3"`, `port`, `calibration_dir="calibration/arm3"` and `allow_real=True`.

## 6. If it goes wrong

| Symptom | Fix |
|---|---|
| `... is not recognized` on a variable line | It was typed without `$` or pasted with other lines. Redo section 1, step 4, one line at a time |
| `[WinError 193]`, or the DLL will not load | 64-bit Python. Use the 32-bit one (section 1, step 3) |
| Error 903 | Control is off. Re-run; the tool turns it on |
| Error 911 | A move is still running. Wait, then re-run |
| Error 561 or a home timeout | Probably the wrist roll switch. Check the wrist, then retry |
| Another program has the controller | Close SCORBASE, power-cycle the controller, retry |
| The DLL cannot find the controller after Zadig was used | Zadig swapped the Intelitek `ERUSB` driver for WinUSB. In Device Manager, uninstall the device with "remove the driver" and replug. If needed, Update driver, Have Disk, `ERUSB.inf` from the Intelitek driver folder. Do not run Zadig again |
| `no arm server on 127.0.0.1:8765` | Start terminal 1 first and wait for its `serving` line |
| `move_to needs a Home` or `no box file` | Home in this session (`& $vpy -m scorbot.bridge home`), then run `arm_characterise.py` |
| Another client gets a `busy` line | The server serves one client at a time. Quit the first client, then retry |

Never commit `USBC.dll`, the ini files, manuals or captures: the repository is public.
