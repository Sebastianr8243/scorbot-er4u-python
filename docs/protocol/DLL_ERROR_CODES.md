# Error codes: the lab `USBC.dll` against Stack 2 (2026-11-04, issue #152 item 3)

Every error code the lab `USBC.dll` (532,576 bytes, SHA-256 `6118465d...bd821`) raises or has text for, with its meaning, what the DLL does when it reports it, and whether Stack 2 handles it. A second table covers the reply bits the DLL turns into errors, and a third Stack 2's own codes. Nothing here has been run on the arm.

## Method

- **The DLL's text** comes from its error reporter `0x10013ce2`, which every error passes through. It picks a text per code (read from the DLL file), calls `Stop('&')` itself for some codes, logs "Error Message (n) Axis a text", and hands the code to the host's error callback. Codes it has no text for are reported with the number alone. (V)
- **Names** are from Intelitek's `ERROR.H` (SDK, Visual Studio 2008 project, outside the repo; PL60). The comments in `ERROR.H` are used only where the DLL has no text of its own.
- **Raise sites** were found by searching the lab decompile for each `ERROR.H` number used as a literal in an error context: the error field `+0x81e`, the pending error `0x1008abae`, a return value, or the reporter's argument. Addresses are the lab build's.
  - A code reached only through a variable may have more sites than listed.
  - "Text only" means the reporter has text for it but no literal raise site was found.
  - Hits inside the C runtime and MFC (`0x1004xxxx` and up: 40, 60, 512) are not DLL errors and are left out.
- **What `Stop('&')` does:** it sends `47`, `73 FF`, `42 FF`: clear the controller's buffer, then control and motors off for every axis. This is the desk recording of PR #102's "Stop('&') after 0.6 s" jog (`tests/fixtures/dll_lab_manual.json` on branch `stack2-velocity-jog`). So a code marked "Stop" in the tables below switches the motors off.
- Tags: V means read in the code, I inferred.

## 1. DLL error codes

