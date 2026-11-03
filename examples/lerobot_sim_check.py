"""Prove plugins/lerobot_robot_scorbot (Stack 2) against an installed LeRobot, on the simulator only.

    python examples/lerobot_sim_check.py [--keep DIR] [--speed-pct 25] [--step-limit fixed|speed] [--cameras]

Run it in a separate venv that has `lerobot` installed, plus this repo (`pip install -e ".[planning]"`) and the
plugin (`pip install -e plugins/lerobot_robot_scorbot --no-deps`). Four steps, never the hub. No cameras unless
--cameras, which adds the MuJoCo twin's front and wrist cameras (needs `mujoco`), records 3 episodes and also
checks the image features, the frames after reload and replayed frame 0 against the recorded one:
  1. discovery: LeRobot's own plugin loader registers robot.type=scorbot and both teleoperators
  2. drive: make_robot_from_config, connect, get_observation, send_action (small targets), open and close the
     gripper (each a slow frame: the stream pauses for the vendor move), disconnect
  3. record: the real `lerobot-record` with the scripted teleoperator, EPISODES short episodes, reloaded with
     LeRobotDataset and checked (episodes, frames, names, robot_type, every action one clamped step from the last)
  4. replay: episode 0's actions through a fresh ScorbotRobot; observations must track the recorded ones
Exits 0 only if all pass; 2 if lerobot or the plugin is not installed. Says nothing about the real arm.
"""

import argparse
import os
import subprocess
import sys
import tempfile
import time
from importlib import metadata
from pathlib import Path

REPO_ID = "local/scorbot_sim_check"
EPISODES, SECONDS, FPS = 2, 3, 10
SCRIPT = "1,0,0,0,0;1,1,0,1,0;0,1,-1,1,-1;0,0,0,0,0"     # absolute degrees (base..roll), ends at home
STEP_DEG = 0.5
JOINT_NAMES = ["base.pos", "shoulder.pos", "elbow.pos", "pitch.pos", "roll.pos"]
NAMES = [*JOINT_NAMES, "gripper.pos"]    # gripper 0 closed .. 100 open; the scripted record keeps it closed
ACTION = {"base.pos": 1.0, "shoulder.pos": -0.5, "elbow.pos": 0.5, "pitch.pos": -1.0, "roll.pos": 0.5,
          "gripper.pos": 0.0}
GRIPPER_TOL_PCT = 1.0       # measured jaw vs the applied 0 or 100
TRACK_TOL_DEG = 1.5         # observation vs the target it was asked for: PolicyArm's lead margin
REPLAY_TOL_DEG = 0.25       # replayed observation vs the recorded one at the same frame
CAMERAS = {}                # --cameras: name -> (height, width) of a scorbot_twin camera
CAMERA_SIZE = (120, 160)    # small, so two renders fit well inside a 10 fps frame
ROBOT = {"speed_pct": 25.0, "step_limit": "fixed"}     # the plugin's defaults; set from the command line
CONFIRMED = {"operator_at_stop": True, "jaws_empty": True, "wrist_clear": True}    # the simulator's vendor Home


def env():
    return {**os.environ, "PYTHONUTF8": "1", "HF_HUB_OFFLINE": "1"}    # never the hub; LeRobot prints non-ASCII


def command(root, scratch):
    return [sys.executable, "-m", "lerobot.scripts.lerobot_record",
            "--robot.type=scorbot", "--robot.simulate=true", f"--robot.log_dir={scratch}/sim_logs",
            f"--robot.speed_pct={ROBOT['speed_pct']}", f"--robot.step_limit={ROBOT['step_limit']}",
            f"--robot.calibration_dir={scratch}/calibration",
            *(f"--robot.{k}=true" for k in CONFIRMED),
            *([f"--robot.cameras={camera_arg()}"] if CAMERAS else []),
            "--teleop.type=scorbot_joint_scripted", f"--teleop.script={SCRIPT}", f"--teleop.step_deg={STEP_DEG}",
            f"--teleop.calibration_dir={scratch}/calibration",
            f"--dataset.repo_id={REPO_ID}", f"--dataset.root={root}", "--dataset.single_task=check",
            f"--dataset.num_episodes={EPISODES}", f"--dataset.episode_time_s={SECONDS}", "--dataset.reset_time_s=0",
            f"--dataset.fps={FPS}", "--dataset.video=false", "--dataset.push_to_hub=false", "--play_sounds=false"]


