# Vendor protocol, read from `USBC.dll`

What Intelitek's own DLL sends to and reads from the Controller-USB, worked out by static decompilation. Companion to [PROTOCOL.md](PROTOCOL.md), which describes what our legacy code (`openScorbot/`) sends.

**Status: from disassembly, unverified.** The DLL was read, never loaded or run, and nothing here has been checked against a controller or a USB capture. It describes what the vendor software would send, not what the lab's controller accepts. Project rule (root `CLAUDE.md`, changed 2026-10-04): a sequence built only from command bytes the legacy code already sends may be changed on the strength of this document, and is tried first on a 1 degree jog. Bytes the legacy code never sends still need a capture.

Labels: **V** = read directly from the vendor code. **I** = our inference from it.

## 1. Sources and method

| Build | Where from | Size | SHA-256 |
|---|---|---|---|
| 2018 (built 2018-11-22) | `github.com/kutzer/ScorBotToolbox`, `ScorBotToolboxFunctions/USBC.dll` | 539,784 bytes | `6de887a8d6f2ce686d377586d4be8981ff643cad13c158cd4603f8b44f20555e` |
| 2008 (dated 2008-05-04) | `MTIS.zip` in `github.com/baijuch/sboter4u` | 471,040 bytes | `91e99a915c4f5e22c942b3e8dc757ac6b0f3610c01abdab0c2ea2ad0aa555240` |

Both are 32-bit and not packed. Decompiled with Ghidra 12.1.4 in headless mode; how to repeat it is in [tools/usbc_analysis/README.md](../../tools/usbc_analysis/README.md). Function addresses below are from the 2018 build. The lab's own `USBC.dll` has not been examined and may be a third build.

Not examined: Intelitek's kernel driver, which sits between the DLL and the cable. That it passes the 64 bytes through unchanged is I.

