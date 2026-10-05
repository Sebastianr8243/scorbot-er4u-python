"""scorbot.vendor_model: the vendor's count/angle conversion, read from USBC.dll.

Three independent checks, none of which involves hardware:

- GOLDEN: outputs of the vendor's own function run in Ghidra's emulator.
- The USNA MTIS toolbox (Wick, Esposito, Knowles, 2010), a separately written
  and published conversion that was used on real ER-4u arms.
- The home pose published in Kutzer's ScorBot Toolbox for MATLAB.
"""

import math
import random
import unittest

from scorbot import vendor_model as vm

# (encoder counts) -> (base, shoulder, elbow, pitch, roll) in radians, from the
# 2018 build's function at 0x100303db run in the p-code emulator with the
# $Default ER-4u parameters.
GOLDEN = (
    ((0, 0, 0, 0, 0), (-0.0, -2.0992641199814726, 1.6584386433838836, 1.5499540657681792, 0.0)),
    ((1, 0, 0, 0, 0), (-0.000123006760124894, -2.0992641199814726, 1.6584386433838836,
                       1.5499540657681792, 0.0)),
    ((0, 1, 0, 0, 0), (-0.0, -2.0994178784316286, 1.6585924018340399, 1.549954065768179, 0.0)),
    ((0, 0, 1, 0, 0), (-0.0, -2.0992641199814726, 1.6585924018340399, 1.5498003073180229, 0.0)),
    ((0, 0, 0, 1, 0), (-0.0, -2.0992641199814726, 1.6584386433838836, 1.5499540657681792, 0.0)),
    ((0, 0, 0, 1, 1), (-0.0, -2.0992641199814726, 1.6584386433838836, 1.5499540657681792,
                       0.0006255660401413368)),
    ((0, 0, 0, 1, -1), (-0.0, -2.0992641199814726, 1.6584386433838836, 1.5505796318083205, 0.0)),
    ((0, 0, 0, -3, 0), (-0.0, -2.0992641199814726, 1.6584386433838836, 1.5493284997280379,
                        -0.0006255660401413368)),
    ((0, 0, 0, 3, 2), (-0.0, -2.0992641199814726, 1.6584386433838836, 1.5499540657681792,
                       0.0012511320802826736)),
    ((-25000, -18000, -25000, -15000, -15000),
     (3.07516900312235, 0.6683879828286429, -4.95317471332917, 5.393915319671117,
      -9.383490602120052)),
    ((20000, 1500, 20000, 15000, 15000),
     (-2.4601352024978804, -2.329901795215649, 4.96424532174041, -1.5252149373541712,
      9.383490602120052)),
    ((12770, -10216, 10216, 2511, 2511),
     (-1.5707963267948966, -0.528467793186576, 1.6584386433838836, -0.02084226102671738,
      1.5707963267948968)),
)
LIMITS = ((-25000, 20000), (-18000, 1500), (-25000, 20000), (-15000, 15000), (-15000, 15000))


def mtis_counts_to_degrees(counts):
    """ScorCnts2Deg from the USNA MTIS toolbox, with its own constants."""
    kb, kse, kw = -141.8888, -113.5111, -27.9
    offs, offe, offw = 120.27, -25.24, 63.57
    c1, c2, c3, c4, c5 = counts
    return (c1 / kb,
            c2 / kse - offs,
            offs + offe - (c2 + c3) / kse,
            (c5 - c4) / (2 * kw) + c3 / kse + offw - offe,
            (c4 + c5) / (-2 * kw))


def random_counts(seed, n):
    rng = random.Random(seed)
    return [[rng.randint(low, high) for low, high in LIMITS] for _ in range(n)]


class ForwardTests(unittest.TestCase):
    def test_matches_the_emulated_vendor_function(self):
        for counts, expected in GOLDEN:
            with self.subTest(counts=counts):
                joints = vm.counts_to_joints(counts)
                for name, value in zip(vm.JOINTS, expected):
                    self.assertAlmostEqual(joints[name], value, delta=1e-12)

    def test_agrees_with_the_usna_mtis_conversion(self):
        # MTIS rounds its offsets to 0.01 degree, so agreement is to that order.
        for counts in [[0] * 5, *random_counts(7, 500)]:
            joints = vm.counts_to_joints(counts)
            for name, expected in zip(vm.JOINTS, mtis_counts_to_degrees(counts)):
                self.assertAlmostEqual(math.degrees(joints[name]), expected, delta=0.03,
                                       msg=f"{name} at {counts}")

    def test_zero_counts_give_the_published_home_pose(self):
        # Kutzer ScorSimGoHome: BSEPRhome = [0, 2.09925, -1.65843, -1.54994, 0] rad
        home = vm.counts_to_joints([0, 0, 0, 0, 0])
        reported = vm.toolbox_degrees(home)
        for name, radians in zip(vm.JOINTS, (0.0, 2.09925, -1.65843, -1.54994, 0.0)):
            self.assertAlmostEqual(math.radians(reported[name]), radians, delta=2e-5)
        # Kutzer ScorGoHome: XYZPRhome pitch = -1.10912 rad (toolbox sign)
        self.assertAlmostEqual(-vm.absolute_angles(home)["gripper_pitch"], -1.10912, delta=2e-5)

    def test_counts_per_degree_match_the_ini_priors(self):
        expected = (-141.89, -113.51, 113.51, 27.90, 27.90)
        for axis, value in enumerate(expected):
            self.assertAlmostEqual(vm.ER4U.counts_per_degree(axis), value, delta=0.005)

    def test_five_counts_are_required(self):
        with self.assertRaises(ValueError):
            vm.counts_to_joints([0, 0, 0, 0])


