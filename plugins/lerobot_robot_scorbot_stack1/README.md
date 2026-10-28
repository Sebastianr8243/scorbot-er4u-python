# lerobot_robot_scorbot_stack1

A LeRobot-style `Robot` for Stack 1: `scorbot.bridge.Arm`, the vendor-DLL path, xyz in mm. **Unverified on the arm: nothing here has run on it.** Against an installed `lerobot` 0.6.1 (2026-10-28, FakeArm only, torch 2.15.0 CPU although 0.6.1 asks for torch below 2.12) it passed `examples/lerobot_install_check.py` (the real `Robot` base class, registration by name, `make_robot_from_config`, `calibration_dir` as str or Path, a FakeArm drive, the `--robot.type=scorbot_stack1` parse) and `examples/lerobot_record_check.py` (the real `lerobot-record` for 2 episodes, with no cameras and with two fake cameras and video on; the datasets load back with `LeRobotDataset` and every state is the last state plus the action). That says nothing about the arm, a real camera, a person at the keyboard teleoperator, training (never run) or `lerobot-eval`. Without the package the adapter imports and uses a stand-in base class. Requirements and the test behind each: [docs/requirements/stack1-lerobot-adapter.md](../../docs/requirements/stack1-lerobot-adapter.md).

## Try it on a laptop (one command, no arm, no DLL, no USB)

```powershell
python examples/sim_demo.py
```

Runs a scripted policy on `FakeArm`, prints each step, prints `refused: <reason>` for a move before Home, a 60 mm step and two targets outside the box, and saves an episode to `rehearsal/sim_demo-<time>/episode_000000.jsonl` (every frame has `data_source: simulated`), which it reloads and checks.

## Record a LeRobot dataset on a laptop (no arm, no DLL, no USB)

This is what students will run, rehearsed on a FakeArm. Use a throwaway venv (the repo does not depend on `lerobot`). The check venv had `lerobot` 0.6.1 plus `opencv-python-headless`, `pandas`, `pyarrow`, `jsonlines`, `av`, `datasets`, `torchvision`, `einops`, `gymnasium` and `pynput`; LeRobot's own extras `lerobot[dataset]` and `lerobot[hardware]` cover the same ground but were not tried.

```powershell
python -m pip install --no-deps -e . -e plugins/lerobot_robot_scorbot_stack1     # LeRobot finds a plugin by its installed name
$env:PYTHONUTF8 = "1"        # Windows: LeRobot's --help text has a character the default console cannot print
python examples/lerobot_record_check.py                                          # records twice and reads both datasets back

lerobot-record --robot.type=scorbot_stack1 --robot.simulate=true --teleop.type=scorbot_keys `
  --robot.cameras="{front: {type: scorbot_fake, width: 64, height: 64, fps: 10}}" `
  --dataset.repo_id=local/my_first_demo --dataset.root=rehearsal/my_first_demo --dataset.single_task="move the block" `
  --dataset.push_to_hub=false --dataset.num_episodes=2 --dataset.episode_time_s=20 --dataset.reset_time_s=5 --dataset.fps=10