The vendor binaries and the decompiled output are never committed (they are Intelitek's code). They live outside the repo.

## 2. Transport and pacing

- One communication thread (`0x1003ce38`) opens the device named by `DriverLink` in `USBC.INI` with `CreateFileA`, then loops: `WriteFile` of exactly 64 bytes, a wait, `ReadFile` of 64 bytes (V). One OUT, then one IN.
- The thread only sends messages from a queue. When the queue is empty it sends a `0D` message carrying the current setpoints (V).
- Pacing uses the reply: the DLL compares `sent ID - echoed ID` with a limit taken from `Buffers` (45), `HomingBuffers` (20), `ManualBuffers` (4) or `TPBuffers` (6) depending on what it is doing. Below the limit it waits 1 ms before the next message; otherwise about half of `PCPeriod` (V for the code, I for the reading: the difference is the number of messages still queued inside the controller, and the DLL keeps that queue topped up).

## 3. OUT message (64 bytes)

Zeroed by `0x1003e7fc`, numbered when queued (`0x1003ed99`), setpoints filled in when dequeued (`0x1003ee7e`). All V.

| Offset | Length | Field | Legacy code (PROTOCOL.md section 2) |
|---|---|---|---|
| 0 | 1 | Message ID, +1 per queued message | The sequence byte |
| 1 | 1 | Zero | Zero |
| 2 | 1 | Zero, or one byte taken from a small queue in the link object when the message is a setpoint `0D` (`0x1003fdf3`). What fills that queue was not traced | Zero, except `get_msg2(84)`, a `0D` message where it is `0C` |
| 3 | 1 | Same, from a second queue | Zero |
| 4 | 1 | Command, an ASCII letter (section 4) | "Command byte at offset 4" |
| 5 | 1 | Axis bitmask, bit N = axis N | The `3F`, `20`, `FF` values |
| 6-7 | 2 | 16-bit argument (on/off flag, sub-mode, parameter index) | e.g. the `01` in `42 FF 01` |
| 8-11 | 4 | 32-bit argument (parameter value, move value) | The 3 data bytes of the parameter table |
| 12-43 | 32 | One signed 32-bit little-endian value per axis, 8 axes. Written into **every** message from the DLL's setpoint table | The encoder/setpoint region at 12-35 (6 axes). The legacy "value + `0000`/`FFFF` sign word" is the same thing as a signed 32-bit integer |
| 44-63 | 20 | Zero | Zero |

ID wrap is not resolved: the increment is a plain byte +1 (which would wrap 255 -> 0), but the DLL's ID-difference arithmetic uses modulus 255 in two places and 256 in one, and its "wait until this ID is echoed" helpers (`0x1004004b`, `0x100401bb`) also wrap with modulus 255. The legacy code wraps 255 -> 1 and never sends 0.

Axis bitmask from a group code (`0x1003fc7b`, with `ER4CONF.INI`: `Gripper = 5`, `First_B = 6`, `First_C = 8`):

| Group | Axes | Mask |
|---|---|---|
| `'A'` robot | 0-5 (five arm motors and the gripper) | `3F` |
| `'B'` peripherals | 6-7 | `C0` |
| `'C'` | none on this configuration | `00` (message not sent) |
| `'&'` all | 0-7 | `FF` |
| single axis N | N | `1 << N`; the gripper is `20` |

## 4. Command letters

Names are the DLL's own log strings (`0x100401f7`), identical in both builds. V.

| Byte | DLL's name | Arguments | In the legacy tables |
|---|---|---|---|
| `0D` | No operation | Carries the setpoints | The idle / setpoint message |
| `47` G | Clear communication buffer | none | `openMov`, homing axis start |
| `4F` O | Mode | mask; byte 6 = `50` P, `54` T, `53` S or `48` H. The DLL logs "SLAVE MODE" when it sends `53` | `4F 3F 53`, `4F FF 53`, `4F FF 54` |
| `73` s | Control Off | mask | `73 20`, `73 FF` |
| `42` B | Turn motors | mask; bytes 6-7 = 1 on, 0 off | `42 FF 01`, `42 20`, `42 FF` |
| `54` T | Stop | mask | never sent |
| `5A` Z | Reset | none | handshake pkt1 |
| `53` S | Set parameter | mask; bytes 6-7 = parameter index; bytes 8-11 = value | handshake pkt2 |
| `72` r | Get Version | none | handshake pkt2 |
| `64` d | Get digital output | none | handshake, `motorsoff` |
| `61` a | Get analog output | none | handshake, `motorsoff` |
| `4C` L | Slave Cmd | mask | gripper sequence `4C 20 00` |
| `4D` M | Move | mask; bytes 8-11 = value | not sent |
| `48` H | Set position | mask; bytes 8-11 and the axis's own slot = value | not sent |
| `57` W | Get PWM | | not sent |
| `44` D, `41` A | Set digital output, Set analog output | | not sent |
| `75` u | Stop USB test | | not sent |
| `4B`, `46` | Built by the DLL, not named in its log table, no caller found | | not sent |

Every command byte the legacy code sends is in this table, with the same axis masks. This is the first independent support the legacy packets have had (I).

## 5. Sequences

Message bytes are shown from offset 4. Each list is the messages built by the builder calls read in that function (V). The small helper calls between them were not all read and could queue more, so treat each list as "at least these, in this order".

**Connect** (`Initialization(mode, ...)`, `0x10014eff`). What it sends depends on the mode argument:

- Mode 1: the reset handshake below, which ends with the motors **off**. The USNA shim uses this mode: `RInitialize` calls `Initialization(1, 0x29, ok, error)` (V, from `RobotDll.dll`), and Kutzer's toolbox then calls `RControl` separately to turn control on.
- Mode 0 (the default; online on the first call): no reset handshake. It calls the same routine as `Control('&', 1)`, so it turns the motors **on**, then reports success.
- Mode 2 is the DLL-only simulation. Mode 3 was not traced.

Reset handshake (`0x10005736`, mode 1):

1. `5A` Reset
2. `4F FF 54` Mode T, all axes
3. `73 FF` Control Off, all axes
4. `42 FF 00 00` Turn motors **off**, all axes
5. Wait for the queue to drain (up to 50 x 25 ms)
6. `72`, `64`, `61` (version, digital outputs, analog outputs)
7. `53 00 08 00` + a 32-bit value: Set parameter 8, no axis (V). I: the value is `PCPeriod`. The same variable is used as the DLL's cycle time, and with `PCPeriod = 16` the message is `53 00 08 00 10`, exactly the legacy `get_msg2(81)`
8. The per-axis parameter download (`53` with one axis bit and a parameter index each). In the legacy table this is the 79-message block where "b6" doubles every ten messages: b6 is the axis bit, b7 the parameter index (I, from the matching layout)

Steps 1-4 are the legacy pkt1, byte for byte. Steps 6-8 are the legacy pkt2.

So whether a vendor connect energises the arm depends on the mode. The legacy handshake behaves like mode 1 followed immediately by control on: pkt1 and pkt2 are the reset handshake, and pkt3 appends the motors-on sequence, which is why our `connect()` energises the arm.

In online mode the DLL's monitor loop (`0x10019f5f`) watches the emergency bit: when it turns on, the DLL calls `Control('&', 0)` and raises error 300; when it clears, error 301 (V; these are "emergency on/off" in Kutzer's error list).

