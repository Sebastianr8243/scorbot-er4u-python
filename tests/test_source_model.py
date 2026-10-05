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

    def test_the_window_is_the_travel_cap_around_home_where_no_limit_is_nearer(self):
        window = motion_window()
        self.assertEqual(set(window), set(limits.ARM_MOTORS))
        for motor in ("base", "elbow"):
            low, high = window[motor]
            self.assertEqual(low, -high)
            per_degree = source_model.COUNTS_PER_DEGREE[motor]
            self.assertAlmostEqual(high / per_degree, limits.TRAVEL_CAP_DEG, places=6)

    def test_a_pose_is_inside_only_if_no_arm_motor_leaves_the_cap(self):
        self.assertEqual(source_model.outside_window(HOME_ANGLES), {})
        three_up = source_model.pose_for_arm(shoulder=HOME_ANGLES["shoulder"] + 3)
        self.assertEqual(source_model.outside_window(three_up), {})
        five_down = source_model.pose_for_arm(shoulder=HOME_ANGLES["shoulder"] - 5)
        self.assertEqual(source_model.outside_window(five_down), {})
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


class GeometryTests(unittest.TestCase):
    """Tool position from joint angles and back, with the vendor's dimensions."""

    def test_the_dimensions_are_the_vendors_and_the_toolboxes(self):
        # ROB_4u.INI and the USNA toolbox's DH table give the same five numbers.
        g = source_model.geometry()
        self.assertEqual((g.d[0], g.a[0], g.a[1], g.a[2], g.d[4]),
                         (349.0, 16.0, 221.0, 221.0, 145.125))

    def test_home_is_where_the_toolbox_says(self):
        x, y, z, pitch, roll = source_model.xyzpr_from_angles(HOME_ANGLES)
        # ScorGoHome.m XYZPR = [169.300, 0, 504.328, -1.10912 rad, 0]; forward
        # kinematics on the toolbox's own joint vector gives 169.09, 504.22.
        self.assertAlmostEqual(x, 169.3, delta=0.3)
        self.assertAlmostEqual(y, 0.0, delta=1e-6)
        self.assertAlmostEqual(z, 504.33, delta=0.2)
        self.assertAlmostEqual(math.radians(pitch), -1.10912, places=4)
        self.assertAlmostEqual(roll, 0.0, places=6)

    def test_all_zero_angles_is_the_arm_stretched_out_level(self):
        x, y, z, pitch, roll = source_model.xyzpr_from_angles(dict.fromkeys(JOINTS, 0.0))
        self.assertAlmostEqual(x, 16.0 + 221.0 + 221.0 + 145.125)      # 603.125
        self.assertAlmostEqual(z, 349.0)
        self.assertEqual((round(y, 9), round(pitch, 9), round(roll, 9)), (0.0, 0.0, 0.0))

    def test_position_to_angles_and_back(self):
        rng = random.Random(8)
        checked = 0
        while checked < 100:
            angles = {"base": rng.uniform(-120, 160), "shoulder": rng.uniform(-20, 120),
                      "elbow": rng.uniform(-135, -10), "pitch": rng.uniform(-100, 120),
                      "roll": rng.uniform(-170, 170)}
            target = source_model.xyzpr_from_angles(angles)
            facing = math.radians(angles["base"])
            ahead = target[0] * math.cos(facing) + target[1] * math.sin(facing)
            if ahead < 60 or abs(target[3]) > 90:
                continue      # reaching back over the base: not a pose the solver returns
            back = source_model.angles_from_xyzpr(*target)
            for name in JOINTS:
                self.assertAlmostEqual(back[name], angles[name], places=6, msg=name)
            checked += 1

    def test_the_elbow_up_solution_is_the_one_returned(self):
        # As the toolbox does on hardware: elbow-up means a negative elbow angle.
        angles = source_model.angles_from_xyzpr(300.0, 100.0, 400.0, -45.0, 0.0)
        self.assertLess(angles["elbow"], 0)

    def test_a_point_out_of_reach_is_refused(self):
        with self.assertRaises(ValueError):
            source_model.angles_from_xyzpr(900.0, 0.0, 349.0, 0.0, 0.0)
        with self.assertRaises(ValueError):
            source_model.angles_from_xyzpr(0.0, 0.0, 600.0, 90.0, 0.0)      # on the base axis


