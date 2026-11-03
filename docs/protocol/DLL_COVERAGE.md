# Coverage: every lab `USBC.dll` export against Stack 2 (2026-11-02)

The lab build (sha256 `6118465d...bd821`) exports 120 names. 17 of them belong to its `INIFile` helper class, which leaves 103 functions; `Initialization` and `MoveTorque` each have two overloads. Every one of the 103 is listed below with a verdict:

- **match**: Stack 2 sends what the DLL sends, or reads the same reply bytes. The proof is named in the row.
- **partial**: the same commands, but a different setpoint profile, or only part of the function.
- **build**: the DLL does something on the wire that Stack 2 lacks and should have. Ranked in "Build order" below.
- **skip**: not worth copying. The row gives the reason.

Totals (overloads counted separately): 21 match, 8 partial, 11 build, 63 skip. The skips are mostly host-side bookkeeping that sends nothing (point store, INI handling, callbacks, simulated inputs).

## Method

- The export table was read from the DLL file itself, and the bodies from the lab decompile. Addresses are the lab build's.
- The wire column comes from a desk run, done twice on 2026-11-02. The lab DLL ran online against `tools/usbc_fake/pipe_server.py` (no arm, no USB device; the fake's guards were on). A script outside the repo called each export after a finished `Home('A')`, with a pause between calls, and the OUT messages were matched to the calls by time. Messages are shown from byte 4 (command, mask, bytes 6-7, bytes 8-11).
- "Nothing" in the wire column means no message other than `0D` setpoints was sent during that call.
- The raw recordings and the script stay outside the repo, like the DLL.
- The Intelitek function reference (`USBC-document.pdf`, outside the repo) gives most meanings as "?", so it was used only for argument names, from a summary. Its pages were not opened to check the quotes.

What this does not prove: that the controller accepts any of it. The fake arm is our model. Every row that touches I/O lines, the jaw or torque is **unverified** on the arm until a dated bench log exists.

## Build order

1. **Pendant Abort becomes a stop** (`GetTPInfo` and the pendant handler; `LEDGER.md` PL63 to PL67). Reply byte 3 = `0x21` or `0xA1` makes the vendor call `Stop('&')`; Stack 2 ignores it. It is a stop path, and all its bytes are already traced.
2. **Digital outputs** (`SetDigitalOutput`, PL68): `44`, with the whole 32-bit output word in bytes 8-11.
3. **Digital and analog inputs** (`GetDigitalInputs`, `GetAnalogInput`, PL69): no command is sent. They are reply byte 6 and bytes 7-10, which Stack 2 receives on every reply but does not decode.
4. **Jaw to a width in mm** (`JawMetric`, `GetJaw`, PL71): the same messages as Stack 2's post-Home open and close (PL61), aimed at `RangeEnc` x mm / `RangeMM`. Stack 2's planner already computes the target this way (`vendor_gripper.target_counts`), but only for 0 and 70 mm. The vendor's ramp is shorter for shorter moves, and that profile is not decoded (PL71).
5. **Analog output** (`SetAnalogOutput`, PL70): `41`, value in bytes 6-7, output number in bytes 8-11.
6. **Torque (PWM) readout** (`ShowTorque`, PL74): `57` with an axis mask; the answer is reply bytes 11-14.
7. **Digital output readback** (`GetDigitalOutputs`): the DLL keeps its own copy of the output word. Stack 2 needs the same copy once item 2 exists.

## Table

| Export (lab address) | What it does | On the wire (desk run) | Stack 2 | Verdict |
|---|---|---|---|---|
| **Connection** | | | | |
| `Initialization` (2 overloads) | Connect; mode 1 runs the reset handshake | Handshake (section 5) | `Scorbot.connect` | match (`DESK_BYTE_CHECK.md`) |
| `CloseUSBC` | Close | Stop group, `42 FF 00 00`, `4F FF 54` | `disconnect` | match (`DESK_BYTE_CHECK.md`) |
| `IsOnLineOk` | Online flag | nothing | `connected` in `RobotState` | match |
| `WaitUSBCCommunication`, `MyPumpMessages` | Windows message pumping for the DLL's threads | nothing | none needed | skip: Windows plumbing |
| `USBDeviceArrival`, `USBDeviceRemoved`, `USBDeviceQueryRemove` | Plug and unplug notices from the host program | nothing | link loss is detected from read errors (PL53) | skip: host plumbing |
| `GetUSBDeviceNumber`, `GetDeviceLinkName` | Which device or pipe the DLL opened | nothing | `transport/device_file.py` path | skip: host side |
| `GetVersion` | Firmware version word | nothing during the call (the `72` is sent in the handshake); returned 0 on the fake | handshake sends `72`; the answer is not decoded | skip: informational |
| `GetConfig`, `ChangeConfig`, `EnumPeriph` | Configuration struct, device IDs | nothing | INI files loaded by Stack 2 | skip: host side |
| `SetParameter`, `SetParameterFolder`, `GetParameterFolder` | Set one parameter; which INI folder to load | folder calls: nothing. `SetParameter` was not run (I: a `53` download) | `vendor_home_source`, `streaming` read the INI values | skip: host side |
| `GetMode` | DLL mode (1 = online on the fake) | nothing | none | skip: host state |
| **Control and safety** | | | | |
| `Control` | Control on or off for a group | Section 5 sequences | `enable`, `disable` | match (`DESK_BYTE_CHECK.md`) |
| `Stop` | Arm stop: `47`, `4F 3F 53`, `73 20`, `42 20 00` (seen twice in this run) | as stated | `request_stop` | match (`tests/test_software_stop.py`, `test_stop_mid_jog_sends_clear_buffer_then_close_and_reports_it`) |
| `IsEmergency` | Reply byte 2 bit 0 | nothing | emergency check in the reply decode | match |
| `IsTeachMode` | Pendant mode flag kept from every reply's byte 3 (PL64): `40 4F C0 F9` set it, `47 7E F8 FE` clear it, `00` keeps it, any other code sets it unless byte 2 bit 0 (emergency) is set | nothing | `state.teach_mode_after`, `RobotState.teach_mode`; `start_stream` and `PolicyArm.connect` refuse while it is on (Stack 2's own rule: the DLL gates nothing on it) | match (decode only: `tests/test_teach_mode.py`, 22 values read from the lab DLL on the pipe fake) |
| `GetTPInfo` | Pendant key text; on the fake: "Code: 0x00 Button: Undefined" | nothing | none | build (item 1) |
| `Impact` | Takes 8 values and runs them through the planner (`0x1001ca7a`, `0x10024e97`); returned 0 on the fake and sent nothing | nothing | the controller's own impact flag, byte 59, is decoded (`collision_bits`) | skip for now: not decoded, and byte 59 is already covered |
| `ShowPositErr`, `ClosePositErr` | Callback with each axis's position error, from reply bytes 22 + 5N | nothing | `controller_error_counts` (`scorbot/state.py` `ERROR_OFFSETS`, 22 + 5N) | match |
| **Homing and counters** | | | | |
| `Home` | Vendor Home | 330 commands (PL10) | `vendor_home` | match (`tests/test_vendor_home.py`) |
| `SetHome` | `'A'`: zero every arm counter where the arm is now, with no switch search (PL72). Single axis 1 was refused | `4F 3F 54`, then for each axis `48 <bit> 0`, `47`, `4F <bit> 53`; last `73 20`, `42 20 00` | the vendor Home's `48` steps | skip: it makes any position "home"; `vendor_home` covers zeroing |
| `IsTheBasePosition` | Point-store check | not run | none | skip: point store |
| **Motion** | | | | |
| `MoveJoint` | Joint move planned on the PC, sent as `0D` setpoints | `0D` stream (section 5, I) | `move_joint`, streaming | partial: the profile is Stack 2's own (`DESK_BYTE_CHECK.md`, "Move") |
| `EnterManual`, `MoveManual`, `CloseManual` | Velocity jog: `47`, small queue, `0D` stream | section 5 | `jog_joint` | partial: Stack 2's jog is a position step, not a velocity jog (`VENDOR_MANUAL_MOVE_TRACE.md`) |
| `SetJoint`, `SetJoints` | Set the planner's target directly | `0D` stream (I) | `start_stream` | partial: same idea, Stack 2's own pacing |
| `Speed`, `Time` | Store a speed percent or a time for the next move | nothing | `move_joint` arguments | match: host settings (PL55 to PL57 give the math) |
| `MoveLinear`, `MoveCircularVect`, `SpeedLin` | Cartesian moves through the PC planner | not run (I: `0D` stream like `MoveJoint`) | none; `kinematics.py` has the forward and inverse model | skip for now: Stack 2 can stream Cartesian paths itself |
| `ShowXYZ`, `CloseXYZ` | Callback with the tool position | nothing | forward kinematics on `get_joint_angles` | partial: never compared with the DLL's xyz |
| `Velocity` | Constant-velocity move of a peripheral axis | refused on axis 0 with error 910 "Invalid axis type", then `47`, `4F FF 53`, `73 20`, `42 20 00`, `64`, `61` | none | skip: the ER-4u has no peripheral axes here (`'B'` is empty) |
| `MoveTorque` (2 overloads) | Open-loop PWM drive with no position loop | `4F FF 54` (mode T, all axes), `4D <bit> <value>` (PL73); ended by `Stop('A')` | the vendor Home's `4D` drives | skip: no position control and no stop on reaching anything |
| `ShowTorque`, `CloseTorque` | Callback with the PWM read back | `57 <mask>` (`57 3F` for `'A'`, `57 01` for axis 0); answer in reply bytes 11-14 (PL74) | none | build (item 6) |
| **Gripper** | | | | |
| `OpenGripper`, `CloseGripper` | After Home: position move to 70 mm or 0 mm (PL48, PL51) | `4F 20 53`, `4C 20 00`, `42 20 01`, ramp, `73 20`, `42 20 00` | `open_gripper`, `close_gripper` | match (PL61) |
| `JawMetric` | Same move to any width below `RangeMM` (PL71) | same as above; 40 mm went to 2857 counts and 10 mm to 714 | `vendor_gripper.target_counts` for 0 and 70 mm only | build (item 4) |
| `GetJaw` | Jaw as percent and mm, from the gripper count (57 % and 40 mm; 14 % and 10 mm) | nothing | `GripperMove` reports counts only | build (item 4) |
| `SetJawMetric` | Stores a jaw width (`0x10018a5f`); returned 0 on the fake | nothing | none | skip: not traced, and not needed for a move |
| **Digital and analog I/O** | | | | |
| `SetDigitalOutput` | Output 0-31 on or off (PL68) | `44 00 00 00 <output word>`: output 1 on gave word `2`, 3 gave `8`. Third argument 1 ("immediate") sends `47` first, but only when the arm is at rest (no move running and every axis within 10 counts of its setpoint); 0 waits for the queue to drain first. SCORBASE passes output number - 1, and 1 at most call sites | none | build (item 2) |
| `GetDigitalOutputs` | The DLL's own copy of the output word | nothing | none | build (item 7) |
| `GetDigitalInputs` | Input word from reply byte 6 (PL69) | nothing | not decoded | build (item 3) |
| `GetAnalogInput` | Input 0-3 from reply bytes 7-10 (PL69) | nothing | not decoded | build (item 3) |
| `SetAnalogOutput` | Output 0 or 1, value 0-255 (PL70) | `41 00 <value16> <output32>`: (1, 100) gave `41 00 64 00 01 00 00 00` | none | build (item 5) |
| `ForceDigitalInput`, `ForceAnalogInput`, `ForceStatusDigitalInput`, `ForceStatusAnalogInput`, `GetForceStatusDigitalInput`, `GetForceStatusAnalogInput` | Overwrite an input inside the DLL for testing; a force is refused unless its force status is set (PL69) | nothing | none | skip: simulated inputs, nothing reaches the controller |
| `EnableDigitalInput`, `DisableDigitalInput`, `GetDigitalInputEnabledStatus` | Stubs: return 1 and do nothing (`0x10019957`) | nothing | none | skip: they do nothing |
| **Watch callbacks** | | | | |
| `WatchMotion`, `WatchHoming`, `WatchJoint`, `WatchControl`, `WatchDigitalInp`, `WatchDigitalOut`, `WatchAnalogInp`, `WatchAnalogOut`, and the matching `CloseWatch*` (6) | Register a callback for a state the DLL already has | nothing | `get_state`, the session record | skip: callbacks over data covered by the rows above |
| `ShowEnco`, `CloseEnco`, `ShowJoint`, `CloseJoint`, `ShowHomeSwitches`, `CloseHomeSwitches`, `GetCurrentPosition` | Callbacks or calls returning counts, joint values, switch bits | nothing | `encoder_counts`, `get_joint_angles`, `home_switch_bits` | match on data |
| **Point store** | | | | |
| `DefineVector`, `RenameVector`, `Teach`, `Here`, `SetEncoders`, `AddPoints`, `AttachPosit`, `BuildPositForWrite`, `SavePoints`, `DeletePoint`, `IsPointExist`, `GetNextPoint`, `GetPointInfo`, `GetPointsPeriphID`, `ClearPointsAttributes` | SCORBASE's named-position files | not run (host side) | session records and episode files | skip: SCORBASE's own file format, which Stack 2 does not use |

The per-row count behind the totals: every name in a cell counts as one export, so the `Watch*` row is 14 and the point-store row is 15.

## Not done here

- Bench checks of the I/O lines, the jaw width and the PWM readout, on the arm.
- The vendor's jaw ramp for short moves (PL71): it lasts about 40 ticks for a full stroke, 29 for 2857 counts and 13 for 714. Stack 2's fitted full-stroke profile, stretched to the best length, is up to 90 counts off.
- `Impact`'s purpose, `SetJawMetric`, the Cartesian moves on the wire.