| Code | `ERROR.H` name | DLL text (reporter `0x10013ce2`) | Raised by (lab addresses) | DLL reaction | Stack 2 |
|---|---|---|---|---|---|
| 2 | `ERR_TR_SND` | "Invalid simulation mode" (passed with the call) | `0x10006c17` | | n/a: no simulation mode in the DLL sense |
| 5 | `ERR_SKIN` | "Cannot execute movement along a circular path" | text only | Stop | n/a: no circular moves |
| 11 | `KIN_CFG` | "Kinematics: invalid configuration parameters" | text only | Stop | n/a |
| 20 | `ERR_NOT_RESPOND` | "Controller is not responding" | monitor loop `0x10019dfc`, once, after initialisation (`0x1008aa20` = 1) when the USB loop has marked the link failed (`0x1008aa24` = 0, set on a failed `WriteFile`): it stops the link and resets the arm state (`0x10023df6('&')`) first | clears reporter field `+0xb4` | partial: a lost link is detected from read errors (PL53), raised as 577 in vendor Home and "USB link lost" in `scorbot/lab/faults.py`; no code 20 |
| 201 | `ERR_TRCK` | "Position error during the motion is too great or impact has occurred" | planner tick `0x10025afc` (signed error word above `ImpactDetect`, PL98); homing `0x1000805b`, `0x1000823d` and `0x1000ad0c` (any bit of reply byte 59); the `Impact` export (`0x1001ca7a`, through `0x10024e97`), which works only offline or in simulation (online it returns 0). The monitor loop does not report 201 for an axis whose type has bit 1 (the gripper) | | handled: idle check `robot.py` `_idle_reply`, streaming fault, legacy jogs (LF01 to LF03), vendor Home `ERR_IMPACT` |
| 300 | `ERR_EMERG_ON` | "Emergency is on" | monitor loop on reply byte 2 bit 0 turning on: `Control('&', 0)` first (V); homing waits `0x10007f04`, `0x1000805b`, `0x1000823d`, `0x10009f22`, `0x100232c0` | control off | handled: `packet.py` `on_emergency` latches even a one-reply pulse, and the motors are queued off (`robot.py` `_emergency_input`); vendor Home `ERR_EMERGENCY` |
| 301 | `ERR_EMERG_OFF` | "Emergency is off" | monitor loop on the bit turning off | | not reported: Stack 2's emergency fault stays latched until a new session, by design |
| 522 | `ERR_PF_NF` | "File path is not found." | `BuildPositForWrite` | | n/a |
| 533 | `ERR_INVPAR` | "Invalid parameter" | `Initialization`, `SetParameterFolder` | | n/a: Stack 2 reads its own copies of the parameter files |
| 561 | `ERR_HT` | "Time of homing elapsed" | homing `0x10007f04`, `0x1000823d`, `0x10009f22`, `0x1000ad0c` | | handled: vendor Home `ERR_TIMEOUT` |
| 563 | `ERR_HNF` | none | no raise site in the lab build | | Stack 2 uses 563 for its own "home switch not found" (vendor Home `ERR_NOT_FOUND`) |
| 568 | `ERR_NOT_CONNECT` | "Peripheral axis is not connected" | `Velocity` path `0x100100d7` | Stop | n/a: no peripherals |
| 569 | `ERR_HOME_ABORTED` | "Homing aborted by user" | homing `0x1000805b`, `0x1000823d`, `0x10009f22`, `0x1000ad0c` | | different number: Stack 2 reports an outside stop of Home as 14 (`ERR_STOPPED`, PL60) |
| 580 | `ERR_A_BIG` | none | jog set-up `0x1000ec6e` (code outside every axis range) | | n/a: Stack 2 checks axis names itself |
| 581 | `ERR_A_PAR` | "Invalid acceleration parameter" | move set-ups `0x1000b8e0`, `0x1000bdea`, `0x1000c2f4`, `0x1000c7fe` (planner parameter checks) | Stop | n/a: Stack 2 passes the DLL's fixed constants |
| 582 | `ERR_AA_PAR` | "Invalid parameter for increase of acceleration" | the same set-ups, and `0x1000ec6e` | Stop | n/a |
| 583 | `ERR_VELO_PAR` | "Invalid velocity" | the same set-ups | Stop | n/a |
| 586 | `ERR_MDUR` | "Invalid movement duration" | planner refused (`0x10012c6b`) in the set-ups `0x1000e4e2`, `0x1000e866`, `0x1000ec6e`; `Speed` below 1 (`0x10016f63`) | Stop | handled for `Speed`: `vendor_move` refuses `speed_percent` below 1 |
| 587 | `ERR_VEL_Z` | none | `0x10023068` | | n/a |
| 588 | `ERR_MTYP` | none | move set-up `0x10035d3b` | | n/a |
| 589 | `ERR_CTYP` | none | `0x10035d3b` | | n/a |
| 602 | `ERR_GRP_NSP` | none | point-group checks `0x10035b0c`, `0x10035cb0`, `0x10035d3b` | | n/a |
| 605 | `ERR_INIT` | "Initialization error" | `Initialization` (driver open and handshake failures) | | partial: `Scorbot.connect` raises its own error, no code |
| 903 | `ERR_COFF` | "Control is Off" | every move and jog set-up while control is off (13 sites) | | handled: "Enable motors before moving joints" and "Enable and home before jogging" (`robot.py`), no code |
| 904 | `ERR_INCOO` | none | jog set-up `0x1000ec6e` | | n/a |
| 906 | `ERR_ENC_INV` | "Position is not in the Encoders workspace" | point check `0x10034a4a` (through the code it stored), set-ups `0x1000e866`, `0x1000ec6e`, tick `0x10025afc` | | partial: `scorbot/nominal.py` has the vendor 906/907 target checks |
| 907 | `ERR_JNT_INV` | "Position is not in the Joint workspace" | the same, and `SetJoint` (`0x10025206`) | Stop | partial: `scorbot/nominal.py` |
| 908 | `ERR_XYZ_INV` | "Position is not in the Cartesian workspace" | set-ups, tick, `BuildPositForWrite` | | n/a: no Cartesian moves |
| 909 | `ERR_TIME_PAR` | "Invalid time duration for motion" | move set-ups (a `Time` below the full-speed duration, PL97) | Stop | handled: `move_joint_vendor` refuses it |
| 910 | `ERR_AXTYPE` | "Invalid axis type" | `Velocity` `0x100100d7` (arm axes; PL74) | Stop | n/a |
| 911 | `ERR_MOVP` | "Motion in progress" | 20 sites: move, jog and Home entries while a move runs, and similar busy states | Stop | different reaction: `robot.py` refuses a second joint move and cites 911, but does not stop the running one |
| 912 | `ERR_CCHANGE` | "Robot cannot execute movement due to angle of robot arm at axis 3" | move set-ups `0x1000b8e0`, `0x1000bdea`, `0x1000c2f4` | Stop | n/a |
| 913 | `ERR_SPEED` | "Excessive speed required" | speed checks in the set-ups `0x1000e4e2`, `0x1000e866`, `0x1000ec6e`, `0x100100d7`; `Speed` above 100 | Stop | handled for `Speed`: `vendor_move` refuses above 100 |
| 914 | `ERR_ACCL` | "Excessive acceleration required" | text only | Stop | n/a |
| 916 | `ERR_RCR` | "Relative chain loops or exceeds 255 positions" | point store | | n/a |
| 924 | `ERR_GRP_INV` | "Invalid axes group" | `0x10036194` | Stop | n/a |
| 925 | `ERR_UNASS` | "Undefined position" | point store | | n/a |
| 929 | `ERR_PROC` | none | `0x100230c4` (a ramp share outside 0 to 1) | | n/a |
| 930 | `ERR_B_INV` | "Position is not in the peripheral workspace" | text only | Stop | n/a |
| 931 | `ERR_C_INV` | none | tick `0x10025afc` | | n/a |
| 935 | `ERR_MWRK` | none | move set-ups `0x1000b8e0`, `0x1000bdea`, `0x1000c2f4` | | n/a |
| 937 | `ERR_ROBOT_HM_ND` | "Robot has not been homed" | circular path `0x1000dec6`; jog set-up `0x1000ec6e` (its Cartesian branch) | | handled in effect: `move_joint_vendor` needs a fresh vendor Home |
| 938 | `ERR_AXIS_HM_ND` | "Peripheral device has not been homed" | `0x100100d7` | | n/a |
| 939 | `ERR_UNASS_ROBOT` | "The robot position is undefined" | `MoveJoint` (point not found) | Stop | n/a: Stack 2 takes targets, not stored points |
| 940 | `ERR_UNASS_PERIPH` | "The peripherals position is undefined" | `MoveJoint` | Stop | n/a |
| 950 | `ERR_CHAR_TEMPL_LIB` | "Error in character template library./nContact your product dealer." (a literal `/n` in the DLL) | `BuildPositForWrite` | | n/a |
| 961, 962, 963 | `KIN_ZERO`, `KIN_BETA`, `KIN_ALPHA` | the 908 text | kinematics `0x10031df5` | | n/a |
| 970 | `TP_TEACH` | "TP  in teach mode" (two spaces in the DLL) | monitor loop, teach flag on (PL64) | | handled: `state.py` `teach_mode_after`; streaming refuses or stops in teach mode |
| 971 | `TP_AUTO` | "TP  in auto mode" | monitor loop, teach flag off | | handled: the same flag |
| 973 | `ERR_PERIPH_CONFIG` | "Peripherals positions are not suitable for peripherals configuration" | `MoveJoint`, `MoveLinear`, `MoveCircularVect`, `0x1000e866` | | n/a |

