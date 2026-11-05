# lerobot_robot_scorbot

The ScorBot ER-4U as a LeRobot `Robot`, on Stack 2 (`scorbot.policy_arm.PolicyArm` over `Scorbot.start_stream`). **Real arm OFF by default:** `simulate=false` (the default) is refused with `REAL_ARM_REFUSED` unless `real_arm_confirmed=true` is also set. The real-arm path is **unverified on the arm**. LeRobot's tools connect outside their cleanup and have no stop key; the plugin installs cleanup hooks for an opted-in real session.

Checked 2026-11-01 against `lerobot` 0.6.1 (Python 3.12.2, torch 2.11.0 CPU, `lerobot[dataset]` for `datasets` 4.8.5 and `av` 15.1.0, `ruckig` 0.19.4) with `examples/lerobot_sim_check.py`: LeRobot's plugin loader finds `robot.type=scorbot` and both teleoperators; `make_robot_from_config`, connect, observe, eight small actions and disconnect; the real `lerobot-record` for 2 episodes of 3 s at 10 fps with the scripted teleoperator and no cameras; the dataset reloaded with `LeRobotDataset` (60 frames, `robot_type` `scorbot_sim`, every action within 0.5 degree of the last, the next observation within 0.7 degree of each action); episode 0 replayed through a fresh simulated robot (observations within 0.1 degree of the recorded ones). `lerobot-record` without `--robot.simulate=true` stops with the refusal. That says nothing about the arm, a camera, a person at the keyboard teleoperator, training or `lerobot-eval`.

## Install and check (a separate venv, never the lab `.venv`)

```powershell
py -3.12 -m venv "$env:USERPROFILE\envs\lerobot-stack2"
& "$env:USERPROFILE\envs\lerobot-stack2\Scripts\python.exe" -m pip install "lerobot[dataset]>=0.6,<0.7"
& "$env:USERPROFILE\envs\lerobot-stack2\Scripts\python.exe" -m pip install -e ".[planning]"
& "$env:USERPROFILE\envs\lerobot-stack2\Scripts\python.exe" -m pip install -e plugins/lerobot_robot_scorbot --no-deps   # LeRobot finds lerobot_robot_* by name
& "$env:USERPROFILE\envs\lerobot-stack2\Scripts\python.exe" examples/lerobot_sim_check.py                             # all checks passed, exit 0
```

Without the `dataset` extra, `lerobot.datasets` refuses to import. On Windows set `$env:PYTHONUTF8 = "1"` before calling `lerobot-record` yourself. A `torchcodec` load error is printed and LeRobot falls back to PyAV; it does not matter without cameras.

The keyboard teleoperator needs `pynput` even to be built (`pip install "lerobot[pynput-dep]"` in the same venv; 1.8.2 was used). The letter map and listener integration have automated tests; no person has tried these keys under `lerobot-record`.

## Record your first sim demo

1. From the repository root in PowerShell, use the separate venv installed above. Activate it, install the keyboard extra only if pynput is missing, and enable UTF-8 output:

```powershell
. "$env:USERPROFILE\envs\lerobot-stack2\Scripts\Activate.ps1"
python -m pip install "lerobot[pynput-dep]"   # only if pynput is missing
$env:PYTHONUTF8 = "1"
$env:HF_HUB_OFFLINE = "1"
```

If activation is unavailable, call `python.exe`, `lerobot-record.exe` and `lerobot-replay.exe` under `$env:USERPROFILE\envs\lerobot-stack2\Scripts` by their full quoted paths with `&`. Twin cameras also need the existing MuJoCo installation.

2. Record two 20 s episodes at 10 fps with 5 s to reset between them. Use a fresh dataset root under `rehearsal/` each run. Both cameras are 320x240; images are saved without video encoding and nothing is pushed to the hub. The three Home confirmations are still required in simulation.

```powershell
lerobot-record --robot.type=scorbot --robot.simulate=true --teleop.type=scorbot_joint_keys `
  --robot.cameras="{front: {type: scorbot_twin, camera: front, width: 320, height: 240}, wrist: {type: scorbot_twin, camera: wrist, width: 320, height: 240}}" `
  --robot.operator_at_stop=true --robot.jaws_empty=true --robot.wrist_clear=true `
  --dataset.repo_id=local/my_first_sim_demo --dataset.root=rehearsal/my_first_sim_demo --dataset.single_task="move the arm" `
  --dataset.push_to_hub=false --dataset.num_episodes=2 --dataset.episode_time_s=20 --dataset.reset_time_s=5 --dataset.fps=10 `
  --dataset.video=false --play_sounds=false
