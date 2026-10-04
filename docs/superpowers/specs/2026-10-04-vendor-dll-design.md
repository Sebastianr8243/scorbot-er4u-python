# Vendor DLL probe: design

Date: 2026-10-04. Status: draft, awaiting review. Nothing here is hardware-validated.

## Purpose

Intelitek's `USBC.dll` already knows how to do things our own USB code cannot yet do: stop a move, jog at a velocity, stream setpoints, report position error and emergency state. We use the DLL as a **teacher, not as a driver**. The arm keeps running on our pyusb path.

We learn from it in two steps:

1. **Read the DLL's code (no arm, no lab).** The DLL is not packed and builds its messages itself, so the message layouts can be worked out by disassembling it at a desk. This is the main source of protocol knowledge.
2. **Confirm at the lab with a few captures.** A small number of labelled USBPcap captures check that what the disassembly says is what appears on the wire. Captures confirm; they are no longer how we discover the protocol.

Success is: `docs/PROTOCOL.md` describes the vendor messages for stop, velocity jog, setpoint, control on/off and status, each marked "from disassembly, unverified" until a capture confirms it.

Not goals: a second way to control the arm, a replacement for pyusb, teleop through the DLL, kinematics.

## Decisions

| # | Question | Decision | Why |
|---|---|---|---|
| 1 | What is the DLL for | Reference only: disassemble it first, then confirm with a few labelled captures | The semester goal runs on our own driver; the DLL's value is what it reveals about the protocol, and most of that can be read without the arm |
| 2 | 32-bit in-process or helper process | One standalone tool run by a 32-bit Python, standard library only. It talks to the rest of the project through files | A reference tool needs no live link to the 64-bit SDK, so no helper/client pair and no IPC to build or debug |
| 3 | `USBC.dll` or the USNA `RobotDll` shim | `USBC.dll` directly, from an explicit allowlist. `RobotDll.h` is used only as a reference for what calls mean | `RobotDll` has no `Stop`, no velocity jog, no `SetJoint` and no monitoring callbacks, which are the calls we most want to see on the wire |
| 4 | Driver swap | Run DLL probes inside the S1 visit block that already has the Intelitek driver installed for SCORBASE. No extra swap | `USBC.dll` needs Intelitek's kernel driver (`\\.\ERUSBDevice0`); our pyusb path needs WinUSB. They cannot be active together |
| 5 | Where the gates live | In the probe tool itself, as tiers (below). It does not go through `Scorbot` and is not part of the SDK | `Scorbot` is built around the legacy USB workers. Keeping the DLL tool outside `scorbot/` keeps "`Scorbot` is the only class that sends commands" true for the SDK |

## Facts this design rests on

From a static read of the export tables of the copies in `github.com/kutzer/ScorBotToolbox` (nothing was loaded or run). D = read from the file, I = inference.

- `USBC.dll` there is a 32-bit PE, 539,784 bytes, built 2018-11-22. It is not the 452 KB 2004 copy `docs/PROTOCOL.md` section 12 describes. The lab copy may differ from both (D).
- 120 exports: 103 free `__cdecl` functions, no `__stdcall`, the rest members of a C++ `INIFile` class (D).
- It imports only Windows system DLLs, no USB library and no separate C runtime (D).
- Parameters are small types. Decoded by hand from the mangled names, not yet confirmed with `undname`: `Control(unsigned char, int)`, `Stop(unsigned char)`, `MoveManual(unsigned char, long)`, `Home(unsigned char, callback)`, `SetJoint(long[8]*)`, `Initialization(short, short, callback, callback)` plus a `(short, callback, callback)` overload. Callbacks are `void (__cdecl *)(void *)` (I).
- It exports `MyPumpMessages` and imports `USER32`, so callbacks may only arrive while something pumps Windows messages (I).
- It is not packed: 410 KB of plain code, with source paths left in (`Usbc\USBLink.cpp`, `Usbc\Config.cpp`) and a diagnostic string that names message fields ("To ID", "From ID", "Buffers") (D).
- It reaches the driver with `CreateFileA`, `WriteFile` and `ReadFile` only, no `DeviceIoControl`. So the bytes it hands to `WriteFile` are the message, and the functions that call `WriteFile` are where to start reading (D for the imports, I for the conclusion).
- Between `WriteFile` and the cable sits Intelitek's kernel driver, which we do not have. Whether it passes the bytes through unchanged is unknown; one capture settles it (I).
- No public source documents the wire bytes. The open-source material (Kutzer, MTIS, Mosebo) describes the DLL's functions, not its packets (D).
- Kutzer reaches the DLL from 64-bit MATLAB through a separate 32-bit `ScorbotServer.exe`, shipped as an installer without source (D).