class MechanismTests(unittest.TestCase):
    """What the formulas say about the arm, stated as tests."""

    def test_shoulder_motor_alone_keeps_forearm_and_gripper_orientation(self):
        for counts in random_counts(3, 200):
            moved = list(counts)
            moved[1] += 500
            before = vm.absolute_angles(vm.counts_to_joints(counts))
            after = vm.absolute_angles(vm.counts_to_joints(moved))
            self.assertNotAlmostEqual(before["upper_arm"], after["upper_arm"], delta=1e-3)
            self.assertAlmostEqual(before["forearm"], after["forearm"], delta=1e-12)
            self.assertAlmostEqual(before["gripper_pitch"], after["gripper_pitch"], delta=1e-12)

    def test_wrist_motors_opposite_pitch_and_together_roll(self):
        start = vm.counts_to_joints([0, 0, 0, 100, -100])
        pitched = vm.counts_to_joints([0, 0, 0, 200, -200])     # opposite directions
        self.assertAlmostEqual(pitched["roll"], start["roll"], delta=1e-12)
        self.assertAlmostEqual(math.degrees(pitched["pitch"] - start["pitch"]), 100 / 27.9,
                               delta=1e-6)
        rolled = vm.counts_to_joints([0, 0, 0, 200, 0])         # same direction
        self.assertAlmostEqual(rolled["pitch"], start["pitch"], delta=1e-12)
        self.assertAlmostEqual(math.degrees(rolled["roll"] - start["roll"]), 100 / 27.9,
                               delta=1e-6)

    def test_odd_wrist_difference_loses_half_a_count_toward_zero(self):
        self.assertEqual(vm._half(3), 1)
        self.assertEqual(vm._half(-3), -1)
        self.assertEqual(vm.counts_to_joints([0, 0, 0, 3, 0])["pitch"],
                         vm.counts_to_joints([0, 0, 0, 2, 0])["pitch"])


class InverseTests(unittest.TestCase):
    def test_each_term_is_truncated_toward_zero(self):
        # The DLL converts with __ftol, which sets the x87 rounding mode to
        # truncate. (Ghidra's emulator ignores that mode and rounds to nearest,
        # so the truncation is read from the instructions, not from emulation.)
        self.assertEqual(vm._truncate(-4064.8), -4064)
        self.assertEqual(vm._truncate(4064.8), 4064)
        counts = vm.joints_to_counts(
            {"base": 0.5, "shoulder": -1.0, "elbow": 1.0, "pitch": 0.25, "roll": 0.0})
        self.assertEqual(counts["base"], -4064)

    def test_matches_the_emulated_vendor_function_apart_from_its_rounding(self):
        # Emulated 0x10030ed2 outputs. The emulator rounds to nearest, so the
        # structure of the formula is compared with rounding switched to match.
        emulated = (
            ((0.5, -1.0, 1.0, 0.25, 0.0), (-4065, -7149, 2867, -1373, 1373)),
            ((-0.5, -2.0, 1.6, 1.5, 1.0), (4065, -646, 266, 1584, 1614)),
            ((1e-4, -1e-4, 1e-4, -1e-4, 1e-4), (-1, -13652, 2867, -1773, 1773)),
        )
        truncate = vm._truncate
        vm._truncate = round
        try:
            for joints, expected in emulated:
                counts = vm.joints_to_counts(dict(zip(vm.JOINTS, joints)))
                self.assertEqual(tuple(counts[name] for name in vm.MOTORS), expected)
        finally:
            vm._truncate = truncate

    def test_round_trip_stays_within_three_counts(self):
        # Truncation of each term and of the wrist half-difference costs up to
        # three counts on a wrist motor; nothing accumulates beyond that.
        worst = 0
        for counts in random_counts(11, 20000):
            back = vm.joints_to_counts(vm.counts_to_joints(counts))
            worst = max(worst, max(abs(back[name] - value)
                                   for name, value in zip(vm.MOTORS, counts)))
        self.assertLessEqual(worst, 3)

    def test_other_gearings_are_refused(self):
        with self.assertRaises(ValueError):
            vm.joints_to_counts(dict.fromkeys(vm.JOINTS, 0.0),
                                vm.VendorAxes(gearing=(0, 0, 0, 0)))

    def test_axes_reject_bad_parameters(self):
        with self.assertRaises(ValueError):
            vm.VendorAxes(no_enc_90=(0, 1, 1, 1, 1))
        with self.assertRaises(ValueError):
            vm.VendorAxes(gearing=(2, 0, 0, 0))


class LegacyComparisonTests(unittest.TestCase):
    def test_legacy_wrist_scale_disagrees_with_the_vendor_scale(self):
        # Recorded, not fixed: the legacy jog planner uses 33.8 counts per
        # degree for pitch where the vendor formula gives 27.9 per motor.
        from openScorbot.motion_profile import COUNTS_PER_DEGREE
        legacy = COUNTS_PER_DEGREE["wrist_pitch"]
        self.assertAlmostEqual(legacy, 33.8, delta=0.05)
        self.assertGreater(legacy / vm.ER4U.counts_per_degree(3), 1.2)


if __name__ == "__main__":
    unittest.main()
