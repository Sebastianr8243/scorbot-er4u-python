"""scorbot.vendor_profile: the vendor's motion profile, read from USBC.dll.

GOLDEN holds outputs of the vendor's own evaluator run in Ghidra's emulator.
Ruckig, an independent jerk-limited trajectory generator, is the second
source for the claim that this is the standard jerk-limited profile.
"""

import importlib.util
import unittest

from scorbot.vendor_profile import (DEFAULT_ACCEL_FRACTION, DEFAULT_JERK_FRACTION,
                                    DEFAULT_TOTAL_TIME, VendorProfile, speed_factor)

HAS_RUCKIG = importlib.util.find_spec("ruckig") is not None

# time -> (position, velocity, acceleration) from the 2018 build's evaluator at
# 0x10012e48 after set-up 0x1001295d with (3.0 s, 0.3, 0.3).
GOLDEN = (
    (-0.30000000000000004, (-0.0125976316452507, 0.12597631645250698, -0.8398421096833798)),
    (0.0, (0.0, 0.0, 0.0)),
    (0.27, (0.009183673469387758, 0.10204081632653064, 0.7558578987150416)),
    (0.5238236200992481, (0.059432655913486176, 0.2938954044589933, 0.7558578987150416)),
    (0.6299999999999999, (0.09489795918367343, 0.37414965986394555, 0.7558578987150416)),
    (0.8999999999999999, (0.21428571428571427, 0.47619047619047616, 0.0)),
    (1.1785506220405761, (0.3469288676383696, 0.47619047619047616, 0.0)),
    (1.569447625966021, (0.5330702980790577, 0.47619047619047616, 0.0)),
    (2.1, (0.7857142857142858, 0.47619047619047616, -0.0)),
    (2.2637162648762024, (0.8616270216242539, 0.4386733078790979, -0.4583193776103757)),
    (2.37, (0.9051020408163266, 0.3741496598639455, -0.7558578987150416)),
    (2.73, (0.9908163265306122, 0.10204081632653064, -0.7558578987150417)),
    (3.0, (1.0, 0.0, 0.0)),
    (3.3000000000000003, (1.0, 0.0, 0.0)),
)
PROFILES = ((3.0, 0.3, 0.3), (1.0, 0.5, 0.5), (2.0, 0.3, 0.05), (0.75, 0.1, 0.2),
            (10.0, 0.45, 0.01))


