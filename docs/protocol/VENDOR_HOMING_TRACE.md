# Vendor homing, traced in `USBC.dll`

What Intelitek's DLL does when `Home` is called, read from the decompiled 2018 build and cross-checked against the 2008 build. It completes section 13 of [VENDOR_DLL_PROTOCOL.md](VENDOR_DLL_PROTOCOL.md), which read only the end of the per-axis routine.

**Status: from disassembly, unverified on the arm.** The DLL was read as text and never loaded or run. Nothing here has been checked against a controller or a USB capture. Labels follow VENDOR_DLL_PROTOCOL.md:

- **V**: read directly from the vendor code.
- **I**: our inference from it.
- **S**: agrees with a second source that never went through the decompiler.

Function addresses are from the 2018 build unless marked 2008. Axis numbers are 0-based, as in the DLL: 0 base, 1 shoulder, 2 elbow, 3 wrist motor 1, 4 wrist motor 2, 5 gripper. The INI files `ER4Ax1..6.ini` are axes 0-5.

## Summary

Values come from the `$Default` parameter files shipped with the USNA toolbox: `ER4Ax1-6.ini [Homing]` and `ROB_4u.INI [HomingSeq]`.

| Axis | `Type` | `Velocity` (the `4D` value) | Coarse search (V) | Final approach in counts (I) | Fine edge search (V) | Polarity (V) | `Offset`, counts (V) | Moves with it (V) | `48` Set position 0 (V) |
|---|---|---|---|---|---|---|---|---|---|
| 1 shoulder | 16 | +90 | If already on the switch, drive +90 until off. Then Mode H, drive -90 until on | + | back off in 22-count steps until off, creep 2 counts until on | bit set = on | -190 | elbow +190, m1 +95, m2 -95 | elbow, m1, m2, shoulder |
| 2 elbow | 14 | +100 | Drive +100 through the switch (on, then off). Back -100 through it. Mode H, +100 until on | - | 22-count steps, then 2 counts | same | +45 | m1 +22, m2 -22 | m1, m2, elbow |
| 4 wrist m2 ("roll") | 16 | -90 | As the shoulder; m1 driven with the same value | m1 and m2 both - | 10-count steps, then 1 count | same | -690 | m1 -690 (roll -24.7 deg) | m1, m2 |
| 3 wrist m1 ("pitch") | 15 | +90 | Drive +90 (m2 gets -90) until on, then on until off. Mode H, -90 until on | m1 +, m2 - | 10-count steps, then 1 count | same | +850 | m2 -850 (pitch 30.5 deg) | m2, m1 |
| 0 base | 14 | +110 | As the elbow | - | 28-count steps, then 2 counts | same | 0 | none | base |
| 5 gripper | 17 | -165 | Open (drive -165), then close (drive +165). No switch | n/a | none | n/a | 0 | none | only in the end-of-group sweep |

The rows are in homing order (`HomingSeq` 2, 3, 5, 4, 1, 6). On an ER-4u a pre-pass runs before them; see section 1.

## 1. `Home` (`0x10018e19`): control flow

**First, for every call (V).**

1. Log the start, store the callback, and clear the "homed" bits of the group (`0x10023f66`).
2. `Control('&', 1)`, which turns all motors on (VENDOR_DLL_PROTOCOL.md section 5).
3. Reload `ROB_4u.INI` (`0x10003655`) and the configuration and axis files (`0x10004d89`). A load failure is error 533 (`0x215`) and returns 0. So INI edits take effect at every `Home`.
4. If online (`aa14 == 1`), run the parameter download (section 2).
5. Refresh the planner state and the copied configuration (`0x10020ef4`, `0x1002f69e`) and the cycle time.
6. Set the homing flag `0x10078c28` to 1. The communication thread reads this flag to choose the `HomingBuffers` queue limit (section 3).

**Then it branches on the argument.**

| Argument | What runs (V) |
|---|---|
| `'&'` (all) | Robot group `0x10023430`. Then hold the setpoints (`0x10021939`) and send `73 20`, `42 20 00 00` (gripper control off, motor off; `0x10023050`). Then the peripheral group `0x100239dd`, but only if `0x10005bb7` finds a peripheral axis file other than `NOC` (I: never on the default ER-4u configuration, where axes 6-7 are `NOC.INI`) |
| `'A'` (robot) | `0x10023430`, then the same `73 20`, `42 20` |
| `'B'` | Peripheral group only |
| 0-7 | That one axis through `0x1002326a`. No pre-pass and no end-of-group sweep |

