"""scorbot/mujoco_twin.py: the MuJoCo scene agrees with arm_chain. Skips without mujoco.

Both use the same nominal numbers, so this checks the scene is wired right, not that it
matches the real arm (unverified)."""

from importlib.util import find_spec
import math
import unittest

from scorbot import arm_chain, mujoco_twin
from scorbot.nominal import AXIS_RANGES

POSES = [(0, 0, 0, 0, 0), (30, 40, -60, -50, 10), (-120, 100, -120, 90, -300), (150, -30, 120, -120, 560)]


@unittest.skipUnless(find_spec("mujoco"), "needs mujoco")
class MujocoTwinTests(unittest.TestCase):
    def setUp(self):
        import mujoco
        self.mj = mujoco
        self.model, self.data = mujoco_twin.load()

    def test_the_tool_tip_matches_arm_chain(self):
        for q in POSES:
            for name, deg in zip(arm_chain.JOINTS, q):
                self.data.joint(name).qpos[0] = math.radians(deg)
            self.mj.mj_kinematics(self.model, self.data)
            tip = self.data.site("tool").xpos * 1000.0
            for got, want in zip(tip, arm_chain.skeleton(q)[-1]):
                self.assertAlmostEqual(got, want, delta=0.01, msg=q)

    def test_joint_ranges_are_the_manuals(self):
        for name in arm_chain.JOINTS:
            low, high = (math.degrees(v) for v in self.model.joint(name).range)
            r = AXIS_RANGES[name]
            self.assertAlmostEqual(high - low, r.span_deg, places=6, msg=name)

    def test_the_arm_holds_a_commanded_pose(self):
        target = [math.radians(v) for v in (20, 30, -40, -30, 0)]
        self.data.ctrl[:] = target
        for _ in range(1500):                       # 3 s of simulated time
            self.mj.mj_step(self.model, self.data)
        for name, want in zip(arm_chain.JOINTS, target):
            self.assertAlmostEqual(self.data.joint(name).qpos[0], want, delta=math.radians(2), msg=name)


class SceneTextTests(unittest.TestCase):
    def test_scene_names_both_cameras(self):
        text = mujoco_twin.mjcf()
        for cam in mujoco_twin.CAMERAS:
            self.assertIn(f'<camera name="{cam}"', text)


if __name__ == "__main__":
    unittest.main()