```

3. During each episode, hold a joint key from the [Teleoperators table](#teleoperators) to step its target; release to hold. Use t/g for wrist pitch, l/j for wrist roll and o/c to open/close. Letters work with shift held; opposite keys cancel, and o+c holds the gripper. **Keys are global:** pynput hears them in any window, so keep this terminal focused and do not type elsewhere. Right arrow (or n) ends the episode early, left arrow (or r) re-records it, and Esc (or q) stops recording. Releasing Esc also stops the teleoperator's listener; its last target holds.

4. After recording exits, reload the local dataset and print its frame count and features. Expect roughly 400 frames if both episodes ran their full length, six state/action values and front/wrist image features. Early endings and slow gripper frames can reduce that count.

```powershell
python -c "from lerobot.datasets.lerobot_dataset import LeRobotDataset; d = LeRobotDataset('local/my_first_sim_demo', root='rehearsal/my_first_sim_demo'); print(d.num_frames, d.features)"
```

Replay episode 0 with the same simulator robot and camera arguments:

```powershell
lerobot-replay --robot.type=scorbot --robot.simulate=true `
  --robot.cameras="{front: {type: scorbot_twin, camera: front, width: 320, height: 240}, wrist: {type: scorbot_twin, camera: wrist, width: 320, height: 240}}" `
  --robot.operator_at_stop=true --robot.jaws_empty=true --robot.wrist_clear=true `
  --dataset.repo_id=local/my_first_sim_demo --dataset.root=rehearsal/my_first_sim_demo --dataset.episode=0 --dataset.fps=10 `
  --play_sounds=false
```

5. This is a hand-run recipe, not a completed human trial. No person has tried these keys under `lerobot-record`; which way the real arm turns, physical zero and calibration remain unverified. The twin is a nominal visual model.

## Real-arm switch (unverified on the arm)

Use only after the lab **Home, jog, stream and gripper** steps all pass. Keep an operator at the physical stop with empty jaws and a clear wrist path. Connect runs vendor Home, which moves the wrist and closes the gripper. All three Home confirmations still default to false and are required separately from the real-arm switch.

After those checks, the existing record command can select the real `scorbot.Scorbot` through `PolicyArm` with these robot arguments (keep the dataset local and use a separate real-data directory):

```powershell
--robot.type=scorbot --robot.simulate=false --robot.real_arm_confirmed=true `
  --robot.operator_at_stop=true --robot.jaws_empty=true --robot.wrist_clear=true `
  --robot.log_dir=logs/lerobot-real
```

Use a single real connection. The plugin registers an `atexit` cleanup before connecting. Any error or Ctrl+C inside `connect`, `get_observation` or `send_action` closes the arm before it propagates; one between calls ends the program, and the `atexit` cleanup closes the arm then. There is deliberately no Ctrl+C handler: stopping from inside one could cut into a command the main thread is sending. Cleanup calls `PolicyArm.close()` (stream end, motors off, disconnect) once per connection and tolerates cleanup errors. `transport` defaults to `auto` (Intelitek's driver first, as `scripts/lab_day.py`). With missing Home confirmations or either real-arm switch absent, no hardware is constructed. Simulator sessions install no process hooks.

These are desk-tested cleanup attempts, **unverified on the arm**; Python cleanup cannot run after forced process termination or power loss, and cannot guarantee motor-off delivery over a failed USB link. The physical stop remains authoritative. There is still no LeRobot stop key.

## Twin cameras (simulator images)

`type: scorbot_twin` (`sim_cameras.py`, needs `mujoco`; 3.17.0 used) renders the MuJoCo twin (`scorbot/mujoco_twin.py`). On every `get_observation` the robot reads the simulator's five encoder counts and `gripper.pos` together, turns the counts into the twin's joint angles through `scorbot.source_model` (`mujoco_twin.chain_angles`: the elbow motor sets the forearm's angle to the floor and the wrist motors the gripper's, so it is not degrees from home plus the home angle), poses arm and jaw under one lock (`set_pose`, `set_jaw`, no physics step) and renders the cameras. Each frame shows the pose and jaw opening in the same observation. Frames are `uint8` `(height, width, 3)` RGB, at most 640x480.

```powershell
lerobot-record --robot.type=scorbot --robot.simulate=true `
  --robot.cameras="{front: {type: scorbot_twin, camera: front, width: 320, height: 240}, wrist: {type: scorbot_twin, camera: wrist, width: 320, height: 240}}" `
  ...   # the rest as above