**Robot group `0x10023430` on an ER-4u (V).** When `SystemType` is `0x29`, the routine runs a pre-pass `0x1000808b` before the normal loop. `0x29` is the code's ER-4u case, and it is also the default when the key is missing. `SystemType` is not in the shipped `ER4CONF.INI`; the file it is read from (`0x10002a38`) was not available, so on our arm the value is I.

The pre-pass (`0x1000808b`):

1. Shoulder, then elbow. Each runs the per-axis routine through `0x10007ff4`, which zeroes the `Offset` for that pass and switches off the fine edge search.
2. Close the gripper and wait for it, then wait 20 cycles.
3. Wrist motor 2 ("roll"), with its offset kept and no fine search.
4. Wrist motor 1 ("pitch"), with its offset zeroed and no fine search.
5. `47`, `4F FF 53`, then a planner move of wrist motor 1 by its `Offset` + 200 = **1050 counts**, about 37.6 degrees of pitch. The address `0x1008a5e4` is axis 3's `Offset` field: `0x1008a290 + 3 x 0xA8 + 0x15C`.
6. An impact check.

Every pre-pass axis also ends with `48` (the common tail in section 5 runs).

After the pre-pass, the normal loop walks `HomingSeq` until it reaches a 0. With `2, 3, 5, 4, 1, 6, 0` that is shoulder, elbow, wrist m2, wrist m1, base, gripper. Each runs through `0x1002326a`.

- The base is homed only in this loop, not in the pre-pass.
- An axis whose `Type` is 0 or `Velocity` is 0 is skipped with no motion and no `48`. No ER-4u axis is set that way.

`0x1002326a` starts each axis with `4F <mask> 53` (Mode S) and `4C <mask>` (Slave Cmd), first for the coupled motors and then for the axis (`0x10007cf3`). It then calls the per-axis routine `0x1000826d`. In simulation it sends only `48` value 0.

**Last: the end-of-group sweep (`0x1002407f` with `'A'`).**

1. Wait for the queue to drain, `47`, wait again.
2. Copy the measured positions into the setpoint table.
3. If the controller version is below 9, send `4F 3F 54` (Mode T, robot).
4. For each axis 0-5, gripper included: set its setpoint to 0, `48 <1<<n>` value 0, drain, `47`, `4F <1<<n> 53`, drain.
5. `73 20`, `42 20 00 00`, so **the gripper motor is left off after homing**.
6. Log "End of robot homing" and store a position named `0` in group A (`Here`).

On success `Home` returns 1. The arm motors are still on from the opening `Control('&', 1)`; only the gripper motor ends off. On abort it calls `Control('&', 1)` and returns 0.

**What "wait N cycles" means (I, from `0x1004004b` and `0x1004008e`).** The DLL waits until the echoed message ID has moved N past the last echoed ID. It polls every 5 ms and gives up after 1000 polls (about 5 s), but returns success either way. One cycle is one reply: 24 ms with the shipped INI (VENDOR_DLL_PROTOCOL.md section 11.6). "Drain" (`0x100401bb`) waits the same way until the last queued message is echoed.

The version is the 32-bit result of `72`, stored at `link+0xC0` from reply bytes 11-14 (V, `0x1003ce38`, read by `GetVersion`). Our arm's version is unknown: the 2026-09-29 log does not record it.

## 2. Parameter download at the start of `Home`

It runs only online, and **after** `Control('&', 1)`, so the servo parameters are rewritten while the motors are energised (V). That is a new context for bytes the legacy code sends only at connect. Treat it as a safety point.

| # | Message (bytes from offset 4) | Count | Builder | Layout |
|---|---|---|---|---|
| 1 | `72` Get Version | 3 | `0x100405f9` | command only |
| 2 | `64` Get digital output | 3 | `0x1003fac0` | command only |
| 3 | `61` Get analog output | 3 | `0x1003fb6a` | command only |
| 4 | `53 00 08 00` + value | 1 | `0x1003f372` | byte 5 = 0 (no axis); bytes 6-7 = 8; bytes 8-11 = `PCPeriod` (16), int32 little-endian |
| 5 | `53 <1<<n> <idx> 00` + value | 10 per axis, axes n = 0..`NoOfAxis`-1 (8), so 80 | `0x10005476` loops over `0x100052f0` | byte 5 = axis bit; byte 6 = parameter index; byte 7 = 0; bytes 8-11 = int32 little-endian |
| 6 | wait for the queue to drain | | `0x100401bb` | |

