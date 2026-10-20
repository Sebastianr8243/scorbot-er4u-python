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

`--simulate` instead of `--online` runs the DLL's own simulation (no arm). Press `s` during a run to stop early. The red physical stop is the only emergency stop.

After a jog, the log's `position_delta` says which joint moved and by how many counts. Write the axis mapping down in `logs\NOTES.md`.

## If it goes wrong

| Symptom | Fix |
|---|---|
| `... is not recognized` on a variable line | It was typed without `$` or pasted with other lines. Redo setup step 4, one line at a time |
| `[WinError 193]`, or the DLL will not load | 64-bit Python. Use the 32-bit one (setup step 3) |
| Error 903 | Control is off. Re-run; the tool turns it on |
| Error 911 | A move is still running. Wait, then re-run |
| Error 561 or a home timeout | Probably the wrist roll switch. Check the wrist, then retry |
| Another program has the controller | Close SCORBASE, power-cycle the controller, retry |

Never commit `USBC.dll`, the ini files, manuals or captures: the repo is public.