```

Checked 2026-11-03 with `examples/lerobot_sim_check.py --cameras` (lerobot 0.6.1, mujoco 3.17.0, Windows 11): 3 episodes of 3 s at 10 fps with both cameras at 160x120, 90 frames reloaded with image features, replayed frame 0 identical to the recorded one. The scene is nominal solids, not CAD and not measured. The jaw now follows the observed `gripper.pos`, from nearly touching at 0 percent to the original plate centres at y = +/-24 mm at 100 percent. The block never moves because the arm is posed, not simulated. Off-screen rendering that fails raises with the reason. Two 160x120 renders took about 30 ms per observation on this laptop in that check.

## Shape

| | |
|---|---|
| Observation | `base.pos`, `shoulder.pos`, `elbow.pos`, `pitch.pos`, `roll.pos`: measured degrees from home (vendor prior, uncalibrated), plus one `(height, width, 3)` frame per camera. Pitch is the gripper's angle to the floor and roll its roll, both from the two coupled wrist motors through `scorbot.source_model` (`PolicyArm(wrist=True)`); holding pitch while the shoulder or elbow moves leaves the wrist motors still. Signs from the source model (positive pitch lifts), unverified; the legacy wrist-pitch jog moves about 1.22 times further per degree than the model says. `gripper.pos`: the measured jaw in percent open, `100 x count / RangeEnc` (0 closed, 100 open) |
| Action | the same six keys. `gripper.pos` is binary: below 50 closes, 50 or more opens, missing or not a number holds; a change pauses the stream for the blocking vendor gripper move and restarts it (that frame is slow), and `send_action` returns the value applied, 0 or 100. A fault during the move disconnects. The five joints are an absolute target. `PolicyArm` clamps it: 0.5 degree per call (or, with `step_limit=speed`, what `speed_pct` of the vendor's MaxSpeed covers since the last call, at most 1.5 degrees), both Intelitek travel windows (ROB_4u joint angles and EncLimit absolute counts) after vendor Home, 1.5 degrees ahead of the measured joint; for pitch and roll each, one wrist motor carrying both. Call at least every 0.5 s or the stream holds |
| Config | `simulate` (true for rehearsal; false requires `real_arm_confirmed=true`, default false), `operator_at_stop`, `jaws_empty`, `wrist_clear` (all three must be true, default false: connect runs the vendor Home, which moves the wrist and closes the gripper), `log_dir` (the controller event log, default `rehearsal/lerobot`), `robot_id`, `cameras`, plus LeRobot's `id` and `calibration_dir` |
| Name | `scorbot_sim` for simulation, `scorbot` for real sessions; `lerobot-record` writes this as the dataset's `robot_type` (a v3 dataset has no `data_source` field) |

## Teleoperators

| Type | What it does |
|---|---|
| `scorbot_joint_keys` | Letter keys below drive all five joints, including wrist pitch and roll, and o/c drive the gripper. Each loop with a joint key held moves an absolute target by `step_deg` (default and most 0.5), through `scorbot.policy_arm.clamp`; the robot applies both vendor travel windows using its session Home counts, with no teleoperator travel cap. Needs `pynput`. **No person has tried it under `lerobot-record`** |
| `scorbot_joint_scripted` | Walks `--teleop.script="base,shoulder,elbow[,pitch,roll];..."` (absolute degrees; pitch and roll 0 when left out) at `step_deg` per loop, around again. For unattended runs and the check |

| Joint / gripper | Positive / open | Negative / close |
|---|---|---|
| Base | a | d |
| Shoulder | s | w |
| Elbow | k | i |
| Wrist pitch | t | g |
| Wrist roll | l | j |
| Gripper | o (100 percent) | c (0 percent) |

Letters also work with shift held. Duplicate keys count once, opposite joint keys cancel and o+c holds the gripper. Right/left arrows, Esc, n, r and q are left to `lerobot-record`: right/n ends an episode, left/r re-records, Esc/q stops. Unbound keys hold the target. A stopped keyboard listener also holds its last target. Which way the real arm turns is unverified.

Named apart from the Stack 1 teleoperators (`scorbot_keys`, `scorbot_scripted` on `main`) so both plugins can share one venv. The map logic is in `teleop_map.py` and needs no lerobot.

PolicyArm holds the entire frame at the last allowed target when either vendor window rejects it. Policy degrees keep their existing signs and scales (`preview_jog` for the three arm motors); vendor degrees are computed from absolute counts, so positive vendor base is negative PolicyArm base. Roll can exceed 360 degrees when both windows allow it. Zero and signs on our arm remain unverified until lab G/X.

## Not done yet

- **The gripper is binary and blocking.** Open or closed only, no width or force; each change stalls the loop for the vendor move plus a stream restart. Unverified on the arm. Teleop: `scorbot_joint_keys` opens with o and closes with c; `scorbot_joint_scripted` takes an optional sixth number per waypoint (0 to 100).
- `examples/lerobot_sim_check.py` was last run against lerobot 0.6.1 with the five joints and the gripper on 2026-11-03: all six checks passed. Its replay check (0.25 degree between replayed and recorded observations) is tight and failed some runs on simulator timing alone, with or without the gripper.
- Twin block contact (the arm and jaw are posed, not simulated).
- The real-arm path (explicit opt-in, unverified on the arm), real cameras with this robot, training, a policy run through `lerobot-record --policy.path`.

## Tests

`tests_extra/test_lerobot_scorbot_plugin.py`: without lerobot it uses stand-in base classes (default refusal, real-arm opt-in patched to the simulator, idempotent cleanup and process hooks, features, a simulated drive, both teleoperator maps, the check's record command); with lerobot importable it also checks registration, `make_robot_from_config`, both teleoperators and twin camera frames. `tests_extra/test_mujoco_twin.py` checks the pure jaw mapping and, with MuJoCo installed, plate separation and holding under physics. See the [teleop requirements](../../docs/requirements/lerobot-teleop.md). Run the repository's full suite in the lab `.venv`: in the lerobot venv, draccus 0.11.6 installs its own top-level `tests` package, which hides this repository's `tests/` (8 modules fail to import there). In the lerobot venv run only these two extra test files.