```

| Piece | What it is |
|---|---|
| `--robot.simulate=true` | `connect()` builds its own `FakeArm`, already homed (`lerobot-record` never calls `home()`), with a default box of x 200 to 400, y -100 to 100, z 100 to 300 mm unless `box_mm` or `arm.json` says otherwise. It never builds an `Arm`, even with `allow_real`. The robot is then called `scorbot_stack1_sim`, and `lerobot-record` writes that as the dataset's `robot_type`: a v3 dataset has no `data_source` field, so this is where "simulated" lives. LeRobot's resume check compares `robot_type`, so a real run should not be able to add to a simulated dataset (read from its source, not run) |
| `scorbot_keys` teleop | LeRobot's `keyboard_ee` (arrows: x and y, shift: z, ctrl: gripper) scaled by `step_mm` (default 10, at most 50) to our action keys; the gripper "stay" value keeps the last state. Needs `pynput`. **No person has tried it**: which key moves the real arm which way is unverified |
| `scorbot_scripted` teleop | Replays `--teleop.script="dx,dy,dz,gripper;..."` in a loop. For unattended runs and the check, with `--robot.simulate=true` only: with `--robot.allow_real=true` it would drive the real arm with nobody at the keys (the safety layer still applies; the red stop is the only emergency stop) |
| `scorbot_fake` camera | A fake image source with a moving bar, up to two in `--robot.cameras`. Marked simulated: `connect()` refuses it next to a real arm (real and simulated data never mix) |
| Real cameras | `type: opencv` works through LeRobot's own camera classes; nothing here has run with a real camera |

Datasets from `lerobot-record` are LeRobot v3 (Parquet, plus MP4 when cameras are on). Training was not run, on purpose.

## Shape

| | |
|---|---|
| Observation | `x.mm`, `y.mm`, `z.mm`, `gripper.pos` (1 open, 0 closed; the last commanded, since `Arm` cannot read it), plus one `(height, width, 3)` frame per camera named in `cameras` |
| Action | `dx.mm`, `dy.mm`, `dz.mm` (a step), `gripper.pos` (0.5 and up opens) |
| Config | `arm_id`, `host`, `port`, `calibration_dir`, `allow_real`, `simulate`, `box_mm`, `max_step_mm`, `cameras` |

Safety layer, in `send_action`, before the arm sees anything: a step over 50 mm on any axis, a target outside the box (`<calibration_dir>/arm.json`), no box, a malformed or non-finite action, or any move before `home()` raises `ActionRefused`. Any error from the arm calls `arm.stop()` once and latches the adapter (`AdapterStopped`) until `reset()`, which also needs a new `home()`. `arm.stop()` is not an emergency stop; the red stop is.

`connect()` refuses a real arm server unless the config has `allow_real=True`. `data_source` is `real` only when the server's `ping` says `simulated: false`. A config for a real arm also refuses a server that says it is simulating (use `simulate=True` for simulated data). A camera error (no frame) is raised as it is: no `stop()`, no latch, because the arm did nothing wrong; it ends the recording.

```python
from lerobot_robot_scorbot_stack1 import Stack1RobotConfig, Stack1Robot

robot = Stack1Robot(Stack1RobotConfig(arm_id="arm3", port=8765, calibration_dir="calibration/arm3", allow_real=True))
robot.connect()        # needs tools/usbc_server running (docs/lab/RUN_THE_ARM.md)
robot.home()           # about 135 s on the lab arm; a person at the red stop
robot.send_action({"dx.mm": 5.0, "dy.mm": 0.0, "dz.mm": 0.0, "gripper.pos": 0.0})
```

## Episodes

`DatasetWriter(root, data_source, fps)` writes LeRobot's column names as JSON lines (`episode_index`, `frame_index`, `timestamp`, `task`, `observation.state`, `action`, `next.done`, plus `data_source`), one file per episode, never overwritten. A root holds one data source: a frame or a writer with the other one raises `MixedDataError`. It is not a `LeRobotDataset` (no parquet or video); use `lerobot-record` (above) for datasets LeRobot can train on. Nothing converts old JSON-lines episodes yet.

## Ten arms

One PC per arm, each with its own `tools/usbc_server`, `--port` and calibration folder: see "Ten arms" in [docs/lab/RUN_THE_ARM.md](../../docs/lab/RUN_THE_ARM.md).

## Tests

`python -m pytest -q tests/test_lerobot_stack1.py tests/test_lerobot_teleop.py tests/test_lerobot_record_check.py`: FakeArm only and no `lerobot`; the adapter test module makes `socket.create_connection` raise. The two `lerobot` checks (`examples/lerobot_install_check.py`, `examples/lerobot_record_check.py`) are run by hand in the throwaway venv.
