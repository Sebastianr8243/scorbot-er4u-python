"""scorbot.arm_chain: where each link sits for given joint angles. Pure, offline.

The chain is checked three ways: against the manual's dimensions, against the
sign conventions it claims, and against the separate DH formulation in
scorbot.kinematics.
"""

import math
import random
import unittest

import numpy as np

from scorbot import arm_chain, kinematics, nominal
from scorbot.arm_chain import MANUAL, MESH_MODEL, link_poses, skeleton

ZERO = (0.0, 0.0, 0.0, 0.0, 0.0)


def position(pose):
    return tuple(round(float(v), 6) for v in pose[:3, 3])


class ZeroPoseTests(unittest.TestCase):
    def test_at_zero_every_link_is_aligned_with_the_base_and_the_arm_points_along_x(self):
        poses = link_poses(ZERO)
        self.assertEqual(tuple(poses), arm_chain.LINKS + ("tool",))
        for name, pose in poses.items():
            np.testing.assert_allclose(pose[:3, :3], np.eye(3), atol=1e-12, err_msg=name)
        height = MESH_MODEL.base_to_turret_mm + MESH_MODEL.turret_to_shoulder_mm
        self.assertEqual(position(poses["base_link"]), (0.0, 0.0, 0.0))
        self.assertEqual(position(poses["upper_arm_link"]), (29.0, 0.0, round(height, 6)))
        self.assertEqual(position(poses["forearm_link"]), (249.0, 0.0, round(height, 6)))
        self.assertEqual(position(poses["wrist_link"]), (469.0, 0.0, round(height, 6)))
        self.assertEqual(position(poses["flange_link"]), position(poses["wrist_link"]))
        self.assertEqual(position(poses["tool"]), (614.0, 0.0, round(height, 6)))

    def test_the_skeleton_is_the_line_through_the_joints(self):
        points = skeleton(ZERO)
        self.assertEqual(len(points), 6)
        self.assertEqual(points[0], (0.0, 0.0, 0.0))
        self.assertEqual(points[-1], position(link_poses(ZERO)["tool"]))
        lengths = [math.dist(a, b) for a, b in zip(points, points[1:])]
        self.assertAlmostEqual(lengths[2], 220.0)      # upper arm
        self.assertAlmostEqual(lengths[3], 220.0)      # forearm
        self.assertAlmostEqual(lengths[4], 145.0)      # wrist axis to tool tip


class ManualAgreementTests(unittest.TestCase):
    def test_upper_arm_and_forearm_are_the_manuals(self):
        for geometry in (MESH_MODEL, MANUAL):
            self.assertEqual(geometry.upper_arm_mm, nominal.UPPER_ARM_MM.value)
            self.assertEqual(geometry.forearm_mm, nominal.FOREARM_MM.value)

    def test_stretched_out_reach_is_within_a_few_mm_of_the_manuals_radius(self):
        reach = link_poses(ZERO)["tool"][0, 3]
        self.assertLess(abs(reach - nominal.MAX_OPERATING_RADIUS_MM.value), 5.0)

    def test_the_two_sources_disagree_on_the_shoulder_and_that_is_on_record(self):
        # Manual side view: 364 mm from the base bottom. CAD model: 346 mm from
        # the mounting face, and the vendor's parameter file says 349. Nobody
        # has measured our arm, so neither is corrected to the other.
        mesh = MESH_MODEL.base_to_turret_mm + MESH_MODEL.turret_to_shoulder_mm
        manual = MANUAL.base_to_turret_mm + MANUAL.turret_to_shoulder_mm
        self.assertEqual(manual, nominal.SHOULDER_AXIS_HEIGHT_MM.value)
        self.assertAlmostEqual(manual - mesh, 18.0, delta=0.1)
        self.assertLess(abs(mesh - nominal.VENDOR_BASE_HEIGHT_MM.value), 3.5)
        self.assertEqual((MESH_MODEL.shoulder_forward_mm, MANUAL.shoulder_forward_mm),
                         (29.0, 16.0))

    def test_both_geometries_say_they_are_not_measured(self):
        for geometry in (MESH_MODEL, MANUAL):
            self.assertIn("not measured", geometry.status)
            self.assertTrue(geometry.source)


