"""scorbot.source_model: joint angles from what is already known about the ER-4U.

The numbers come from the vendor's own code and parameter files. Nothing here
is measured on our arm; these tests check that the model is the sources',
that it is consistent, and that it keeps saying where it came from.
"""

import math
import random
import unittest

from scorbot import arm_chain, limits, source_model
from scorbot.source_model import (HOME_ANGLES, JOINTS, MOTORS, angles_from_counts,
                                  counts_from_angles, motion_window)

HOME = dict.fromkeys(MOTORS, 0)


class WhereItComesFromTests(unittest.TestCase):
    def test_it_says_it_is_from_sources_and_not_measured(self):
        self.assertIn("from sources", source_model.STATUS)
        self.assertIn("not measured", source_model.STATUS)
        self.assertEqual(source_model.TIER, "sources")

    def test_home_is_the_pose_the_usna_toolbox_publishes(self):
        # kutzer/ScorBotToolbox ScorGoHome.m: XYZPR = [169.300, 0, 504.328, -1.10912, 0].
        self.assertEqual(angles_from_counts(HOME), HOME_ANGLES)
        q = [HOME_ANGLES[name] for name in JOINTS]
        tool = arm_chain.link_poses(q, arm_chain.VENDOR_INI)["tool"][:3, 3]
        for got, want in zip(tool, (169.300, 0.0, 504.328)):
            self.assertAlmostEqual(float(got), want, delta=0.3)
        pitch = HOME_ANGLES["shoulder"] + HOME_ANGLES["elbow"] + HOME_ANGLES["pitch"]
        self.assertAlmostEqual(math.radians(pitch), -1.10912, places=4)

    def test_the_scales_are_the_vendor_parameter_files(self):
        # ER4Ax1-5.ini NoEnc90: counts for 90 degrees of each axis.
        for motor, joint, counts_per_90 in (("base", "base", 12770),
                                            ("shoulder", "shoulder", 10216)):
            moved = angles_from_counts({**HOME, motor: counts_per_90})
            self.assertAlmostEqual(abs(moved[joint] - HOME_ANGLES[joint]), 90.0, places=6)


