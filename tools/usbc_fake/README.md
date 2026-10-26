# Running the vendor DLL against a fake controller

Intelitek's `USBC.dll` talks to its controller through the file named in `USBC.INI` (`DriverLink`). Point that at a Windows named pipe and a Python fake can stand in for the arm. This is how the vendor home was recorded (327 commands) and how its needs were measured. **The DLL never touches USB.** The arm model is a guess, so what this proves is what the vendor's code does, not what the controller accepts.

## Guards

- `pipe_server.py` and `fake_home.py` refuse to run if a ScorBot USB device (`VID_09F1`) is present, or if that cannot be checked.
- `fake_home.py` refuses to load the DLL unless the `USBC.INI` beside it has `DriverLink = \\.\pipe\scorbot_fake` and nothing else.
- Never commit the DLL, its ini files or its logs.

## Steps

1. Copy a SCORBASE `BIN` tree (it holds `USBC.dll`, `Par\`, `ER4CONF.INI`, `Scbs.ini`) somewhere outside the repo, plus a 32-bit Python (the embeddable zip works) into the same folder.
2. Replace its `USBC.INI` with:

   ```ini
   [General]
   ConfigFile  =ER4CONF.INI
   Monitoring  = 100
   DriverLink  = \\.\pipe\scorbot_fake
   [Log]
   File = USBC.LOG
   Level = 1
   ```

3. Start the fake, then the DLL (two shells, or the first in the background):

   ```
   .venv\Scripts\python.exe tools\usbc_fake\pipe_server.py OUT.jsonl --seconds 400 --roll-width 800
   <the tree>\python.exe tools\usbc_fake\fake_home.py <the tree> DLL.jsonl 300
   ```

Wait for the pipe server's `listening` line before starting the DLL (its arm-presence check can take 10 seconds; a DLL that starts earlier never connects). `fake_oracle.py DLL_DIR OUT.json` replaces the second command to run 44 small moves after Home and log the DLL's encoder, joint and xyz arrays (results in `docs/DESK_FINDINGS.md`).

`--band-width` sets every switch's width in counts (default 2000), `--roll-width` the wrist roll's. A run with a good switch takes about 10 seconds. A run that misses a switch ends 80 seconds later with the vendor's own error 561.

## Results (2026-10-12, 2008 build, one fake arm model)

Desk results are on the `stack2` branch (`docs/archive/specs/2026-10-08-vendor-home-port.md`).