class SignConventionTests(unittest.TestCase):
    def tip(self, q):
        return link_poses(q)["tool"][:3, 3]

    def test_positive_base_turns_counter_clockwise_seen_from_above(self):
        self.assertGreater(self.tip((10, 0, 0, 0, 0))[1], 0)
        np.testing.assert_allclose(self.tip((90, 0, 0, 0, 0))[:2], (0.0, 614.0), atol=1e-9)

    def test_positive_shoulder_elbow_and_pitch_each_lift_what_is_beyond_them(self):
        level = self.tip(ZERO)[2]
        for index in (1, 2, 3):
            q = [0.0] * 5
            q[index] = 10.0
            self.assertGreater(self.tip(q)[2], level, arm_chain.JOINTS[index])
        np.testing.assert_allclose(link_poses((0, 90, 0, 0, 0))["tool"][:3, 3],
                                   (29.0, 0.0, 345.99 + 585.0), atol=1e-9)

    def test_only_the_joints_before_a_link_move_it(self):
        base = link_poses(ZERO)
        moved = link_poses((0, 0, 30, 0, 0))                 # elbow only
        for name in ("base_link", "turret_link", "upper_arm_link"):
            np.testing.assert_allclose(moved[name], base[name], atol=1e-12)
        self.assertFalse(np.allclose(moved["wrist_link"], base["wrist_link"]))
        self.assertEqual(position(moved["forearm_link"]), position(base["forearm_link"]))

    def test_roll_turns_the_flange_about_the_tool_direction_and_leaves_the_tip_alone(self):
        rolled = link_poses((0, 0, 0, 0, 90))
        np.testing.assert_allclose(rolled["tool"][:3, 3], link_poses(ZERO)["tool"][:3, 3],
                                   atol=1e-9)
        np.testing.assert_allclose(rolled["wrist_link"][:3, :3], np.eye(3), atol=1e-12)
        # right-handed about +x: the flange's y axis now points up
        np.testing.assert_allclose(rolled["flange_link"][:3, 1], (0.0, 0.0, 1.0), atol=1e-12)

    def test_every_pose_is_a_rigid_transform(self):
        rng = random.Random(3)
        for _ in range(50):
            q = [rng.uniform(-170, 170) for _ in range(5)]
            for name, pose in link_poses(q).items():
                rotation = pose[:3, :3]
                np.testing.assert_allclose(rotation @ rotation.T, np.eye(3), atol=1e-9,
                                           err_msg=name)
                self.assertAlmostEqual(float(np.linalg.det(rotation)), 1.0, places=9)
                np.testing.assert_allclose(pose[3], (0, 0, 0, 1), atol=0)


class AgreementWithTheDhModelTests(unittest.TestCase):
    """Same geometry, two formulations: the joint centres must coincide."""

    def test_wrist_centre_and_tool_tip_match_scorbot_kinematics(self):
        rng = random.Random(11)
        for geometry in (MESH_MODEL, MANUAL):
            height = geometry.base_to_turret_mm + geometry.turret_to_shoulder_mm
            dh = kinematics.DHParameters(
                d=(height, 0.0, 0.0, 0.0, geometry.tool_length_mm),
                a=(geometry.shoulder_forward_mm, geometry.upper_arm_mm, geometry.forearm_mm,
                   0.0, 0.0))
            for _ in range(40):
                q = [rng.uniform(-150, 150), rng.uniform(-35, 130), rng.uniform(-130, 130),
                     rng.uniform(-130, 130), rng.uniform(-170, 170)]
                # kinematics.py measures wrist pitch from 90 degrees below the forearm
                q_dh = [q[0], q[1], q[2], q[3] + 90.0, q[4]]
                poses = link_poses(q, geometry)
                np.testing.assert_allclose(poses["wrist_link"][:3, 3],
                                           kinematics.wrist_center(q_dh, dh), atol=1e-6)
                np.testing.assert_allclose(poses["tool"][:3, 3],
                                           kinematics.tool_position(q_dh, dh), atol=1e-6)


class InputTests(unittest.TestCase):
    def test_five_finite_angles_are_required(self):
        for bad in ((0, 0, 0, 0), (0, 0, 0, 0, 0, 0), (0, 0, float("nan"), 0, 0),
                    (0, 0, float("inf"), 0, 0), "abcde", None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                link_poses(bad)

    def test_the_module_needs_no_usb_and_no_legacy_code(self):
        import subprocess
        import sys
        code = ("import sys; import scorbot.arm_chain; "
                "print(any(m == 'usb' or m.startswith('usb.') or m in ('libdef', 'libcomm') "
                "for m in sys.modules))")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             check=True)
        self.assertEqual(out.stdout.strip(), "False")


if __name__ == "__main__":
    unittest.main()
