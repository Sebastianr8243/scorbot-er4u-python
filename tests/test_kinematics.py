"""Offline checks of the UNVALIDATED kinematics reference; no USB or arm required."""

import importlib.util
import itertools
import math
import unittest

import numpy as np

from scorbot import Scorbot
from scorbot import kinematics as kin


def _angle_error(a, b):
    return np.abs((np.asarray(a) - np.asarray(b) + 180.0) % 360.0 - 180.0)


# Joint grid kept away from the elbow-straight and wrist-on-base-axis singularities.
JOINT_GRID = list(itertools.product(
    (-120.0, -45.0, 0.0, 30.0, 150.0),   # base
    (20.0, 60.0, 90.0, 120.0),           # shoulder
    (-130.0, -90.0, -30.0, 40.0, 100.0), # elbow (both postures)
    (-60.0, 0.0, 45.0),                  # wrist pitch
    (-90.0, 0.0, 170.0),                 # wrist roll
))


class ForwardKinematicsTests(unittest.TestCase):
    def test_assumed_home_pose(self):
        # angRef = [0, 90, -90, 0, 0]: upper arm vertical, forearm horizontal,
        # tool pointing down under the module's ASSUMED convention.
        pose = kin.forward([0, 90, -90, 0, 0])
        np.testing.assert_allclose(pose[:3, 3], [236.0, 0.0, 584.0 - 145.125], atol=1e-9)
        np.testing.assert_allclose(pose[:3, 2], [0.0, 0.0, -1.0], atol=1e-12)
        np.testing.assert_allclose(kin.wrist_center([0, 90, -90, 0, 0]),
                                   [236.0, 0.0, 584.0], atol=1e-9)

    def test_pose_is_rigid_transform(self):
        rng = np.random.default_rng(1)
        for q in rng.uniform(-180, 180, size=(20, 5)):
            rotation = kin.forward(q)[:3, :3]
            np.testing.assert_allclose(rotation @ rotation.T, np.eye(3), atol=1e-12)
            self.assertAlmostEqual(np.linalg.det(rotation), 1.0, places=12)

    def test_rejects_bad_joint_vectors(self):
        for q in ([0, 0, 0, 0], [0, 0, 0, 0, math.nan]):
            with self.assertRaises(ValueError):
                kin.forward(q)