Every message also carries the DLL's setpoint table in bytes 12-43, as all vendor messages do (VENDOR_DLL_PROTOCOL.md section 3).

Per-axis parameter index, the INI key it comes from, and its builder. The keys come from the axis-file loader `0x100043b2`; the builders are `0x1003f10a` through `0x1003f3fa`. All V:

| Index | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|
| Key | `VelocityLimit` | `AccelLimit` | `PropGain` | `DifferGain` | `IntegralGain` | `FeedForward` | `TorqueLimit` | `Bias` + ROB `BiasAxis_n` | `TorquePWM` | `ImpactDetect` |

Index 8 is never sent per axis; it is the global `PCPeriod`.

**Against the legacy code.** `libsync.send_pkt2` sends the same block:

- `72` twice (the vendor sends it three times), then `64` and `61` three times each;
- `53 00 08 00 10`;
- 80 `53` messages, where `countByte7` counts 0-10 and skips 8, and the mask doubles every ten;
- then a `0D` with byte 2 = `0C`, which the vendor does not send here.

For axes 0-5 the 60 legacy values equal the vendor's from the `$Default` INIs exactly. This was checked by script against `libhex.get_msg2` (S: two independent sources). Axes 6-7 come from `NOC.INI`, which is not available, so they were not compared. The legacy values are 61440, 2048, 15000, 10000, 1000, 0, 1000, 0, 1, 1000000.

I: `ROB_4u.INI` spells two keys `BiastAxis_3` and `BiastAxis_8`, so a loader reading `BiasAxis_n` would read 0 for them, which fits the legacy bias of 0.

**Bytes:** `72`, `64`, `61` and `53` (with these masks and indices) are all already sent by the legacy code. None of the reset handshake (`5A`, `4F FF 54`, `73 FF`, `42 FF 00 00`) is resent.

The homing settings (`Type`, `Velocity`, `SwitchState`, `SwitchMask`, `MaxTime`, ...) are **not** downloaded. `SwitchState` and `SwitchMask` are loaded (record offsets `0x148` and `0x14A`) and written by `SetParameter`, but nothing else in the 2018 dump reads them (V, by grep of the addresses and offsets).

## 3. The search motion

**Where the parameters come from.** Axis record base `0x1008a290`, stride `0xA8`, loaded by `0x100043b2` (V). `[Homing]` fields:

| Key | Offset | Value used |
|---|---|---|
| `Type` | `+0x144` | short |
| `Velocity` | `+0x146` | short |
| `SwitchState` | `+0x148` | short |
| `SwitchMask` | `+0x14A` | short |
| `ImpactCondEnc` | `+0x14C` | int |
| `ImpactCondTicks` | `+0x150` | int |
| `MaxTime` | `+0x154` | int |
| `MaxDistance` | `+0x158` | int |
| `Offset` | `+0x15C` | int |

`NoEnc90` is at `+0xDC`. `HomingSeq` is at `+0x6B0` and `[Gearing]` at `+0x6C0`; both are shorts, and each INI key is `prefix + (i+1)`, read from the instructions at `0x10003ce5`. The DLL copies Gearing 1, 3 and 4 to `0x1008aba8`, `0x1008abac` and `0x1008abae` (`0x1002f69e`).

| Key | Base | Shoulder | Elbow | Wrist m1 | Wrist m2 | Gripper |
|---|---|---|---|---|---|---|
| `Type` | 14 | 16 | 14 | 15 | 16 | 17 |
| `Velocity` | 110 | 90 | 100 | 90 | -90 | -165 |
| `ImpactCondEnc` / `Ticks` | 2 / 5 | 2 / 10 | 2 / 10 | 2 / 2 | 5 / 10 | 2 / 2 |
| `MaxTime` (I: ms) | 110000 | 60000 | 90000 | 55000 | 75000 | 15 |
| `MaxDistance` | 99999 | 30000 | 10000 | 10000 | 10000 | 10000 |
| `Offset` | 0 | -190 | 45 | 850 | -690 | 0 |
| `SwitchState` / `SwitchMask` | 1 / 4 | 1 / 4 | 1 / 4 | 1 / 4 | 1 / 4 | 1 / 4 (unused) |

**How the search is driven.** This is not the PC planner (V).

