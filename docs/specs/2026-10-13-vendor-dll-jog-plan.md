# Jog one axis through the vendor's `USBC.dll`: plan

Date: 2026-10-13. Status: owner asked for this ("get our code moving the arm"); nothing below has been built or run on the arm. Hardware work: requirements, a design check and two reviewers come before any commit to `main`.

## Decision

Extend the 2026-10-08 rule ("run the vendor DLL, not our own USB bytes") from `Home` to **manual jog**, as a second, separate lab tool: `tools/usbc_jog/usbc_jog.py`. It calls Intelitek's own `EnterManual`, `MoveManual` and `CloseManual`, so the controller and the DLL decide the packets and the limits. No capture is needed first, and no change to `scorbot/` (our pure-Python USB path stays untouched).

Why a new tool and not a flag on `usbc_home`: that tool's contract is "Home and nothing else; no joint-move export is reachable". Keeping it that way keeps its review valid.

## What we know (V = read from the SDK header, the vendor description file or the demo source; I = inference)

| Fact | Source |
|---|---|
| Exports, all `__cdecl`, found in the 2008 `BIN` DLL's export table: `?EnterManual@@YAHF@Z` (short type), `?MoveManual@@YAHEJ@Z` (uchar axis, long velocity), `?CloseManual@@YAHXZ`, `?GetCurrentPosition@@YAHPAY07J00@Z` (three `long[8]` out arrays: encoder, joint, XYZ), `?Stop@@YAHE@Z`, `?IsEmergency@@YAHXZ` | V, PE export table, 2026-10-13 |
| `EnterManual(MANUAL_TYPE_ENC = 0)` first, then `MoveManual(axis, velocity)`; the velocity is signed, so the sign is the direction. "When you move manually the MoveStart and MoveEnd callbacks are NOT called. You have to decide when the move finished yourself (maybe with a timer, 300 milliseconds)." Then `CloseManual()` | V, vendor description, section 6.1 |
| The Intelitek demo re-sends `MoveManual` every 300 ms while a button is held, with velocity `(speed index) * 10`, and calls `CloseManual` on release | V, `ManualMoveDlg.cpp:289-345`, `ManualMoveDlg.h:19` |
| The demo passes the axis as 0 to 5 (`m_iCurrAxis`). Our notes number them 0 base, 1 shoulder, 2 elbow, 3 pitch, 4 roll, 5 gripper | V for the demo (`ManualMoveDlg.cpp:299-303`), D for the names (`PROTOCOL.md:380`); never confirmed on the arm |
| The velocity is a percent of the axis's INI top speed; the sign picks the direction | V, disassembly, `VENDOR_DLL_PROTOCOL.md:313` (the vendor files do not state it) |
| A manual jog works from power-on, without Home | V, `VENDOR_MANUAL_MOVE_TRACE.md` |
| Connect and control-on energise the motors; `Initialization(1, 41)` is online | V, `usbc_home` and `VENDOR_DLL_PROTOCOL.md` |
| Before Home the DLL does not check the joint window; whether the controller does is unknown. A shoulder jog also changes the elbow angle (one motor moves, the elbow is not held). `MoveManual` is refused while a move runs (911) or with control off (903) | V, `VENDOR_MANUAL_MOVE_TRACE.md`, `VENDOR_DLL_PROTOCOL.md:353` |

## Requirements