def camera_arg():
    return "{" + ", ".join(f"{n}: {{type: scorbot_twin, camera: {n}, width: {w}, height: {h}}}"
                           for n, (h, w) in CAMERAS.items()) + "}"


def camera_configs():
    if not CAMERAS:
        return {}
    from lerobot_robot_scorbot.sim_cameras import ScorbotTwinCameraConfig
    return {n: ScorbotTwinCameraConfig(camera=n, width=w, height=h) for n, (h, w) in CAMERAS.items()}


def frame(value):
    """A dataset image (float CHW in 0..1) or a camera frame (uint8 HWC) as uint8 HWC numpy."""
    import numpy as np
    if hasattr(value, "permute"):
        return (value.permute(1, 2, 0).numpy() * 255).round().astype(np.uint8)
    return np.asarray(value)


def check_discovery():
    from lerobot.robots import RobotConfig
    from lerobot.teleoperators import TeleoperatorConfig
    from lerobot.utils.import_utils import register_third_party_plugins
    register_third_party_plugins()
    robot = RobotConfig.get_choice_class("scorbot")
    teleops = [TeleoperatorConfig.get_choice_class(n).__name__ for n in ("scorbot_joint_keys", "scorbot_joint_scripted")]
    return f"scorbot -> {robot.__module__}.{robot.__name__}; teleops {teleops}"


def make(scratch):
    from lerobot.robots.utils import make_robot_from_config
    from lerobot_robot_scorbot import ScorbotRobotConfig
    config = ScorbotRobotConfig(simulate=True, log_dir=Path(scratch) / "sim_logs", **ROBOT, **CONFIRMED,
                                calibration_dir=Path(scratch) / "calibration", cameras=camera_configs())
    return make_robot_from_config(config)


def check_drive(scratch):
    from lerobot.robots import Robot
    robot = make(scratch)
    assert isinstance(robot, Robot), f"{type(robot)} is not a lerobot Robot"
    robot.connect()
    try:
        first = robot.get_observation()
        images = {n: first.pop(n) for n in CAMERAS}
        assert first == dict.fromkeys(NAMES, 0.0), f"first observation {first}"
        for n, (h, w) in CAMERAS.items():
            assert images[n].shape == (h, w, 3) and str(images[n].dtype) == "uint8", f"{n} {images[n].shape}"
        for _ in range(8):
            sent = robot.send_action(ACTION)
            time.sleep(1 / FPS)
        time.sleep(0.5)
        last = robot.get_observation()
        jaw = []
        for asked, applied in ((80.0, 100.0), (20.0, 0.0)):     # threshold at 50; each change is one slow frame
            start = time.perf_counter()
            got = robot.send_action({**ACTION, "gripper.pos": asked})["gripper.pos"]
            seconds = time.perf_counter() - start
            seen = robot.get_observation()["gripper.pos"]
            assert got == applied, f"asked gripper {asked}, applied {got}"
            assert abs(seen - applied) <= GRIPPER_TOL_PCT, f"gripper measured {seen:.2f} %, applied {applied}"
            jaw.append(f"{asked:g} -> {got:g} (measured {seen:.1f} %, {seconds:.2f} s)")
    finally:
        robot.disconnect()
    assert not robot.is_connected, "still connected after disconnect"
    assert sent == ACTION, f"last target {sent}"
    assert all(abs(last[k] - sent[k]) < 0.1 for k in NAMES), f"arm at {last}, asked {sent}"
    seen = f"; images {', '.join(f'{n} {h}x{w}' for n, (h, w) in CAMERAS.items())}" if CAMERAS else ""
    return (f"{type(robot).__name__} connect, 8 actions, reached {', '.join(f'{k} {last[k]:.2f}' for k in NAMES)}; "
            f"gripper {'; '.join(jaw)}{seen}")


