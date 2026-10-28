"""Run the real `lerobot-record` on the Stack 1 adapter, FakeArm only, and read the datasets back.

    python examples/lerobot_record_check.py

Run it in a throwaway venv that has `lerobot` installed and this repo and plugins/lerobot_robot_scorbot_stack1 installed
with `pip install --no-deps -e` (LeRobot finds a plugin by its installed package name). Two runs of 2 short episodes with
`--robot.simulate=true` and the scripted teleoperator, never the hub:
  1. no cameras, no video
  2. two fake cameras (`scorbot_fake`) with video on, so the video encoder and decoder are exercised
Each result is loaded with LeRobotDataset and checked: episodes, frames, names, the simulated robot_type, and that every
state is the last state plus the action. Exits 0 only if all pass; 2 if lerobot or the plugin is not installed.
No arm, no DLL, no USB, no server, no training. Says nothing about the real arm or a real camera.
"""

import os
import subprocess
import sys
import tempfile
from importlib import metadata
from pathlib import Path

REPO_ID = "local/scorbot_record_check"
EPISODES, SECONDS, FPS = 2, 2, 10
STEPS = ["10,0,0,0", "0,10,0,1", "-10,0,0,1", "0,-10,0,0"]       # returns to the start every 4 steps
START = [300.0, 0.0, 200.0, 0.0]                                # the FakeArm start, gripper closed
OBS_NAMES = ["x.mm", "y.mm", "z.mm", "gripper.pos"]
ACT_NAMES = ["dx.mm", "dy.mm", "dz.mm", "gripper.pos"]
CAMERAS = {"front": (64, 64), "wrist": (48, 32)}                # name: (height, width)


def command(root, calibration, cameras=False):
    cams = ", ".join(f"{name}: {{type: scorbot_fake, width: {w}, height: {h}, fps: {FPS}}}" for name, (h, w) in CAMERAS.items())
    return [sys.executable, "-m", "lerobot.scripts.lerobot_record",
            "--robot.type=scorbot_stack1", "--robot.simulate=true", f"--robot.calibration_dir={calibration}",
            *([f"--robot.cameras={{{cams}}}"] if cameras else []),
            "--teleop.type=scorbot_scripted", f"--teleop.calibration_dir={calibration}", f"--teleop.script={';'.join(STEPS)}",
            f"--dataset.repo_id={REPO_ID}", "--dataset.no_stamp=true", f"--dataset.root={root}", "--dataset.single_task=check",
            f"--dataset.num_episodes={EPISODES}", f"--dataset.episode_time_s={SECONDS}", "--dataset.reset_time_s=0",
            f"--dataset.fps={FPS}", f"--dataset.video={'true' if cameras else 'false'}", "--dataset.push_to_hub=false",
            "--play_sounds=false"]


def record(root, calibration, cameras):
    env = {**os.environ, "PYTHONUTF8": "1"}        # LeRobot prints characters the Windows console cannot encode
    done = subprocess.run(command(root, calibration, cameras), capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env, timeout=600)
    assert done.returncode == 0, f"lerobot-record exited {done.returncode}: " + " | ".join(
        (done.stdout + done.stderr).strip().splitlines()[-8:])
    return "lerobot-record exited 0"


def load(root):
    from lerobot.datasets import LeRobotDataset
    return LeRobotDataset(REPO_ID, root=root)


def rows(dataset):
    """Per frame: (episode, state, action) as plain lists, read from the parquet without decoding any video."""
    table = dataset.hf_dataset
    return [(int(row["episode_index"]), [float(v) for v in row["observation.state"]], [float(v) for v in row["action"]])
            for row in (table[i] for i in range(len(table)))]


def check_metadata(dataset, cameras):
    assert dataset.meta.total_episodes == EPISODES, f"{dataset.meta.total_episodes} episodes"
    assert dataset.meta.robot_type == "scorbot_stack1_sim", f"robot_type {dataset.meta.robot_type!r}"
    assert dataset.fps == FPS, f"fps {dataset.fps}"
    names = {key: list(dataset.features[key]["names"]) for key in ("observation.state", "action")}
    assert names == {"observation.state": OBS_NAMES, "action": ACT_NAMES}, f"names {names}"
    found = {key.split(".")[-1]: tuple(spec["shape"]) for key, spec in dataset.features.items() if spec["dtype"] in ("video", "image")}
    wanted = {name: (h, w, 3) for name, (h, w) in CAMERAS.items()} if cameras else {}
    assert found == wanted, f"camera features {found}, expected {wanted}"
    return f"{EPISODES} episodes, robot_type scorbot_stack1_sim, names ok, cameras {sorted(found) or 'none'}"