class ProfileTests(unittest.TestCase):
    def test_matches_the_emulated_vendor_evaluator(self):
        profile = VendorProfile(3.0, 0.3, 0.3)
        for t, expected in GOLDEN:
            with self.subTest(t=t):
                sample = profile.sample(t)
                got = (sample.position, sample.velocity, sample.acceleration)
                for value, want in zip(got, expected):
                    self.assertAlmostEqual(value, want, delta=1e-12)

    def test_defaults_are_the_vendor_ini_values(self):
        profile = VendorProfile()
        self.assertEqual((profile.total_time, profile.accel_fraction, profile.jerk_fraction),
                         (DEFAULT_TOTAL_TIME, DEFAULT_ACCEL_FRACTION, DEFAULT_JERK_FRACTION))
        for got, want in zip(profile.times, (0.27, 0.63, 0.9, 2.1, 2.37, 2.73)):
            self.assertAlmostEqual(got, want, delta=1e-12)
        self.assertAlmostEqual(profile.peak_velocity, 1 / 2.1, delta=1e-12)

    def test_goes_from_zero_to_one_without_backing_up(self):
        for parameters in PROFILES:
            profile = VendorProfile(*parameters)
            with self.subTest(parameters=parameters):
                positions = [profile.sample(profile.total_time * i / 2000).position
                             for i in range(2001)]
                self.assertEqual(positions[0], 0.0)
                self.assertAlmostEqual(positions[-1], 1.0, delta=1e-12)
                self.assertTrue(all(b >= a for a, b in zip(positions, positions[1:])))

    def test_position_velocity_and_acceleration_join_at_every_boundary(self):
        for parameters in PROFILES:
            profile = VendorProfile(*parameters)
            for boundary in profile.times:
                with self.subTest(parameters=parameters, boundary=boundary):
                    step = profile.total_time * 1e-9
                    before, after = profile.sample(boundary - step), profile.sample(boundary + step)
                    self.assertAlmostEqual(before.position, after.position, delta=1e-7)
                    self.assertAlmostEqual(before.velocity, after.velocity,
                                           delta=1e-6 * profile.peak_velocity)
                    self.assertAlmostEqual(before.acceleration, after.acceleration,
                                           delta=1e-5 * profile.peak_acceleration)

    def test_velocity_is_the_slope_of_position(self):
        profile = VendorProfile()
        step = 1e-6
        for i in range(1, 300):
            t = profile.total_time * i / 300
            slope = (profile.sample(t + step).position - profile.sample(t - step).position) / (2 * step)
            self.assertAlmostEqual(slope, profile.sample(t).velocity, delta=1e-6)

    def test_slowing_down_mirrors_speeding_up(self):
        profile = VendorProfile(2.0, 0.3, 0.05)
        for i in range(200):
            t = profile.total_time * i / 200
            self.assertAlmostEqual(profile.sample(t).velocity,
                                   profile.sample(profile.total_time - t).velocity, delta=1e-12)

    def test_limits_are_never_exceeded(self):
        for parameters in PROFILES:
            profile = VendorProfile(*parameters)
            for i in range(2001):
                sample = profile.sample(profile.total_time * i / 2000)
                self.assertLessEqual(sample.velocity, profile.peak_velocity * (1 + 1e-12))
                self.assertLessEqual(abs(sample.acceleration),
                                     profile.peak_acceleration * (1 + 1e-12))

    def test_bad_parameters_are_rejected(self):
        for parameters in ((0.0005, 0.3, 0.3), (3.0, 0.0, 0.3), (3.0, 0.3, 0.0),
                           (3.0, 0.51, 0.3), (3.0, 0.3, 0.51), (float("nan"), 0.3, 0.3)):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                VendorProfile(*parameters)

    def test_setpoints_scale_the_profile_and_end_on_the_distance(self):
        # 0.016 s does not divide 3 s (187.5 ticks); 0.017 s leaves the last
        # regular tick at 2.992 s; 0.5 s divides it exactly. All must end on
        # the full distance, with every tick before it short of it.
        for period, count in ((0.016, 189), (0.017, 178), (0.5, 7)):
            with self.subTest(period=period):
                points = VendorProfile().setpoints(142.0, period)
                self.assertEqual(points[0], 0.0)
                self.assertEqual(points[-1], 142.0)
                self.assertEqual(len(points), count)
                self.assertTrue(all(point < 142.0 for point in points[:-1]))
                self.assertTrue(all(b >= a for a, b in zip(points, points[1:])))
        with self.assertRaises(ValueError):
            VendorProfile().setpoints(142.0, 0)

    def test_speed_factor_maps_percent_onto_0_307_to_1(self):
        self.assertAlmostEqual(speed_factor(1), 0.307, delta=1e-12)
        self.assertAlmostEqual(speed_factor(50), 0.65, delta=1e-12)
        self.assertAlmostEqual(speed_factor(100), 1.0, delta=1e-12)
        for bad in (0, 101, -5, 1.5, True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                speed_factor(bad)


@unittest.skipUnless(HAS_RUCKIG, "ruckig not installed (pip install .[planning])")
class RuckigAgreementTests(unittest.TestCase):
    """Given peak velocity, acceleration and jerk computed from our own model, a
    time-optimal jerk-limited planner should produce the same normalised move.

    This shows the curve is the standard jerk-limited shape. It is not evidence
    about the DLL beyond that: the limits come from the model under test."""

    def test_ruckig_reproduces_the_vendor_profile_from_its_limits(self):
        from ruckig import InputParameter, Result, Ruckig, Trajectory
        for parameters in ((3.0, 0.3, 0.3), (2.0, 0.3, 0.05), (1.0, 0.5, 0.5)):
            profile = VendorProfile(*parameters)
            request = InputParameter(1)
            request.current_position, request.target_position = [0.0], [1.0]
            request.max_velocity = [profile.peak_velocity]
            request.max_acceleration = [profile.peak_acceleration]
            request.max_jerk = [profile.jerk]
            trajectory = Trajectory(1)
            self.assertIn(Ruckig(1).calculate(request, trajectory),
                          (Result.Working, Result.Finished))
            with self.subTest(parameters=parameters):
                self.assertAlmostEqual(trajectory.duration, profile.total_time, delta=1e-6)
                for i in range(101):
                    t = profile.total_time * i / 100
                    position, velocity, _ = trajectory.at_time(t)
                    self.assertAlmostEqual(position[0], profile.sample(t).position, delta=1e-6)
                    self.assertAlmostEqual(velocity[0], profile.sample(t).velocity, delta=1e-6)


if __name__ == "__main__":
    unittest.main()
