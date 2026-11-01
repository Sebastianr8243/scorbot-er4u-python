# lerobot_robot_scorbot

The ScorBot ER-4U as a LeRobot `Robot`, on Stack 2 (`scorbot.policy_arm.PolicyArm` over `Scorbot.start_stream`). **Simulator only:** `simulate=false` (the default) is refused with `REAL_ARM_REFUSED`, because LeRobot's tools connect outside their cleanup and have no stop key. Nothing here has run on the arm.

Checked 2026-11-01 against `lerobot` 0.6.1 (Python 3.12.2, torch 2.11.0 CPU, `lerobot[dataset]` for `datasets` 4.8.5 and `av` 15.1.0, `ruckig` 0.19.4) with `examples/lerobot_sim_check.py`: LeRobot's plugin loader finds `robot.type=scorbot` and both teleoperators; `make_robot_from_config`, connect, observe, eight small actions and disconnect; the real `lerobot-record` for 2 episodes of 3 s at 10 fps with the scripted teleoperator and no cameras; the dataset reloaded with `LeRobotDataset` (60 frames, `robot_type` `scorbot_sim`, every action within 0.5 degree of the last, the next observation within 0.7 degree of each action); episode 0 replayed through a fresh simulated robot (observations within 0.1 degree of the recorded ones). `lerobot-record` without `--robot.simulate=true` stops with the refusal. That says nothing about the arm, a camera, a person at the keyboard teleoperator, training or `lerobot-eval`.

## Install and check (a separate venv, never the lab `.venv`)

```powershell
py -3.12 -m venv C:\envs\lerobot-stack2
C:\envs\lerobot-stack2\Scripts\python.exe -m pip install "lerobot[dataset]>=0.6,<0.7"
C:\envs\lerobot-stack2\Scripts\python.exe -m pip install -e ".[planning]"
C:\envs\lerobot-stack2\Scripts\python.exe -m pip install -e plugins/lerobot_robot_scorbot --no-deps   # LeRobot finds lerobot_robot_* by name
C:\envs\lerobot-stack2\Scripts\python.exe examples/lerobot_sim_check.py                             # all checks passed, exit 0
```

Without the `dataset` extra, `lerobot.datasets` refuses to import. On Windows set `$env:PYTHONUTF8 = "1"` before calling `lerobot-record` yourself. A `torchcodec` load error is printed and LeRobot falls back to PyAV; it does not matter without cameras.

```powershell
lerobot-record --robot.type=scorbot --robot.simulate=true --teleop.type=scorbot_joint_keys `
  --dataset.repo_id=local/my_first_demo --dataset.root=rehearsal/my_first_demo --dataset.single_task="move the arm" `
  --dataset.push_to_hub=false --dataset.num_episodes=2 --dataset.episode_time_s=20 --dataset.reset_time_s=5 --dataset.fps=10
```

## Shape

| | |
|---|---|
| Observation | `base.pos`, `shoulder.pos`, `elbow.pos`: measured degrees from home (vendor prior, uncalibrated), plus one `(height, width, 3)` frame per camera |
| Action | the same three keys, an absolute target. `PolicyArm` clamps it: 0.5 degree per call, `travel_cap_deg` from home (default 2), 1.5 degrees ahead of the measured joint. Call at least every 0.5 s or the stream holds |
| Config | `simulate` (must be true), `travel_cap_deg`, `log_dir` (the simulator's event log, default `rehearsal/lerobot`), `robot_id`, `cameras`, plus LeRobot's `id` and `calibration_dir` |
| Name | `scorbot_sim`, which `lerobot-record` writes as the dataset's `robot_type` (a v3 dataset has no `data_source` field) |

## Teleoperators

| Type | What it does |
|---|---|
| `scorbot_joint_keys` | LeRobot's `keyboard_ee` keys: left/right arrows base, up/down arrows shoulder, left/right shift elbow (which way the arm turns is unverified). Each loop with a key held moves an absolute target by `step_deg` (default and most 0.5), never past `travel_cap_deg` (default and most 2) from home, through `scorbot.policy_arm.clamp`. Needs `pynput`. **No person has tried it** |
| `scorbot_joint_scripted` | Walks `--teleop.script="base,shoulder,elbow;..."` (absolute degrees) at `step_deg` per loop, around again. For unattended runs and the check |

Named apart from the Stack 1 teleoperators (`scorbot_keys`, `scorbot_scripted` on `main`) so both plugins can share one venv. The map logic is in `teleop_map.py` and needs no lerobot.

## Not done yet

- **The gripper.** `PolicyArm` drives base, shoulder and elbow only; no `gripper.pos` in the observation or action. This is the next gap for pick-and-place demos.
- The real arm (refused on purpose), cameras with this robot, training, a policy run through `lerobot-record --policy.path`.

## Tests

`tests_extra/test_lerobot_scorbot_plugin.py`: without lerobot it uses stand-in base classes (refusal, features, a simulated drive, both teleoperator maps, the check's record command); with lerobot importable it also checks registration and `make_robot_from_config`.
