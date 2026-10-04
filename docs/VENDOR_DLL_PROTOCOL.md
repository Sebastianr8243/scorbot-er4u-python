# Vendor protocol, read from `USBC.dll`

What Intelitek's own DLL sends to and reads from the Controller-USB, worked out by static decompilation. Companion to [PROTOCOL.md](PROTOCOL.md), which describes what our legacy code (`openScorbot/`) sends.

**Status: from disassembly, unverified.** The DLL was read, never loaded or run, and nothing here has been checked against a controller or a USB capture. It describes what the vendor software would send, not what the lab's controller accepts. Project rule (root `CLAUDE.md`, changed 2026-10-04): a sequence built only from command bytes the legacy code already sends may be changed on the strength of this document, and is tried first on a 1 degree jog. Bytes the legacy code never sends still need a capture.

Labels: **V** = read directly from the vendor code. **I** = our inference from it.

## 1. Sources and method

| Build | Where from | Size | SHA-256 |
|---|---|---|---|
| 2018 (built 2018-11-22) | `github.com/kutzer/ScorBotToolbox`, `ScorBotToolboxFunctions/USBC.dll` | 539,784 bytes | `6de887a8d6f2ce686d377586d4be8981ff643cad13c158cd4603f8b44f20555e` |
| 2008 (dated 2008-05-04) | `MTIS.zip` in `github.com/baijuch/sboter4u` | 471,040 bytes | `91e99a915c4f5e22c942b3e8dc757ac6b0f3610c01abdab0c2ea2ad0aa555240` |

Both are 32-bit and not packed. Decompiled with Ghidra 12.1.4 in headless mode; how to repeat it is in [tools/usbc_analysis/README.md](../tools/usbc_analysis/README.md). Function addresses below are from the 2018 build. The lab's own `USBC.dll` has not been examined and may be a third build.

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

How each claim here gets confirmed on the controller: [VENDOR_PROTOCOL_LAB_PLAN.md](VENDOR_PROTOCOL_LAB_PLAN.md).

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