**Control on, all axes** (`Control('&', 1)`, `0x100226c4`): `47`, `4F FF 53`, `42 FF 01 00`, then `73 20`, `42 20 00 00`. The legacy `motorson` is `0D 47 0D 4FFF53 42FF01 7320 4220`: the same commands in the same order with idle messages between.

**Control off, all axes** (`Control('&', 0)`): `47`, `73 FF`, `42 FF 00 00`, `4F FF 53`, `73 FF`, `73 20`, `42 20 00 00`, then `64`/`61` three times. The legacy `motorsoff` starts `0D 47 73FF 42FF 4FFF53 73FF 7320 4220`: the same commands in the same order. The trailing status reads differ in count and order.

**The `73 20`, `42 20` pair** (`0x10023050`) is "Control Off, then motor off, for the gripper axis". It ends the all-axes and robot-group paths: `Control('&', ...)`, `Control('A', ...)` and `Stop('A')`. The peripheral, group C and single-axis branches of `Control`, and `Stop('B')` and `Stop('C')`, do not send it.

**Stop.** There is no single stop message for the arm.

- `Stop('A')` (`0x10021a47`): first the DLL copies its measured-position array over its setpoint array (`0x10021441`; I: "hold where you are", from which arrays the other code reads as position and as setpoint). Then `47` Clear communication buffer, `4F 3F 53` Mode S for the robot axes, then the `73 20`, `42 20` pair. The legacy `closeMov` is the last three of these (`4F 3F 53`, `73 20`, `42 20`) **without the leading `47`**. So `closeMov` is the vendor's end-of-move tail, not the vendor's stop: without `47` the setpoints already queued in the controller are still executed. I: the arm stops because the controller's queue of setpoints is thrown away; it then holds the last setpoint it has.
- `Stop('B')` (`0x10021b8b`): for each peripheral axis that is moving, `4D` Move with value 0 and `4F` Mode S for that axis.
- `Stop('C')` (`0x10021c3d`) is the only caller that sends `54`. With `First_C = 8` the group is empty, so on an ER-4u the `54` command is never sent.

**Velocity jog and direct setpoints** (`MoveManual`, `SetJoint`). Neither has a message of its own. `MoveManual(axis, percent)` scales a per-axis maximum speed (separate values for each direction) and hands it to a planner inside the DLL; `SetJoint` converts eight joint values and stores them as the planner's target. The planner runs on the PC and changes the setpoint table; the communication thread carries the table out in ordinary `0D` messages. `EnterManual` sends `47` first and switches the queue limit to `ManualBuffers`.

I: motion is setpoint streaming. Every move the vendor software makes is a stream of `0D` messages with changing per-axis targets, and what makes a jog responsive is keeping only a few (`ManualBuffers = 4`) queued in the controller.

**Homing** (`Home`, `0x10018e19`): calls `Control('&', 1)` first, downloads the parameters again, then homes axis by axis. The switch wait (`0x10007f34`) loops until the axis's bit in reply byte 5 reaches the wanted state, and stops on the emergency bit or on a timeout that raises error 561 ("homing time elapsed" in Kutzer's error list). There is one wait for the bit to come on and one for it to go off, which fits the manual's "move until the switch activates, then until it releases". The motion profile of the search itself was not traced.

## 6. IN message (64 bytes)

Read straight into the link object; the accessors are small functions near `0x1000b100`. V unless marked.

| Offset | Field | Legacy code (PROTOCOL.md section 3) |
|---|---|---|
| 0 | Echo of a message ID; used for pacing (section 2) | "Unknown, not read" |
| 1 | Echo of the command letter | "Handshake acknowledgment": `send_wait` waits for 13, which is the echo of `0D` |
| 2 | Bit 0 = emergency (`IsEmergency`, `0x1000b240`) | Unknown |
| 5 | Home switches, bit N = axis N (V). The homing wait loops until the bit equals a requested state; which state means "on the switch" is I | Home-switch bits, same bit order |
| 6 | Digital inputs | Unknown |
| 7-10 | Four analog inputs | Unknown |
| 11-14 | 32-bit result of the echoed command: `57` PWM, `61` analog output, `64` digital output, `72` version | Unknown |
| 19 + 5N, 3 bytes | Axis N position, 24-bit little-endian, count = raw - `0x7FFFFF` (`0x1000b160`) | Count (2 bytes) + "sign byte" |
| 22 + 5N, 2 bytes | Axis N position error, 16-bit (signedness I). Feeds the DLL's "Position error change" log and impact check | The "error word" |
| 59 | Non-zero = impact reported (`0x1000b270`; the DLL logs "Impact Axes") | Unknown |

