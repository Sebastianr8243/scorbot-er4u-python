"""Run the real `lerobot-record` on the Stack 1 adapter, FakeArm only, and read the dataset back.

    python examples/lerobot_record_check.py

Run it in a throwaway venv that has `lerobot` installed and this repo and plugins/lerobot_robot_scorbot_stack1 installed
with `pip install --no-deps -e` (LeRobot finds a plugin by its installed package name). Records 2 short episodes with
`--robot.simulate=true` and the scripted teleoperator, no cameras, no hub, then loads the result with LeRobotDataset and
checks episodes, frames, names, the simulated robot_type and that every state is the last state plus the action.
Exits 0 only if all pass; 2 if lerobot or the plugin is not installed. No arm, no DLL, no USB, no server, no training.
Proves the recording path works; says nothing about the real arm, cameras, video or training.
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


def command(root, calibration):
    return [sys.executable, "-m", "lerobot.scripts.lerobot_record",
            "--robot.type=scorbot_stack1", "--robot.simulate=true", f"--robot.calibration_dir={calibration}",
            "--teleop.type=scorbot_scripted", f"--teleop.calibration_dir={calibration}", f"--teleop.script={';'.join(STEPS)}",
            f"--dataset.repo_id={REPO_ID}", "--dataset.no_stamp=true", f"--dataset.root={root}", "--dataset.single_task=check",
            f"--dataset.num_episodes={EPISODES}", f"--dataset.episode_time_s={SECONDS}", "--dataset.reset_time_s=0",
            f"--dataset.fps={FPS}", "--dataset.video=false", "--dataset.push_to_hub=false", "--play_sounds=false"]


def record(root, calibration):
    env = {**os.environ, "PYTHONUTF8": "1"}        # LeRobot prints characters the Windows console cannot encode
    done = subprocess.run(command(root, calibration), capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=env, timeout=600)
    assert done.returncode == 0, f"lerobot-record exited {done.returncode}: " + " | ".join(
        (done.stdout + done.stderr).strip().splitlines()[-8:])
    return "lerobot-record exited 0"


def load(root):
    from lerobot.datasets import LeRobotDataset
    return LeRobotDataset(REPO_ID, root=root)


def rows(dataset):
    """Per frame: (episode, state, action) as plain lists."""
    table = dataset.hf_dataset
    return [(int(row["episode_index"]), [float(v) for v in row["observation.state"]], [float(v) for v in row["action"]])
            for row in (table[i] for i in range(len(table)))]


def check_metadata(dataset):
    assert dataset.meta.total_episodes == EPISODES, f"{dataset.meta.total_episodes} episodes"
    assert dataset.meta.robot_type == "scorbot_stack1_sim", f"robot_type {dataset.meta.robot_type!r}"
    assert dataset.fps == FPS, f"fps {dataset.fps}"
    names = {key: list(dataset.features[key]["names"]) for key in ("observation.state", "action")}
    assert names == {"observation.state": OBS_NAMES, "action": ACT_NAMES}, f"names {names}"
    extra = [key for key, spec in dataset.features.items() if spec["dtype"] in ("video", "image")]
    assert not extra, f"unexpected camera features {extra}"
    return f"{EPISODES} episodes, robot_type scorbot_stack1_sim, names ok, no cameras"


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


def run(tmp):
    """[(name, passed, detail)]. Recording gates the rest."""
    results, root, dataset = [], str(Path(tmp) / "dataset"), None
    for name, fn in (("lerobot-record", lambda: record(root, Path(tmp) / "calibration")),
                     ("load back", lambda: load_note(root)), ):
        try:
            results.append((name, True, fn()))
        except Exception as error:
            results.append((name, False, f"{type(error).__name__}: {error}"))
            return results
    dataset = load(root)
    for name, fn in (("metadata", check_metadata), ("values", check_values), ("task", check_task)):
        try:
            results.append((name, True, fn(dataset)))
        except Exception as error:      # a failed check must not hide the others
            results.append((name, False, f"{type(error).__name__}: {error}"))
    return results


def load_note(root):
    dataset = load(root)
    return f"LeRobotDataset loaded: {len(dataset)} frames"


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
        results = run(tmp)
    for name, passed, detail in results:
        print(f"{'PASS' if passed else 'FAIL'}  {name:<16} {detail}")
    ok = all(passed for _, passed, _ in results)
    print("all checks passed" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