def record(root, scratch):
    done = subprocess.run(command(root, scratch), capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env(), timeout=600)
    assert done.returncode == 0, f"lerobot-record exited {done.returncode}: " + " | ".join(
        (done.stdout + done.stderr).strip().splitlines()[-8:])
    return "lerobot-record exited 0"


def rows(dataset):
    table = dataset.hf_dataset
    return [(int(row["episode_index"]), [float(v) for v in row["observation.state"]], [float(v) for v in row["action"]])
            for row in (table[i] for i in range(len(table)))]


def check_dataset(dataset):
    assert dataset.meta.total_episodes == EPISODES, f"{dataset.meta.total_episodes} episodes"
    assert dataset.meta.robot_type == "scorbot_sim", f"robot_type {dataset.meta.robot_type!r}"
    assert dataset.fps == FPS, f"fps {dataset.fps}"
    names = {key: list(dataset.features[key]["names"]) for key in ("observation.state", "action")}
    assert names == {"observation.state": NAMES, "action": NAMES}, f"names {names}"
    data = rows(dataset)
    for episode in range(EPISODES):
        frames = sum(1 for row in data if row[0] == episode)
        assert 0.8 * SECONDS * FPS <= frames <= 1.2 * SECONDS * FPS + 1, f"episode {episode} has {frames} frames"
    assert data[0][1] == [0.0] * len(NAMES), f"first state {data[0][1]}"
    joints = len(JOINT_NAMES)       # step and tracking are joint checks; the gripper column is its own
    assert all(row[1][joints] == 0.0 and row[2][joints] == 0.0 for row in data), "the gripper left closed"
    worst = 0.0
    for (ep, _, action), (ep2, after, nxt) in zip(data, data[1:]):
        action, after, nxt = action[:joints], after[:joints], nxt[:joints]
        if ep == ep2:
            assert all(abs(a - b) <= STEP_DEG + 1e-6 for a, b in zip(action, nxt)), f"step {action} -> {nxt}"
            worst = max(worst, *(abs(a - b) for a, b in zip(after, action)))
    assert worst <= TRACK_TOL_DEG, f"observation lags its action by {worst:.3f} degrees"
    moved = max(abs(v) for _, _, action in data for v in action[:joints])
    assert moved >= 0.99, f"the actions never left home (largest {moved})"
    images = ""
    for n, (h, w) in CAMERAS.items():
        key = f"observation.images.{n}"
        feature = dataset.features[key]
        assert feature["dtype"] == "image" and tuple(feature["shape"]) == (h, w, 3), f"{key} {feature}"
        frames = [frame(dataset[i][key]) for i in range(len(dataset))]
        assert all(f.shape == (h, w, 3) for f in frames), f"{key} frame shapes"
        assert frames[0].std() > 0, f"{key} frame 0 is blank"
        assert any((f != frames[0]).any() for f in frames), f"{key} never changed while the arm moved"
        images += f"; {key} {len(frames)} frames {h}x{w}x3"
    return (f"{len(data)} frames, robot_type scorbot_sim, names ok; next observation within {worst:.3f} deg "
            f"of action{images}")


