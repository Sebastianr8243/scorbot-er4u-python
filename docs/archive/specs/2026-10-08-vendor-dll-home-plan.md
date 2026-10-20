ye# Home the arm through the vendor's `USBC.dll`: plan

Date: 2026-10-08. Status: owner approved the rule change; nothing below has been built or run. Nothing here is hardware-validated.

## Decision (owner, 2026-10-08)

Reopen the 2026-10-04 rule "read the DLL, never run it" for **one narrow case**: a lab-only tool that loads Intelitek's `USBC.dll` and calls its `Home`. This is how the MATLAB ScorBot Toolbox (Kutzer, US Naval Academy) homes the arm: it calls the DLL, it does not send its own bytes. The owner does not want to use `SCORBASE.exe`; the DLL is the same code SCORBASE calls.

Why: every route to a first home from our own code needs bytes we have never seen on the wire (`4D` drive, `48` set-zero). The DLL already sends them. Running its `Home` once, logged, gets the arm homed and gives us the evidence to write the home ourselves.

## What we already know (V = read from the DLL or its docs, I = inference)

| Fact | Source |
|---|---|
| Both builds we read (2018 Kutzer, 2008 MTIS) are 32-bit x86. A 64-bit Python cannot load them | V, `peread.py`, machine `0x14c` |
| The exports are plain C++ `__cdecl`, mangled. Names: `?Initialization@@YAHFFP6AXPAX@Z1@Z` (short mode, short systemType, cb, cb), `?Control@@YAHEH@Z` (uchar group, int on), `?Home@@YAHEP6AXPAX@Z@Z` (uchar group, cb), `?Stop@@YAHE@Z`, `?IsEmergency@@YAHXZ`, `?CloseUSBC@@YAXXZ`, `?SetHome@@YAHE@Z`. **`IsOnlineOk` is not exported by either build** (Mosebo's list is from an older header). Callbacks are `void(*)(void*)`, cdecl. In ctypes: `CDLL` and `CFUNCTYPE(None, c_void_p)`, never `WinDLL` | V, export table of the 2018 build |
| `Initialization(sMode, ...)`: 0 = last mode from the ini, **1 = force online**, 2 = simulation. `systemType` 41 = ER-4 with USB. Success is signalled only by the `fnInitEnd` callback, which may never come: time out at 5 s | V, Mosebo p. 10 |
| The shipped `ER4CONF.INI` says `Simulation = Yes`. Mode 1 must be passed or nothing touches the arm | V, ini on disk |
| `Home(group, cb)`: `'A'` robot, `'G'` gripper, `'&'` all (the "normal" one), `'0'-'7'` one axis. The callback receives `0xFF` start, `1-8` axis being homed, `0x40` end, and "does not indicate success or failure". The return value is the success flag | V, Mosebo p. 61 |
| The home itself: per axis a controller-side `4D` drive until the switch, a back-off offset, then `48` set position 0. The "homed" mask is kept in the DLL's memory, not in the controller | V, `VENDOR_HOMING_TRACE.md`, `VENDOR_MANUAL_MOVE_TRACE.md` |
| The DLL builds the paths of `USBC.INI` and `ER4CONF.INI` from `GetModuleFileNameA(NULL)`, the path of the **host program**, so the ini files go next to `python.exe`, not only next to the DLL | I, decompile of the 2018 build (Mosebo says "with the dll") |
| The vendor calls take a lock-step conversation with a controller 20 to 45 messages deep (`HomingBuffers` 20) | V |

## The two things nobody knows yet

1. **Does the home survive the DLL?** `48` zeroes counters in the controller (I). Our pyusb connect sends `5A` Reset first. If Reset clears the counters, a DLL home buys our code nothing except the trace. If not, the arm stays zeroed while the DLL exits, and our code can read counts that start from the vendor zero. **Phase 4 answers this.**
2. **Is the lab's `USBC.dll` one of the two builds we read?** Fingerprint it first (size, date, SHA-256). A third build may differ.

## Design: `tools/usbc_home/`

A standalone script. It is not part of `scorbot/` and `Scorbot` never imports it, so the SDK keeps one path to the arm.

```
python32 tools/usbc_home/usbc_home.py --dll-dir <folder> --probe
python32 tools/usbc_home/usbc_home.py --dll-dir <folder> --simulate
python32 tools/usbc_home/usbc_home.py --dll-dir <folder> --online --output rehearsal\...jsonl ...
```

| Piece | Rule |
|---|---|
| Standard library only (`ctypes`, `json`, `hashlib`, `threading`). No new dependency | Owner's dependency rule |
| **Allowlist.** The tool binds exactly: `Initialization`, `IsEmergency`, `Control`, `Home`, `Stop`, `CloseUSBC`. Anything else (`MoveManual`, `SetJoint`, `MoveTorque`, `MoveLinear`, `SetHome`, ...) is not reachable from the code | This is the narrowness of the rule change |
| `--probe`: load the DLL, hash it, resolve the allowlisted exports, print the result. Never calls `Initialization` | Safe at the desk and on the lab PC. **Run at the desk: passes** |
| `--simulate`: `Initialization(2, 41, ...)`. No USB. `Home` in simulation is not a real home. It proves the callbacks, the timeouts, the logging | Desk rehearsal |
| `--online`: `Initialization(1, 41, ...)`. Needs `--acknowledge-vendor-home` and a typed `HOME`. **The online `Initialization` itself runs the vendor handshake, which energises the motors** (same as ours). So the arm is supervised from this call on, not only from `Home` | Lab only |
| Sequence: hash DLL and inis, `Initialization` (a return of 0 is refused at once: every failure path of the DLL returns 0), wait for `fnInitEnd` (5 s), `IsEmergency` must be false, `Control('A', 1)`, prompt, `Home('A', cb)` on a worker thread, `S` key calls `Stop('A')`, then `Control('A', 0)`, `CloseUSBC` | Sequence matches the vendor's own, nothing extra |
| Log: append-only JSONL, one line per event with a monotonic time: DLL SHA-256, ini hashes, every callback value, every return value, key presses, `source` = `vendor_dll` or `vendor_dll_simulated`. A simulated line can never pass for a real one | Matches "real and simulated never share a session" |
| Never run by tests, CI or an agent without `--simulate` or `--probe` | Same rule as the lab scripts. `Stop` is not an emergency stop; the physical stop is |
| Tests: `tests_extra/test_usbc_home.py` with a **fake library object** injected in place of the DLL: allowlist holds, a missing callback times out, an emergency stops the sequence, the log never mixes sources. The tool is off the first-trial path, so it is not in the gate | Matches the repo's test split |

## Environment

| Item | Choice |
|---|---|
| 32-bit Python | Python's **embeddable zip, win32** (python.org, about 10 MB), unzipped to `C:\Users\sebas\tools\py32`. No installer, no admin, nothing on PATH, `ctypes` included. A second copy goes on the lab PC |
| DLL and ini files | Do not copy Intelitek's files into the repo. On the lab PC point `--dll-dir` at the SCORBASE install folder so the **lab's own** `USBC.dll`, `USBC.INI`, `ER4CONF.INI` and `PAR\` are used. If the DLL cannot find its ini, copy `python.exe` into that folder (the ini lookup follows the host program, I) |
| Driver | The DLL talks to the controller through Intelitek's driver (`DriverLink` in `USBC.INI`, driver `ERUSBClass`). Our pyusb path needs WinUSB. **Only one can be bound at a time.** The lab PC has been switched to WinUSB with Zadig; restoring Intelitek's driver is in `docs/archive/lab/S1_CAPTURE_LAB_CARD.md` (reinstall SCORBASE or roll the driver back in Device Manager). Phase 3 uses Intelitek's, Phase 4 uses WinUSB; note the Driver Provider each time |

## Desk results, 2026-10-08

Built: `tools/usbc_home/usbc_home.py` and `tests_extra/test_usbc_home.py` (16 tests with a fake library, ruff clean). A 32-bit Python 3.13.5 (embeddable zip) is at `C:\Users\sebas\tools\py32`.

| Check | Result |
|---|---|
| `--probe` with the real 2018 `USBC.dll` under 32-bit Python | **Passes.** Loads, SHA-256 matches `VENDOR_DLL_PROTOCOL.md`, all 6 allowlisted exports resolve |
| `--simulate` with the real DLL and my stub inis | Failed: `Initialization` returned 0, error 533 ("Invalid parameter"), in 6 ms. The tool logged it and exited 2 |
| `--simulate` with the real DLL and the **support tree from Kutzer's ScorBot Toolbox** (`ScorBotToolboxSupport/`: `USBC.INI`, `Scbs.ini`, `ER4CONF.INI`, `Par/`; copied next to `python.exe`) | **Passes the plumbing.** Init callback fired and returned true, emergency false, control on, `Home('A')` started and reported "homing started" (0xFF). With no controller the DLL's own homing timer ended it after 60 s with error 561 "Time of homing elapsed"; the tool decoded that, switched control off and closed. Exit 7 (home failed), as it should. No USB, no controller on this PC |

What the decompile and the failed runs showed (V from the 2018 build unless marked):

- **`Initialization` also reads and WRITES `SCBS.INI` and `USBC.INI`** (SCORBASE's own file and the DLL's): it writes `SystemType` and `ConfigFile`. On the lab PC that changes SCORBASE's ini files. Run from a copy of the install folder, or accept the write and record it.
- It returns 0 if `SCBS.INI` is missing, and (with it present) error 533 if the config load fails. The config set needs `ER4CONF.INI`, `ROB_4u.ini`, the `PAR\ER4u\$Default` axis inis, `NOC.INI` for the two unused axes and `USBC.INI` with `DriverLink`. Only the lab's own install has all of them. A stub `NOC.INI` did not fix it, so the cause is not found.
- In simulation mode the init callback is called **synchronously** inside `Initialization`, so no message pump is needed there. Online mode is not read yet (I: the callback comes from the DLL's communication thread).
- The error callback receives a struct: `u32 code`, text at offset 16. The tool logs both.
- Mode 3 also exists in the DLL; the tool does not use it.

Where the working files came from, and what they add (all V, read from the files):

- Kutzer's repo (`github.com/kutzer/ScorBotToolbox`) ships a complete working support tree. Its `USBC.INI` has `DriverLink = \\.\ERUSBDevice0` (the Intelitek driver's device name) and a `[Log]` section writing `USBC.LOG`. Its `ER4CONF.INI` is identical to the one we already had.
- **The MATLAB toolbox does not call `USBC.dll` directly.** It calls `RobotDll.dll` (`RInitialize`, `RIsInitDone`, `RControl`, `RHome`, `RIsHomeDone`, `RMoveJoint`, ...), a polling wrapper over `USBC.dll`; `RobotDll.dll` and `RobotDll.h` are already in `C:\Users\sebas\tools\usbc_analysis\bin`. A 64-bit MATLAB reaches the 32-bit DLL through a helper server (`Install_ScorbotServer.msi`). Same idea as our 32-bit Python.
- Intelitek's own download host has the vendor package: `https://downloads.intelitek.com/Software/Robotics/ER-4u/Previous_Versions/` (it needs a browser User-Agent; plain `curl` gets a 406). `Scorbase_Robocell_V5335.zip` (version 5.3.3.5, 2009-12-31) is saved at `C:\Users\sebas\tools\intelitek\` with the manuals and an InstallShield `Setup.exe`. Not unpacked, not installed, never committed. `ER4u_Parameters_4_ER3_Accessories.zip` holds only peripheral parameter files.

### The official package, unpacked (2026-10-08)

`Scorbase_Robocell_V5335.zip` (SCORBASE 5.3.3.5, built 2009-12-31) from Intelitek's own download host was unpacked without running anything: 7-Zip portable (from its MSI with `msiexec /a`, at `C:\Users\sebas\tools\7zip`), then a Python InstallShield extractor written from the `unshield` sources (`C:\Users\sebas\tools\intelitek\isc_extract.py`; 3497 of 3497 files match the MD5s stored in the cab). Everything is under `C:\Users\sebas\tools\intelitek\scorbase_files`, outside the repo, never committed.

| Finding | Evidence |
|---|---|
| **`BIN\USBC.dll` is byte-identical to the 2008 build we already decompiled** (SHA-256 `91e99a91...`). Our `VENDOR_*` analysis is of the DLL the vendor ships with SCORBASE, not only a GitHub copy | V, hash checked by hand |
| `--probe` and `--simulate` both pass against the official `BIN\` tree plus `Scbs.ini`; the run ends the same way (60 s DLL homing timer, error 561, clean shutdown) | V, run on this PC |
| `ERUSB.inf` binds `USB\VID_09F1&PID_0007` (our controller); driver `ERUSB.sys` v1.00.2000 (2009-05-17) with `erusb.cat` and the `USBDriver.exe` / `USBDriver_x64.exe` installers. We now hold what is needed to put Intelitek's driver back after a Zadig swap without reinstalling SCORBASE. Whether the 2009 driver installs on the lab's Windows is not tested | V for the INF, unknown for the install |
| `BIN\Scorbase.exe` (3.3 MB) is on disk, not decompiled. It would answer which mode and system type SCORBASE passes to `Initialization` | V, file present |
| `RobotDll.dll` is not in the package | V |
| The Intelitek manuals (`Books\`) are in the package: local only, never commit | V |

**RobotDll vs USBC.dll, decided: keep `USBC.dll`.** RobotDll wraps the same `Initialization(1, 0x29)`, `Control` and `Home` calls. It adds no capability, keeps one read-once error code, has no `Stop` and no emergency read, and its `RIsHomeDone` returns -1 on error (a boolean check reads that as done). The MATLAB `ScorHome` also sends an extra move to the current pose after homing. This is from a read-only agent's review of the decompile; the call chain has not been re-checked by hand.

### Review (2026-10-08)

Codex (`deep`) raised 6 findings on the first version; all accepted and fixed with tests: the typed word now comes before any online DLL call (the online connect and control-on energise the motors); control-off is attempted after any control-on attempt; a failed control-off warns and exits 8; `CloseUSBC` runs in a nested `finally`; a `Home` that outlives `Stop` skips control-off and close on purpose (process exit stops the keep-alive; physical stop); `--online` refuses a `USBC.dll` whose SHA-256 is not one of the two builds we read, unless `--allow-unknown-dll`. Added after that: `--online --no-home` (connect, emergency read, control on then off, no `Home`; asks for `CONNECT` instead of `HOME`). Second Codex pass (`deep`) on the fixed code raised 6 more; 5 accepted and fixed with tests: Ctrl+C or an error while supervising `Home` now requests `Stop` and, if `Home` is still running, skips cleanup (it used to fall into control-off and `CloseUSBC` under a running `Home`); `Stop` and `Initialization` run on bounded threads so a call that blocks inside the DLL cannot hide the warning; a failed control-off is reported before `CloseUSBC`, which could still raise; the `HOME` prompt now warns about the gripper and wrist. One rejected: "skipping cleanup relies on an unverified loss of control at process exit". That is disclosed on purpose (the keep-alive stopping drops control is supported by the 45 s runs and the DLL pacing, but not tested for this exact case) and the tool prints the physical-stop instruction. **Found by that review and confirmed against `VENDOR_HOMING_TRACE.md`: the vendor `Home('A')` also closes the gripper and homes both wrist motors, including a wrist-pitch move of about 37.6 degrees (1050 counts). Lab step 5 needs empty jaws and a clear wrist path.** Gemini (`agy`, found by the shared script since the PATH fix) then ran and, with Codex cross-checking, found the same class of bug in paths still open: `busy` must be set before any DLL call that can outlive its caller and cleared only once the call is proven finished (Ctrl+C during `Initialization`; an error inside the interrupt handler; a `Stop` still running inside the DLL, now counted from launch in `state["stop_open"]`). All accepted and fixed with tests (34 tests now). Earlier note on why Gemini failed once: `agy` is at `C:\Users\sebas\AppData\Local\agy\bin\agy.exe` (not on Git Bash's PATH), and its headless mode then refuses the review because it needs a `command` permission it cannot prompt for. Fixing that means an allow-rule in agy's settings or `--dangerously-skip-permissions`, which is the owner's call.

## Plan from here

| # | Where | Step | Done when |
|---|---|---|---|
| 1 | Desk, **done** | Tool, 24 fake-DLL tests, `--probe` and `--simulate` against two real config trees (Kutzer's and the official `BIN\`) | Done |
| 2 | Lab PC, arm unpowered | Copy the SCORBASE install folder to a scratch folder, put a 32-bit `python.exe` in it, run `--probe`. Compare the SHA-256 with `KNOWN_DLL_SHA256` | Known build, or recorded as a third build (then `--allow-unknown-dll` is a deliberate choice) |
| 3 | Lab PC, arm unpowered | `--simulate` on the lab's own tree | Ends with error 561 and a clean shutdown |
| 4 | Lab, arm powered, at its home pose by hand, hand on the stop | Intelitek driver bound. `--online --no-home`: type `CONNECT`; the motors energise; emergency read; control on then off | Motors on and off cleanly, LEDs as in `AGENT_RUNBOOK.md` |
| 5 | Lab, arm within a few tens of degrees of home, hand on the stop, **jaws empty and the wrist path clear** (the vendor Home closes the gripper and runs a ~37 degree wrist-pitch move) | `--online`: type `HOME`; the vendor `Home('A')`; press `S` to call `Stop` if anything looks wrong (not an emergency stop) | Progress 1 to 5 then `0x40`; the arm is at its home pose |
| 6 | Lab, straight after | Swap the driver to WinUSB; `examples/record_raw_state.py` (read-only) | Counts near the vendor home values = the home survived |
| 7 | Desk | Record the result in `BRING_UP_SEQUENCE.md` step 5. Home survived: design "adopt the vendor home" (`2026-10-06-adopt-home-design.md`). Did not survive: capture the bytes (USBPcap, or the DLL's `USBC.LOG` at `Level` above 1) and write the home in our own code | Decision recorded, log named |

Off the critical path: decompile `Scorbase.exe` for its `Initialization` mode; a future transport that opens `\\.\ERUSBDevice0` (the Intelitek driver's device name, from `USBC.INI`) with `CreateFile`, `WriteFile` and `ReadFile` on 64-byte packets, which would end the driver swap for our own code.

## Phases (the original table; where it differs, "Plan from here" above wins)

| Phase | Where | What | Pass |
|---|---|---|---|
| 0 | Desk | Fetch the embeddable 32-bit Python. Run `--probe` and `--simulate` against the 2018 build and the ini files in `C:\Users\sebas\tools\usbc_analysis`. Write the tool and its fake-DLL tests | Exports resolve; `--simulate` runs to the end callback; tests pass |
| 1 | Lab, no power on the arm | Fingerprint the lab's `USBC.dll`. `--probe` with the lab's files | Hash matches a read build, or we record it as a third build |
| 2 | Lab, arm at its home pose by hand, hand on the stop | Intelitek driver bound. `--online` to the point of `IsEmergency`, then `Control` on and off. No `Home` | LEDs as expected (`AGENT_RUNBOOK.md`); no fault; `IsEmergency` false |
| 3 | Lab, arm within a few tens of degrees of home, hand on the stop | The DLL's `Home('A')`. Watch for motion and switch clicks. `S` calls `Stop('A')` if anything looks wrong | Callbacks 1 to 5 then `0x40`; the arm reaches its home pose |
| 4 | Lab, directly after Phase 3, arm still powered, nothing touched | Close the tool. Switch the driver to WinUSB. Run `examples/record_raw_state.py` (read-only, existing) and compare the counts to the home counts in `VENDOR_HOMING_TRACE.md` | **Counts near the vendor home values = the home survived.** Near 0 or random = it did not |
| 5 | Desk | Write the result into `BRING_UP_SEQUENCE.md` step 5 and the lab plan. If the home survived: design "adopt the vendor home" on top of `docs/archive/specs/2026-10-06-adopt-home-design.md`. If not: the DLL log plus a USBPcap capture of Phase 3 gives the `4D` and `48` bytes to write the home in our own code | A decision recorded with the log named |

The USBPcap capture in Phase 3 is optional. Without it we still get the DLL's callbacks, return codes and the Phase 4 read-back. With it we also get the bytes.

## Risks

- **The DLL home moves the arm outside our gates and fault latch.** It is the vendor's routine, but it is the first time anyone has run it on this arm under our control. Hand on the physical stop; the arm path clear; `Stop('A')` is not an emergency stop (it needs the live link, and the controller queue may run 20 to 45 messages deep).
- **A wrong build of the DLL or inis.** Mitigated by Phase 1 hashing; use the lab's own files, not the GitHub ones.
- **Driver swaps can strand the controller.** Photograph the Driver Provider before and after each swap. Do not swap when preflight already passes.
- **`Initialization` may hang or never call back.** The 5 s timeout, then `CloseUSBC`. The DLL allows only one connection per process; close SCORBASE first.
- **The gripper.** Whether `'A'` homes the gripper is not known (Mosebo lists `'G'` separately). Phase 3 watches for gripper motion; `'&'` is the vendor's normal call and is left for a later run, only if the gripper must be homed.
- **Phase 4 may show the home does not survive.** That is a result, not a failure: it tells us to write the home ourselves with the captured bytes.

## Out of scope

Running any DLL motion call (`MoveManual`, `SetJoint`, `MoveTorque`, `MoveLinear`), streaming through the DLL, or replacing the pyusb path. Those need their own spec.
