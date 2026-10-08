# Vendor manual jog, homed mask and SetHome, traced in `USBC.dll`

Written 2026-10-07 from the 2018 build (539,784 bytes) and checked against the 2008 build (471,040 bytes). The lab's own `USBC.dll` is a third build (about 576 KB, SHA-256 `6118465D...`) that has not been read; where the two builds differ, that is said. Tags as in the other traces: V verified in the code, I inferred. Nothing here has been run on the arm, and the decompile hides the x87 arithmetic, so sizes and signs are not read here. Addresses are the 2018 build's unless marked.

## Summary

| Question | Answer | Tag |
|---|---|---|
| Does the vendor's joint-mode jog compensate the coupled motors? | **No.** `EnterManual(0)` then `MoveManual(axis, velocity)` drives **one motor**: the setup routine (`0x1000ec9e`, 2008: `0x1000ea77`) builds an 8-slot motor vector with only that axis non-zero. Our pre-home move (`pre_home_jog`) is **not** a copy of it. | V for the vector, I that this is SCORBASE's "Joint" button |
| Are model limits checked before homing? | **No.** The joint-window check (error `0x38b`) runs only when every robot axis is marked homed. | V, both builds |
| What is `0x10023b1e`? | "All robot axes homed": true when the first N bits of a per-axis mask at link `+0x83e` are set (N = number of robot axes). | V, both builds (2008: `0x10022f59`) |
| What sets and clears the homed mask? | `Home` clears all of it first (`Control('&')` -> `0x10023f66`), then sets one bit per axis as each finishes (`0x1002326a`). `Initialization(1)` restores the mask from a process-global saved at the end of `Home`; it lives in PC memory only. `Initialization(0)` (simulation) marks everything homed. | V |
| What is the exported `SetHome(group)`? | It runs the same end-of-group sweep that finishes `Home` (`0x1002407f`, 2008: `0x100234ba`): per axis a `48` set-position value 0 through the builder at `0x1003f845`, plus the mask OR. No switch search, no motion. So the vendor has a "declare this pose home" call. | V for the calls, I for the effect |

## 1. The joint jog moves one motor