| # | The tool must | Checked by |
|---|---|---|
| R1 | Bind only `Initialization`, `IsEmergency`, `Control`, `EnterManual`, `MoveManual`, `CloseManual`, `Stop`, `GetCurrentPosition`, `CloseUSBC`. No `Home`, `SetHome`, `SetJoint`, `Impact`, `Move*`, `Speed`, `Velocity`, torque or gripper call | A unit test lists the bound names |
| R2 | Refuse `--online` without `--acknowledge-vendor-jog`, `--operator` and a typed word (`JOG`); never run from a test or an agent | Unit test on the argument rules; the typed word is a `Console` method replaced in tests |
| R3 | Reuse `usbc_home`'s DLL fingerprint check, bounded calls, the busy and stop bookkeeping, the event log (JSONL, refuses to overwrite) and its clean-up (control off, close, "use the physical stop" messages) | Reuse by import; the existing `usbc_home` tests keep passing |
| R4 | Jog **one** axis for a bounded time: `--axis 0..2` (base, shoulder, elbow; the wrist takes an untraced `EnterManual` path and the gripper is axis 5), `--velocity` a non-zero integer, 1 to 100 in size (percent of top speed, default 10, signed), `--seconds` (default 1.0, at most 5). Re-send `MoveManual` every 300 ms, then `CloseManual` | Unit test with a fake DLL: the call sequence, the period and the end |
| R5 | End the jog on the first of: time up, `s` pressed, `IsEmergency` true, a refused `MoveManual`, an error callback from the DLL, an exception. Every end path calls `CloseManual` and `Stop('&')`, each bounded: an abort calls `Stop` first (it discards the queued setpoints), a normal end calls `CloseManual` first. A `CloseManual` that fails or hangs is reported as unsafe and leaves the session busy ("use the physical stop") | Fake-DLL tests for each end path |
| R6 | Before and after the jog, call `GetCurrentPosition` and log the three arrays and their difference, so the run itself tells us which joint moved, in which direction and by how many counts | Fake-DLL test on the log fields |
| R7 | Not cap the jog below what the arm allows: no angle ceiling and no travel cap of ours. The bounds are R4's (5 s, 1 to 100 percent) and the operator's hand on the physical stop. Not claimed: that the controller stops the jog at a joint limit (before Home the DLL does not check it) | Documented here; no limit constant is added to `scorbot/limits.py` |
| R8 | Say plainly what is unverified: the axis numbering, that there is no limit check before Home, and that a shoulder jog also swings the elbow. The first run is therefore velocity 1 for 0.5 s on one axis (`REQUIREMENTS.md` R18: the first motion by our code is 1 degree or less, and the velocity unit is unverified), watched, with the stop operator's initials on the lab card; the position delta is read before the velocity is raised or the next axis is tried | Printed by the tool before the typed word; recorded in the docs after the first run |

## Not in scope

Home (already `usbc_home`), the gripper, XYZ manual mode (`MANUAL_TYPE_XYZ`), several axes at once, any change to `scorbot/` or to `limits.py`.

## Open for the reviewers

1. Is `CloseManual` then `Stop('&')` the right end sequence, or can `Stop` after `CloseManual` do harm?
2. The demo never calls `Stop` on release. Is there a reason to avoid it?
3. A velocity range of 1 to 100 is a guess from the demo's `(index) * 10`. Is a wider range safe to allow?
4. Should the first run refuse an axis it has not seen move at the first-run velocity (a stepwise unlock), or is the printed warning enough?

## Steps

1. Design check on this file (Codex and Gemini), fix what is real.
2. Record the rule change in `CLAUDE.md` (the invariant row that says the DLL is loaded only through `tools/usbc_home/` and never binds a joint-move export): "changed 2026-10-13 by the owner: also `tools/usbc_jog/`, which binds `EnterManual`, `MoveManual` and `CloseManual` and nothing else that moves". `usbc_home` itself stays Home-only.
3. Add an optional `action` argument to `usbc_home.run`/`_sequence` (the function that runs after control-on, default `_home`); no behaviour change for Home.
4. Write `tools/usbc_jog/usbc_jog.py` and `tests_extra/test_usbc_jog.py` with a fake DLL; run the whole suite.
5. Two reviewers on the diff; fix what is real; PR.
6. At the arm, with a hand on the physical stop: `--probe`, `--simulate`, then `--online` per R8, one axis at a time.

## Design check, 2026-10-13 (Codex and Gemini, both ran; run ids 20261013-175242-5527-codex and -gemini)

Taken: axes limited to 0 to 2; maximum time cut from 10 s to 5 s; the claim that the controller's own limits bound the jog removed (unhomed, the DLL does not check them); a refused `MoveManual` and a failed or hung `CloseManual` now end the run and are reported; an abort calls `Stop` before `CloseManual`; the unverified items, the elbow swing and the no-limit-before-Home fact are printed before the typed word.

Not taken: "the DLL error callback cannot reach the jog" (Gemini). The callback logs through `log.event`, and the jog wraps that attribute for its own duration; `test_a_dll_error_callback_ends_the_jog` covers it. "Add an `action` hook" (Gemini said it already existed): it did not; the hook is the only change to `usbc_home` and its 37 tests still pass.

Open and unresolved: whether `CloseManual` alone brings the arm to rest, or only stops the keep-alive (the demo never calls `Stop`). The tool calls both; the first runs at 10 percent will show it in the position log. Nothing here replaces watching the arm and keeping a hand at the physical stop.

## Code review, 2026-10-13

Gemini reviewed the code twice (run ids 20261013-180108-1141-gemini and 20261013-181154-15453-gemini) and every finding was real and fixed: `Stop` is sent even if `CloseManual` hangs; any exception before the loop takes the abort order; Ctrl+C still lets control switch off; a hung or failed `Stop` and a failed `CloseManual` are reported as unsafe on every path; the position read after the stop cannot block the shutdown; a first position read that hung is not asked again. **Codex gave no review**: both runs failed before opening the diff ("the local file reader is failing"). The branch was pushed with that gap stated, not counted as a pass; no merge until a Codex review runs.