There are 8 axis records, at 19-58. Bytes 3-4, 15-18 and 60-63 were not traced. Teach-mode state is not read from a fixed reply byte by `IsTeachMode`; where it comes from was not traced.

### Count format and the legacy decoder

With count = raw24 - `0x7FFFFF`, what the legacy code calls a "sign byte" is the top byte of a 24-bit number: `80` for small positive counts, `7F` for small negative ones.

| Top byte | Low word | Vendor count | Legacy count |
|---|---|---|---|
| `7F` | L | L - 65535 | L - 65535 |
| `80` | L | L + 1 | L |

So the legacy decoder is exact on the negative side and one count low on the positive side, which amounts to having two zeros. That is where the 65535 modulus in `scorbot.calibration.signed_count_delta` comes from (I). It also means counts beyond +/-65535 are legal for the controller (top byte `81`, `7E`, ...), where `scorbot/state.py:decode_state` raises on any sign byte other than 127/128.

Real data, consistent but not proof: the 2026-09-29 idle capture from the arm (`docs/evidence/`), taken after power-on and before homing, has the six joints at raw `0x7FFFFE`, `0x7FFFFF`, `0x7FFFFF`, `0x7FFFFF`, `0x7FFFFE` and `0x800001` in all 20 samples. A controller that has not moved since power-on reading within 2 counts of `0x7FFFFF` is what a zero at `0x7FFFFF` predicts. The legacy reading fits the same numbers, so this does not separate the two.

How each claim here gets confirmed on the controller: [VENDOR_PROTOCOL_LAB_PLAN.md](../lab/VENDOR_PROTOCOL_LAB_PLAN.md).

## 7. The two builds compared

Identical in the 2008 and 2018 builds (same decompiled body after removing addresses): message zeroing, the command-name table, the group-to-mask function, the builders for Mode, Control Off / Stop and Turn motors, the gripper pair, `Control`, the count decoder, the emergency and impact accessors, the home-switch wait, and the dequeue-and-fill-setpoints function. The communication thread has the same 64-byte sizes and the same reply cases.

Different: the 2018 connect sequence repeats the parameter download once more at the end. `Stop` differs only in how one helper is called.

I: the message format was stable for at least those ten years, which makes it more likely that the lab's build matches.

## 8. What this changes for us

Nothing in the code yet. Candidates, each needing a capture before it touches `openScorbot/`:

| Finding | Possible use |
|---|---|
| Reply byte 0 echoes the ID | Flow control for the phase C streaming driver; today the legacy code sends blind |
| Arm stop = `47` + `4F 3F 53`; the legacy `closeMov` lacks the `47` | A software stop that clears the controller's queue and can be sent mid-move. It would still not be an emergency stop |
| Motion = `0D` setpoint stream | Confirms the design assumption behind the Ruckig planner (PROTOCOL.md unknown 15) |
| Reply byte 2 bit 0 = emergency, byte 59 = impact | New fault sources that should latch the session |
| Count = raw24 - `0x7FFFFF` | A decoder without the double zero and without the +/-65535 limit |
| The vendor's mode 1 connect ends with motors off | A connect that does not energise the arm, by not sending the pkt3 motors-on tail |
| Parameter 8 = `PCPeriod` | The controller is told the host period at connect; ours says 16 ms while the legacy loop runs at 20 ms |

## 9. Open after this pass

- ID wrap (0 or 1 after 255). A capture of more than 256 messages settles it.
- The helper calls inside the connect, control and stop sequences, and `Initialization` mode 3.
- Which mode SCORBASE itself passes to `Initialization`.
- The homing search motion and the planner's sample timing.
- The meaning of Mode sub-codes P, T and H, and of `4C` Slave Cmd.
- OUT bytes 2 and 3 (what the two small queues carry).
- Reply bytes 3-4, 15-18, 60-63 and the teach-mode source.
- Whether the lab's `USBC.dll` matches either build.

## 10. Counts, joint angles and the wrist

Desk analysis, 2026-10-04 (plan: `docs/specs/2026-10-04-vendor-desk-analysis-plan.md`, questions C and B). Re-implemented in `scorbot/vendor_model.py`, tested in `tests/test_vendor_model.py`. **A prior from disassembly, not measured on our arm.**