Not in the table: the other `ERROR.H` numbers (fatal 1 to 7, power-box 511 to 517, file 523 to 536, communication 701 to 751, the remaining 9xx and the desktop range). The lab DLL has no text for them and no raise site was found. (V for this build; the search is limited as described under Method.)

## 2. Reply bits the DLL turns into errors

| Reply bits | DLL use | Error | Stack 2 |
|---|---|---|---|
| Byte 2 bit 0 (emergency input) | `0x1000b210`; the monitor loop reports on each change, and on "on" switches control off first | 300 / 301 | latched as a fault, motors queued off (`packet.py`, `robot.py`) |
| Byte 59, any bit (per-motor collision flag) | `0x1000b240`. As an error only in the homing routines (`0x1000805b`, `0x1000823d` with five checks, `0x1000ad0c`), after an axis drive. Elsewhere: `SetParameter` and `SetParameterFolder` switch control back on (`Control('&', 1)`) after a parameter download when it is set, and the USB loop logs "Impact Axes" | 201 | vendor Home raises 201. Elsewhere it is recorded only (`RobotState.collision_bits`), which matches the DLL: during moves it raises nothing on this byte. The re-enable after a parameter download is not copied (Stack 2 downloads no parameters) |
| Error word at `22 + 5N` (signed) | planner tick; above the axis's `ImpactDetect` (70 arm, 300 gripper) | 201 | handled (PL98) |
| Byte 3 (pendant) | teach flag; Abort runs `Stop('&')` (PL63, PL64) | 970 / 971 | teach flag tracked; Abort becomes `request_stop`, not `Stop('&')` |
| Bytes 15-18 (`Status`) | logged only (PL65) | none | not decoded; nothing to copy |
| No reply (link lost) | monitor loop | 20 | link loss from read errors (PL53) |