## Layout

A self-contained folder, `tools/usbc_probe/`, standard library only, importing nothing from `scorbot/` (the 32-bit Python at the lab has none of the SDK's dependencies).

| File | Job |
|---|---|
| `exports.py` | Read a DLL's export table and machine type from the file bytes. Loads nothing. Runs on any Python |
| `bindings.py` | The allowlist table and the ctypes layer. This is today's `scorbot/vendor_dll.py`, moved here; the row added to `scorbot/CLAUDE.md` is removed |
| `probe.py` | Command line tool: fingerprint, then run one named probe and log it |

Tests stay in `tests/` and use a fake library, as `tests/test_vendor_dll.py` does today. No test, script or CI job loads the real DLL.

## Requirements

1. **Fingerprint before anything else.** `probe.py fingerprint <dll>` prints size, modified date, SHA-256, machine type and the export list, and writes them to a JSON file. Every later probe refuses to run if the DLL's SHA-256 differs from the fingerprint it was given.
2. **Allowlist only.** A probe can call only functions in the table in `bindings.py`. Each entry has the exact decorated symbol, `argtypes`, `restype` and a tier. A symbol missing from the fingerprinted export list is an error, not a skip.
3. **Confirm signatures before first use.** Each allowlist entry records how its signature was established: `undname` output from the lab PC, or "hand-decoded, unconfirmed". Tier 2 and above require `undname`.
4. **One probe per run.** A run makes one named sequence of calls, then closes the DLL connection and exits. No interactive shell, no scripting of arbitrary calls.
5. **Tier flags.** Each tier needs its own command line acknowledgement, in the style of `--acknowledge-connect-handshake` in `examples/record_raw_state.py`.
6. **Bounded waits.** Any wait on a callback or status poll has a timeout (default 5 s for initialisation). On timeout the tool logs it, calls `CloseUSBC` if a connection was started, and exits non-zero.
7. **Callbacks are held.** ctypes callback objects stay referenced until after `CloseUSBC`. If callbacks need a message pump, the tool pumps on the calling thread; whether they do is the first thing tier 1 establishes.
8. **Log.** Append-only JSONL opened with `open(path, "x")`, strict JSON, one line per call: wall time, monotonic time before and after the call, function, arguments, return value, and for every run the DLL fingerprint, driver provider as typed by the operator, and tier. Callback payloads are logged as they arrive.
9. **Vendor binaries and manuals are never committed.** `USBC.dll`, `RobotDll.dll`, the INI parameter files and the PDFs stay out of the repo and out of the bench kit.
10. **Simulation mode is labelled.** A run with `Initialization` in the DLL's simulation mode (`INIT_MODE_SIMULAT = 2`, per Mosebo, unverified) writes `"simulated": true` in every line. Such a log is never evidence about the controller.
11. **String buffers** keep the guard-byte check already in `scorbot/vendor_dll.py`.

## Tiers

| Tier | What it may call | Can it energise the arm | Needs |
|---|---|---|---|
| 0 | Nothing in the DLL; `exports.py` only | No | Any Python, any PC |
| 1 | Load the DLL; `GetVersion`, `GetParameterFolder`, `GetDeviceLinkName`, `GetUSBDeviceNumber`; `Initialization` in simulation mode | Unknown for loading; believed no | 32-bit Python, controller **powered off or unplugged** for the first run |
| 2 | `Initialization` online, `IsEmergency`, `IsTeachMode`, `IsOnLineOk`, `GetCurrentPosition`, `WatchJoint`, `ShowEnco`, `ShowHomeSwitches`, `CloseUSBC` | Depends on the mode (from disassembly, unverified): mode 1 ends with motors off, mode 0 turns them on. The probe uses mode 1 only, and still treats the arm as possibly energised | Operator at the arm, physical stop in reach, USBPcap running |
| 3 | `Control` on and off, `Stop`, `Home` | Yes | Tier 2 evidence reviewed first; same supervision; start pose confirmed as for `Scorbot.home` |
| 4 | `MoveManual`, `SetJoint`, `Velocity`, `Time`, `Speed`, `Move*` | Yes, with no distance bound in the DLL | **Out of scope.** Needs its own spec after tier 3 evidence, because a velocity jog has no built-in limit |

Never bound, in any tier: `MoveTorque` (raw PWM, no impact protection), `SetParameter`, `SetEncoders`, `SetHome`, the `Force*` I/O calls, `BuildPositForWrite`, `GetConfig`, every `INIFile` member.

`disable()`-style calls and `Stop` are not an emergency stop. The physical stop is authoritative.

## Order of work

1. **Phase 0, no arm, no DLL load.** `exports.py`, the allowlist table with hand-decoded signatures, move of the bindings, tests. Add the export-table facts to `docs/PROTOCOL.md` section 12 with D and I labels.
2. **Phase 0b, no arm, no DLL load: disassembly.** Open the Kutzer copy of `USBC.dll` in a disassembler on the desk PC. Trace from `Stop`, `Control`, `MoveManual`, `SetJoint`, `Home` and the status getters down to the `WriteFile` call, and from `ReadFile` up to the callbacks. Write each message layout into `docs/PROTOCOL.md` marked "from disassembly of the 2018 build, unverified". Compare with what `openScorbot/` sends: where they agree, that is the first independent support the legacy packets have had. The disassembler is a desk tool, not a project dependency, and its project files (which contain vendor code) are never committed.
3. **Phase 1, lab, tier 1.** Install a 32-bit Python on the lab PC. Fingerprint the lab's own `USBC.dll`, run `undname` on the allowlist, run the tier 1 probes with the controller off.
4. **Phase 2, lab, tiers 2 and 3.** A short confirming set of captures during the S1 block with the Intelitek driver active, chosen from what phase 0b could not settle. Add them to `docs/S1_CAPTURE_LAB_CARD.md`.
5. Mark each message in `docs/PROTOCOL.md` as confirmed or contradicted, and feed phase B of the USB upgrade.

Phases 1 and 2 stay because the project rule is that packet construction in `openScorbot/` does not change without a captured trace. Disassembly tells us what the vendor's 2018 DLL would send; it does not tell us what the lab's controller accepts.

## Open risks

- Disassembly is slow, and a misread layout looks as convincing as a correct one. Everything from phase 0b stays "unverified" until a capture agrees.
- The disassembled copy is the 2018 build from Kutzer's repo. The lab's DLL and the controller firmware may be older and differ.

- Loading the DLL, or `Initialization`, may do something to the controller we have not predicted. Tier 1's first run is with the controller off for that reason.
- A wrong hand-decoded signature corrupts the process when called. Requirement 3 exists for this.
- The lab DLL may be a different build with different exports. Requirement 1 exists for this.
- A 32-bit Python on the lab PC is a new install; whether the lab allows it is not confirmed.
- SCORBASE and the probe cannot hold the controller at the same time (one connection, one process).