Labels here: **V** read from the instructions; **E** the Python version agrees with the vendor function run in Ghidra's emulator; **S** agrees with a second source that never went through our emulator.

### 10.1 Parameters

Per axis, from Intelitek's `ER4AxN.ini` (loader `0x100043b2`) and `ROB_4u.INI` (loader `0x10003655`). Values are the `$Default` set shipped with the USNA toolbox.

| Axis (motor) | `NoEnc90`: counts per 90 degrees | Counts per degree | `HorizPos`: count where the DLL's angle is zero |
|---|---|---|---|
| 1 base | -12770 | -141.89 | 0 |
| 2 shoulder | -10216 | -113.51 | -13653 |
| 3 elbow | 10216 | 113.51 | -10786 |
| 4 wrist motor 1 | 2511 | 27.90 | -1773 |
| 5 wrist motor 2 | 2511 | 27.90 | 0 |

`[Gearing]` 1-4 = `1, -1, -1, 1`: signs that say how encoders combine (below). The DLL stores radians per count = (pi/2) / `NoEnc90` and counts per radian = `NoEnc90` / (pi/2) (`0x100068ff`, V).

### 10.2 Encoder counts to joint angles

Function `0x100303db` (V, E, S). With e1..e5 the encoder counts, k the radians per count, H the `HorizPos`:

```text
base     = (e1 - H1) * k1
shoulder = (e2 - H2) * k2
elbow    = (e3 + e2 - H3) * k3                  elbow uses the shoulder encoder too
pitch    = -shoulder - elbow + ((e4 - e5)/2 - H4) * k4
roll     = ((e4 + e5)/2 - H5) * k5
```

The two halvings are integer divisions that truncate toward zero, so an odd wrist difference or sum loses half a count. The function has a branch for each gearing sign (-1, 0, 1); the lines above are the ER-4u case. Controller types `0x24` and `0xEC` take a different wrist path that was not traced.

The inverse is `0x10030ed2` (V; E for its structure). It converts each term with `__ftol`, which sets the x87 rounding mode to truncate toward zero before storing. A round trip can therefore differ from the starting counts by up to three on a wrist motor.

### 10.3 What the formulas say about the arm