class ConsistencyTests(unittest.TestCase):
    def test_angles_to_counts_and_back_agree_within_two_counts_worth(self):
        rng = random.Random(4)
        for _ in range(200):
            counts = {m: rng.randint(-1400, 1400) for m in MOTORS}
            back = counts_from_angles(angles_from_counts(counts))
            for motor in MOTORS:
                self.assertLessEqual(abs(back[motor] - counts[motor]), 2, motor)

    def test_the_shoulder_motor_alone_changes_the_elbow_angle_too(self):
        # The vendor's elbow angle is relative to the upper arm, and the elbow
        # motor holds the forearm's angle to the floor. A model that treated
        # the motors as independent would get this wrong.
        raised = angles_from_counts({**HOME, "shoulder": -1135})
        self.assertAlmostEqual(abs(raised["shoulder"] - HOME_ANGLES["shoulder"]), 10.0, places=1)
        self.assertAlmostEqual(raised["shoulder"] + raised["elbow"],
                               HOME_ANGLES["shoulder"] + HOME_ANGLES["elbow"], places=6)

    def test_the_wrist_motors_together_roll_and_against_each_other_pitch(self):
        together = angles_from_counts({**HOME, "wrist_motor_1": 280, "wrist_motor_2": 280})
        self.assertAlmostEqual(together["pitch"], HOME_ANGLES["pitch"], places=6)
        self.assertNotAlmostEqual(together["roll"], HOME_ANGLES["roll"], places=1)
        against = angles_from_counts({**HOME, "wrist_motor_1": 280, "wrist_motor_2": -280})
        self.assertAlmostEqual(against["roll"], HOME_ANGLES["roll"], places=6)
        self.assertNotAlmostEqual(against["pitch"], HOME_ANGLES["pitch"], places=1)

    def test_extra_names_such_as_the_gripper_are_ignored(self):
        self.assertEqual(angles_from_counts({**HOME, "gripper": 900}), HOME_ANGLES)

    def test_bad_input_is_refused(self):
        for bad in ({}, {"base": 0}, {**HOME, "base": float("nan")}, {**HOME, "base": "1"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                angles_from_counts(bad)
        with self.assertRaises(ValueError):
            counts_from_angles({"base": 0.0})
        with self.assertRaises(ValueError):
            counts_from_angles({**HOME_ANGLES, "elbow": float("inf")})


class MotionWindowTests(unittest.TestCase):
    """What the source tier may command: only what stays inside the travel cap."""

    def test_the_window_is_the_travel_cap_around_home_for_the_three_arm_motors(self):
        window = motion_window()
        self.assertEqual(set(window), set(limits.ARM_MOTORS))
        for motor, (low, high) in window.items():
            self.assertEqual(low, -high)
            per_degree = source_model.COUNTS_PER_DEGREE[motor]
            self.assertAlmostEqual(high / per_degree, limits.TRAVEL_CAP_DEG, places=6)

    def test_a_pose_is_inside_only_if_no_arm_motor_leaves_the_cap(self):
        self.assertEqual(source_model.outside_window(HOME_ANGLES), {})
        five_up = source_model.pose_for_arm(shoulder=HOME_ANGLES["shoulder"] + 5)
        self.assertEqual(source_model.outside_window(five_up), {})
        far = dict(HOME_ANGLES, base=HOME_ANGLES["base"] + 25)
        self.assertEqual(list(source_model.outside_window(far)), ["base"])

    def test_moving_the_arm_motors_alone_changes_the_relative_pitch(self):
        # The wrist motors hold the gripper's angle to the floor. With them left
        # alone, raising the shoulder 5 degrees lowers the pitch (relative to
        # the forearm chain) by 5: the gripper keeps pointing where it did.
        pose = source_model.pose_for_arm(shoulder=HOME_ANGLES["shoulder"] + 5)
        self.assertAlmostEqual(pose["shoulder"], HOME_ANGLES["shoulder"] + 5, delta=0.02)
        self.assertAlmostEqual(pose["elbow"], HOME_ANGLES["elbow"], delta=0.02)
        self.assertAlmostEqual(pose["pitch"], HOME_ANGLES["pitch"] - 5, delta=0.05)
        self.assertAlmostEqual(pose["shoulder"] + pose["elbow"] + pose["pitch"],
                               sum(HOME_ANGLES[j] for j in ("shoulder", "elbow", "pitch")),
                               delta=0.05)
        counts = counts_from_angles(pose)
        self.assertLessEqual(max(abs(counts[m]) for m in limits.WRIST_MOTORS), 2)

    def test_arm_counts_are_what_a_stream_is_given(self):
        counts = source_model.arm_counts_from_angles(
            base=HOME_ANGLES["base"] + 3, shoulder=HOME_ANGLES["shoulder"],
            elbow=HOME_ANGLES["elbow"] - 2)
        self.assertEqual(set(counts), set(limits.ARM_MOTORS))
        back = angles_from_counts({**HOME, **counts})
        self.assertAlmostEqual(back["base"], HOME_ANGLES["base"] + 3, delta=0.02)
        self.assertAlmostEqual(back["elbow"], HOME_ANGLES["elbow"] - 2, delta=0.02)

    def test_wrist_angles_other_than_home_are_outside_for_now(self):
        # Wrist motion stays disabled until its two motors are measured.
        tilted = dict(HOME_ANGLES, pitch=HOME_ANGLES["pitch"] + 5)
        self.assertIn("wrist_motor_1", source_model.outside_window(tilted))

    def test_the_module_needs_no_usb_and_no_legacy_code(self):
        import subprocess
        import sys
        code = ("import sys; import scorbot.source_model; "
                "print(any(m.split('.')[0] in ('usb', 'libdef', 'libcomm', 'ruckig') "
                "for m in sys.modules))")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             check=True)
        self.assertEqual(out.stdout.strip(), "False")


if __name__ == "__main__":
    unittest.main()
