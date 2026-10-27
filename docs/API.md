# Arm API

`scorbot.bridge.Arm` is a client of `tools/usbc_server`, which keeps one homed vendor-DLL session open. Standard library only. Start the server first ([lab/RUN_THE_ARM.md](lab/RUN_THE_ARM.md)). `FakeArm` has the same calls, keeps positions in memory and needs no server.

```python
from scorbot.bridge import Arm, ArmError

with Arm() as arm:                     # connects to 127.0.0.1:8765
    arm.speed(20)                      # start slow
    arm.move_by(dz=20)
    arm.gripper(open=False)
    print(arm.where())
```

## Units

- Positions and distances are **mm** in the DLL's own task frame. Its origin and axis directions are whatever the DLL uses after Home; `examples/arm_characterise.py` measures them.
- The server sends micrometres to the DLL's Teach. The DLL's xyz array is read back at 1000 units per mm (`XYZ_UNITS_PER_MM`). That holds in the DLL's own model (a 10 mm step read back as 9,996 units on the fake controller); the physical scale is **unmeasured**.
- `pitch` and `roll` are the DLL's own units, passed through unchanged. Desk runs against the fake controller suggest 0.001 degree per unit ([DESK_FINDINGS.md](DESK_FINDINGS.md)); the wrist moves in whole motor counts of about 0.036 degree, so a request of 100 moved about 71. Unverified on the arm.

## Calls

| Call | Does | Refused when |
|---|---|---|
| `where()` | Returns a `Position` | |
| `home()` | Vendor Home (about 135 s on the lab arm). Closes the gripper and moves the wrist | |
| `speed(percent)` | Whole number, 1 to 100 | outside 1 to 100 |
| `move_by(dx=0, dy=0, dz=0)` | Relative move in mm | any axis above 50 mm, a non-finite value, or (once `arm_characterise.py` has measured the box in `calibration/arm.json`) a step that ends further outside the box than it starts |
| `wrist_by(dpitch=0, droll=0)` | Relative wrist step in the DLL's pitch and roll units (probably 0.001 degree, unverified); the tool tip stays put in the DLL's model | a step above 10000 units on one axis, one that rounds to zero, or a non-finite value. The box does not apply |
| `move_to(x, y, z, pitch=None, roll=None)` | Absolute move in mm | no Home in this session, no box file, or a target outside the box (never clipped) |
| `gripper(open)` | `open=True` or `False` | emergency stop active |
| `stop()` | Asks the server to Stop. Safe from another thread during a move | |
| `quit()` | Ends the session: Control off, then CloseUSBC | |
| `teach(name)` | Saves the current spot to `calibration/positions.json` and grows the box to include it | |
| `go(name)` | `move_to` a taught spot | unknown name |
| `run(commands)` | Sends `{"op": ...}` dicts in order; stops at the first failure | |

Moves and gripper calls return a `Position`: `x`, `y`, `z` in mm, `pitch`, `roll`, plus the raw `encoder`, `joint` and `xyz` arrays.

A move's `Position` also has `timing`, a dict of seconds the server spent per step: `read`, `define`, `teach`, `move`, `settle` (`define` is 0 after the first move of a session); a gripper call has `call` and `settle`. `where()` leaves it empty. `examples/arm_latency.py` prints the median per step.

## Errors

Every refusal or failed DLL call raises `ArmError`; `error.reply` holds the server's whole answer. `Arm()` raises it too if no server is listening. Ctrl+C during a move calls `stop()` before re-raising, and a client that disconnects mid-move gets the arm stopped. A client that disconnects otherwise leaves the homed session for the next one. The server serves one client at a time: a second connection gets `{"ok": false, "busy": true, "error": ...}` between commands (during a move it waits until the move ends) and is closed; `Arm` raises that as an `ArmError`.

The server logs every command and every reply to `logs/server-<time>.jsonl`. `python scripts/summarize_log.py <log>` prints it as one row per command (ok, refused with the reason, or a DLL error). It reports what was asked and answered, not whether the arm physically moved.

## The box

`move_to` and `go` only run inside `calibration/arm.json`, the region the arm has been driven in under supervision. `arm_characterise.py` writes it, and `teach` grows it. The server and the scripts must start in the same folder, because the path is relative.

## Command line

```powershell
python -m scorbot.bridge [--port N] [--calibration-dir DIR] where | by DX DY DZ | to X Y Z | open | close | speed N | home | teach NAME | go NAME | stop | quit
```

`--calibration-dir DIR` (default `calibration`) is where `teach` and `go` keep `positions.json` and `arm.json`: one folder per arm. Start the server with `--box DIR\arm.json` so it checks the same box ([lab/RUN_THE_ARM.md](lab/RUN_THE_ARM.md), "Ten arms").

## LeRobot adapter

`plugins/lerobot_robot_scorbot_stack1` wraps `Arm` as a LeRobot-style `Robot` with a safety layer in front (step over 50 mm, target outside the box and any move before Home are refused; an arm error calls `stop()` and latches). `python examples/sim_demo.py` runs it on `FakeArm`. Unverified on the arm. See its [README](../plugins/lerobot_robot_scorbot_stack1/README.md).

## Where the guarantees are tested

The requirements and the test behind each (A01 to A10) are in [REQUIREMENTS.md](../REQUIREMENTS.md).