- **Wrist mixing.** Pitch comes from half the *difference* of the two wrist motors and roll from half their *sum*. Moving the motors in opposite directions pitches; moving them together rolls. One count on both motors is 1/27.9 degree.
- **The forearm and gripper keep their orientation when the shoulder moves** (with the default parameters: it needs gearing 1 = 1, gearing 2 = -1, and shoulder and elbow scales that are equal and opposite, which the defaults have). `shoulder + elbow` then depends only on the elbow encoder, and `shoulder + elbow + pitch` only on the wrist encoders. The elbow and wrist motors set angles to the horizontal, not to the previous link. So a move of the shoulder *motor* alone changes three relative joint angles (shoulder, elbow, pitch) while the forearm and gripper stay pointing the same way.
- **"One joint" in the legacy code means one motor.** A legacy shoulder jog is therefore not a pure shoulder rotation in joint-angle terms. Anything that needs joint angles (kinematics, a dataset's state, soft limits) has to go through this mapping, not through per-motor scales.
- **All counts zero is the pose the toolboxes publish as home.** There the formulas give shoulder 120.28, elbow -95.02, pitch -88.81 degrees in the toolboxes' sign convention, and a gripper pitch of -63.55 degrees to the horizontal. This is the vendor's nominal zero-count pose. It does not show that our arm's counters read zero after homing: the legacy homing sends nothing that zeroes them, the SDK records whatever counts it sees after homing, and calibration deliberately does not assume homed angles. Whether the vendor's homing zeroes the counters (it has a Set position command, `48`) is part of the homing question.

Sign conventions: the DLL's internal angles are as above. The USNA toolboxes negate shoulder, elbow and pitch when reporting (`ScorGetJt`), to match the teach pendant.

### 10.4 Evidence

| Check | Result |
|---|---|
| Emulation, counts to angles | 1,018 count vectors (zeros, single counts, odd wrist values, both ends of `EncLimit`, 1,000 random): Python equals the emulated function to 2e-15 rad on every output |
| Emulation, angles to counts | 1,005 angle vectors: equal on every count once rounding is set to match the emulator. See the note below |
| Second build | The five functions involved (`0x100303db`, `0x10030ed2`, `0x100068ff`, `0x10006a1c`, `__ftol`) are instruction-for-instruction identical in the 2008 build |
| USNA MTIS `ScorCnts2Deg` (2010), a separately written conversion used on real arms | Agrees on all five joints over 500 random count vectors to within 0.03 degree, which is the rounding of its published offsets |
| Kutzer `BSEPRhome` and `XYZPRhome` | Zero counts reproduce the published home joint angles and home pitch to 2e-5 rad |

**An emulator artifact, caught.** Ghidra's emulator ignored the rounding mode that `__ftol` sets, and rounded to nearest. Trusting it would have put the inverse one count out on most inputs. The truncation was read from the instructions (`OR AH,0x0C` on the control word before `FISTP`), which is the standard MSVC `_ftol`. The Python model truncates; the test compares structure with the emulator under the emulator's rounding.

### 10.5 Against the legacy code

Counts per degree in `openScorbot/motion_profile.py:COUNTS_PER_DEGREE` beside the vendor's:

| Legacy "joint" | Legacy | Vendor | Difference |
|---|---|---|---|
| base | 141.85 | 141.89 | none to speak of |
| shoulder | 115.0 | 113.51 | legacy 1.3% high |
| elbow | 112.6 | 113.51 | legacy 0.8% low |
| wrist pitch | 33.8 | 27.90 per motor | legacy 21% high |
| wrist roll | 27.9 | 27.90 per motor | same |

The legacy code and the vendor agree on which way the wrist motors move for pitch (opposite) and roll (together). The pitch scale is the one real disagreement: if the vendor is right, a legacy pitch jog of 1 degree is 1.21 degrees. Wrist jogs stay disabled; this tells the bench test what to measure.

### 10.6 Still to confirm on the arm

Reviewed by Codex against the disassembly and INI files: the gearing branches, pitch signs and truncation were confirmed; two statements were narrowed as a result (the orientation property holds for the default parameters only, and zero counts is the nominal pose, not a measured home).

Added to the lab plan as V16-V18: the counts per degree of base, shoulder and elbow against a physical angle; that the forearm keeps its orientation during a shoulder jog; and, when wrist jogs are bench-tested, the 27.9 scale and the difference/sum mixing.

## 11. Motion planner

Desk analysis, 2026-10-04, question A of the plan. **Partly done.** The profile shape and the limits are recovered; how a move's distance and duration are worked out, and the planner's lifecycle, are not. Re-implemented in `scorbot/vendor_profile.py`, tested in `tests/test_vendor_profile.py`. A prior from disassembly, not measured on our arm. Labels as in section 10 (V read, E emulated, S second source, I inference).

### 11.1 The profile

Set-up `0x1001295d`, evaluator `0x10012e48` (V, E). A move is a normalised S-curve: position runs from 0 to 1 over a total time T, with jerk-limited ramps at both ends. Three parameters:

| Parameter | INI key (`ROB_4u.INI` `[Motion]`) | Default | Meaning |
|---|---|---|---|
| Total time T | `TotalTimeA` | 3.0 s | Duration of the move |
| Acceleration fraction A | `AccelA` | 0.3 | Share of T spent speeding up; the same share slowing down |
| Jerk fraction J | `AccelAccelA` | 0.3 | Share of each speed-up or slow-down phase spent ramping acceleration up, and again down |

The set-up rejects T below 0.001 s and fractions outside 0 to 0.5. Seven segments follow: jerk up, constant acceleration, jerk down, cruise, and the mirror image. With the defaults the boundaries are at 0.27, 0.63, 0.9, 2.1, 2.37 and 2.73 s, so 30% of the time speeding up, 40% cruising, 30% slowing down.

Peak values for a move of distance D: velocity D / ((1 - A) T), acceleration that velocity divided by A T (1 - J), jerk that acceleration divided by J A T. With the defaults: peak speed D / 2.1 s.

This is the standard jerk-limited "double S" profile, described by time fractions instead of by speed, acceleration and jerk limits.

I, not traced: every axis is driven from the one normalised 0..1 curve scaled by its own distance, so all axes start and finish together. It fits a profile normalised to 1, but the code that applies it was not read.

### 11.2 Evidence

| Check | Result |
|---|---|
| Emulation | Six profiles (including the defaults, the largest fractions, a 1 ms move and a 10 s move), 1,032 time samples at and around every segment boundary: position, velocity and acceleration equal the Python model to 2e-15 |
| Second build | Set-up and evaluator are instruction-for-instruction identical in the 2008 build |
| Ruckig, an independent jerk-limited planner | Given peak velocity, acceleration and jerk computed from our own model of the vendor profile, Ruckig's time-optimal move has the same duration and the same positions and velocities to 1e-6. This is a mathematical statement about one normalised curve: it is the standard jerk-limited shape. It is circular as evidence about the DLL (the limits came from our model) and says nothing about real vendor setpoints |

One behaviour worth knowing: the evaluator has no guard for negative time. It runs the first cubic backwards and returns a small negative position.

### 11.3 Speed and time

- `Time(group, ms)` (`0x10016eb8`) stores ms / 1000 seconds and marks the group as "time mode". A negative value is error 909 (V).
- `Speed(group, percent)` (`0x10016f93`) accepts 1 to 100 and stores 0.3 + 0.007 x percent, so a factor from 0.307 to 1.0, in "speed mode". Below 1 is error 586; above 100 is error 913 (V). That is the stored factor only. What speed it produces is not known until the next point is resolved.
- **Unresolved:** how the speed factor and the per-axis limits below turn into a duration T. The move set-up functions (`0x1000b910` and its siblings, reached from `MoveJoint` through `0x10035eab`) were not read.

### 11.4 Velocity jog

`MoveManual(axis, percent)` (`0x1001ba0c`, V) uses the **sign** of percent only to choose between the axis's `Manual_1` (percent negative) and `Manual_2` (percent zero or positive), then multiplies the chosen value by the **magnitude**, |percent| / 100 (the helper at `0x10043217` is the C runtime's `fabs`). The direction therefore comes from the sign of the INI value, which differs per axis: for the base at -50% the result is `Manual_1` x 0.5 = -72.5, and for the shoulder at -50% it is `Manual_1` x 0.5 = +72.5. It then starts a move with A = 0.3 and J = 0.05: a much shorter jerk ramp than a point move, which is what makes a jog feel immediate. In XYZ manual mode the scale comes from `[ManualSpeedXYZ]` instead. `EnterManual` clears the controller's buffer first and the queue limit drops to `ManualBuffers` (section 2).

