"""Laptop simulation of the Stack 1 LeRobot adapter. FakeArm only: no arm, no DLL, no USB, no server.

    python examples/sim_demo.py [--out DIR]

Runs a scripted policy through the adapter's safety layer, prints each step (position, gripper, action sent),
prints "refused: <reason>" for deliberately bad actions, and saves an episode (data_source: simulated) under
rehearsal/ that it reloads and checks. Nothing here says anything about the real arm (unverified).
"""

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for path in (ROOT, ROOT / "plugins" / "lerobot_robot_scorbot_stack1"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from lerobot_robot_scorbot_stack1 import (  # noqa: E402
    ActionRefused, DatasetWriter, Stack1RobotConfig, Stack1Robot, read_episode)
from scorbot.bridge import FakeArm  # noqa: E402

BOX = {"x": [250, 350], "y": [-50, 50], "z": [150, 250]}      # the FakeArm starts at 300, 0, 200
FPS = 10


def step(dx=0.0, dy=0.0, dz=0.0, grip=0.0):
    return {"dx.mm": dx, "dy.mm": dy, "dz.mm": dz, "gripper.pos": grip}


# (label, action, is_bad). The good ones are the policy; the bad ones are what a broken policy might send.
SCRIPT = [
    ("reach in x", step(dx=20), False), ("reach in x", step(dx=20), False),
    ("BAD: 60 mm step", step(dx=60), True),
    ("BAD: target past the box edge (x 340 + 30)", step(dx=30), True),
    ("reach in y", step(dy=20), False), ("reach in y", step(dy=20), False),
    ("open gripper", step(grip=1.0), False),
    ("down", step(dz=-25, grip=1.0), False), ("down", step(dz=-25, grip=1.0), False),
    ("BAD: target below the box (z 150 - 10)", step(dz=-10, grip=1.0), True),
    ("close gripper", step(), False),
    ("up", step(dz=30), False), ("back in x", step(dx=-40), False), ("back in y", step(dy=-40), False),
]


def show(label, action, obs):
    grip = "open" if obs["gripper.pos"] else "closed"
    print(f"{label:<42} xyz ({obs['x.mm']:7.1f}, {obs['y.mm']:7.1f}, {obs['z.mm']:7.1f}) mm  gripper {grip:<6} "
          f"sent dx {action['dx.mm']:+.0f} dy {action['dy.mm']:+.0f} dz {action['dz.mm']:+.0f} grip {action['gripper.pos']:.0f}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(Path("rehearsal") / f"sim_demo-{time.strftime('%Y%m%dT%H%M%S')}"),
                        help="dataset folder to create (default rehearsal/sim_demo-<time>)")
    args = parser.parse_args(argv)

    print("SIMULATION: FakeArm in memory. Nothing is connected; nothing here has run on the arm.")
    robot = Stack1Robot(Stack1RobotConfig(arm_id="sim0", box_mm=BOX))
    robot.connect(arm=FakeArm(homed=False))
    writer = DatasetWriter(args.out, robot.data_source, fps=FPS)

    print("\n-- before Home --")
    try:
        robot.send_action(step(dx=10))
    except ActionRefused as error:
        print(f"refused: {error}")
    robot.home()
    obs = robot.get_observation()
    print(f"homed. start xyz ({obs['x.mm']:.1f}, {obs['y.mm']:.1f}, {obs['z.mm']:.1f}) mm, box {BOX}")

    print("\n-- scripted policy --")
    episode = writer.start_episode("reach, grasp and return")
    sent = []
    for label, action, bad in SCRIPT:
        try:
            done = robot.send_action(action)
        except ActionRefused as error:
            print(f"{label}\nrefused: {error}")
            continue
        if bad:
            print("UNEXPECTED: a bad action was accepted")
            return 1
        after = robot.get_observation()
        writer.add_frame(obs, done, robot.data_source)
        sent.append((obs, done))
        show(label, done, after)
        obs = after
    writer.end_episode()
    robot.disconnect()

    records = read_episode(episode)
    same = [(r["observation.state"], r["action"]) for r in records] == sent
    print(f"\nsaved {len(records)} frames to {episode} (data_source {records[0]['data_source']}); reloaded matches what was sent: {same}")
    return 0 if same else 1


if __name__ == "__main__":
    sys.exit(main())