def check_values(dataset):
    data = rows(dataset)
    assert len(data) == len(dataset), f"{len(data)} rows but len(dataset) is {len(dataset)}"
    for episode in range(EPISODES):
        frames = sum(1 for row in data if row[0] == episode)
        assert 0.8 * SECONDS * FPS <= frames <= 1.2 * SECONDS * FPS + 1, f"episode {episode} has {frames} frames"
    script = [[float(v) for v in step.split(",")] for step in STEPS]
    assert data[0][1] == START, f"first state {data[0][1]}, expected {START}"
    for index, (_, _, action) in enumerate(data):
        assert action == script[index % len(script)], f"action {index} is {action}, script says {script[index % len(script)]}"
    for (_, state, action), (_, after, _) in zip(data, data[1:]):       # the arm is not reset between episodes
        expected = [state[0] + action[0], state[1] + action[1], state[2] + action[2], action[3]]
        assert all(abs(a - b) < 1e-3 for a, b in zip(after, expected)), f"state {state} + action {action} gave {after}"
    return f"{len(data)} frames; first state, scripted actions and every state = last state + action match"


def check_task(dataset):
    tasks = list(dataset.meta.tasks.index)
    assert tasks == ["check"], f"tasks {tasks}"
    return "task 'check'"


def check_video(dataset, root):
    """Videos exist, decode to the camera shape, and the frames change over time (the fake camera moves a bar)."""
    files = sorted(Path(root, "videos").rglob("*.mp4"))
    assert len(files) >= len(CAMERAS), f"{len(files)} video files for {len(CAMERAS)} cameras"
    first, later = dataset[0], dataset[len(dataset) - 1]
    for name, (h, w) in CAMERAS.items():
        key = f"observation.images.{name}"
        shape = tuple(first[key].shape)
        assert shape == (3, h, w), f"{key} decoded to {shape}, expected {(3, h, w)}"
        assert float((first[key] - later[key]).abs().mean()) > 0.001, f"{key} first and last frame are identical"
    return f"{len(files)} mp4 files; both cameras decode to their shape and change over time"


def scenario(tmp, label, cameras):
    """[(name, passed, detail)] for one recording. Recording and loading gate the rest."""
    results, root = [], str(Path(tmp) / f"dataset_{label}")
    try:
        results.append((f"{label}: record", True, record(root, Path(tmp) / "calibration", cameras)))
        dataset = load(root)
        results.append((f"{label}: load back", True, f"LeRobotDataset loaded: {len(dataset)} frames"))
    except Exception as error:
        return results + [(f"{label}: " + ("load back" if results else "record"), False, f"{type(error).__name__}: {error}")]
    checks = [("metadata", lambda: check_metadata(dataset, cameras)), ("values", lambda: check_values(dataset)),
              ("task", lambda: check_task(dataset))]
    if cameras:
        checks.append(("video", lambda: check_video(dataset, root)))
    for name, fn in checks:
        try:
            results.append((f"{label}: {name}", True, fn()))
        except Exception as error:      # a failed check must not hide the others
            results.append((f"{label}: {name}", False, f"{type(error).__name__}: {error}"))
    return results


def main():
    try:
        import lerobot  # noqa: F401
    except ImportError as error:
        print(f"lerobot is not importable ({error}). Install it in a throwaway venv, then run this again.")
        return 2
    try:
        metadata.version("lerobot_robot_scorbot_stack1")
    except metadata.PackageNotFoundError:
        print("the plugin is not installed: run `pip install --no-deps -e . -e plugins/lerobot_robot_scorbot_stack1` in this venv.")
        return 2
    with tempfile.TemporaryDirectory() as tmp:
        results = scenario(tmp, "no cameras", False) + scenario(tmp, "two cameras", True)
    for name, passed, detail in results:
        print(f"{'PASS' if passed else 'FAIL'}  {name:<22} {detail}")
    ok = all(passed for _, passed, _ in results)
    print("all checks passed" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
