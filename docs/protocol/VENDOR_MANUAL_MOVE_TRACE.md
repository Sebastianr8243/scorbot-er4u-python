# Vendor manual jog, homed mask and SetHome, traced in `USBC.dll`

Written 2026-10-07 from the 2018 build (539,784 bytes) and checked against the 2008 build (471,040 bytes). The lab's own `USBC.dll` is a third build (532,576 bytes, SHA-256 `6118465d...bd821`); its velocity jog was read on 2026-11-04 and is in section 4, which uses the lab build's addresses. Where the builds differ, that is said. Tags as in the other traces: V verified in the code, I inferred. Nothing here has been run on the arm. Sections 1 to 3 do not read the x87 arithmetic, so sizes and signs are not given there; section 4 reads it from the assembly. Addresses in sections 1 to 3 are the 2018 build's unless marked.

## Summary

| Question | Answer | Tag |
|---|---|---|
| Does the vendor's joint-mode jog compensate the coupled motors? | **No.** `EnterManual(0)` then `MoveManual(axis, velocity)` drives **one motor**: the setup routine (`0x1000ec9e`, 2008: `0x1000ea77`) builds an 8-slot motor vector with only that axis non-zero. Our pre-home move (`pre_home_jog`) is **not** a copy of it. | V for the vector, I that this is SCORBASE's "Joint" button |
| Are model limits checked before homing? | **No.** The joint-window check (error `0x38b`) runs only when every robot axis is marked homed. | V, both builds |
| What is `0x10023b1e`? | "All robot axes homed": true when the first N bits of a per-axis mask at link `+0x83e` are set (N = number of robot axes). | V, both builds (2008: `0x10022f59`) |
| What sets and clears the homed mask? | `Home` clears all of it first (`Control('&')` -> `0x10023f66`), then sets one bit per axis as each finishes (`0x1002326a`). `Initialization(1)` restores the mask from a process-global saved at the end of `Home`; it lives in PC memory only. `Initialization(2)` (simulation: the SDK header's `INIT_MODE_SIMULAT`) marks everything homed. Mode 0 is `INIT_MODE_DEFAULT`, which takes the mode from the INI file (checked against `USBCDEF.h:16-19`, 2026-10-13). | V |
| What is the exported `SetHome(group)`? | It runs the same end-of-group sweep that finishes `Home` (`0x1002407f`, 2008: `0x100234ba`): per axis a `48` set-position value 0 through the builder at `0x1003f845`, plus the mask OR. No switch search, no motion. So the vendor has a "declare this pose home" call. | V for the calls, I for the effect |

## 1. The joint jog moves one motor

`EnterManual(type)` (`0x1001b8eb`) sets one of two flags and a base index: type 0 sets flag A and base `0x28`; type 1 sets flag B and base 1. `MoveManual(axis, velocity)` (`0x1001ba0c`) scales the velocity by a per-axis INI speed (`Manual_1` for negative, `Manual_2` for positive; the loader's defaults are -100 and 100, the lab files were not checked for them) in type 0, or by the XYZ speeds (`ManualSpeedXYZ`) in type 1, then calls the setup routine with `base + axis`.

- **Type 1** (base 1..5) builds a five-value vector with one non-zero entry: the Cartesian jog (X, Y, Z, pitch, roll). Not used by us.
- **Type 0** (base `0x28`..) lands in the setup routine's default case: it zeroes an 8-slot vector and stores the velocity in the slot of that one axis (`0x1002fda6`). For the ER-4u (`SystemType` `0x29`, the default) the next step adds that to the current motor positions, converts the result with the counts-to-joints function (`0x100303db`) and tests the **joint** window only to decide whether to refuse. The motion itself stays in motor space.

So the vendor's "shoulder" jog in this mode is the shoulder **motor** alone. By the model's own table (VENDOR_COUPLING_TRACE.md section 3), shoulder motor +1000 counts changes the shoulder angle by -8.81 degrees **and** the elbow angle by +8.81, with the pitch unchanged; the vendor does not add counts on the elbow or wrist motors to hold the elbow still. The SCORBASE manual's remark that an axis limit error may appear when jogging an unhomed robot fits this.

What the DLL cannot say: which SCORBASE button calls what. The front end (`SCORBASE.exe`) is not in the DLL, so "joint mode jogs one motor" is an inference from the only manual-move API the DLL has (I).

**What this changes for us.**
- Before this trace, `PRE_HOME_HELP`, `lab/LAB_SESSION.md (in git history)`, `project/PROJECT_LOG.md (in git history)` and the docstring of `scorbot/joint_move.py` said the elbow and wrist follow the shoulder "as in SCORBASE's joint mode". The manual-move path read here conflicts with that, and because the SCORBASE front end is not in the DLL it is unproven rather than disproved. On 2026-10-07 option A was chosen: the pre-home move drives one motor by default (`pre_home_jog(..., coupled=False)`), the coupled version is opt-in (`--coupled-pre-home`), and the help text and docs were reworded. Inch home and the park still use coupled moves.
- The single-motor move needs no wrist jog at all (the legacy `_jog_joint(..., homing=True)` already does it for base, shoulder and elbow). The coupled move keeps the elbow angle fixed but sends the wrist pitch jog for the first time, with unverified signs. Which one to use for the first trial is a decision for the supervisor; no code was changed.

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

This disagrees with `docs/protocol/PROTOCOL.md`, which says `SetHome` only stores a position and does not mark the arm homed; the code read here ORs the mask, so that line should be corrected after a capture settles it. What `48` does on the controller is an inference from the legacy name ("set position"). If it is right, this is the vendor's own "take the current pose as home", the call a parked `adopt_home` would copy (`specs/2026-10-06-adopt-home-design.md (in git history)`). It needs `48`, a byte the legacy code never sends, so by the project rule it needs a USB capture first, and it needs the arm to actually be at its home pose or the zero is wrong for every later move.

## Not found or not answerable

- The PWM scale of `4D`, any coupling inside the controller, why the offset move halves the wrist, and the signs and polarities on our hardware: not in the DLL (VENDOR_COUPLING_TRACE.md "Unresolved").
- Not traced this pass: the wrist jog pair in type 0 (axes 3 and 4 take a separate branch of the setup routine, `0x1000ec9e` second half), the version-8 branches of `Home`, what ends a gripper move (`0x10023e7e`), the peripheral string compare, and the unread reply bytes. None of them blocks a first trial.
- The lab build: sections 1 to 3 are from the 2018 build and were checked in the 2008 build, where the structure, the error codes and the mask functions agree. In the lab build only the velocity jog has been read (section 4); its homed mask and `SetHome` have not.

## 4. The lab build's velocity jog (2026-11-04, issue #148)

Read from the lab `USBC.dll` (532,576 bytes, SHA-256 `6118465d...bd821`, hash checked before reading) in a Ghidra decompile and its assembly; `EnterManual` and `CloseManual` were decompiled again in a second Ghidra session (same output), and the 2008 build's `EnterManual` was compared the same way. Addresses are the lab build's. V means read in the code, I inferred, and **desk** means checked against the lab DLL's own recording on the fake controller: `tests/fixtures/dll_lab_manual.json` on branch `stack2-velocity-jog` (PR #102, closed; not on `stack2`), 12 jogs, 2026-11-02. A model of the code below (kept outside the repo, like the decompile) reproduces the jogged setpoint of every message of the 10 recorded `MoveManual`/`CloseManual` jogs (axes 0, 1 and 2, speeds -100 to +50, about 1,000 messages) with no difference. Nothing here has been run on the arm.

### Summary

| Question | Answer | Tag |
|---|---|---|
| 1. What does `EnterManual` send? | At most one message, `47`, and only when the arm is settled; otherwise nothing. It does not switch the queue limit; `MoveManual` does | V (the gate), I (reading the gate as "settled") |
| 2. How does `MoveManual(axis, v)` become setpoints? | One per-axis number, `slot` = trunc(`Manual` x \|v\|/100 / 1000 x `NoEnc90` / (pi/2)) counts, fixed at the first call. Every tick, setpoint = start + trunc(`slot` x s(t)), where s(t) is a jerk-limited S-curve that reaches a slope of 1/0.3 per second after 0.3 s and keeps it. Cruise = `slot` / 0.3 counts per second = `slot` x 0.08 counts per tick | V, desk |
| 3. Queue depth, and when the host stops calling | `ManualBuffers` (4 in `ER4CONF.INI`, 5 if missing), doubled once per process when ticks run late, at most 45. A 300-tick keep-alive: when it runs out, the jog ramps down and closes exactly as after `CloseManual` | V |
| 4. What does `CloseManual` send? | Nothing itself; it sets the keep-alive to 1. The next tick starts a 0.3 s ramp down; at its end the tick sends `4F 3F 53`, `73 20`, `42 20 00 00` (no `47`) | V, desk |
| 5. Differences from the earlier traces | The `47` is conditional (also in the 2008 build); PR #102's "EnterManual sends nothing" and "no keep-alive" are wrong; the ramp constants are times, not accelerations | see below |

### 1. `EnterManual(type)` (`0x1001b788`)

- Returns 0 with no effect if `0x1001ee40` is true; raises 0x38f (and clears both mode flags) if any axis has a non-zero entry at link `+0x480` (`0x10024e46`); if a group is moving (`0x10024bad`) it calls `0x10021b61` and returns 0. (V)
- Type 0 sets the joint-mode flag (`0x10078730`) and the axis base `0x28`; type 1 the XYZ flag (`0x10078734`) and base 1; any other type returns 0. Same as the 2018 build. (V)
- Then, if `0x10024d1d` returns 1, it queues `47` (`0x1004045c`: a message whose only content is command byte `0x47`, queued through `0x1003ec29`). It returns 1 whether or not the `47` was queued. (V)
- `0x10024d1d` is 1 only when no group is moving and, on every axis, \|link `+0xe0` - `+0x140`\| <= 10 and \|`+0x220`\| <= 10 counts (V). Reading `+0xe0` as the measured counts, `+0x140` as the setpoint and `+0x220` as the following error the tick stores, this is "the arm has settled" (I). So a jog started right after a move, before the arm settles, has no `47` in front of it.
- Every recorded jog starts with `47` (desk). The recording cannot show which call queued it: the script called `EnterManual` and `MoveManual` back to back, and the `47` message already carries the first jog setpoint.

### 2. `MoveManual(axis, v)` (`0x1001b8a9`) and the per-tick setpoints

**The call** (V). Returns 0 unless a mode flag is set; 0x38f if an `+0x480` entry is non-zero. In joint mode, for axis <= 7, `m` = the axis's `Manual_1` (INI `[Speed]`, record `+0xd4`) when v < 0, else `Manual_2` (`+0xd8`), and vel = m x \|v / 100.0\| (`0x10065d10` = 100.0; `0x100430a7` is `fabs`). It calls the set-up `0x1000ec6e(link, 0x28 + axis, vel, 0.3, 0.05)`; on an error it calls `CloseManual`, reports the code with axis + 1 and returns 0. An axis whose type has bit 1 set (`0x10007232`) takes a gripper path instead (`0x10022f1e`), not traced and not the axis-0 path.

**Set-up for codes `0x28` to `0x2a`** (base, shoulder, elbow under `SystemType 0x29`):
- If a jog is already running (keep-alive `+0x4b6` non-zero): the same code as the running jog (`+0x833`) re-arms the keep-alive to 300 and returns 0; any other code returns 0 with no effect. The velocity is never changed, so a new speed or sign is accepted and ignored (V; desk: the "renewed, then -40 and +20 re-sent" jog keeps its first step). Otherwise the group state `+0x79e` must be 0 (9 raises 0x387, any other value returns 0 silently). (V)
- `slot` = trunc((float)(vel / 1000.0) x `scale[axis]`), stored as an 8-slot vector with only that axis non-zero (`0x1002fc36` into link `+0x520`). The divide is stored as a 4-byte float (`0x1000ef35`); the product is `FLD float`, `FMUL [0x1008ab68 + 8 x axis]`, `__ftol` at `0x1000f0ae`; `__ftol` (`0x10042ef4`) sets the rounding control to chop, so it truncates toward zero. (V)
- `scale[axis]` is set when the axis file loads (`0x100068cf`): `NoEnc90` / (pi/2) when `AxisType` bit 0 is clear, `NoEnc90` / 90 when set (constants `0x10065cc0` = 1.5708, `0x10065cd0` = 90.0). (V.) All five lab parameter sets give axis 0 `AxisType 0`, `NoEnc90 -12770`, `Manual_1 -145`, `Manual_2 145`.
- Speed check: `slot` x (1 / 0.3) per axis against link `+0x360` (`0x10030142`; the factor is `[EBP-0x10]`, stored at `0x1000f429`), error 0x391. This is the DLL's own statement that the peak speed is `slot` / 0.3 counts per second. That `+0x360` holds `MaxSpeed` was not traced (I).
- Planner `+0x808` set up by `0x10012c6b(2 x 0.3, 0.05)`, which calls `0x1001292d(T = 0.6, 0.5, 0.05)` and sets its cruise flag; error 0x24a. The start is the commanded setpoint, not the measured count: `+0x140` is copied to `+0xf70`. The predicted-position check (`0x10006e23`, 0x38b) runs only when homed (`0x100239ae`), as in section 2. Then `+0x833` = code and the group state = 4. (V)
- Wrist codes `0x2b`, `0x2c` under `SystemType 0x29` build a joint vector instead (`0x100113d0`, link `+0x4f0`) and take the joint branch (state 1). (V for the branch; not traced further.)

**The S-curve** (`0x1001292d`, evaluated by `0x10012e18`; V, desk). With T = 0.6, p = 0.5, j = 0.05: jerk from 0 to 0.015 s (t1 = j p T), constant acceleration to 0.285 s, jerk down to 0.3 s (t3 = p T) where s = 0.5 and the slope is V = 1 / ((1 - p) T) = 10/3 per second; with the cruise flag set, s = 0.5 + (t - 0.3) x 10/3 from then on. So the vendor's "A = 0.3" is the ramp time in seconds and "J = 0.05" the share of the ramp spent in jerk, not an acceleration and a jerk (correction to `VENDOR_DLL_PROTOCOL.md` section 11).

**The tick** (`0x10025afc`, called by the USB loop `0x1003ccc8` at `0x1003e075` after each reply; V). In state 4: setpoint(`+0x140`) = `+0xf70` + trunc(`+0x520`[i] x s(t)) for each axis (`0x1002ff65`, then `0x1002fe87`, at `0x10026cda`); then t (`+0x7b8`) advances by the fixed period `+0x850` = `PCPeriod` x `USBCPeriod` x 0.001 = 16 x 1.5 x 0.001 = 0.024 s (`0x10015178`), whatever the real reply interval. The communication thread carries the setpoint out in ordinary `0D` messages (`VENDOR_DLL_PROTOCOL.md` section 5). Desk: the first jog message carries the setpoint of t = 0.024, so message k (counted from the one that carries `47`, from 0) carries s((k + 1) x 0.024).

**Clamps** (V): none on `v` itself (\|v\| over 100 scales past `Manual`; only the speed check 0x391 limits it). When homed, the window and predicted-position checks of section 2 apply at set-up and on every tick (0x38a, 0x38b); a failure sets the keep-alive to 1, which ramps the jog down.

### 3. Queue depth and the keep-alive

**Queue limit** (USB loop `0x1003ccc8`, V). The INI reader `0x10002a38` fills the configuration object at `0x1008a298`: `+0x54` `PCPeriod` (`0x1008a2ec`), `+0x58` `USBCPeriod` (`0x1008a2f0`), `+0x60` `Buffers` (`0x1008a2f8`), `+0x64` `ManualBuffers` (`0x1008a2fc`), `+0x68` `HomingBuffers`, `+0x6c` `TPBuffers`. On each pass, while a manual code is stored (`0x10011340`: `+0x833` or `+0x834` non-zero, so from the first `MoveManual` until the jog has closed), the limit is `ManualBuffers`. Once per link object, when the fixed period is not more than the measured tick gap (`+0x850` <= `+0x828`, both truncated), the manual limit is doubled and kept, logged "Automatically manual buffer change", capped at 45; the flag that allows it (`+300`) is cleared only in the constructor `0x1003e735`, so it is once per process. `ManualBuffers` defaults to 5 in the code and is 4 in the SDK's `ER4CONF.INI`; which `ER4CONF.INI` the lab tree loads was not checked (I).

**Keep-alive** (V). The set-up sets `+0x4b6` = 300; a repeat same-axis `MoveManual` resets it to 300. Each tick decrements it; when it reaches 0 while the group state is non-zero, the tick calls the planner stop `0x10012cba` at the current t. That is the same path `CloseManual` takes, so a host that stops calling gets the normal ramp down and close after 300 ticks (7.2 s of planner time; the real time depends on the reply rate). No error is raised on that path (I: read, not run). PR #102 concluded "no keep-alive" from a 3 s jog, which was 281 messages, under 300.

### 4. `CloseManual` (`0x1001bc47`) and the close

- `CloseManual` clears both mode flags and calls `0x100100b2`, which sets the keep-alive to 1 if it is non-zero. It sends nothing and returns 1; with no jog running it changes nothing else. (V)
- The next tick takes the keep-alive to 0 and calls `0x10012cba` at its t. Past t = 0.3 s the ramp down starts now, ends 0.3 s later, and s ends 0.5 above its value now (the mirror of the ramp up). At or before 0.3 s the profile is reflected instead (end = 2t, final s = 2 s(t)); never seen on the desk. The cruise flag is cleared. (V)
- The tick on which t passes the end time sends `4F 3F 53` (`0x1003f379(link, 3, 'A')`: Mode S for group A), then `0x10022ee0`: `73 20` and `42 20 00 00` (`0x1003f2d7` with 0 gives `73`, `0x1003f32a` gives `42` value 0, both on the gripper axis bit), and sets state 3. It sends these only when a manual code is stored and the gripper's slot is 0, as on any jog of axes 0 to 4. The next tick (state 3) clears the manual code and the velocity vector and goes to state 0, with no message; the setpoint stays at its last value. (V)
- Desk: in every recorded close, the last setpoint is repeated on the messages that carry `4F 3F 53`, `73 20` and `42 20 00 00`. That is the same tail as a point move (`BRING_UP_SEQUENCE.md` row 6), without the `47` of `Stop`.

### 5. Differences from the earlier traces

- **The `47` is conditional.** `VENDOR_DLL_PROTOCOL.md` section 5 says `EnterManual` "sends `47` first". In the lab build it does so only when settled, and the 2008 build's `EnterManual` (`0x1001b15a`) has the same gate (`0x100242c8`, then `0x1003f195`). The 2018 build was not re-read.
- **PR #102** (`scorbot/vendor_manual.py` on `stack2-velocity-jog`) assumed "`EnterManual(0)` sends nothing; the first `MoveManual` sends `47`" (the code queues it in `EnterManual`), "no keep-alive" (there is a 300-tick one) and straight-line ramps of 10 and 12 messages fitted to the recording (they are the S-curve above). Its fitted cruise rate, -0.9426 counts per message per percent on the base, agrees with `slot` x 0.08 = -0.9424 at 50%.
- **Ramp constants**: "A = 0.3, J = 0.05" are a 0.3 s ramp and a 5% jerk share, not acceleration values.
- **Structure**: the same as the 2018 build. Type 0 jogs one motor (section 1): `+0x520` has one non-zero slot; model limits apply only when homed (section 2).

### Expected messages for #133: `EnterManual(0)`, `MoveManual(0, +50)`, `CloseManual`

This is the recorded "closed after 0.8 s, speed 50" jog, so a test can compare against the fixture once #133 restores it. Online, after `Home('A')`, arm settled; base setpoint at the jog start = S. Base `Manual_2` 145 and `NoEnc90` -12770 give `slot` = trunc(0.0725f x -8129.86) = **-589**. Only the base slot changes. k counts OUT messages from the one that carries `47`.

| k | Command bytes (from byte 4) | Base setpoint - S |
|---|---|---|
| 0 | `47` | -1 |
| 1 to 13 | `0D` | -5, -14, -27, -43, -64, -88, -117, -149, -186, -226, -271, -318, -365 |
| 14 to 70 | `0D` | trunc(-589 x (0.5 + ((k + 1) x 0.024 - 0.3) x 10/3)): -47 or -48 per message (k = 53: -2249; k = 70: -3051) |
| 71 to 82 | `0D` | -3097, -3139, -3177, -3212, -3242, -3269, -3292, -3310, -3325, -3335, -3342, -3345 |
| 83 | `4F 3F 53` | -3345 |
| 84 | `73 20` | -3345 |
| 85 | `42 20 00 00` | -3345 |

Rows 0 to 70 are V and desk; the close is desk. Where the ramp down starts (k = 71 here) depends on when the host calls `CloseManual` and is the one fitted number; the ramp itself is fixed (the tick after `CloseManual` stops the planner at its t, then 0.3 s, then the three-message tail). A general test should take the close tick as an input, not wall-clock time. The `0D` byte layout is in `BRING_UP_SEQUENCE.md` row 6.

**What the x87 hides, and what is left open.** Every value above is computed in 80-bit x87 registers; the model uses 64-bit doubles. They can differ only where a product falls within about 1e-15 of a whole number before truncation. No such case appeared in about 1,000 recorded messages, but exactness is shown only for the recorded speeds (5, 10, 20, 50 and 100 percent). Not compared with any recording: the reflected close before 0.3 s, the keep-alive expiry, the doubled manual queue, the `47` being left out when not settled, axes 3 to 5, type 1 (XYZ) and the gripper path. All of it is **unverified on the arm**.
