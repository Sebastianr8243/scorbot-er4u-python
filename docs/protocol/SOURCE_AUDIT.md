# Source audit of Stack 2 (`scorbot/`)

Where every value Stack 2 relies on comes from, started 2026-11-01 on top of the vendor-watcher Home (PR #72). Two tables: the byte sequences that reach the controller, then every module-level constant in `scorbot/`. A ranked to-do list closes the page. `tests/test_source_audit.py` fails when a constant in `scorbot/` has no row here, or a row names a constant that is gone, so a new number cannot slip in untagged.

**Tags.**

- `vendor`: read from Intelitek's software. The row gives the `USBC.dll` address, the `ER4AxN.ini` / `ROB_4u.INI` / `USBC.INI` key, or the vendor document.
- `legacy`: from the older openScorbot project (`openScorbot/`), someone else's reverse engineering. A value counts as legacy only if it is in that project's first commit `43b03b1` (2020). Values this project added to files under `openScorbot/` later are `ours`.
- `ours`: chosen here. The row says why.

**Backed by** names the note in `docs/protocol/` (or `LEDGER.md` ID) that supports the tag. **unverified** means no note in `docs/protocol/` backs it: the tag rests on a code comment or a source outside these notes. Nothing in either table has been seen on the arm yet (`LEDGER.md`: no `arm` rows); "backed" means read from the DLL or matched on the fake.

Addresses are from the builds named in the cited note (2018 build for `VENDOR_DLL_PROTOCOL.md` and `VENDOR_HOMING_TRACE.md`, SDK Release build for `LEDGER.md`).

## 1. Byte sequences Stack 2 sends

Every OUT message reaches the wire through one of two writers: the legacy worker (`openScorbot/libsync.py`, `libcomm.py`, built from the `libhex.py` tables) driven by `scorbot/robot.py`, or `scorbot/vendor_home.py:encode_out` driven by `vendor_home_source.py` through the worker's order 22. `scorbot/transport/codec.py` is a tested mirror of the legacy tables and sends nothing.

| Sequence (bytes from offset 4) | Sent by | Tag | Source | Backed by |
|---|---|---|---|---|
| Handshake pkt1: `5A`, `4F FF 54`, `73 FF`, `42 FF` | `libsync.msg_start` (connect) | legacy | `libhex.get_msg1`; equals the vendor reset handshake steps 1-4 (`0x10005736`) | `DESK_BYTE_CHECK.md` match; `VENDOR_DLL_PROTOCOL.md` section 5 |
| Handshake pkt2: `72` x2, `64` x3, `61` x3, `53 00 08 00 10`, `53 01 00 00 00 F0`, then 79 per-axis `53 b6 b7 00` + 3 data bytes | `libsync.send_pkt2` | legacy | `libhex.get_msg2`; vendor steps 6-8 (parameter 8 = `PCPeriod` 16 is inference) | `DESK_BYTE_CHECK.md` match (PL04) |
| Handshake close: `0D` with header `00 0C 00` | `libsync` | legacy | `libhex.get_msg2(84)`; the vendor writes the same message | `DESK_BYTE_CHECK.md` match |
| Enable: `0D 47 0D 4F FF 53 42 FF 01 73 20 42 20` (handshake pkt3 tail, `enable()`) | `libcomm.motors_on` | legacy | `libhex.motorson`; vendor `Control('&', 1)` (`0x100226c4`) | `DESK_BYTE_CHECK.md` match (PL04) |
| Disable: `0D 47 73 FF 42 FF 4F FF 53 73 FF 73 20 42 20`, then `64`/`61` three times (26 messages) | `libcomm.motors_off` | legacy | `libhex.motorsoff`; vendor `Control('&', 0)` | `DESK_BYTE_CHECK.md` "Control off" match |
| Disconnect: disable, then `47 4F FF 53 73 20 42 20 64 61` group, then `42 FF`, `4F FF 54` | `libcomm.scorbotoff` (order `EXIT`) | legacy | `libhex.get_scorbotoff`, `get_msg1(4)`, `get_msg1(2)` | `DESK_BYTE_CHECK.md` **differs**: the middle group is extra against the DLL; whether the controller needs it is unverified |
| Jog: `47`, then `0D` with the target setpoints, then `4F 3F 53`, `73 20`, `42 20` | `libdef.openMov` / `closeMov` (orders 4-9) | legacy | `libhex.mov_comm(1..5)`; the tail is the vendor's end-of-move tail without its leading `47` | region layout: `DESK_BYTE_CHECK.md` match; the setpoint ramp is legacy policy, not compared (**unverified**) |
| Software stop: `47`, `4F 3F 53`, `73 20`, `42 20`, setpoints held at the measured position | `libdef.stopMov` | ours | added in `2612bd1` (2026-10-04), copied from vendor `Stop('A')` (`0x10021a47`) | `VENDOR_DLL_PROTOCOL.md` section 5; not run on the fake |
| Stream: `0D` carrying three moving setpoints, one per period (24 ms) | `libcomm.stream_targets` (order 21) | ours | the target-chasing loop is ours (`f4b0613`); the message (`0D` carries the setpoint table) and the period are the vendor's | `VENDOR_DLL_PROTOCOL.md` sections 2, 5, 11.6 |
| Gripper: `4F 20 53`, `4C 20 00`, `42 20 01`, about 45 `0D` ramping the gripper setpoint, `73 20`, `42 20` | `libcomm.clamp` (orders 14, 15) | legacy | `libhex.clamp`; the vendor's gripper is different (`4F 20 54` + one `4D`) | `VENDOR_DLL_PROTOCOL.md` section 12.2 says they differ; never compared byte for byte |
| Vendor Home: `47`; `4F <mask> 54/53/48`; `4C <mask>`; `4D <mask>` + drive value; `48 <mask>` value 0; `42 20 00/01`; `73 20`; every message with the 8-slot setpoint table, slots 6 and 7 raw `0x800001` | `vendor_home.encode_out` (order 22) | vendor | `Home('A')`, per-axis routine `0x1000826d`, drive `0x100072c9`, `48` `0x1000758e`; masks `01`..`20`, `3F`, `FF` | PL10: the same 330 non-`0D` commands as the lab DLL on the fake (`tests/fixtures/vendor_home_trace05.json`) |
| Vendor Home stop on a fault: `47`, `4D 0` to every motor, two cycles, setpoints held, `4F FF 53` | `vendor_home` abort path | ours | modelled on the vendor stall stop (PL22) and `Stop('A')`; the vendor raises no such fault | **unverified**: not in the trace (the trace has no faults) |
| Inch home: legacy jog messages only | `scorbot/inch_home.py` through orders 4-9 | ours | our search, built from legacy jogs | bytes as the jog row; the search is ours |
| Legacy home (order 18) | `openScorbot/setHome.py` | legacy | upstream homing; never sends `48` | `VENDOR_DLL_PROTOCOL.md` section 13 |

## 2. Constants

Keys are `module.NAME` relative to `scorbot/`. One row may list several constants that share a tag and a reason.

### Wire layer

| Constant | Value | Tag | Source, or why it is ours | Backed by |
|---|---|---|---|---|
| `transport.codec.MSG_LEN` | 64 | vendor | 64-byte `WriteFile`/`ReadFile` (communication thread `0x1003ce38`); legacy `MSG_LEN` 128 hex characters agrees | `VENDOR_DLL_PROTOCOL.md` sections 2, 14 |
| `transport.codec.MAX_SEQUENCE` | 255 | legacy | `libdef.countByte1` wraps 255 to 1; the DLL goes 255 to 0 | `DESK_BYTE_CHECK.md` (differs, both accepted on the arm per `BRING_UP_SEQUENCE.md`) |
| `transport.codec.MAX_COUNT`, `transport.codec.COUNT_MODULUS` | 65535 | legacy | `libdef.suma`/`resta`; the vendor count is 24-bit, so 65535 is the legacy double-zero modulus | `VENDOR_DLL_PROTOCOL.md` section 6 |
| `transport.codec.SIGN_POSITIVE`, `transport.codec.SIGN_NEGATIVE`, `transport.codec._SIGN_WORDS` | 128 / 127, `00 00` / `FF FF` | legacy | `libdef.get_signo`; the vendor reads them as the top byte of a 24-bit count | `VENDOR_DLL_PROTOCOL.md` section 6; `DESK_BYTE_CHECK.md` (DLL uses only these two words) |
| `transport.codec.HEADER` | `00 00 00` | legacy | bytes 1-3 of every legacy packet; the vendor's are zero except two small queues | `VENDOR_DLL_PROTOCOL.md` section 3 |
| `transport.codec.COMMAND_OFFSET`, `transport.codec.REGION_OFFSET`, `transport.codec.FIELD_LEN`, `transport.codec.REGION_LEN` | 4, 12, 4, 24 | vendor | OUT layout from `0x1003e7fc` / `0x1003ee7e`; same offsets in the legacy code | `VENDOR_DLL_PROTOCOL.md` section 3; `DESK_BYTE_CHECK.md` region match |
| `transport.codec.MOV_COMM` | `0D`, `47`, `4F 3F 53`, `73 20`, `42 20`, prefix | legacy | `libhex.mov_comm` | `DESK_BYTE_CHECK.md`; `VENDOR_DLL_PROTOCOL.md` section 4 (every byte named by the DLL) |
| `transport.codec.GET_MSG1` | `5A`, `4F FF 54`, `73 FF`, `42 FF` | legacy | `libhex.get_msg1` | `DESK_BYTE_CHECK.md` match |
| `transport.codec.GET_MSG2` | 79 three-byte entries | legacy | `libhex.get_msg2(1..79)` | `DESK_BYTE_CHECK.md` match |
| `transport.codec.MOTORS_ON` | 7 messages | legacy | `libhex.motorson` | `DESK_BYTE_CHECK.md` match |
| `transport.codec.MOTORS_OFF` | 26 messages | legacy | `libhex.motorsoff` | `DESK_BYTE_CHECK.md` "Control off" match |
| `transport.codec.SCORBOT_OFF` | 11 messages | legacy | `libhex.get_scorbotoff` | `DESK_BYTE_CHECK.md` **differs** (extra group) |
| `transport.codec.CLAMP` | `4F 20 53`, `4C 20 00`, `42 20 01`, `73 20`, `42 20` | legacy | `libhex.clamp` | `VENDOR_DLL_PROTOCOL.md` section 12.2 (vendor differs) |
| `transport.codec.IDLE`, `transport.codec.MOVE_OPEN`, `transport.codec.MOVE_CLOSE` | `0D`; `47`; the three close messages | legacy | named slices of `MOV_COMM` | as `MOV_COMM` |
| `transport.codec.PKT2_MODES` | `72`, `64`, `61` | legacy | `libsync.send_pkt2` | `DESK_BYTE_CHECK.md` match; `VENDOR_DLL_PROTOCOL.md` section 5 |
| `transport.codec.PKT2_SETUP_1` | `53 00 08 00 10` | legacy | `get_msg2(81)`; the vendor's "set parameter 8" (value `PCPeriod` 16 is inference) | `VENDOR_DLL_PROTOCOL.md` section 5 |
| `transport.codec.PKT2_SETUP_2` | `53 01 00 00 00 F0` | legacy | `get_msg2(82)` | `DESK_BYTE_CHECK.md` match |
| `transport.codec.PKT2_END_HEADER`, `transport.codec.PKT2_END_COMMAND` | `00 0C 00`, `0D` | legacy | `get_msg2(84)` | `DESK_BYTE_CHECK.md` match |
| `transport.device_file.DEFAULT_PATH` | `\\.\ERUSBDevice0` | vendor | `USBC.INI` `DriverLink` | `VENDOR_DLL_PROTOCOL.md` section 14; `PROTOCOL.md` |
| `transport.device_file.PACKET_SIZE` | 64 | vendor | 64-byte reads and writes (`FUN_1003bc88`, 2008 build) | `VENDOR_DLL_PROTOCOL.md` section 14; PL01 |
| `transport.device_file.MAX_EMPTY_READS` | 10 | vendor | the DLL gives up after ten zero-byte reads in a row | `VENDOR_DLL_PROTOCOL.md` section 14 |
| `transport.device_file._GENERIC_READ_WRITE`, `transport.device_file._OPEN_EXISTING` | `0xC0000000`, 3 | vendor | the DLL's `CreateFileA` access and disposition | `VENDOR_DLL_PROTOCOL.md` section 14; PL01 |
| `transport.device_file.EMPTY_READ_TIMEOUT_MS` | 4000 | ours | below the driver's 5000 ms wait, so a full-length empty read is told apart from a quick retry | PL30, PL31 |
| `transport.device_file.CANCEL_RETRY_S` | 0.01 | ours | a cancel sent before the read is pending is lost, so the watchdog repeats it | **unverified** |
| `transport.device_file.ERROR_FILE_NOT_FOUND`, `transport.device_file.ERROR_PATH_NOT_FOUND`, `transport.device_file.ERROR_ACCESS_DENIED`, `transport.device_file.ERROR_SHARING_VIOLATION`, `transport.device_file.ERROR_OPERATION_ABORTED`, `transport.device_file._THREAD_TERMINATE` | 2, 3, 5, 32, 995, `0x0001` | ours | Windows API constants (`winerror.h`, `OpenThread` access), not controller values | n/a |
| `transport.probe.CONNECT_WARNING` | text | ours | operator warning text | n/a |
| `preflight.VID`, `preflight.PID` | `0x09F1`, `0x0007` | vendor | the controller's USB descriptor | `BRING_UP_SEQUENCE.md` step 1; `ULPGC_THESIS_FINDINGS.md` |
| `pacing.ID_RING` | 255 | legacy | IDs run 1 to 255 (`libdef.countByte1`); the DLL wraps with 255 in its ID arithmetic in two places and 256 in one | `VENDOR_DLL_PROTOCOL.md` section 3 |
| `pacing.QUEUE_LIMIT` | 45 | vendor | `Buffers` | PL03 |
| `pacing.FAST_DELAY_S` | 0.001 | vendor | the DLL waits 1 ms below the queue limit | `VENDOR_DLL_PROTOCOL.md` section 2 |
| `packet.MAX_TRACE_PACKETS` | 4000 | ours | size of the in-memory packet log | n/a |

### Reply decoding

| Constant | Value | Tag | Source, or why it is ours | Backed by |
|---|---|---|---|---|
| `state.JOINTS` | six motor names | legacy | the legacy order of the six count fields | `VENDOR_DLL_PROTOCOL.md` section 6 (axis N order) |
| `state.ENCODER_OFFSETS` | 19 + 5N | vendor | axis position at 19 + 5N (`0x1000b160`); legacy reads the same bytes | `VENDOR_DLL_PROTOCOL.md` section 6 |
| `state.ERROR_OFFSETS` | 22 + 5N | vendor | axis position error at 22 + 5N | `VENDOR_DLL_PROTOCOL.md` section 6 |
| `state.PACKET_MIN_LENGTH` | 49 | ours | derived: the last field decoded ends at byte 48 | n/a |
| `state.HOME_SWITCH_BITS` | 1, 2, 4, 8, 16 | legacy | `libdef.get_switch`; byte 5 bit N in the DLL, order confirmed by the ULPGC thesis | `VENDOR_DLL_PROTOCOL.md` section 6; `ULPGC_THESIS_FINDINGS.md` |
| `state.COLLISION_OFFSET` | 59 | vendor | impact byte (`0x1000b270`) | `VENDOR_DLL_PROTOCOL.md` section 6 |
| `state.COLLISION_MOTORS` | names for bits 0-7 | ours | labels; bit-per-motor reading from the ULPGC thesis p. 57 | `ULPGC_THESIS_FINDINGS.md` |
| `vendor_home.ZERO24` | `0x7FFFFF` | vendor | count = raw24 - `0x7FFFFF` (`0x1000b160`) | `VENDOR_DLL_PROTOCOL.md` section 6 |
| `vendor_home.REPLY_MIN_LENGTH` | 60 | ours | the last byte decoded is the impact byte 59 | n/a |

### Vendor Home (`vendor_home.py`)

| Constant | Value | Tag | Source, or why it is ours | Backed by |
|---|---|---|---|---|
| `vendor_home.NOP`, `vendor_home.CLEAR`, `vendor_home.MODE`, `vendor_home.SLAVE`, `vendor_home.MOVE`, `vendor_home.SET_POSITION`, `vendor_home.CONTROL_OFF`, `vendor_home.MOTORS` | `0D`, `47`, `4F`, `4C`, `4D`, `48`, `73`, `42` | vendor | command letters, names from the DLL's log table (`0x100401f7`) | `VENDOR_DLL_PROTOCOL.md` section 4 |
| `vendor_home.MODE_T`, `vendor_home.MODE_H`, `vendor_home.MODE_S` | `54`, `48`, `53` | vendor | Mode sub-codes | `VENDOR_DLL_PROTOCOL.md` section 4 |
| `vendor_home.UNUSED_SLOT` | raw `0x800001` | vendor | what the DLL writes in setpoint slots 6 and 7 | **unverified**: stated in the module docstring only; the trace fixture stores no setpoints |
| `vendor_home.BASE`, `vendor_home.SHOULDER`, `vendor_home.ELBOW`, `vendor_home.WRIST_M1`, `vendor_home.WRIST_M2`, `vendor_home.GRIPPER` | 0-5 | vendor | the DLL's 0-based axis numbers | `VENDOR_HOMING_TRACE.md` |
| `vendor_home.NAMES` | six labels | ours | log labels for those axes | n/a |
| `vendor_home.AXES` | per-axis `Type`, `Velocity`, `ImpactCondEnc`/`Ticks`, `MaxTime`, `MaxDistance`, `Offset`, `NoEnc90` | vendor | `ER4Ax1-6.ini [Homing]`, `NoEnc90` | `VENDOR_HOMING_TRACE.md` section 3 table. **One open point:** the gripper's `MaxTime` is 15 in the file (unit "I: ms" in the note) but 15.0 s here |
| `vendor_home.HOMING_SEQUENCE` | shoulder, elbow, m2, m1, base, gripper | vendor | `ROB_4u.INI [HomingSeq]` 2, 3, 5, 4, 1, 6 | `VENDOR_HOMING_TRACE.md` section 1 |
| `vendor_home.PREPASS_PITCH_COUNTS` | 1050 | vendor | ER-4u pre-pass (`0x1000808b`): wrist m1 `Offset` + 200 | `VENDOR_HOMING_TRACE.md` section 1 |
| `vendor_home.COUPLED` | coupled motors per axis | vendor | drive scaling `0x100072c9`, `48` with coupling `0x1000758e` | `VENDOR_HOMING_TRACE.md`; `VENDOR_COUPLING_TRACE.md` |
| `vendor_home.GRIPPER_MAX_CYCLES` | 120 | vendor | gripper countdown `0x78` (`0x1000d693`) | PL20, PL21 |
| `vendor_home.GRIPPER_GRACE_CYCLES` | 10 | vendor | the stall check runs from 109 ticks left (`0x1002539c`); plus our measured lag | PL22 |
| `vendor_home.GRIPPER_STALL_CHECKS` | 1 | vendor | one quiet check ends the drive | PL22 (PL26 still says "twice": stale since #72) |
| `vendor_home.ERR_EMERGENCY`, `vendor_home.ERR_IMPACT`, `vendor_home.ERR_TIMEOUT` | 300, 201, 561 | vendor | the per-axis routine's errors | `VENDOR_HOMING_TRACE.md` section 3 |
| `vendor_home.ERR_NOT_FOUND` | 563 | vendor | the vendor's "home switch not found" number; the vendor never raises it in these routines, we do | `VENDOR_HOMING_TRACE.md` section 3 |
| `vendor_home.ERR_STALL`, `vendor_home.ERR_DIRECTION`, `vendor_home.ERR_TRAVEL`, `vendor_home.ERR_GRIPPER`, `vendor_home.ERR_BAD_REPLY`, `vendor_home.ERR_USB`, `vendor_home.ERR_LAG` | 570, 571, 572, 574, 575, 577, 578 | ours | our fault codes, placed in the vendor's 5xx range; not checked against the vendor's own list for clashes | **unverified** |
| `vendor_home.ERR_STOPPED` | 14 | ours | the worker's `STOPPED`, added to `openScorbot/libcomm.py` in `2612bd1` | n/a |
| `vendor_home.RAMP_COUNTS_PER_CYCLE` | 6 | ours | planner moves are straight ramps, not the vendor's S-curve (module docstring) | n/a |
| `vendor_home.SETTLE_COUNTS` | 8 | ours | settle band; the vendor's `ExactEpsilon` is 5 or 20 per axis | **unverified** |
| `vendor_home.DIRECTION_FAULT_COUNTS` | 150 | ours | stops a wrong `4D` sign within a few degrees | n/a |
| `vendor_home.TICK_GRACE_CYCLES`, `vendor_home.TICK_GRACE_MARGIN` | 60, 15 | ours | the controller may run about 47 messages behind | PL07 |
| `vendor_home.MAX_LAG` | 60 | ours | the vendor paces at `HomingBuffers` 20 and never needs it; ours does not pace that way (to-do 1) | PL03, PL07 |
| `vendor_home.TICK_STALL_LIMIT` | 4 | ours | a type 16 search has no stall check in the vendor; 4 copies its type 14 counter | `VENDOR_HOMING_TRACE.md` section 3 |
| `vendor_home_source.VENDOR_HOME_ORDER` | 22 | ours | internal worker order code (`68fe9f1`), never on the wire | n/a |
| `vendor_home_source.TOTAL_SECONDS` | 800 | ours | bound on the whole Home: every `MaxTime` in both passes adds to about 730 s | `VENDOR_HOMING_TRACE.md` section 3 table |
| `vendor_home_model.DRIVE_GAIN`, `vendor_home_model.FOLLOW_STEP`, `vendor_home_model.BANDS`, `vendor_home_model.GRIPPER_GAIN`, `vendor_home_model.GRIPPER_START` | 0.1, 400, switch bands, 0.5, 3000 | ours | the fake arm the Home is tested against (same model as `tools/usbc_fake`); not physics | n/a |
| `vendor_home_model.GRIPPER_STOPS` | -200, 6000 | vendor | gripper `EncLimit_1`/`_2` | `VENDOR_DLL_PROTOCOL.md` section 11.5 |

### Streaming, pacing and the policy clamp

| Constant | Value | Tag | Source, or why it is ours | Backed by |
|---|---|---|---|---|
| `streaming.DEFAULT_PERIOD_S` | 0.024 | vendor | `PCPeriod` x `USBCPeriod` = 16 x 1.5 ms (`0x10014eff`, tick `0x10025c6c`) | `VENDOR_DLL_PROTOCOL.md` section 11.6 |
| `streaming.VENDOR_MAX_SPEED_COUNTS_S` | 6500 | vendor | `ER4AxN.ini MaxSpeed`; counts/s is inferred | `VENDOR_DLL_PROTOCOL.md` sections 11.5, 11.6 |
| `streaming.DEFAULT_ERROR_LIMIT` | 40 | legacy | `conf.py MAX_ERROR` (in `43b03b1`) | `VENDOR_HOMING_TRACE.md` section 7 ("error word 40") |
| `streaming.STREAM_ORDER` | 21 | ours | internal worker order code, added to `openScorbot/libcomm.py` in `f4b0613` (2026-10-04); never on the wire | n/a |
| `streaming.DEFAULT_HOLD_TIMEOUT_S`, `streaming.PERIOD_RANGE_S`, `streaming.HOLD_TIMEOUT_RANGE_S` | 0.5, 0.01-0.05, 0.05-2.0 | ours | stream safety: hold when targets stop; the accepted range | n/a |
| `streaming.DEFAULT_SPEED_FRACTION` | 0.25 | ours | start at a quarter of the vendor limit | n/a |
| `streaming.MAX_STREAM_STEPS` | 20000 | ours | step records kept for the log | n/a |
| `streaming.MOTORS` | `limits.ARM_MOTORS` | ours | alias | n/a |
| `streaming.TRACKING`, `streaming.HOLDING`, `streaming.STOPPED`, `streaming.FAULTED`, `streaming.ENDED`, `streaming.SEND`, `streaming.STOP`, `streaming.END` | state and action names | ours | internal labels | n/a |
| `policy_arm.MOTORS` | `limits.ARM_MOTORS` | ours | alias | n/a |
| `policy_arm.DEFAULT_TRAVEL_CAP_DEG`, `policy_arm.LEAD_LIMIT_DEG`, `policy_arm.LEAD_MARGIN_DEG`, `policy_arm.STEP_MAX_DEG` | 2.0, 2.0, 1.5, 0.5 | ours | the PolicyArm clamp | n/a |
| `vendor_profile.MIN_TOTAL_TIME` | 0.001 | vendor | the profile set-up rejects T below 1 ms (`0x1001295d`) | `VENDOR_DLL_PROTOCOL.md` section 11.1 |
| `vendor_profile.DEFAULT_TOTAL_TIME`, `vendor_profile.DEFAULT_ACCEL_FRACTION`, `vendor_profile.DEFAULT_JERK_FRACTION` | 3.0, 0.3, 0.3 | vendor | `ROB_4u.INI [Motion] TotalTimeA`, `AccelA`, `AccelAccelA` | `VENDOR_DLL_PROTOCOL.md` section 11.1 |
| `vendor_profile.MANUAL_ACCEL_FRACTION`, `vendor_profile.MANUAL_JERK_FRACTION` | 0.3, 0.05 | vendor | the velocity jog's A and J (`MoveManual`, `0x1001ba0c`) | `VENDOR_DLL_PROTOCOL.md` section 11.4 |

### Limits and moves

| Constant | Value | Tag | Source, or why it is ours | Backed by |
|---|---|---|---|---|
| `limits.ARM_MOTORS`, `limits.WRIST_MOTORS`, `limits.RECORDED_MOTORS` | motor names | ours | which motors may move (wrist jogs disabled) and which are logged | n/a |
| `limits.MAX_JOG_DEG` | 5.0 | ours | SDK jog ceiling | **unverified** |
| `limits.TRAVEL_CAP_DEG` | 180.0 | ours | lifted from 10 on 2026-10-06; the source model's limits bound travel | n/a |
| `limits.DRIFT_COUNTS` | 20 | legacy | the legacy jog's 20-count settle loop (`libcomm.move_hips` and siblings, in `43b03b1`) | **unverified** |
| `limits.STABLE_COUNTS` | 2 | ours | lab idle band | n/a |
| `joint_move.MOTORS` | `source_model.MOTORS` | ours | alias | n/a |
| `joint_move.JOINTS`, `joint_move.ARM_MOTORS` | base, shoulder, elbow | ours | what a coupled move may be asked for | n/a |
| `joint_move.PRIMARY_MIN_COUNTS` | 20 | ours | equals the legacy settle band, below which a jog cannot correct | n/a |
| `joint_move.SLICE_DEG`, `joint_move.COUPLED_MIN_COUNTS`, `joint_move.FINAL_MIN_COUNTS`, `joint_move.STEP_LIMIT_COUNTS`, `joint_move.PROGRESS_CHECK_COUNTS`, `joint_move.PRE_HOME_STEP_DEG`, `joint_move.PRE_HOME_CAP_DEG`, `joint_move.PARK_MAX_PASSES`, `joint_move.ROLL_FAULT_COUNTS`, `joint_move._VECTORS` | see `joint_move.py` | ours | coupled-move slicing and fault limits | n/a |
| `inch_home.ORDER` | shoulder, elbow, base | vendor | the `HomingSeq` order, limited to the joints the SDK may move | `VENDOR_HOMING_TRACE.md` section 1 |
| `inch_home.APPROACH_COUNTS` | +1, -1, -1 | vendor | final approach direction, inferred from the vendor's drive signs; the legacy search agrees | `VENDOR_HOMING_TRACE.md` summary table (marked I) |
| `inch_home.OFFSET_COUNTS` | -190, 45, 0 | vendor | `ER4Ax [Homing] Offset` | `VENDOR_HOMING_TRACE.md` section 3 table |
| `inch_home.AXIS_SECONDS`, `inch_home.COARSE_DEG`, `inch_home.FINE_DEG`, `inch_home.PROGRESS_CHECK_COUNTS`, `inch_home.DEFAULT_SEARCH_DEG`, `inch_home.EDGE_CAP_DEG` | see `inch_home.py` | ours | our jog-by-jog home search | n/a |

### Geometry and calibration priors

| Constant | Value | Tag | Source, or why it is ours | Backed by |
|---|---|---|---|---|
| `vendor_model.ER4U` | `NoEnc90`, `HorizPos`, `Gearing` per axis | vendor | `ER4AxN.ini` / `ROB_4u.INI` (loaders `0x100043b2`, `0x10003655`); formula `0x100303db` | `VENDOR_DLL_PROTOCOL.md` section 10 |
| `vendor_model.STATUS`, `vendor_model.MOTORS`, `vendor_model.JOINTS` | text, names | ours | labels | n/a |
| `source_model.COUNTS_PER_DEGREE` | from `vendor_model.ER4U` | vendor | `NoEnc90` / 90 | `VENDOR_DLL_PROTOCOL.md` section 10.1 |
| `source_model.HOME_ANGLES` | angles at zero counts | vendor | the vendor's nominal zero-count pose | `VENDOR_DLL_PROTOCOL.md` section 10.3 |
| `source_model.DH_OFFSETS_MM`, `source_model.DH_LENGTHS_MM` | 349, 145.125; 16, 221, 221 | vendor | `ROB_4u.INI` and the USNA toolbox `ScorDHtable.m` | **unverified** |
| `source_model.LIMITS_DEG` | per joint | vendor | the narrower of the USNA toolbox's `ScorBSEPRLimits.m` and `ROB_4u.INI` | **unverified** |
| `source_model.TIER`, `source_model.STATUS`, `source_model.JOINTS`, `source_model.MOTORS`, `source_model._AXES` | labels, aliases | ours | labels and aliases | n/a |
| `nominal.ER4U_MANUAL`, `nominal.ER4U_CATALOG`, `nominal.SPECS`, `nominal.SIDE_VIEW`, `nominal.PARTS_LIST` | citations | vendor | the ER-4u user manual #100343 Rev. B | **unverified** (manual, not `docs/protocol/`) |
| `nominal.GEAR_RATIO_ARM`, `nominal.GEAR_RATIO_ARM_PARTS_LIST`, `nominal.GEAR_RATIO_WRIST`, `nominal.GEAR_RATIO_GRIPPER`, `nominal.AXIS_RANGES`, `nominal.SHOULDER_AXIS_HEIGHT_MM`, `nominal.BASE_PEDESTAL_HEIGHT_MM`, `nominal.UPPER_ARM_MM`, `nominal.FOREARM_MM`, `nominal.VERTICAL_ENVELOPE_MM`, `nominal.MAX_OPERATING_RADIUS_MM`, `nominal.REPEATABILITY_MM`, `nominal.MAX_PAYLOAD_KG`, `nominal.MAX_PATH_SPEED_MM_S`, `nominal.ARM_AMBIENT_MIN_C`, `nominal.ARM_AMBIENT_MAX_C` | manual values | vendor | user manual pp. 4-6 and p. 28, cited per value in `nominal.py` | **unverified** (manual) |
| `nominal.LEGACY_BASE_COUNTS_PER_DEGREE` | 2837 / 20 | legacy | `openScorbot/motion_profile.COUNTS_PER_DEGREE` | `VENDOR_DLL_PROTOCOL.md` section 10.5 |
| `nominal.HYPOTHESIS_COUNTS_PER_MOTOR_REV` | derived | ours | a hypothesis from the legacy scale and the gear ratio | n/a |
| `nominal.VENDOR_INI`, `nominal.VENDOR_STATUS` | citation, status text | ours | labels | n/a |
| `nominal.VENDOR_COUNTS_PER_DEGREE` | `NoEnc90` / 90 | vendor | `ER4Ax1-5.ini NoEnc90` | `VENDOR_DLL_PROTOCOL.md` section 10.1 |
| `nominal.VENDOR_BASE_HEIGHT_MM`, `nominal.VENDOR_UPPER_ARM_MM`, `nominal.VENDOR_FOREARM_MM`, `nominal.VENDOR_GRIPPER_LENGTH_MM` | 349, 221, 221, 145 | vendor | `ROB_4u.INI` | **unverified** |
| `nominal.VENDOR_JOINT_LIMITS_DEG` | per joint | vendor | `ROB_4u.INI` angle limits | **unverified** |
| `nominal.VENDOR_ENCODER_SOFT_LIMITS` | per motor | vendor | `ER4AxN.ini EncLimit_1`/`_2` | `VENDOR_DLL_PROTOCOL.md` section 11.5 |
| `nominal.VENDOR_LIMIT_REPORT_STATUS` | text | ours | label | n/a |
| `nominal.DATASHEET`, `nominal.DATASHEET_JOINT_SPEED_DEG_S`, `nominal.DATASHEET_PATH_SPEED_MM_S`, `nominal.DATASHEET_SHOULDER_SPAN_DEG` | datasheet values | vendor | ER-4u datasheet 35-1005-8600 Rev K | **unverified** (datasheet) |
| `nominal.STATUS`, `nominal._SIGN_NOTE`, `nominal.VERDICTS`, `nominal.CONTRADICTIONS` | text | ours | labels and our reading of where sources disagree | n/a |
| `kinematics.JOINT_NAMES` | joint names | ours | labels | n/a |
| `kinematics.NOMINAL` | DH defaults | legacy | `conf.py link-offset` / link lengths (in `43b03b1`) | **unverified** |
| `arm_chain.JOINTS`, `arm_chain.LINKS`, `arm_chain.STATUS` | names, text | ours | labels | n/a |
| `arm_chain.MESH_MODEL` | community CAD dimensions | ours | a third-party CAD model, used only to place its own meshes | **unverified** |
| `arm_chain.MANUAL` | from `nominal` | vendor | user manual | **unverified** (manual) |
| `arm_chain.VENDOR_INI` | from `nominal` | vendor | `ROB_4u.INI` | **unverified** |
| `calibration.CALIBRATED_JOINTS` | base, shoulder, elbow | ours | the joints calibration fits | n/a |
| `mujoco_twin.CAMERAS`, `mujoco_twin._AXES` | names, axis vectors | ours | simulation twin set-up | n/a |
| `bridge.XYZ_UNITS_PER_MM` | 1000 | vendor | the DLL's xyz unit, read back on the fake (2026-10-20); physical scale unmeasured | **unverified** |
| `bridge.HOST`, `bridge.PORT`, `bridge.POSITIONS`, `bridge.BOX`, `bridge.REPLY_TIMEOUT_S`, `bridge.POLL_S`, `bridge.LINE_END`, `bridge.FAILURE_CODES` | local server settings | ours | our link to `tools/usbc_server` and its exit codes | n/a |

### Simulator, lab tools, session log and camera

None of these reach the controller.

| Constant | Value | Tag | Source, or why it is ours | Backed by |
|---|---|---|---|---|
| `simulated._EXIT`, `simulated._MOTORS_OFF`, `simulated._MOTORS_ON`, `simulated._HOME`, `simulated._JOG_ORDERS`, `simulated._GRIPPER_OPEN`, `simulated._GRIPPER_CLOSE` | 528, 16, 17, 18, 4-13, 14, 15 | legacy | the legacy worker's order codes (`conf.py EXIT`, `libcomm.execute`, in `43b03b1`) | n/a |
| `simulated.MAX_COUNT` | 65535 | legacy | as `transport.codec.MAX_COUNT` | `VENDOR_DLL_PROTOCOL.md` section 6 |
| `simulated._ERROR_UNKNOWN_ORDER`, `simulated._ERROR_INJECTED`, `simulated._ERROR_COUNT_RANGE`, `simulated._ERROR_MOTORS_OFF`, `simulated._ERROR_HOME_SEARCH`, `simulated._ERROR_NO_SETTLE`, `simulated._STOPPED` | 1, 3, 4, 5, 2, 2, 14 | ours | result codes the simulator puts in the result queue, matching what the worker reports | n/a |
| `simulated.FAULT_KINDS`, `simulated.ARM_MOTORS`, `simulated.REHEARSAL_PROFILE` | names, profile | ours | simulator set-up | n/a |
| `lab.camera.CAMERA_ID`, `lab.faults.COMMON_STEPS`, `lab.faults._RULES`, `lab.faults._UNKNOWN`, `lab.moves.RETURN_ORDER`, `lab.moves.MAX_MARKS`, `lab.moves.STEP_DEG`, `lab.moves.HALF_STEP_DEG`, `lab.moves._TOLERANCE`, `lab.operator.ENTER`, `lab.operator.TICK`, `lab.operator.CHOICE_ATTEMPTS`, `lab.profile.FIELDS`, `lab.profile.LABELS`, `lab.profile.EXAMPLE_VALUES`, `lab.profile.PROFILE_ATTEMPTS`, `lab.review.LAB_LED_STEPS`, `lab.review.UNJOGGED_TOLERANCE`, `lab.terminal.SPECIAL`, `lab.terminal.RELEASE_POLL_S`, `lab.terminal.RELEASE_WARN_S`, `lab.teleop.TELEOP_IDLE_DISARM_S`, `lab.teleop.TICK_S`, `lab.teleop.HELP`, `lab.__main__._REPO_ROOT` | text, keys, timings | ours | lab tool prompts, keys and timings | n/a |
| `lab.session.STEPS`, `lab.session.TRAVEL_CAP_DEG`, `lab.session.ARM_MOTORS`, `lab.session.IDLE_DISARM_S`, `lab.session.IDLE_SAMPLES`, `lab.session.STABLE_COUNTS`, `lab.session.DRIFT_COUNTS`, `lab.session.JOG_KEYS`, `lab.session.CHECKLIST`, `lab.session.MOTORS_KEYS`, `lab.session.POWER_KEYS`, `lab.session.DIRECTION_KEYS`, `lab.session.YES_NO_UNSURE`, `lab.session.HELP`, `lab.session._MISMATCH_TEXT`, `lab.session.PRE_HOME_JOINTS`, `lab.session._PRE_HOME_COMMON`, `lab.session._PRE_HOME_END`, `lab.session.PRE_HOME_HELP`, `lab.session.PRE_HOME_HELP_COUPLED`, `lab.session.EXIT_OK`, `lab.session.EXIT_FAILED`, `lab.session.EXIT_DECLINED` | text, keys, aliases of `limits` | ours | the lab session's prompts, keys and exit codes | n/a |
| `session.__main__.SUBCOMMANDS`, `session.analysis.AMBIGUOUS`, `session.analysis._FORMULA_START`, `session.analysis.STATE_COLUMNS`, `session.analysis.EVENT_COLUMNS`, `session.analysis.COMMAND_COLUMNS`, `session.analysis.NOTES_COLUMNS`, `session.notes.NOTES_SCHEMA`, `session.notes.FREE_NOTES_HEADING`, `session.notes.YES_NO`, `session.notes.RUN_ENDINGS`, `session.notes.FIELDS`, `session.notes._BY_LABEL`, `session.notes._LABELS`, `session.notes._FIELD_LINE`, `session.plot.THEMES`, `session.plot.DURATION_NOTE`, `session.plot.HEADER_INCHES`, `session.record.IMAGE_FORMATS`, `session.record._REPO_ROOT`, `session.record.FSYNC_INTERVAL_NS`, `session.record.REPLACE_ATTEMPTS`, `session.record.REPLACE_RETRY_S`, `session.rerun_view.APPLICATION_ID`, `session.rerun_view._LOG_TOPICS`, `session.schemas.SCHEMA_VERSION`, `session.schemas.DATA_SOURCES`, `session.schemas.COMMAND_STATUSES`, `session.schemas.EPISODE_EVENTS`, `session.schemas.EPISODE_STATUSES`, `session.schemas.TOPICS`, `session.schemas.REQUIRED`, `session.schemas._CAMERA_TOPIC`, `session.schemas._REC`, `session.schemas.PROPERTIES` | schema, columns, text | ours | the session log format and its tools | n/a |
| `session.replay._KNOWN_OPCODES`, `session.replay._MAGIC`, `session.replay._MAX_RECORD`, `session.schemas.IMAGE_SCHEMA`, `session.replay._IDENTITY_KEYS` | MCAP opcodes and magic, Foxglove schema name | ours | the MCAP and Foxglove file formats, not the controller | n/a |
| `camera.recorder.EMPTY_READS_BEFORE_FAILURE`, `camera.recorder.HEALTH_ROWS_KEPT`, `camera.source._BLOCK`, `camera.stream.STREAM_VERSION`, `camera.stream.STREAM_METADATA`, `camera.stream._IDENTITY` | camera settings | ours | camera recording | n/a |
| `provenance._SOURCE_FILES` | file list | ours | which legacy files are hashed into a session's provenance | n/a |

## 3. Corrections found while tagging

- **Order 21 (stream) and order 22 (vendor Home) are ours, not legacy.** Both were added to `openScorbot/libcomm.py` by this project (`f4b0613`, `68fe9f1`), as was `STOPPED = 14` (`2612bd1`). None is a wire byte: they are codes on the worker's own queue. `MAX_ERROR = 40` is genuinely legacy (`43b03b1`).
- **The motors-off gap is closed, shutdown is not.** `DESK_BYTE_CHECK.md` already compared both: the 26-message disable matches the DLL. The shutdown differs by one extra group.
- **`LEDGER.md` PL26 is stale.** It says the gripper drive must be quiet twice in a row. Since #72 `GRIPPER_STALL_CHECKS` is 1, the vendor's value. The `vendor_home.py` docstring still names `STALL_CYCLES` and `GRIPPER_MIN_TRAVEL`, which #72 removed.
- **Gripper `MaxTime`.** `AXES` turns every other axis's `MaxTime` from milliseconds into seconds (110000 to 110.0), but the gripper's 15 into 15.0 s. Either the file's 15 is seconds for this axis or the gripper gets 1000 times the vendor's bound. The gripper ends on its 120-reply countdown first, so it does not change behaviour today.

## 4. Ranked to-do

Ranked by whether the gap reaches the controller, then by how soon it runs on the arm.

1. **Pace the vendor Home at `HomingBuffers` (20).** The vendor keeps at most 20 messages in flight while homing (PL03); our Home does not pace that way, and guards the gap with `MAX_LAG` 60 and a lag-based stall grace instead. This is the Home the lab will run. Close it by wiring `pacing` into the order-22 loop with a limit of 20 and checking the trace still matches (PL10).
2. **Decide on the extra disconnect group.** `scorbotoff` sends `47 4F FF 53 73 20 42 20 64 61` that the DLL does not (`DESK_BYTE_CHECK.md`). It reaches the arm on every disconnect. Either drop it to match the DLL, or keep it and record why. Needs only the desk fake.
3. **Decode the per-axis flag word at `+0x83e` (PL94).** It decides when the vendor zeroes the gripper count after a close (`48 20 0`, PL24) and when `OpenGripper` makes a 70 mm position move instead of a drive (PL28). Close it in Ghidra by reading its writers.
4. **Settle the gripper `MaxTime` unit** (section 3). Desk only: read the loader at `0x100043b2` for the `MaxTime` field.
5. **Check our Home fault codes (570-578) against the vendor's error list,** so a log never shows a vendor number with our meaning.
6. **Compare the legacy gripper sequence with the vendor's on the fake.** Stack 2 still sends `libhex.clamp` (`4F 20 53`, `4C 20 00`, a position ramp). The vendor sends `4F 20 54` and one `4D` drive (`VENDOR_DLL_PROTOCOL.md` section 12.2). Not on the lab plan for this visit.
7. **Not on the wire, lowest:**
   - The stream's error limit 40 is a threshold on the reply, not a value sent.
   - Orders 21 and 22 are internal.
   - The "unverified" geometry rows (`ROB_4u.INI` link lengths and angle limits, manual and datasheet values) need a note in `docs/protocol/` or `docs/manual/` that quotes them.
   - Fix PL26 and the `vendor_home.py` docstring (section 3).
