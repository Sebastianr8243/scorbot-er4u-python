# Arm API reference

Purpose: look up every call of `scorbot.bridge.Arm`, its units, and when it refuses or raises.

`scorbot.bridge.Arm` is a client of `tools/usbc_server`, which keeps one homed vendor-DLL session open. It uses only the standard library. `FakeArm` has the same calls, keeps positions in memory and needs no server.

Before you call anything on the real arm: start the server ([lab/RUN_THE_ARM.md](lab/RUN_THE_ARM.md), section 3) and keep a hand near the red stop. The red stop is the only emergency stop.

```python
from scorbot.bridge import Arm, ArmError

with Arm() as arm:                     # connects to 127.0.0.1:8765
    arm.speed(20)                      # start slow
    arm.move_by(dz=20)
    arm.gripper(open=False)
    print(arm.where())
```

## Units

| Quantity | Unit | Note |
|---|---|---|
| x, y, z, distances | mm, in the DLL's own task frame | The origin and axis directions are whatever the DLL uses after Home. `examples/arm_characterise.py` measures them |
| Wire format to the DLL's Teach | micrometres | The DLL's xyz array is read back at 1000 units per mm (`XYZ_UNITS_PER_MM`). This held in the DLL's own model (a 10 mm step read back as 9,996 units on the fake controller). The physical scale is **not measured** |
| `pitch`, `roll` | the DLL's own units, passed through | Desk runs suggest 0.001 degree per unit ([DESK_FINDINGS.md](DESK_FINDINGS.md)). The wrist moves in whole motor counts of about 0.036 degree, so a request of 100 moved about 71. Unverified on the arm |

## Calls

Every call returns a `Position` unless noted. Every refusal or failed DLL call raises `ArmError` (see [Errors](#errors)).

### where

**Syntax:** `arm.where()`

Reads the current position. Returns a `Position`: `x`, `y`, `z` in mm; `pitch`, `roll`; and the raw `encoder`, `joint` and `xyz` arrays. Its `timing` dict is empty.

### home

**Syntax:** `arm.home()`

Runs the vendor Home. Takes about 135 s on the lab arm. It closes the gripper and moves the wrist. Clear the arm's path first.

### speed

**Syntax:** `arm.speed(percent)`

**Inputs:** `percent`, a whole number from 1 to 100.
**Refused when:** the value is outside 1 to 100.
**Note:** an `ok` reply does not prove the arm is slow. Time it ([lab/LAB_RUN_SHEET.md](lab/LAB_RUN_SHEET.md), step 2).

### move_by

**Syntax:** `arm.move_by(dx=0, dy=0, dz=0)`

Relative move of the tool in mm.

**Inputs:** `dx`, `dy`, `dz`, numbers in mm.
**Returns:** a `Position` whose `timing` dict holds the seconds the server spent per step: `read`, `define`, `teach`, `move`, `settle`. `define` is 0 after the first move of a session.
**Refused when:** any axis is above 50 mm, a value is not finite, or (once `arm_characterise.py` has measured the box in `calibration/arm.json`) the step ends further outside the box than it starts. A step back toward the box is allowed.
**Example:** `arm.move_by(dz=20)` raises the tool 20 mm.

### wrist_by

**Syntax:** `arm.wrist_by(dpitch=0, droll=0)`

Relative wrist step in the DLL's pitch and roll units. The tool tip stays put in the DLL's model.

**Refused when:** a step is above 10000 units on one axis, rounds to zero, or is not finite. The box does not apply to wrist steps.

### move_to

**Syntax:** `arm.move_to(x, y, z, pitch=None, roll=None)`

Absolute move in mm. A target is never clipped; it is refused.

**Refused when:** this session has not homed, there is no box file, or the target is outside the box.

### gripper

**Syntax:** `arm.gripper(open)`

**Inputs:** `open=True` opens the jaws, `open=False` closes them.
**Returns:** a `Position` whose `timing` dict has `call` and `settle`.
**Refused when:** the emergency stop input is active.

### stop

**Syntax:** `arm.stop()`

Asks the server to call the DLL's Stop. Safe to call from another thread during a move. **This is not an emergency stop.**

### quit

**Syntax:** `arm.quit()`

Ends the session: Control off, then CloseUSBC. The next client must start the server again.

### teach

**Syntax:** `arm.teach(name)`

Saves the current spot to `calibration/positions.json` and grows the box to include it.

### go

**Syntax:** `arm.go(name)`

`move_to` a taught spot. **Refused when:** the name is unknown, plus every `move_to` condition.

### run

**Syntax:** `arm.run(commands)`

Sends `{"op": ...}` dicts in order and stops at the first failure.

## Errors

Every refusal or failed DLL call raises `ArmError`; `error.reply` holds the server's whole answer. `Arm()` raises it too if no server is listening.

- Ctrl+C during a move calls `stop()` before re-raising. A client that disconnects mid-move gets the arm stopped. A client that disconnects otherwise leaves the homed session for the next one.
- The server serves one client at a time. A second connection gets `{"ok": false, "busy": true, "error": ...}` between commands and is closed; during a move it waits until the move ends. `Arm` raises that reply as an `ArmError`.
- The server logs every command and every reply to `logs/server-<time>.jsonl`. `python scripts/summarize_log.py <log>` prints it as one row per command. The log records what was asked and answered, not whether the arm physically moved.

## The box

`move_to` and `go` only run inside `calibration/arm.json`, the region the arm has been driven in under supervision. `arm_characterise.py` writes it and `teach` grows it. The server and the scripts must start in the same folder, because the path is relative.

## Command line

```powershell
python -m scorbot.bridge [--port N] [--calibration-dir DIR] where | by DX DY DZ | to X Y Z | open | close | speed N | home | teach NAME | go NAME | wrist DPITCH DROLL | stop | quit
```

`--calibration-dir DIR` (default `calibration`) is where `teach` and `go` keep `positions.json` and `arm.json`: one folder per arm. Start the server with `--box DIR\arm.json` so it checks the same box ([lab/RUN_THE_ARM.md](lab/RUN_THE_ARM.md), section 5).

## See also

- [LeRobot adapter](../plugins/lerobot_robot_scorbot_stack1/README.md): wraps `Arm` as a LeRobot-style `Robot` with a safety layer (step over 50 mm, target outside the box and any move before Home are refused; an arm error calls `stop()` and latches). `python examples/sim_demo.py` runs it on `FakeArm`; `python examples/lerobot_install_check.py` and `python examples/lerobot_record_check.py` check it against an installed `lerobot` (0.6.1, `FakeArm` only; the second runs the real `lerobot-record` with `--robot.simulate=true` and loads the dataset back). Unverified on the arm.
- [Requirements A01 to A10](requirements/arm-api.md): the guarantees above and the test behind each.
- [STATUS.md](STATUS.md): how sure we are about each call.