### 11.5 Per-axis limits

From `ER4AxN.ini`, loaded by `0x100043b2` into a 0xA8-byte record per axis (V for the loading; the `$Default` values are shown). **Units of the speed and acceleration values are unresolved.**

| Axis | `EncLimit_1` / `_2` (counts) | Same in degrees, by section 10 | `MaxSpeed` | `MaxAccel` | `Manual_1` / `_2` | `ImpactDetect` | `ExactEpsilon` |
|---|---|---|---|---|---|---|---|
| 1 base | -25000 / 20000 | +176.2 / -141.0 | 6500 | 11000 | -145 / 145 | 70 | 5 |
| 2 shoulder | -18000 / 1500 | DLL shoulder angle +38.3 / -133.5 (158.6 / -13.2 from the zero-count pose) | 6500 | 11000 | 145 / -145 | 70 | 5 |
| 3 elbow | -25000 / 20000 | motor counts; the elbow angle also depends on the shoulder | 6500 | 11000 | 160 / -160 | 70 | 20 |
| 4 wrist motor 1 | -15000 / 15000 | motor counts | 6500 | 11000 | 300 / -300 | 70 | 20 |
| 5 wrist motor 2 | -1000000 / 1000000 | effectively none | 6500 | 11000 | -475 / 475 | 70 | 20 |
| 6 gripper | -200 / 6000 | | 15000 | 50000 | -7500 / 7500 | 300 | 200 |

The encoder limits are per motor, in the vendor's counts. They are **diagnostic priors, not limits our SDK can apply**: whether our counts share the vendor's zero and signs is unverified (section 10.3; the legacy homing does not zero the counters), and the limits are asymmetric, so an offset or a flipped sign would let a bad target through or refuse a good one. They become usable only after the home count and motor signs are measured. The same record holds the servo gains (`PropGain`, `DifferGain`, `IntegralGain`, `FeedForward`), the thermal model, and the homing settings used in question D (`Type`, `Velocity`, `SwitchState`, `SwitchMask`, `ImpactCondEnc`, `ImpactCondTicks`, `MaxTime`, `MaxDistance`).

`[Motion]` also gives `MaxJointSpeed = 2.0` and `MaxLinearSpeed = 0.20`. They are copied into the planner's state (`0x10020ef4`); where they are applied was not traced.

### 11.6 Duration, sampling, shared curve and retargeting

A second pass, by following only the code that touches the values in question. Read from the instructions; **none of this pass was checked by emulation**, so it is V at best and marked I where the meaning of a value is inferred.

**How long a move takes** (`0x10010c40`, called from the joint-move set-up `0x1000b910`).

