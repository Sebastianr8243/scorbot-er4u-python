# lerobot_robot_scorbot_stack1

A LeRobot-style `Robot` for Stack 1: `scorbot.bridge.Arm`, the vendor-DLL path, xyz in mm. **Unverified on the arm: nothing here has run on it.** It is also unverified against an installed `lerobot` (not installed when this was written); the adapter imports without the package and uses a stand-in base class. Requirements and the test behind each: [docs/requirements/stack1-lerobot-adapter.md](../../docs/requirements/stack1-lerobot-adapter.md).

## Try it on a laptop (one command, no arm, no DLL, no USB)

```powershell
python examples/sim_demo.py
```

Runs a scripted policy on `FakeArm`, prints each step, prints `refused: <reason>` for a move before Home, a 60 mm step and two targets outside the box, and saves an episode to `rehearsal/sim_demo-<time>/episode_000000.jsonl` (every frame has `data_source: simulated`), which it reloads and checks.

## Shape

| | |
|---|---|
| Observation | `x.mm`, `y.mm`, `z.mm`, `gripper.pos` (1 open, 0 closed; the last commanded, since `Arm` cannot read it) |
| Action | `dx.mm`, `dy.mm`, `dz.mm` (a step), `gripper.pos` (0.5 and up opens) |
| Config | `arm_id`, `host`, `port`, `calibration_dir`, `allow_real`, `box_mm`, `max_step_mm` |

Safety layer, in `send_action`, before the arm sees anything: a step over 50 mm on any axis, a target outside the box (`<calibration_dir>/arm.json`), no box, a malformed or non-finite action, or any move before `home()` raises `ActionRefused`. Any error from the arm calls `arm.stop()` once and latches the adapter (`AdapterStopped`) until `reset()`, which also needs a new `home()`. `arm.stop()` is not an emergency stop; the red stop is.

`connect()` refuses a real arm server unless the config has `allow_real=True`. `data_source` is `real` only when the server's `ping` says `simulated: false`.

```python
from lerobot_robot_scorbot_stack1 import Stack1RobotConfig, Stack1Robot

robot = Stack1Robot(Stack1RobotConfig(arm_id="arm3", port=8765, calibration_dir="calibration/arm3", allow_real=True))
robot.connect()        # needs tools/usbc_server running (docs/lab/RUN_THE_ARM.md)
robot.home()           # about 135 s on the lab arm; a person at the red stop
robot.send_action({"dx.mm": 5.0, "dy.mm": 0.0, "dz.mm": 0.0, "gripper.pos": 0.0})
```

## Episodes

`DatasetWriter(root, data_source, fps)` writes LeRobot's column names as JSON lines (`episode_index`, `frame_index`, `timestamp`, `task`, `observation.state`, `action`, `next.done`, plus `data_source`), one file per episode, never overwritten. A root holds one data source: a frame or a writer with the other one raises `MixedDataError`. It is not a `LeRobotDataset` (no parquet or video); convert later.

## Ten arms

One PC per arm, each with its own `tools/usbc_server`, `--port` and calibration folder: see "Several arms" in [docs/lab/RUN_THE_ARM.md](../../docs/lab/RUN_THE_ARM.md).

## Tests

`python -m pytest -q tests/test_lerobot_stack1.py`: FakeArm only; the test module makes `socket.create_connection` raise.