def check_replay(dataset, scratch):
    """Episode 0's recorded actions at the recorded rate through a fresh simulated robot."""
    episode = [(state, action) for ep, state, action in rows(dataset) if ep == 0]
    robot = make(scratch)
    robot.connect()
    seen, first_images = [], {}
    try:
        for _, action in episode:
            start = time.perf_counter()
            obs = robot.get_observation()
            seen.append([obs[k] for k in NAMES])
            first_images = first_images or {n: frame(obs[n]) for n in CAMERAS}
            robot.send_action(dict(zip(NAMES, action)))
            time.sleep(max(0.0, 1 / FPS - (time.perf_counter() - start)))
    finally:
        robot.disconnect()
    vs_recorded = max(abs(a - b) for (state, _), obs in zip(episode, seen) for a, b in zip(state, obs))
    vs_action = max(abs(a - b) for (_, action), obs in zip(episode, seen[1:]) for a, b in zip(action, obs))
    assert vs_recorded <= REPLAY_TOL_DEG, f"replayed observations differ from recorded by {vs_recorded:.3f} deg"
    assert vs_action <= TRACK_TOL_DEG, f"replayed observations lag actions by {vs_action:.3f} deg"
    images = ""
    for n in CAMERAS:       # frame 0 is at home in both runs, and the dataset stores images losslessly
        recorded = frame(dataset[0][f"observation.images.{n}"]).astype(int)
        diff = int(abs(recorded - first_images[n]).max())
        assert diff <= 1, f"replayed {n} frame 0 differs from the recorded one by {diff} levels"
        images += f"; {n} frame 0 matches the recording (max {diff} levels)"
    return (f"{len(episode)} frames; replayed vs recorded observation max {vs_recorded:.3f} deg, "
            f"vs action max {vs_action:.3f} deg{images}")


def run(scratch):
    from lerobot.datasets import LeRobotDataset
    results, root = [], str(Path(scratch) / "dataset")

    def step(name, fn):
        try:
            results.append((name, True, fn()))
            return True
        except Exception as error:      # one failed check must not hide the others
            results.append((name, False, f"{type(error).__name__}: {error}"))
            return False

    step("discovery", check_discovery)
    step("drive", lambda: check_drive(scratch))
    if step("record", lambda: record(root, scratch)):
        holder = {}
        if step("load back", lambda: f"LeRobotDataset: {len(holder.setdefault('ds', LeRobotDataset(REPO_ID, root=root)))} frames"):
            step("dataset", lambda: check_dataset(holder["ds"]))
            step("replay", lambda: check_replay(holder["ds"], scratch))
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--keep", help="write into this folder (kept) instead of a temp folder")
    parser.add_argument("--speed-pct", type=float, default=ROBOT["speed_pct"], help="robot.speed_pct, 1 to 100")
    parser.add_argument("--step-limit", choices=("fixed", "speed"), default=ROBOT["step_limit"], help="robot.step_limit")
    parser.add_argument("--cameras", action="store_true",
                        help="add the MuJoCo twin's front and wrist cameras (needs mujoco); record 3 episodes")
    args = parser.parse_args(argv)
    ROBOT.update(speed_pct=args.speed_pct, step_limit=args.step_limit)
    global EPISODES
    CAMERAS.clear()
    if args.cameras:
        CAMERAS.update(front=CAMERA_SIZE, wrist=CAMERA_SIZE)
        EPISODES = 3
    try:
        import lerobot  # noqa: F401
    except ImportError as error:
        print(f"lerobot is not importable ({error}). Install it in a separate venv, then run this again.")
        return 2
    try:
        metadata.version("lerobot_robot_scorbot")
    except metadata.PackageNotFoundError:
        print("the plugin is not installed: run `pip install -e plugins/lerobot_robot_scorbot --no-deps` in this venv.")
        return 2
    if args.cameras:
        try:
            import mujoco  # noqa: F401
        except ImportError as error:
            print(f"--cameras needs mujoco ({error}): pip install mujoco in this venv.")
            return 2
    import lerobot_robot_scorbot
    import scorbot
    print(f"using {Path(lerobot_robot_scorbot.__file__).parent} and {Path(scorbot.__file__).parent}")
    os.environ.update(env())
    if args.keep:
        Path(args.keep).mkdir(parents=True, exist_ok=False)
        results = run(args.keep)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            results = run(tmp)
    for name, passed, detail in results:
        print(f"{'PASS' if passed else 'FAIL'}  {name:<10} {detail}")
    ok = len(results) == 6 and all(passed for _, passed, _ in results)
    print("all checks passed" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