class LimitTests(unittest.TestCase):
    """The joint limits the vendor's controller accepted, as the USNA toolbox found them."""

    def test_home_is_inside_and_the_published_numbers_are_used(self):
        self.assertEqual(source_model.outside_limits(HOME_ANGLES), {})
        # The narrower of the toolbox's findings and the vendor's parameter file.
        self.assertEqual(source_model.LIMITS_DEG["base"], (-132.0, 174.0))
        self.assertEqual(source_model.LIMITS_DEG["shoulder"], (-28.28, 124.0))
        self.assertEqual(source_model.LIMITS_DEG["elbow"], (-140.80, -5.16))
        self.assertEqual(source_model.LIMITS_DEG["pitch"], (-109.65, 113.0))
        self.assertEqual(source_model.LIMITS_DEG["roll"], (-360.0, 360.0))

    def test_elbow_down_is_outside(self):
        # The toolbox refuses elbow-down on hardware; so does this model.
        bent_down = dict(HOME_ANGLES, elbow=10.0)
        self.assertEqual(list(source_model.outside_limits(bent_down)), ["elbow"])

    def test_each_joint_past_its_limit_is_named(self):
        for joint, (low, high) in source_model.LIMITS_DEG.items():
            for value in (low - 1, high + 1):
                with self.subTest(joint=joint, value=value):
                    self.assertIn(joint, source_model.outside_limits(dict(HOME_ANGLES,
                                                                            **{joint: value})))

    def test_home_is_only_a_few_degrees_below_the_shoulders_upper_limit(self):
        # Home has the upper arm 120 degrees up. The vendor's limit is 124 and
        # the manual's travel ends at 130, so ten degrees further up from home
        # is past both. The travel cap alone does not protect that direction.
        headroom = source_model.LIMITS_DEG["shoulder"][1] - HOME_ANGLES["shoulder"]
        self.assertAlmostEqual(headroom, 3.72, delta=0.01)
        up_ten = source_model.pose_for_arm(shoulder=HOME_ANGLES["shoulder"] + limits.TRAVEL_CAP_DEG)
        self.assertIn("shoulder", source_model.outside_limits(up_ten))
        self.assertIn("shoulder", source_model.outside_window(up_ten))

    def test_everywhere_else_the_travel_cap_is_inside_the_limits(self):
        for joint, sign in (("base", -1), ("base", 1), ("shoulder", -1), ("elbow", -1),
                            ("elbow", 1)):
            pose = source_model.pose_for_arm(
                **{joint: HOME_ANGLES[joint] + sign * limits.TRAVEL_CAP_DEG})
            self.assertEqual(source_model.outside_limits(pose), {}, (joint, sign))
            self.assertEqual(source_model.outside_window(pose), {}, (joint, sign))

    def test_the_limit_window_has_no_travel_cap_in_it(self):
        # For jogs, which have no cap of their own: only the joint limits.
        window = source_model.limit_window()
        self.assertEqual(set(window), {"base", "shoulder"})
        per_degree = source_model.COUNTS_PER_DEGREE
        low, high = window["shoulder"]
        self.assertAlmostEqual(high / per_degree["shoulder"], 3.72, delta=0.02)
        self.assertAlmostEqual(-low / per_degree["shoulder"], 148.56, delta=0.05)
        low, high = window["base"]
        self.assertGreater(min(-low, high) / per_degree["base"], 130)

    def test_the_shoulder_motors_window_stops_at_its_limit(self):
        low, high = source_model.motion_window()["shoulder"]
        per_degree = source_model.COUNTS_PER_DEGREE["shoulder"]
        # Raising the shoulder raises its count in the vendor's convention.
        self.assertAlmostEqual(high / per_degree, 3.72, delta=0.02)
        self.assertAlmostEqual(-low / per_degree, limits.TRAVEL_CAP_DEG, places=6)


if __name__ == "__main__":
    unittest.main()
