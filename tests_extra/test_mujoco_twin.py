"""scorbot/mujoco_twin.py: the MuJoCo scene agrees with arm_chain. Skips without mujoco.

Both use the same nominal numbers, so this checks the scene is wired right, not that it
matches the real arm (unverified)."""

from importlib.util import find_spec
import math
import unittest

from scorbot import arm_chain, mujoco_twin, source_model
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


# Counts from the session home, all five motors off home, so coupled joints move together.
COUNTS = {"base": -1418, "shoulder": 900, "elbow": -2000, "wrist_motor_1": 600, "wrist_motor_2": -150}


class ChainAnglesTests(unittest.TestCase):
    """Counts from home to arm_chain degrees: through source_model, never joint-by-joint sums."""

    def test_home_is_the_vendor_home_pose(self):
        q = mujoco_twin.chain_angles(dict.fromkeys(source_model.MOTORS, 0))
        self.assertEqual(q, [source_model.HOME_ANGLES[j] for j in source_model.JOINTS])

    def test_coupled_counts_give_the_source_model_angles(self):
        angles = source_model.angles_from_counts(COUNTS)
        self.assertEqual(mujoco_twin.chain_angles(COUNTS), [angles[j] for j in source_model.JOINTS])


@unittest.skipUnless(find_spec("mujoco"), "needs mujoco")
class SetPoseTests(unittest.TestCase):
    def test_qpos_is_radians_and_the_tool_tip_matches_arm_chain(self):
        model, data = mujoco_twin.load()
        block = data.qpos[:7].copy()                    # the block's free joint comes first
        q = mujoco_twin.chain_angles(COUNTS)
        mujoco_twin.set_pose(model, data, q)
        for name, deg in zip(arm_chain.JOINTS, q):
            self.assertAlmostEqual(data.joint(name).qpos[0], math.radians(deg), places=12, msg=name)
        tip = data.site("tool").xpos * 1000.0
        want = arm_chain.link_poses(q)["tool"][:3, 3]
        for got, w in zip(tip, want):
            self.assertAlmostEqual(got, w, delta=0.01)
        self.assertEqual(list(data.qpos[:7]), list(block))      # the block is left alone
        self.assertEqual(data.time, 0.0)                          # kinematic: no physics step


class SceneTextTests(unittest.TestCase):
    def test_scene_names_both_cameras(self):
        text = mujoco_twin.mjcf()
        for cam in mujoco_twin.CAMERAS:
            self.assertIn(f'<camera name="{cam}"', text)


if __name__ == "__main__":
    unittest.main()
