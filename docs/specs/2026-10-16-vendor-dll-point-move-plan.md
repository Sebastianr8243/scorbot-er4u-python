# Vendor DLL point move (2026-10-16)

Owner decision, 2026-10-16: take what the USNA ScorBot Toolbox does (it is the only public, working way to move this arm) and bring it into our tools. Status: built and tested against a fake library; the real DLL resolves all four new exports; **never run on the arm, and never run end to end against the DLL on this PC** (see "Desk result").

## What the MATLAB toolbox does (read from `github.com/kutzer/ScorBotToolbox`, 2026-10-16)

It does not use `MoveManual`. Its call order, through `RobotDll.dll` (a wrapper over `USBC.dll`):

1. `RInitialize`, then poll `RIsInitDone` every 0.1 s.
2. `RControl('A', 1)`, then `RHome('A')`, poll `RIsHomeDone` and read `RIsError`; then a move to the current pose.
3. To move: `RDefineVector(name, n)`, `RAddToVecXYZPR(name, point, relative, x, y, z, pitch, roll)` (micrometres and thousandths of a degree), `RMoveJoint(name, point)`.
4. Wait for `RIsMotionDone` before the next move. It never sends a move while one runs.

Licence: US Government work, public domain, asks for an acknowledgement and a note on derivatives. We copy no code; we copy the call order.

## What it is in `USBC.dll` (from `tools/usbc_analysis` robotdll.c and usbc_2018.c)

| RobotDll | USBC.dll export | Arguments |
|---|---|---|
| `RDefineVector` | `?DefineVector@@YAHEPADF@Z` | group `'A'`, name, point count |
| `RAddToVecXYZPR` | `?Teach@@YAHPADFPAJFJ@Z` | name, point, `long[5]`, 5, flag (-0x7ffe absolute, -0x7fff relative) |
| `RMoveJoint` | `?MoveJoint@@YAHPADF0F@Z` | name, point, NULL, 0 |
| `RIsMotionDone` | none found | a flag `RobotDll` sets from somewhere not traced; we poll the position until it is still |

Both builds we hold (2008 and 2018) export all four names (`--probe`, 2026-10-16).

## The tool

`tools/usbc_move/usbc_move.py`: `--probe`, `--simulate`, `--online`, in the same shape and with the same guards as `usbc_jog` (typed word `MOVE`, fingerprint check, bounded calls, stop bookkeeping). One RELATIVE step in x, y or z, at most 20 mm in size per run. Relative so a wrong unit scales a small step instead of sending the arm to an absolute pose. The log records the position arrays before and after; compare them with the request before raising `--mm`.

Open on the arm, in the order they will show up:

1. Does the DLL accept a move before a home? (`move_returned ok=false` is a normal outcome; home first.)
2. Is the `Teach` unit micrometres? (Compare `position_delta.xyz` with the request.)
3. Does the DLL refuse the next move while one runs (error 911 on the jog tool's third call)? This tool waits for `MoveJoint` to return and the position to settle before it finishes, which is what the toolbox does.
4. What fires the done flag? The log shows every DLL callback.

## Desk result (2026-10-16)

- Tests: `tests_extra/test_usbc_move.py`, 11 pass.
- `--probe` against both real builds: all nine exports resolve.
- **`Initialization` is refused on this PC, with the unchanged `usbc_home.py --simulate` and `tools/usbc_fake/fake_home.py` too**, in a freshly built folder (BIN tree, 32-bit Python beside the DLL, pipe-only `USBC.INI`). The DLL never opened the pipe. Cause not found; the lab PC, which has the vendor install, is the place to look. So the fake-pipe run of `tools/usbc_fake/fake_move.py` (home, then one point move) is written but not yet seen to complete.

## Not covered

Rotation about the base axis, joint-angle targets (the vendor vector takes task-space points only), streaming at more than a few Hz, the wrist and gripper.