- The DLL puts the axis in **Mode T**: `4F <mask> 54`, sent to the axis and its coupled motors by `0x100079cd`.
- It then sends one **`4D` Move** with the value `Velocity` (`0x100072c9`, builder `0x1003f89c`).
- After the first pass it uses **Mode H** (`4F <mask> 48`, axis only) followed by `4D`.
- The controller moves the axis. The PC only polls the reply.
- To stop, it sends `47` and then `4D` value 0 to the axis and its coupled motors.

There is no ramp on the PC side and no `0D` setpoint stream for the search. Any acceleration is the controller's own. I: from the downloaded `AccelLimit` 2048 and `VelocityLimit` 61440, units unknown.

The `4D` layout (V, `0x1003f89c`):

| Byte | Content |
|---|---|
| 4 | `4D` |
| 5 | `1 << axis` |
| 6-7 | 0 |
| 8-11 | the signed value, int32 little-endian |
| 12-43 | the setpoint table, as in every message |

The same builder serves `MoveTorque` and the gripper. Units of `Velocity` are not found. I: a controller-side speed or drive command, not counts per cycle.

**Coupled motors get scaled values (V, `0x100072c9`; division truncates toward zero).**

| Homed axis drives V | Elbow | Wrist m1 | Wrist m2 |
|---|---|---|---|
| Shoulder | -V/2 | -V/4 | +V/4 |
| Elbow | | +V/2 | -V/2 |
| Wrist m1 | | | -V |
| Wrist m2 | | +V | |

So homing m1 is a pitch motion (motors opposite) and homing m2 is a roll motion (motors together). Why the shoulder and elbow fractions are halves and quarters, when the position moves below use whole and half counts, was not resolved.

**Search per Type (V, `0x1000826d`).** "Wait on" and "wait off" mean waiting for the axis's bit in reply byte 5 to be set or clear.

- **Type 14** (base, elbow):
  1. Mode T, drive +V, wait on, then keep going and wait off. The search continues **through** the switch.
  2. Stop. Mode H, drive -V, wait on.
  3. Mode T, drive -V, wait off.
  4. Stop. Mode H, drive +V, wait on. Stop.
- **Type 15** (wrist m1): Mode T, drive +V, wait on, then wait off. Stop. Mode H, drive -V, wait on. Stop.
- **Type 16** (shoulder, wrist m2):
  1. Mode T. If already on the switch, drive +V and wait off.
  2. Mode H, drive -V, wait on. Stop.

  This type has no stall or distance check.
- **Type 17** (gripper): open, wait for the gripper routine to report done, wait 15 cycles, close, wait, wait 20. What ends a gripper move (`0x10023e7e`) was not traced.
- Types 10, 18 and 19 exist (other robots) and are not used on an ER-4u.

**Stall and distance reversal (types 14 and 15).** On each pass the search loop does two checks:

- If the position changed by less than `ImpactCondEnc` since the last check, a counter goes up and the loop waits `ImpactCondTicks` cycles. Otherwise the counter resets.
- If the counter goes past 4 (type 14) or 1 (type 15), or the axis has moved more than `MaxDistance` from its start, the routine skips the rest of the forward pass and goes straight to the reverse step.

I: hitting a hard stop or running too far without finding the switch makes it search the other way.

**Stops (V).**

- `MaxTime` from the start of the axis, covering coarse, fine and offset moves: error **561**. The time comes from `QueryPerformanceCounter` and `GetTickCount` (`0x10007e6f`); I: milliseconds.
- Emergency bit (reply byte 2 bit 0): error **300**.
- Homing flag cleared (abort): error **569** (`0x239`). Its name is not found in the repo's error lists.
- Impact (reply byte 59) during the fine search and the offset move: error **201**.

On a stop during the coarse search the DLL sends `4D` value 0 to the axis and its coupled motors. Error **563** ("home switch not found") is never raised by these routines (not found).

**Fine edge search (V, `0x1000ad3c`).** It runs after the coarse search in the normal loop; the pre-pass switches it off. It starts by holding the setpoints at the measured position and sending `47` and `4F FF 53`, so all axes are in Mode S and follow the PC.

1. **Back off until off.** Steps of `max(10, |NoEnc90| / 450)` counts, with the first step three times as large: base 28 (first 84), shoulder and elbow 22 (66), wrist motors 10 (30). Each step is a planner point move (`0x10009f52`).
2. **Creep until on.** Steps of 2 counts, or 1 on wrist motors 3 and 4. Each step is written straight into the setpoint table at `0x10077768` (`0x1000a75c`); there is no planner. After each it waits for the queue to drain and 3 cycles, then sends `47`.