`EnterManual(type)` (`0x1001b8eb`) sets one of two flags and a base index: type 0 sets flag A and base `0x28`; type 1 sets flag B and base 1. `MoveManual(axis, velocity)` (`0x1001ba0c`) scales the velocity by a per-axis INI speed (`Manual_1` for negative, `Manual_2` for positive; the loader's defaults are -100 and 100, the lab files were not checked for them) in type 0, or by the XYZ speeds (`ManualSpeedXYZ`) in type 1, then calls the setup routine with `base + axis`.

- **Type 1** (base 1..5) builds a five-value vector with one non-zero entry: the Cartesian jog (X, Y, Z, pitch, roll). Not used by us.
- **Type 0** (base `0x28`..) lands in the setup routine's default case: it zeroes an 8-slot vector and stores the velocity in the slot of that one axis (`0x1002fda6`). For the ER-4u (`SystemType` `0x29`, the default) the next step adds that to the current motor positions, converts the result with the counts-to-joints function (`0x100303db`) and tests the **joint** window only to decide whether to refuse. The motion itself stays in motor space.

So the vendor's "shoulder" jog in this mode is the shoulder **motor** alone. By the model's own table (VENDOR_COUPLING_TRACE.md section 3), shoulder motor +1000 counts changes the shoulder angle by -8.81 degrees **and** the elbow angle by +8.81, with the pitch unchanged; the vendor does not add counts on the elbow or wrist motors to hold the elbow still. The SCORBASE manual's remark that an axis limit error may appear when jogging an unhomed robot fits this.

What the DLL cannot say: which SCORBASE button calls what. The front end (`SCORBASE.exe`) is not in the DLL, so "joint mode jogs one motor" is an inference from the only manual-move API the DLL has (I).

**What this changes for us.**
- These say the elbow and wrist follow the shoulder "as in SCORBASE's joint mode": `PRE_HOME_HELP` in `scorbot/lab/session.py`, `docs/lab/LAB_SESSION.md`, `docs/project/PROJECT_LOG.md` and the docstring of `scorbot/joint_move.py`. That conflicts with the manual-move path read here. Because the SCORBASE front end is not in the DLL (above), treat it as unproven rather than disproved: our coupled move is probably a deliberate improvement on the vendor's jog, not a copy of it. None of those four was changed.
- The single-motor move needs no wrist jog at all (the legacy `_jog_joint(..., homing=True)` already does it for base, shoulder and elbow). The coupled move keeps the elbow angle fixed but sends the wrist pitch jog for the first time, with unverified signs. Which one to use for the first trial is a decision for the owner; no code was changed.

## 2. The homed mask

Link word at `+0x83e`, one bit per axis. (V in both builds.)

| Function | Effect | 2008 |
|---|---|---|
| `0x10023b1e` | true when all robot-axis bits are set | `0x10022f59` |
| `0x1002326a` | at the end of an axis in `Home`: sets that axis's bit, and when all robot axes are set resets the jog state | `0x100226a5` |
| `0x10023f66` | clears: `'&'` everything, `'A'` the robot axes, `'B'` the second group, an axis number one axis. Called at the start of `Home`, by `ChangeConfig` and others | `0x100233a1` |
| `0x1001ecc0` | writes the whole word; called only from `Initialization` for a real robot, with a value `Home` saved in a global at its end | `0x1001e510` |
| `0x1002407f` | the sweep; ORs in all robot bits | `0x100234ba` |

Consequences:
- The mask the DLL reads is PC memory, and a new process starts it clear. Whether the controller keeps any homing state of its own is not shown by this trace (unverified); the encoder counters reading about zero at power-on is consistent with it keeping none.
- The joint-window check (`0x38b`) and the predicted-position check use the mask as their gate, so **before homing the vendor applies no window at all**; only the speed check (`0x391`) and the target-already-moving checks apply. Our own pre-home move has no window either, which matches the vendor, and the source model's window is no reason to trust it.

## 3. `SetHome`

`SetHome(group)` (`0x1001ca1e`, 2008: `0x1001c2c5`) accepts `'&'`, `'A'`, `'B'` or an axis number. It refuses (error `0x38f`) while control is off or the link is busy, then calls the sweep. The sweep sends `48` value 0 for each axis (through `0x1003f845`), waits for the queue, and sets the mask. In the simulation mode the same function is called by `Initialization` with `'&'` so everything starts homed.

This disagrees with `docs/protocol/PROTOCOL.md`, which says `SetHome` only stores a position and does not mark the arm homed; the code read here ORs the mask, so that line should be corrected after a capture settles it. What `48` does on the controller is an inference from the legacy name ("set position"). If it is right, this is the vendor's own "take the current pose as home", the call a parked `adopt_home` would copy (`docs/specs/2026-10-06-adopt-home-design.md`). It needs `48`, a byte the legacy code never sends, so by the project rule it needs a USB capture first, and it needs the arm to actually be at its home pose or the zero is wrong for every later move.

## Not found or not answerable

- The PWM scale of `4D`, any coupling inside the controller, why the offset move halves the wrist, and the signs and polarities on our hardware: not in the DLL (VENDOR_COUPLING_TRACE.md "Unresolved").
- Not traced this pass: the wrist jog pair in type 0 (axes 3 and 4 take a separate branch of the setup routine, `0x1000ec9e` second half), the version-8 branches of `Home`, what ends a gripper move (`0x10023e7e`), the peripheral string compare, and the unread reply bytes. None of them blocks a first trial.
- The lab build: every finding above is from the 2018 build and was checked in the 2008 build, where the structure, the error codes and the mask functions agree. The lab build has not been read.
