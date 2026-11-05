"""scorbot/mujoco_twin.py: pure jaw mapping and scene checks (the latter skip without mujoco).

Both use the same nominal numbers, so this checks the scene is wired right, not that it
matches the real arm (unverified)."""

from importlib.util import find_spec
import math
import unittest

from scorbot import arm_chain, mujoco_twin, source_model
from scorbot.nominal import AXIS_RANGES

POSES = [(0, 0, 0, 0, 0), (30, 40, -60, -50, 10), (-120, 100, -120, 90, -300), (150, -30, 120, -120, 560)]


class JawOpeningTests(unittest.TestCase):
    def test_jaw_joints_are_separate_from_the_five_arm_joints(self):
        self.assertEqual(mujoco_twin.JAW_JOINTS, ("jaw_left", "jaw_right"))
        self.assertEqual(mujoco_twin.JOINTS, arm_chain.JOINTS)
        self.assertEqual(len(mujoco_twin.JOINTS), 5)

    def test_closed_gripper_has_zero_jaw_opening(self):
        self.assertEqual(mujoco_twin.jaw_opening(0), 0.0)

    def test_open_gripper_has_full_positive_jaw_travel(self):
        self.assertGreater(mujoco_twin.JAW_TRAVEL_M, 0)
        self.assertEqual(mujoco_twin.jaw_opening(100), mujoco_twin.JAW_TRAVEL_M)

    def test_half_open_gripper_has_half_jaw_travel(self):
        self.assertAlmostEqual(mujoco_twin.jaw_opening(50), mujoco_twin.JAW_TRAVEL_M / 2, delta=1e-12)

    def test_jaw_opening_clamps_above_one_hundred_percent(self):
        self.assertEqual(mujoco_twin.jaw_opening(150), mujoco_twin.JAW_TRAVEL_M)

    def test_jaw_opening_clamps_below_zero_percent(self):
        self.assertEqual(mujoco_twin.jaw_opening(-5), 0.0)

    def test_nan_jaw_opening_raises(self):
        with self.assertRaises(ValueError):
            mujoco_twin.jaw_opening(math.nan)

    def test_none_jaw_opening_raises(self):
        with self.assertRaises(ValueError):
            mujoco_twin.jaw_opening(None)


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

    def test_view_hold_pose_sets_only_the_five_arm_controls(self):
        from examples import mujoco_twin_view
        pose = (20, 30, -40, -30, 10)
        mujoco_twin_view.hold_pose(self.model, self.data, pose)
        for name, deg in zip(mujoco_twin.JOINTS, pose):
            self.assertAlmostEqual(self.data.ctrl[self.model.actuator(name).id], math.radians(deg), msg=name)
        for name in mujoco_twin.JAW_JOINTS:
            self.assertEqual(self.data.ctrl[self.model.actuator(name).id], 0.0, name)

    def test_the_arm_holds_a_commanded_pose(self):
        target = [math.radians(v) for v in (20, 30, -40, -30, 0)]
        for name, want in zip(arm_chain.JOINTS, target):
            self.data.ctrl[self.model.actuator(name).id] = want
        mujoco_twin.set_jaw(self.model, self.data, 50)
        jaw_target = mujoco_twin.jaw_opening(50)
        for _ in range(1500):                       # 3 s of simulated time
            self.mj.mj_step(self.model, self.data)
        for name, want in zip(arm_chain.JOINTS, target):
            self.assertAlmostEqual(self.data.joint(name).qpos[0], want, delta=math.radians(2), msg=name)
        for name in mujoco_twin.JAW_JOINTS:
            self.assertAlmostEqual(self.data.joint(name).qpos[0], jaw_target, delta=1e-4, msg=name)

    def test_the_jaws_close_and_open_to_the_old_fixed_plate_separation(self):
        import numpy as np
        mujoco_twin.set_jaw(self.model, self.data, 0)
        closed = np.linalg.norm(self.data.body("jaw_left").xpos - self.data.body("jaw_right").xpos)
        for name in mujoco_twin.JAW_JOINTS:
            self.assertAlmostEqual(self.data.joint(name).qpos[0], 0.0, delta=1e-12, msg=name)
            self.assertAlmostEqual(self.data.ctrl[self.model.actuator(name).id], 0.0, delta=1e-12, msg=name)
        mujoco_twin.set_jaw(self.model, self.data, 100)
        opened = np.linalg.norm(self.data.body("jaw_left").xpos - self.data.body("jaw_right").xpos)
        self.assertLess(closed, opened)
        self.assertAlmostEqual(opened, 2 * 0.024, delta=1e-6)
        for name in mujoco_twin.JAW_JOINTS:
            self.assertAlmostEqual(self.data.joint(name).qpos[0], mujoco_twin.JAW_TRAVEL_M, delta=1e-12, msg=name)
            self.assertAlmostEqual(self.data.ctrl[self.model.actuator(name).id],
                                   mujoco_twin.JAW_TRAVEL_M, delta=1e-12, msg=name)


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