## 3. Stack 2's own codes

| Code | Where | Meaning | Clashes with a vendor number? |
|---|---|---|---|
| 1, 2, 3 | legacy worker results (`PROTOCOL.md` section 7) | 1: joint error past the limit, homing failure, or gripper time-out; 2: did not settle, homing cancelled or timed out; 3: XYZ target unreachable | 1 to 3 are vendor fatal numbers, but they never meet: legacy results are not DLL errors |
| 14 | `vendor_home.ERR_STOPPED` | a Home stopped from outside (`request_stop`, cancel) | none (PL60); the vendor uses 569 for this |
| 570 | `ERR_STALL` | homing drive stalled | none (570 to 579 are unused in `ERROR.H`) |
| 571 | `ERR_DIRECTION` | homing moved the wrong way | none |
| 572 | `ERR_TRAVEL` | homing travelled too far | none |
| 574 | `ERR_GRIPPER` | gripper drive ran out of cycles | none |
| 575 | `ERR_BAD_REPLY` | unreadable reply during Home | none |
| 577 | `ERR_USB` | USB link failed during Home | none |
| 578 | `ERR_LAG` | controller more than `MAX_LAG` messages behind | none |

## Gaps (for a later code issue; nothing is built here)

1. **`scorbot/lab/faults.py` does not know the vendor numbers.** Its rules match "timed out", "error code 1" and "error code 2", feedback, worker, LED, drift and "faulted". A vendor Home error, whose message starts "`<code>: `" (`vendor_home.HomeError`), and the emergency fault ("Controller emergency input active") both fall to "Unexpected error". The rules should cover at least 201, 300, 561, 563, 570 to 578 and 14, with the meanings above.
2. **911 reaction.** The DLL switches everything off (`Stop('&')`) when a command arrives during a move. Stack 2 only refuses it. Keeping Stack 2's behaviour is a deliberate choice that should be written down where the refusal is.
3. **569 versus 14** for a Home stopped from outside: PL60 already marks this as a post-lab choice.
4. **Workspace checks** (906, 907): `scorbot/nominal.py` has them. Whether every vendor-style move path applies them as the DLL does (at set-up and on every tick when homed) was not checked here.
5. **"Stop" codes in general.** For the codes marked Stop, the DLL turns a refused command into motors off. Stack 2 raises an exception and leaves the motors as they are. That is safer for a caller's mistake, but unlike the vendor. No change is proposed.

Everything above is **unverified on the arm**.