The direction depends on Type. For type 14 with V > 0, back off goes + and creep goes -. For types 15 and 16 with V > 0, back off goes - and creep goes +. For V < 0 (wrist m2) both are reversed.

The DLL logs "Home switch is Off" after step 1, which waits for the bit to be clear.

**Pacing (V, `0x1003ce38`).** While the homing flag is set (and manual mode is off), the queue limit is `HomingBuffers` = 20 instead of `Buffers` = 45. It governs every message sent during homing; it matters most for the `0D` stream of the planner moves.

**Direction in counts, and in the toolbox convention (I).** Whether a positive `4D` value moves the counts up or down is not in the code. Three separate checks agree that **a positive drive moves the counts down**:

1. With that sign, every axis's final coarse approach is in the same direction as the legacy search (`setHome.py`): base -, shoulder +, elbow -, pitch m1 + and m2 -, roll both -. With the other sign, all five would be reversed.
2. With that sign, the fine search backs out of the switch and creeps back to the same edge the coarse search found, on every Type. With the other sign it would cross the whole switch region to the far edge, which would make the coarse back-and-forth pointless.
3. The gripper opens with drive -165 and closes with +165. It is zeroed closed, and its count limits are -200 to 6000, so opening raises the counts.

Converted with `scorbot/vendor_model.py`, the final approach is:

| Axis | DLL convention | Toolbox (BSEPR) convention |
|---|---|---|
| Base | angle + | + |
| Shoulder motor (with its coupled moves) | angle - | + |
| Elbow motor | - | + |
| Pitch | + | - |
| Roll | - | - |

## 4. Switch polarity (`0x10007f34`)

The wait loops until the axis's bit in the cached reply byte 5 matches the requested state (V). The bit is at `link+0xB8`, which the communication thread copies from reply byte 5 on every reply.

- Helper `0x1000b290` waits for **set**.
- Helper `0x1000b2c0` waits for **clear**.

Every search phase that "finds the switch" loops until the bit is set, and every "leave the switch" phase waits for clear. The DLL logs "Home switch is Off" after a wait for clear.

