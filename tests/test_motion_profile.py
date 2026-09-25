"""Offline checks for exact-count motion plans; no USB or arm required."""

import unittest

from openScorbot.motion_profile import (
    counts_for_degrees, increments_for_counts, plan_jog,
)


class MotionProfileTests(unittest.TestCase):
    def test_exact_target_and_bounded_monotonic_setpoints(self):
        for speed in (1, 2, 10, 20):
            for counts in (1, 2, 5, 28, 34, 112, 142, 709):
                with self.subTest(speed=speed, counts=counts):
                    steps = increments_for_counts(counts, speed)
                    self.assertEqual(sum(steps), counts)
                    self.assertTrue(all(1 <= step <= speed for step in steps))
                    position = 0
                    for step in steps:
                        previous = position
                        position += step
                        self.assertGreater(position, previous)
                        self.assertLessEqual(position, counts)

    def test_short_wrist_jog_has_exact_count_target(self):
        roll = plan_jog(12, 1, 10)
        self.assertEqual(roll["counts_per_motor"], 28)
        self.assertEqual(sum(roll["increments"]), 28)
        self.assertEqual(roll["motor_count_deltas"],
                         {"wrist_motor_1": 28, "wrist_motor_2": 28})
        pitch = plan_jog(10, 1, 10)
        self.assertEqual(pitch["counts_per_motor"], 34)
        self.assertEqual(pitch["motor_count_deltas"],
                         {"wrist_motor_1": -34, "wrist_motor_2": 34})

    def test_joint_direction_codes_and_small_requests(self):
        self.assertEqual(plan_jog(4, 1, 10)["motor_count_deltas"], {"base": -142})
        self.assertEqual(plan_jog(5, 1, 10)["motor_count_deltas"], {"base": 142})
        self.assertEqual(counts_for_degrees("shoulder", 1), 115)
        with self.assertRaisesRegex(ValueError, "smaller than one"):
            plan_jog(12, 0.001, 10)
        with self.assertRaises(ValueError):
            increments_for_counts(28, 0)


if __name__ == "__main__":
    unittest.main()