class InverseKinematicsTests(unittest.TestCase):
    def test_round_trip_over_joint_grid(self):
        checked = 0
        for q in JOINT_GRID:
            x, y, z = kin.tool_position(q)
            base = math.radians(q[0])
            if x * math.cos(base) + y * math.sin(base) < 1.0:
                # Tool point behind (or on) the base axis: inverse only returns
                # the front-facing base solution, so this pose is out of scope.
                continue
            checked += 1
            with self.subTest(q=q):
                pitch, roll = kin.tool_pitch_roll(q)
                elbow = "up" if q[2] < 0 else "down"
                solution = kin.inverse(x, y, z, pitch, roll, elbow=elbow)
                self.assertLess(float(np.max(_angle_error(solution, q))), 1e-6)
                np.testing.assert_allclose(kin.forward(solution), kin.forward(q), atol=1e-6)
        self.assertGreater(checked, len(JOINT_GRID) // 2)

    def test_round_trip_over_cartesian_grid(self):
        for x, y, z, pitch in itertools.product((150, 250, 350), (-150, 0, 150),
                                                (50, 250, 450), (-90, -45, 0)):
            with self.subTest(x=x, y=y, z=z, pitch=pitch):
                self.assertTrue(kin.reachable(x, y, z, pitch))
                for elbow in ("up", "down"):
                    q = kin.inverse(x, y, z, pitch, 25, elbow=elbow)
                    np.testing.assert_allclose(kin.tool_position(q), [x, y, z], atol=1e-6)
                    np.testing.assert_allclose(kin.tool_pitch_roll(q), (pitch, 25), atol=1e-6)
                    self.assertEqual(q[2] <= 0, elbow == "up")

    def test_unreachable_raises(self):
        # Too far for the nominal 220 + 220 mm arm, or on the base axis (singular).
        cases = [(900, 0, 364, 0), (100, 0, 2000, 90), (500, 350, 364, 0),
                 (0, 0, 300, -90)]
        for case in cases:
            with self.subTest(case=case):
                self.assertFalse(kin.reachable(*case))
                with self.assertRaises(ValueError):
                    kin.inverse(*case)

    def test_rejects_bad_arguments(self):
        with self.assertRaises(ValueError):
            kin.inverse(300, 0, 300, -90, elbow="sideways")
        with self.assertRaises(ValueError):
            kin.inverse(math.inf, 0, 300, -90)


class TrapezoidTests(unittest.TestCase):
    def check_profile(self, q0, q1, vmax, amax, dt):
        times, positions = kin.trapezoid(q0, q1, vmax, amax, dt)
        np.testing.assert_array_equal(positions[0], q0)
        np.testing.assert_array_equal(positions[-1], q1)
        self.assertEqual(times[0], 0.0)
        steps = np.diff(times)
        self.assertTrue(np.all(steps > 0))
        self.assertTrue(np.all(steps <= dt + 1e-12))
        velocity = np.diff(positions, axis=0) / steps[:, np.newaxis]
        self.assertLessEqual(float(np.max(np.abs(velocity))), vmax * (1 + 1e-9))
        # 2 * second divided difference equals the acceleration somewhere in
        # the interval, so it is bounded by amax even with an uneven last step.
        spans = (times[2:] - times[:-2])[:, np.newaxis] / 2
        acceleration = np.diff(velocity, axis=0) / spans
        self.assertLessEqual(float(np.max(np.abs(acceleration))), amax * (1 + 1e-6))
        # Starts and ends at rest: the first and last sample intervals move
        # no faster than amax allows from zero velocity.
        self.assertLessEqual(float(np.max(np.abs(velocity[0]))), amax * steps[0] / 2 + 1e-9)
        self.assertLessEqual(float(np.max(np.abs(velocity[-1]))), amax * steps[-1] / 2 + 1e-9)
        return times, positions

    def test_trapezoid_limits_and_exact_target(self):
        for q1, vmax, amax, dt in (([90, -30, 10, 0, 45], 30, 60, 0.01),
                                   ([2, 0, 0, 0, 0], 30, 60, 0.01),   # triangular
                                   ([10, 5, -3, 0, 1], 20, 100, 0.013),
                                   ([-45, 45, 0, 0, 0], 5, 5, 0.1)):
            with self.subTest(q1=q1, vmax=vmax, amax=amax, dt=dt):
                self.check_profile([0, 0, 0, 0, 0], q1, vmax, amax, dt)

    def test_cruise_speed_reached_on_long_moves(self):
        times, positions = self.check_profile([0.0], [100.0], 20, 40, 0.01)
        self.assertAlmostEqual(times[-1], 100 / 20 + 20 / 40, places=9)
        self.assertAlmostEqual(float(np.max(np.diff(positions[:, 0]) / np.diff(times))),
                               20.0, places=6)

    def test_zero_move_and_bad_limits(self):
        times, positions = kin.trapezoid([1, 2], [1, 2], 10, 10, 0.01)
        np.testing.assert_array_equal(times, [0.0])
        np.testing.assert_array_equal(positions, [[1, 2]])
        for args in ((0, 10, 0.01), (10, -1, 0.01), (10, 10, 0), (math.nan, 10, 0.01)):
            with self.assertRaises(ValueError):
                kin.trapezoid([0], [1], *args)
        with self.assertRaises(ValueError):
            kin.trapezoid([0, 0], [1], 10, 10, 0.01)


@unittest.skipUnless(importlib.util.find_spec("roboticstoolbox"),
                     "roboticstoolbox-python is not installed")
class RoboticsToolboxCrossCheckTests(unittest.TestCase):
    def test_forward_matches_rtb_dhrobot(self):
        import roboticstoolbox as rtb

        p = kin.NOMINAL
        robot = rtb.DHRobot([rtb.RevoluteDH(d=p.d[i], a=p.a[i], alpha=p.alpha[i])
                             for i in range(5)], name="ScorBot ER-4U nominal")
        rng = np.random.default_rng(42)
        for q in rng.uniform(-180, 180, size=(50, 5)):
            with self.subTest(q=q):
                expected = robot.fkine(np.radians(q)).A
                np.testing.assert_allclose(kin.forward(q), expected, atol=1e-9)


@unittest.skipUnless(importlib.util.find_spec("usb"), "PyUSB is not installed")
class LegacyInverseKinematicsFindings(unittest.TestCase):
    """Measured disagreement between legacy libdef.cIn and the nominal model.

    FINDING: cIn ignores the 16 mm shoulder offset a1. It tries to compute the
    shoulder position as ``m1*m2*[[0],[0],[0],[1]]``, but with NumPy arrays
    ``*`` is element-wise, so every entry it reads (``coff[1,1]``,
    ``coff[2,1]``; also the wrong indices for a position column) is zero.
    cIn therefore solves a planar two-link arm whose shoulder sits on the base
    axis at z=364. It also ignores the tool length d5 and the wrist: its
    (x, y, z) is the wrist-pitch-axis point, not the tool tip.
    These tests document the disagreement; they do not endorse either model.
    """

    @classmethod
    def setUpClass(cls):
        cls.legacy = Scorbot()._legacy("libdef")

    def arm_cases(self):
        for base, shoulder, elbow in itertools.product((-60.0, 0.0, 35.0),
                                                       (30.0, 60.0, 90.0),
                                                       (-110.0, -90.0, -45.0)):
            yield [base, shoulder, elbow, 0.0, 0.0]

    def test_cin_disagrees_on_model_wrist_center(self):
        worst = 0.0
        for q in self.arm_cases():
            legacy = np.asarray(self.legacy.cIn(*kin.wrist_center(q)), dtype=float)
            worst = max(worst, float(np.max(_angle_error(legacy, q[:3]))))
            self.assertLess(abs(legacy[0] - q[0]), 1e-9)   # base angle agrees
        # Measured: shoulder/elbow disagree by 4.3 to 12.5 degrees on this
        # grid (worst at [-60, 30, -45]: cIn gives [-60, 23.49, -32.54]).
        self.assertAlmostEqual(worst, 12.456601, places=5)

    def test_assumed_home_pose_disagreement(self):
        legacy = np.asarray(self.legacy.cIn(*kin.wrist_center([0, 90, -90, 0, 0])))
        # Model wrist center at HOME is (236, 0, 584); cIn does not return
        # angRef [0, 90, -90] for it. Measured value:
        np.testing.assert_allclose(legacy, [0.0, 85.829151, -85.677409], atol=1e-5)
        # cIn does return angRef for the a1-free point (220, 0, 584).
        np.testing.assert_allclose(self.legacy.cIn(220, 0, 584), [0.0, 90.0, -90.0], atol=1e-9)

    def test_cin_equals_model_with_shoulder_offset_removed(self):
        # Exact characterization: cIn(p) is our model's elbow-up solution for
        # the wrist center p + a1 * radial, i.e. cIn = model with a1 = 0.
        a1 = kin.NOMINAL.a[0]
        for q in self.arm_cases():
            x, y, z = kin.wrist_center(q)
            base = math.radians(q[0])
            legacy = self.legacy.cIn(x - a1 * math.cos(base), y - a1 * math.sin(base), z)
            np.testing.assert_allclose(legacy, q[:3], atol=1e-9)

    def test_cin_unreachable_sentinel_is_a_valid_angle(self):
        # cIn signals "unreachable" with [-1, -1, -1], which is also a valid
        # joint angle; moveXYZ only catches it via the shoulder window check.
        self.assertEqual(list(self.legacy.cIn(1000, 0, 0)), [-1, -1, -1])
        with self.assertRaises(ValueError):
            kin.inverse(1000, 0, 0, 0)


if __name__ == "__main__":
    unittest.main()
