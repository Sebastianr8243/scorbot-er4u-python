"""``joint_move.proportional_jogs``: the pure step planner. No USB."""

import unittest

from scorbot import joint_move


class ProportionalJogsTests(unittest.TestCase):
    def test_a_long_move_is_cut_into_steps_that_keep_the_motors_in_proportion(self):
        errors = {"base": 0.0, "shoulder": 2400.0, "elbow": -2400.0, "wrist_pitch": -590.0}
        limits = {"base": 150, "shoulder": 120, "elbow": 120, "wrist_pitch": 80}
        jogs = dict(joint_move.proportional_jogs(errors, limits))
        # The shoulder is the limiting motor (120 of 2400); the others follow at the same pace.
        self.assertEqual(jogs["shoulder"], 120)
        self.assertEqual(jogs["elbow"], -120)
        self.assertAlmostEqual(jogs["wrist_pitch"] / jogs["shoulder"], -590 / 2400, delta=0.01)
        self.assertNotIn("base", jogs)

    def test_a_small_error_is_done_in_one_step_and_dust_is_skipped(self):
        errors = {"base": 0.0, "shoulder": 70.0, "elbow": -70.0, "wrist_pitch": 5.0}
        limits = {"base": 150, "shoulder": 120, "elbow": 120, "wrist_pitch": 80}
        jogs = dict(joint_move.proportional_jogs(errors, limits))
        self.assertEqual(jogs, {"shoulder": 70, "elbow": -70})

    def test_nothing_to_do_gives_nothing(self):
        errors = dict.fromkeys(("base", "shoulder", "elbow", "wrist_pitch"), 3.0)
        self.assertEqual(joint_move.proportional_jogs(errors, dict.fromkeys(errors, 100)), [])