So **a set bit means on the switch** (V for the DLL's own reading). This is the same as the legacy `libdef.get_switch`.

The SCORBASE manual's Movement Information screen also shows home-switch bits with 1 = pressed (`docs/manual/MANUAL_AND_PRIOR_ART_FINDINGS.md`; S for the display convention).

Confidence: high that the DLL treats set as on. Whether the controller can invert the bit before reporting it is not shown: `SwitchState` = 1 and `SwitchMask` = 4 are loaded but never used or downloaded by this DLL. On the arm the bit still needs one observation (the lab plan's switch check).

## 5. After the switch: offset, then `48`

The common tail of `0x1000826d` (V):

1. Wait 10 cycles.
2. Copy the measured positions into the setpoint table (hold), wait 1, send `47`.
3. If `Offset` is not 0: `4F FF 53`, then a planner point move (`0x10009f52`) to the measured position plus `Offset`, then an impact check.
4. If the version is below 9: `4F <mask> 54` on the axis and its coupled motors.
5. **`48` Set position, value 0** (`0x1000758e`), to the coupled motors first and then the axis:

   | Homed axis | Motors zeroed, in order |
   |---|---|
   | Shoulder | elbow, wrist m1, wrist m2, shoulder |
   | Elbow | wrist m1, wrist m2, elbow |
   | Wrist m1 | wrist m2, wrist m1 |
   | Wrist m2 | wrist m1, wrist m2 |
   | Base | base |

   If the version is above 8, each `48` is followed by setting that setpoint to 0, drain, `47`, drain.
6. Hold the setpoints again, wait 2, send **`4F FF 53`** (Mode S, all), check for impact, and log "End of the homing (Axis %d)".

**The offset move** (V, `0x10009f52` into the move set-up `0x1000d164`):

- an absolute target in counts, built from the measured position;
- in speed mode (flag bit 0 clear);
- speed factor **0.2** (double `0x3FC999999999999A`), below the 0.307 floor that `Speed()` can set;
- `AccelA` 0.3 and `AccelAccelA` 0.3.

The PC planner streams it as `0D` setpoints. The coupled motors get their own targets (V, Gearing 1 = 1, 3 = -1, 4 = 1), converted with `vendor_model` (I for the angles):

| Offset on | Count vector (base, sh, el, m1, m2) | DLL joint change |
|---|---|---|
| Shoulder -190 | 0, -190, +190, +95, -95 | shoulder +1.67, pitch +1.73 deg |
| Elbow +45 | 0, 0, +45, +22, -22 | elbow +0.40, pitch +0.39 deg |
| Wrist m1 +850 | 0, 0, 0, +850, -850 | pitch +30.5 deg |
| Wrist m2 -690 | 0, 0, 0, -690, -690 | roll -24.7 deg |
| Pre-pass m1 +1050 | 0, 0, 0, +1050, -1050 | pitch +37.6 deg |

So home, and the zero of every counter, is the switch edge **plus the offset**. That is why zero counts is the toolbox home pose (VENDOR_DLL_PROTOCOL.md section 10.3).

**The `48` message** (V, builder `0x1003f845`, dequeue `0x1003ee7e`):

| Byte | Content |
|---|---|
| 4 | `48` |
| 5 | `1 << axis` |
| 6-7 | 0 |
| 8-11 | the value (0), int32 little-endian |
| 12-43 | the setpoint table |

The builder also writes the value into the axis's own slot. But the dequeue overwrites every slot from the setpoint table (instruction at `0x1003ef49`), so that slot carries the DLL's setpoint for the axis when the message leaves:

- In the end-of-group sweep the setpoint is set to 0 first, so the slot is 0.
- In the per-axis call (version above 8) it is set to 0 only after queuing. I: the slot may be 0 or the old setpoint.

The value 0 is reliably carried only in bytes 8-11. For a `48` message the dequeue also copies the slot back into the table for the masked axes, which is a no-op.

## 6. The 2008 build

**Same (V).** These functions are identical after replacing addresses (0 differing lines in normalised decompiled text):

| 2018 | 2008 | Role |
|---|---|---|
| `0x1000826d` | `0x10007fdd` | per-axis routine |
| `0x10023430` | `0x1002286b` | robot group |
| `0x1000808b` | `0x10007dfb` | ER-4u pre-pass |
| `0x10007ff4` | `0x10007d64` | pre-pass helper |
| `0x1002326a` | `0x100226a5` | per-axis wrapper |
| `0x10007cf3` | `0x10007a63` | Mode S + `4C` |
| `0x100072c9` | `0x10007039` | `4D` drive |
| `0x100079cd` | `0x1000773d` | Mode per axis |
| `0x10007e6f` | `0x10007bdf` | timeout |
| `0x1000ad3c` | `0x1000aaac` | fine edge search |
| `0x10009f52` | `0x10009cc2` | planner step / offset move |
| `0x1000a75c` | `0x1000a4cc` | creep step |
| `0x1000758e` | `0x100072fe` | `48` with coupling |
| `0x1003f4e9` | `0x1003e0b2` | Mode builder |
| `0x1003f89c` | `0x1003e465` | `4D` builder |
| `0x1000b200` | `0x1000af70` | switch bit |

The end-of-group sweep (`0x100234ba`) decompiles to a different switch shape, but it sends the same messages: the version check, then for each axis `48`, `47` and Mode S, then the gripper-off pair.

**Different (V).** The 2008 `Home` (`0x100187f5`) goes from `Control('&', 1)` straight to the group branches. It **does not reload the INI files and does not resend the parameters**. The download at home is a 2018 addition, like the extra download at connect (VENDOR_DLL_PROTOCOL.md section 7).

## 7. Against the legacy homing

| | Legacy (`setHome.py`, PROTOCOL.md section 9) | Vendor |
|---|---|---|
| Order | shoulder, elbow, pitch, roll, base | ER-4u pre-pass (shoulder, elbow, gripper close, roll, pitch, pitch +1050), then shoulder, elbow, roll, pitch, base, gripper |
| Already on the switch | skip the axis (the wrist's offset phase is skipped with it) | type 16 drives off it first; types 14 and 15 carry on and wait for off |
| Direction, counts | base -, shoulder +, elbow -, pitch m1 +/m2 -, roll both - | the same for the final approach (I, section 3) |
| Speed | 0D setpoint ramp: shoulder and wrists 10 counts per message, base and elbow 20; about 500 and 1000 counts/s at 20 ms per message | controller-driven `4D` with `Velocity` 90-110, units unknown |
| Edge | runs 12 messages past the switch; never sees the release | back off in coarse steps until off, creep 1-2 counts until on |
| Offset | none for the shoulder, elbow and base; the wrist runs 720 counts per motor past the first contact (comparison below) | -190, +45, +850, -690 counts, with coupled motors |
| Counter | never set | `48` value 0 per axis, and again for all six at the end |
| Limits | 30 s per axis, error word 40, cancel | `MaxTime` 55-110 s per axis, emergency, impact, stall or distance reversal |
| End | `closeMov` per axis (`4F 3F 53`, `73 20`, `42 20`) | `4F FF 53` per axis; at the end per-axis Mode S, then `73 20`, `42 20` |

### Wrist: legacy against vendor, compared line by line (2026-10-08)

Read from `openScorbot/setHome.py` (pitch at lines 272-370, roll at 413-505) and `openScorbot/conf.py` (wrist `h_vel` 10), against the table at the top of this page. Step sizes use the legacy `libdef.incremento`; nothing was run on the arm.

| | Legacy | Vendor | Verdict |
|---|---|---|---|
| Pitch direction | m1 `suma` (+), m2 `resta` (-): the direction of jog order 11 | final approach m1 +, m2 - (I: the sign of the `4D` value against the counts was not seen) | **Match.** This resolves BACKLOG 4: "opposite of jog order 10" only says homing uses order 11's direction, which is also the vendor's |
| Roll direction | both `resta` (-,-) | both - (I) | **Match** |
| Order | pitch, then roll | roll, then pitch (after the ER-4u pre-pass) | Differs; only matters for which wrist switch is crossed first |
| Travel past the first switch contact | 60 offset messages + 12 braking messages, each 10 counts (the step does not ramp: `cont_vel` is held at 87, then 88), so **720 counts per wrist motor** | offset from the edge: pitch +850, roll -690 | Same direction. Against the vendor offset the legacy pitch ends **130 counts (4.7 degrees) short** (25.8 against 30.5 degrees) and the roll **30 counts (1.1 degrees) long** (25.8 against 24.7). The two homes are not the same pose, even before the switch width and the contact edge differ |
| Wrist switch already pressed at the start | the whole axis is skipped, offset included | drives off it first, then approaches | **Differs, and matters:** a legacy home that starts with a wrist switch pressed leaves that wrist wherever it was. Start with both wrist switches off |
| Other joints | 12 braking messages only (no offset phase) | offsets -190, +45 | The shoulder, elbow and base have no legacy offset at all |
| Count scale | homing is in raw counts | raw counts | The legacy pitch scale (33.8 counts per degree against the vendor 27.9) does not enter homing. It only mislabels legacy pitch jog degrees by 21 percent; the coupled moves work in the vendor's counts and are unaffected |

What this does not settle: the sign of `4D` against the counts (vendor side), the switch width, and which edge the legacy first contact is. Those need the arm or a capture. Before this comparison the table above said the legacy code has no offset; it does, for the wrist only.

Only the vendor sends:

- the parameter download;
- per-axis `4F <m> 53` and `4C <m>`;
- single-axis Mode T;
- `4D`;
- Mode H;
- `48`;
- the offset moves.

Only the legacy code sends:

- an `0D` stream for the search itself;
- the 12 overshoot messages and the 20 idle messages between axes.

**Bytes a full vendor-style home would need, classified under the packet rule (root `CLAUDE.md`):**

| Bytes | Class |
|---|---|
| `47`, `0D` setpoints, `4F FF 53`, `4F 3F 53`, `73 20`, `42 20 00 00`, `72`, `64`, `61`, `53` (connect masks and indices) | Already sent by the legacy code |
| `4C 20` | Already in the legacy code (gripper); not yet sent to the arm by this project |
| `4D` Move | Needs a capture |
| `48` Set position | Needs a capture |
| `4F <m> 48` (sub-mode H) | Argument the legacy code never sends: needs a capture (owner's call) |
| `4F <single axis> 54`, `4F 3F 54`, `4F <single axis> 53` | Mask or argument the legacy code never sends (it sends `4F FF 54` and `4F 3F/FF 53` only): owner's call, capture first |
| `4C <non-gripper mask>` | Mask never sent: owner's call, capture first |

## Corrections to existing docs

These are recorded here only; the other files were not edited.

- **VENDOR_DLL_PROTOCOL.md section 12.1, the gripper drive value.**
  - The value is the gripper's `[Homing] Velocity` (axis record `+0x146`; -165 in `ER4Ax6.ini`).
  - `0x1000d943` sends that value to open and its negative to close, so with the shipped file **open sends -165 and close sends +165**. The doc says "positive to open".
  - This also answers its "which INI key, not traced" (V).
- **Section 12.1, the `0x29` reading.**
  - `0x29` is the ER-4u `SystemType` (`0x10002a38`), not "a servo gripper configured".
  - `OpenGripper` calls `JawMetric` with `RangeMM` (`0x1008a960` = record `+0x6D0`, 70 mm) when `SystemType` is `0x29` and `0x10023b1e` is true. I: an ER-4u opens by a 70 mm jaw move in that case, and uses the torque drive otherwise and during homing.
  - What `0x10023b1e` tests was not traced. Needs a follow-up.
- **Sections 5 and 13.** "Home downloads the parameters again" holds for the 2018 build only.

## What a vendor-style Python home needs

The vendor's edge finding and back-off use only legacy bytes: `47`, `4F FF 53` and `0D` setpoints. So a home can follow them without a capture.

1. The vendor order, with or without the ER-4u pre-pass.
2. The coarse search as the legacy code does it (`0D` streaming), in the directions above, which match the legacy ones. Add the vendor's timeouts. Stall and `MaxDistance` reversal are optional.
3. The fine edge search, done with setpoints: back off in steps of `max(10, |NoEnc90|/450)` counts (first step three times as large) until the bit is clear, then creep 2 counts (1 on the wrist motors) until it is set. In the vendor code both kinds of step move the coupled motors too, by the same rules as the offset vectors in section 5.
4. The offset move as a slow joint move to the count vectors in section 5, coupled motors included.
5. No `48`. Record the counts at the end as the session home, which the SDK already does. Section 10 of VENDOR_DLL_PROTOCOL.md maps those counts to angles if the home is taken as the vendor zero.

Safety points before any of this runs on the arm:

- The vendor's wrist offsets are about 25-38 degrees and the shoulder offset moves three motors. All of them exceed the SDK's travel cap (`limits.TRAVEL_CAP_DEG`, 10 degrees), so the owner must decide on them.
- The fine search moves the arm with no speed limit other than the planner's step size.
- The 2018 vendor rewrites servo parameters with the motors on; a Python home should not.

## What needs a capture or the lab

- A SCORBASE home with USBPcap (S1 card) to see:
  - `4D` and its value units;
  - Mode H;
  - `48` and the slot contents;
  - whether the 2018-style parameter download happens;
  - the controller version that the reply to `72` returns, which decides the version-8 branches.
- The sign of a `4D` drive against the counts (I above): a capture during homing shows it directly.
- The switch bit's polarity on our controller: watch byte 5 while pressing one switch by hand with the motors off.
- Where `SystemType` lives in the lab's installation (`USBC.INI` was not available), and so whether the ER-4u pre-pass runs.
- Not traced in the dump:
  - what ends a gripper move;
  - what clears the homing flag from outside;
  - the peripheral check's string compare;
  - the unit of `Velocity`;
  - why the coupled drive fractions differ from the coupled position steps.

## Confirmed against the lab arm's own parameter files (2026-10-06)

The ER-4U lab PC's SCORBASE install (`C:\Intelitek\SCORBASE\BIN\Par\er4u\`, read by a script from a pasted dump; the files themselves are not in the repo) agrees with the toolbox values this trace used:

- All 54 `[Homing]` numbers in the summary table (`Type`, `Velocity`, `ImpactCondEnc`/`Ticks`, `MaxTime`, `MaxDistance`, `Offset`, `SwitchState`, `SwitchMask`, six axes): **no difference**.
- `ROB_4u.INI`: `HomingSeq` 2, 3, 5, 4, 1, 6 and `Gearing` 1, -1, -1, 1; `[Limits]` and `[Geometry]` as quoted elsewhere in these docs.
- Axis files: `NoEnc90` (-12770, -10216, 10216, 2511, 2511) and `EncLimit` as in VENDOR_DLL_PROTOCOL.md.
- `ER4CONF.INI` sets `CurrentParFolder = ER4u\$Default`; `$CURRENT` equals `$Default` for all six axis files.
- `SystemType` is in none of `ER4CONF.INI`, `USBC.INI` or the axis files, so the DLL's default (`0x29`, ER-4u) applies and the pre-pass in section 1 probably runs (I).

**Not matched:** the lab's `USBC.dll` (SHA-256 `6118465D2AB2AAA33D72BB85FDF63DB9B9CB601DE7185B1FEEEBBA97F7BBD821`, about 576 KB) is a third build, different from the two analysed here (2018: 539,784 bytes; 2008: 471,040 bytes). The parameter values are the same and the homing helpers were identical in the two builds read, so the behaviour is expected to match; re-running this trace on the lab DLL would confirm it.
