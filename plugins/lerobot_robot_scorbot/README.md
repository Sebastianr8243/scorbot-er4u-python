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

The keyboard teleoperator needs `pynput` even to be built (`pip install "lerobot[pynput-dep]"` in the same venv; 1.8.2 was used). Its map from LeRobot's keyboard action is tested; it has never run under `lerobot-record` or with a person at the keys.

```powershell
lerobot-record --robot.type=scorbot --robot.simulate=true --teleop.type=scorbot_joint_keys `
  --robot.operator_at_stop=true --robot.jaws_empty=true --robot.wrist_clear=true `
  --dataset.repo_id=local/my_first_demo --dataset.root=rehearsal/my_first_demo --dataset.single_task="move the arm" `
  --dataset.push_to_hub=false --dataset.num_episodes=2 --dataset.episode_time_s=20 --dataset.reset_time_s=5 --dataset.fps=10
```

## Shape

| | |
|---|---|
| Observation | `base.pos`, `shoulder.pos`, `elbow.pos`, `pitch.pos`, `roll.pos`: measured degrees from home (vendor prior, uncalibrated), plus one `(height, width, 3)` frame per camera. Pitch is the gripper's angle to the floor and roll its roll, both from the two coupled wrist motors through `scorbot.source_model` (`PolicyArm(wrist=True)`); holding pitch while the shoulder or elbow moves leaves the wrist motors still. Signs from the source model (positive pitch lifts), unverified; the legacy wrist-pitch jog moves about 1.22 times further per degree than the model says. `gripper.pos`: the measured jaw in percent open, `100 x count / RangeEnc` (0 closed, 100 open) |
| Action | the same six keys. `gripper.pos` is binary: below 50 closes, 50 or more opens, missing or not a number holds; a change pauses the stream for the blocking vendor gripper move and restarts it (that frame is slow), and `send_action` returns the value applied, 0 or 100. A fault during the move disconnects. The five joints are an absolute target. `PolicyArm` clamps it: 0.5 degree per call (or, with `step_limit=speed`, what `speed_pct` of the vendor's MaxSpeed covers since the last call, at most 1.5 degrees), both Intelitek travel windows (ROB_4u joint angles and EncLimit absolute counts) after vendor Home, 1.5 degrees ahead of the measured joint; for pitch and roll each, one wrist motor carrying both. Call at least every 0.5 s or the stream holds |
| Config | `simulate` (must be true), `operator_at_stop`, `jaws_empty`, `wrist_clear` (all three must be true, default false: connect runs the vendor Home, which moves the wrist and closes the gripper), `log_dir` (the simulator's event log, default `rehearsal/lerobot`), `robot_id`, `cameras`, plus LeRobot's `id` and `calibration_dir` |
| Name | `scorbot_sim`, which `lerobot-record` writes as the dataset's `robot_type` (a v3 dataset has no `data_source` field) |

## Teleoperators

| Type | What it does |
|---|---|
| `scorbot_joint_keys` | LeRobot's `keyboard_ee` keys: left/right arrows base, up/down arrows shoulder, left/right shift elbow (which way the arm turns is unverified); it has no wrist keys, so pitch and roll hold at home. Each loop with a key held moves an absolute target by `step_deg` (default and most 0.5), through `scorbot.policy_arm.clamp`; the robot applies both vendor travel windows using its session Home counts, with no teleoperator travel cap. Needs `pynput`. **No person has tried it** |
| `scorbot_joint_scripted` | Walks `--teleop.script="base,shoulder,elbow[,pitch,roll];..."` (absolute degrees; pitch and roll 0 when left out) at `step_deg` per loop, around again. For unattended runs and the check |

Named apart from the Stack 1 teleoperators (`scorbot_keys`, `scorbot_scripted` on `main`) so both plugins can share one venv. The map logic is in `teleop_map.py` and needs no lerobot.

PolicyArm holds the entire frame at the last allowed target when either vendor window rejects it. Policy degrees keep their existing signs and scales (`preview_jog` for the three arm motors); vendor degrees are computed from absolute counts, so positive vendor base is negative PolicyArm base. Roll can exceed 360 degrees when both windows allow it. Zero and signs on our arm remain unverified until lab G/X.

## Not done yet

- **The gripper is binary and blocking.** Open or closed only, no width or force; each change stalls the loop for the vendor move plus a stream restart. Unverified on the arm. Teleop: `scorbot_joint_keys` opens with right ctrl and closes with left ctrl; `scorbot_joint_scripted` takes an optional sixth number per waypoint (0 to 100).
- `examples/lerobot_sim_check.py` was last run against lerobot 0.6.1 with the five joints and the gripper on 2026-11-03: all six checks passed. Its replay check (0.25 degree between replayed and recorded observations) is tight and failed some runs on simulator timing alone, with or without the gripper.
- The real arm (refused on purpose), cameras with this robot, training, a policy run through `lerobot-record --policy.path`.

## Tests

`tests_extra/test_lerobot_scorbot_plugin.py`: without lerobot it uses stand-in base classes (refusal, features, a simulated drive, both teleoperator maps, the check's record command); with lerobot importable it also checks registration, `make_robot_from_config` and both teleoperators. Run the repository's full suite in the lab `.venv`: in the lerobot venv, draccus 0.11.6 installs its own top-level `tests` package, which hides this repository's `tests/` (8 modules fail to import there). In the lerobot venv run only this file.
