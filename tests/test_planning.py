"""Offline trajectory planner (scorbot.planning). Needs the optional ruckig package."""

import importlib.util
import json
import unittest

from scorbot import planning

HAS_RUCKIG = importlib.util.find_spec("ruckig") is not None

LIMITS = {
    "base": planning.AxisLimits(max_velocity=2800.0, max_acceleration=6000.0, max_jerk=40000.0),
    "shoulder": planning.AxisLimits(max_velocity=2900.0, max_acceleration=6000.0, max_jerk=40000.0),
}


class LimitsTests(unittest.TestCase):
    def test_datasheet_velocity_priors_in_counts(self):
        velocity = planning.datasheet_velocity_counts_per_s(["base", "shoulder", "elbow"])
        self.assertAlmostEqual(velocity["base"], 20.0 * 12770 / 90)
        self.assertAlmostEqual(velocity["shoulder"], 26.3 * 10216 / 90)
        with self.assertRaises(ValueError):
            planning.datasheet_velocity_counts_per_s(["wrist_motor_1"])

    def test_limits_must_be_positive_and_finite(self):
        for bad in (0.0, -1.0, float("inf"), float("nan"), True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                planning.AxisLimits(max_velocity=bad, max_acceleration=1.0, max_jerk=1.0)


@unittest.skipUnless(HAS_RUCKIG, "ruckig not installed (pip install .[planning])")
class PlanTests(unittest.TestCase):
    def plan(self, start=None, goal=None, **kwargs):
        return planning.plan_point_to_point(start or {"base": 0, "shoulder": 100},
                                            goal or {"base": 1500, "shoulder": -400},
                                            LIMITS, **kwargs)

    def test_starts_and_ends_exactly_at_the_requested_counts(self):
        trajectory = self.plan()
        self.assertEqual(trajectory.motors, ("base", "shoulder"))
        self.assertEqual(trajectory.targets[0], (0, 100))
        self.assertEqual(trajectory.targets[-1], (1500, -400))
        self.assertTrue(all(isinstance(v, int) for row in trajectory.targets for v in row))

    def test_samples_respect_the_period_and_velocity_limits(self):
        trajectory = self.plan(period_s=0.016)
        self.assertEqual(trajectory.period_s, 0.016)
        self.assertGreater(len(trajectory.targets), 2)
        for before, after in zip(trajectory.targets, trajectory.targets[1:]):
            for index, motor in enumerate(trajectory.motors):
                speed = abs(after[index] - before[index]) / trajectory.period_s
                # One count of rounding at each end of the step.
                self.assertLessEqual(speed, LIMITS[motor].max_velocity + 2 / trajectory.period_s)

    def test_all_axes_arrive_together(self):
        trajectory = self.plan()
        last_move = [max(i for i, row in enumerate(trajectory.targets)
                         if row[axis] != trajectory.targets[-1][axis])
                     for axis in range(len(trajectory.motors))]
        self.assertLessEqual(max(last_move) - min(last_move), 1)

    def test_no_motion_gives_a_single_sample(self):
        trajectory = self.plan(start={"base": 5, "shoulder": 5}, goal={"base": 5, "shoulder": 5})
        self.assertEqual(trajectory.targets, ((5, 5),))
        self.assertEqual(trajectory.duration_s, 0.0)

    def test_output_is_labelled_and_strict_json(self):
        trajectory = self.plan()
        self.assertIn("not sent", trajectory.status)
        payload = trajectory.as_dict()
        self.assertEqual(json.loads(json.dumps(payload, allow_nan=False)), payload)

    def test_rejects_mismatched_or_invalid_inputs(self):
        with self.assertRaises(ValueError):
            self.plan(goal={"base": 1})
        with self.assertRaises(ValueError):
            self.plan(start={"base": 0, "elbow": 0}, goal={"base": 1, "elbow": 1})
        with self.assertRaises(ValueError):
            self.plan(start={"base": 0.5, "shoulder": 0})
        with self.assertRaises(ValueError):
            self.plan(period_s=0)


if __name__ == "__main__":
    unittest.main()
