# Run the arm

Drives the arm through Intelitek's own `USBC.dll` (the same calls SCORBASE makes). PowerShell, from the repo root, one line at a time (pasting several lines at once crashed PSReadLine on the lab PC).

## Setup (once per PC)

1. `git pull`
2. Find the folder with `USBC.dll`, `USBC.INI`, `ER4CONF.INI` and `Par\`. If it is only in the SCORBASE install, copy that whole `BIN` folder to `C:\scratch\bin` (never into the repo):
   ```powershell
   Get-ChildItem "C:\Program Files (x86)", "C:\Program Files" -Filter USBC.dll -Recurse -ErrorAction SilentlyContinue
   ```
3. The DLL is 32-bit, so it needs a 32-bit Python next to it. If `C:\scratch\bin\python.exe` is missing:
   ```powershell
   Invoke-WebRequest https://www.python.org/ftp/python/3.13.5/python-3.13.5-embed-win32.zip -OutFile $env:TEMP\py32.zip
   Expand-Archive $env:TEMP\py32.zip -DestinationPath C:\scratch\bin -Force
   ```
4. Variables (every terminal):
   ```powershell
   $bin = "C:\scratch\bin"
   $py = "$bin\python.exe"
   & $py -c "import struct; print(struct.calcsize('P') * 8)"
   ```
   The last line must print `32`.

Close SCORBASE first: only one program can talk to the controller. Teach pendant on Auto (or the bypass plug in).

## Commands

Logs go to `logs\<tool>-<time>.jsonl` by itself (`rehearsal\` when simulated). Add `--output <file>` to name one.

```powershell
# check the DLL loads (no arm)
& $py tools\usbc_home\usbc_home.py --dll-dir $bin --probe

# connect, motors on then off, no motion
& $py tools\usbc_home\usbc_home.py --dll-dir $bin --online --no-home

# jog one axis: 0 base, 1 shoulder, 2 elbow (numbers not yet confirmed on the arm)
# velocity 1 to 100 (percent of top speed, sign = direction), seconds up to 5
& $py tools\usbc_jog\usbc_jog.py --dll-dir $bin --online --axis 0 --velocity 5 --seconds 1

# vendor Home (closes the gripper and moves the wrist too)
& $py tools\usbc_home\usbc_home.py --dll-dir $bin --online

# small relative point move in x, y or z, up to 20 mm (needs a Home first)
& $py tools\usbc_move\usbc_move.py --dll-dir $bin --online --axis x --mm 5
```

`--simulate` runs the DLL's own simulation (no arm). It needs the axis ini files (`ER4Ax*.ini`, `ROB_4u.ini`) beside the DLL or it refuses (`init_refused`), and its Home always ends in error 561 (no switches), so use it to check the connection and relative moves; `tools/usbc_fake` rehearses a full session. Press `s` during a run to stop early. The red physical stop is the only emergency stop.

After a jog, the log's `position_delta` says which joint moved and by how many counts. Write the axis mapping down in `logs\NOTES.md`.

## Python API (arm server)

The server keeps one DLL session open (it homes once) and takes commands from Python scripts. Two terminals.

Terminal 1, 32-bit, leave it running (`s` stops a move; `quit` from a script ends the session):
```powershell
& $py tools\usbc_server\usbc_server.py --dll-dir $bin --online
```

Terminal 2, the repo's normal 64-bit Python (the `.venv` that `uv sync` made). Start both terminals in the repo root: the server and the scripts both use `calibration\arm.json`, relative to where they started.
```powershell
$vpy = ".\.venv\Scripts\python.exe"
& $vpy -m scorbot.bridge where            # position: x y z mm, pitch, roll, encoder counts
& $vpy -m scorbot.bridge speed 20         # percent, start slow
& $vpy -m scorbot.bridge by 10 0 0        # relative move in mm (x y z), at most 50 per axis
& $vpy -m scorbot.bridge close            # gripper (open / close)
& $vpy -m scorbot.bridge teach above_a    # save this spot by name
& $vpy -m scorbot.bridge go above_a       # move back to it (needs a Home in this session)
& $vpy examples\arm_characterise.py       # measure frame, scale, repeatability, grid (mat under the arm)
& $vpy examples\pick_place.py --runs 10   # needs above_a, at_a, above_b, at_b taught
```

Ctrl+C in terminal 2 stops a move that is running (the server calls Stop when its client goes away).

In a script: `from scorbot.bridge import Arm`, then `with Arm() as arm: arm.move_by(dz=20)`. Absolute moves (`to`, `go`) need a Home in this session and nothing else from us; the DLL's own workspace is the only limit. `teach` and `arm_characterise.py` still record the points they reach in `calibrationrm.json`, as a record only. A script that ends leaves the server homed for the next one.

### First run of the Python API (no mat)

Hand on the red stop the whole time. One line at a time; if a line fails, stop and keep `logs\server-*.jsonl`. Terminal 1 is the server line above; wait for `serving`. Terminal 2 from the repo root.

| # | Command | Pass looks like |
|---|---|---|
| 1 | `& $vpy -m scorbot.bridge where` | x y z mm printed; note them |
| 2 | `& $vpy -m scorbot.bridge speed 20` | `ok` |
| 3 | `& $vpy -m scorbot.bridge by 0 0 5` | arm moves about 5 mm; write down which way (up or down) |
| 4 | `& $vpy -m scorbot.bridge by 0 0 -5` | back where it was; `where` agrees within about 0.1 mm |
| 5 | `& $vpy -m scorbot.bridge by 5 0 0`, then `by -5 0 0` | write down forward or back; repeat for y |
| 6 | `& $vpy -m scorbot.bridge open`, then `close` | jaws open, then close |
| 7 | `& $vpy -m scorbot.bridge by 0 0 20`, Ctrl+C during the move | arm stops; the server says it called Stop |
| 8 | `& $vpy -m scorbot.bridge teach above_a` | saves the spot to `calibration\positions.json` |
| 9 | `by 10 0 0`, then `go above_a` | returns to the taught spot (a small move_to, the first one on the arm) |

The 5 mm steps are only so the first look at each direction is easy to read. A refused move prints the reason; a code like `(code 7)` now says what it means. With a ruler or phone camera in step 3 and 5, note how far the tip really moved for each 5 mm: that is a first look at the scale until the mat is ready.

## If it goes wrong

| Symptom | Fix |
|---|---|
| `... is not recognized` on a variable line | It was typed without `$` or pasted with other lines. Redo setup step 4, one line at a time |
| `[WinError 193]`, or the DLL will not load | 64-bit Python. Use the 32-bit one (setup step 3) |
| Error 903 | Control is off. Re-run; the tool turns it on |
| Error 911 | A move is still running. Wait, then re-run |
| Error 561 or a home timeout | Probably the wrist roll switch. Check the wrist, then retry |
| Another program has the controller | Close SCORBASE, power-cycle the controller, retry |
| The DLL cannot find the controller after Zadig was used | Zadig swapped the Intelitek `ERUSB` driver for WinUSB. Device Manager: uninstall the device with "remove the driver", replug; if needed, Update driver → Have Disk → `ERUSB.inf` from the Intelitek driver folder. Do not run Zadig again |
| `no arm server on 127.0.0.1:8765` | Start terminal 1 first and wait for its `serving` line |
| `move_to needs a Home` | Home in this session (`python -m scorbot.bridge home`) |

Never commit `USBC.dll`, the ini files, manuals or captures: the repo is public.