- Speed mode: the duration in milliseconds is the larger of two terms, truncated, plus 1:

  ```text
  linear  = 1000 * 1.5 / (factor * (1 - A)) * (straight-line distance of the tool) / MaxLinearSpeed
  angular = 1000 * 1.5 / (factor * (1 - A)) * sqrt(pitch^2 + roll^2 change)       / MaxJointSpeed
  ```

  `factor` is the stored speed factor (0.307 to 1.0), A the acceleration fraction, and the constant 1.5 is in the DLL. Since the profile's peak velocity is distance / ((1 - A) T), this makes the peak speed `factor * MaxLinearSpeed / 1.5` (V for the arithmetic; I that the distances are metres and radians, from the INI values 0.20 and 2.0).
- Then a per-axis check: the encoder distance of each axis divided by the duration is compared with that axis's `MaxSpeed`; if any axis is over, the duration becomes the largest (counts x 1000 / `MaxSpeed`) + 1 ms. So **`MaxSpeed` is in counts per second** (I, from that arithmetic): 6500 counts/s is about 46 degrees/s for the base and 57 for the shoulder and elbow. An acceleration check with `MaxAccel` follows; it was not read.
- Time mode: the fastest allowed duration is computed the same way with factor 1.0, and a requested time shorter than it is refused with error 909. This matches the toolbox authors' report that a `Time` too short makes the move fail (S).

**Sampling** (per-reply tick `0x10025c6c`, V). The planner's clock advances by a fixed period on every tick, not by measured time. The period is `PCPeriod` x `USBCPeriod` milliseconds (`0x10014eff`): 16 x 1.5 = **24 ms** with the shipped INI, not the 16 ms we assumed. The controller is told `PCPeriod` = 16 separately (parameter 8, section 5). The tick does measure real elapsed time and logs when it runs long, but uses the fixed period for the profile. How a setpoint is rounded to counts was not read; the conversion in section 10 truncates.

**One curve for all joints** (V in part). A move has one profile object (a pointer in the planner's state) evaluated once per tick at the move's clock; its single 0..1 value is then applied to the whole move. The lines that scale each axis by it were not read one by one, so "all joints start and finish together" is still I, though the structure leaves little room for anything else.

**A new target while moving is refused, not blended** (V). The joint-move set-up proceeds only when the group's motion state is idle. Otherwise it returns error 911 ("motion in progress" in Kutzer's list), or 903 if control is off. `SetJoint` and `MoveManual` make the same check. There is no blending of point moves in this DLL: one move finishes, then the next may start. The velocity jog is the exception: once in manual mode, `MoveManual` can be called again while the axis is moving and takes a different path in its set-up routine (`0x1000ec9e`), which was not traced.

### 11.7 Still not done

- The acceleration check in the duration routine, and the rounding of setpoints to counts.
- The velocity jog's behaviour when its speed is changed mid-motion.
- The rest of the lifecycle (queue limit handling inside the tick, late or missing reply, emergency) beyond what sections 2 and 5 already record. By decision this is not being pursued: our streaming driver is a different design, and the controller's own reactions can only be measured.
- Linear, circular and spline moves.

Reviewed by Codex against the disassembly and INI files. The INI table and the profile formulas held. Five statements were corrected: the velocity jog multiplies by the magnitude of the percentage and takes its direction from the sign of the INI value; the encoder limits are diagnostic priors, not limits in our coordinates; the Ruckig agreement is about the curve's shape only; the speed factor is a stored number, not a known speed; and `setpoints` could stop short of the target when the period did not divide the duration.

### 11.8 What this means for us

- The vendor's profile is a curve a jerk-limited planner such as Ruckig can express, so nothing about its shape calls for a different planner. Whether `scorbot/planning.py` reproduces actual vendor setpoints is not shown: that needs the duration and sampling questions answered and a capture to compare with.
- The vendor plans by time, not by limits: the move takes T seconds and the peaks follow from the distance. A policy that sends a new target many times a second is a different regime, closer to the velocity jog with its short jerk ramp.
- The lab check is capture B's slow go-to (lab plan V19): the setpoint stream should follow this curve with the 30/40/30 split.
- **The vendor does not do what a policy needs.** It refuses a new target mid-move. Streaming a new target many times a second is our own design problem, with the vendor's limits and period as priors.
- **Our planner's period prior is probably wrong.** `scorbot/planning.py` defaults to 16 ms (`PCPeriod`); the vendor's planner steps at 24 ms. Which one the controller expects is for the lab to measure.
- **We now have speed priors per motor**: 6500 counts/s, where `planning.py` had only datasheet joint speeds and no acceleration or jerk at all.
