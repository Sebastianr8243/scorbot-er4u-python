"""The no-lerobot part of the teleoperators in plugins/lerobot_robot_scorbot_stack1: the script parser and the map from
LeRobot's keyboard_ee action to Stack 1 actions. The teleoperator classes themselves need lerobot and are run by
examples/lerobot_record_check.py. No arm, no keyboard, no USB."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "plugins" / "lerobot_robot_scorbot_stack1"))
sys.path.insert(0, str(ROOT))

from lerobot_robot_scorbot_stack1.teleop_map import (  # noqa: E402
    DEFAULT_SCRIPT, check_step, keyboard_to_action, parse_script)

KEYS = ("dx.mm", "dy.mm", "dz.mm", "gripper.pos")


def raw(dx=0, dy=0, dz=0, gripper=1):
    return {"delta_x": dx, "delta_y": dy, "delta_z": dz, "gripper": gripper}


class ParseScriptTests(unittest.TestCase):
    def test_the_default_script_has_four_steps_with_the_robot_keys(self):
        steps = parse_script(DEFAULT_SCRIPT)
        self.assertEqual(len(steps), 4)
        for step in steps:
            self.assertEqual(tuple(step), KEYS)
            self.assertTrue(all(isinstance(value, float) for value in step.values()))

    def test_values_and_spaces(self):
        self.assertEqual(parse_script(" 10, 0 ,-5.5, 1 ; 0,0,0,0 "),
                         [dict(zip(KEYS, (10.0, 0.0, -5.5, 1.0))), dict(zip(KEYS, (0.0, 0.0, 0.0, 0.0)))])

    def test_bad_scripts_are_refused(self):
        for text in ("", "   ", ";", "1,2,3", "1,2,3,4,5", "a,0,0,0", "nan,0,0,0", "inf,0,0,0", "51,0,0,0", "0,0,-50.1,0",
                     "10,0,0,0;;10,0,0,0"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_script(text)

    def test_the_cap_itself_is_allowed(self):
        parse_script("50,-50,50,0")


class KeyboardMapTests(unittest.TestCase):
    def test_deltas_are_scaled_to_mm(self):
        action, _ = keyboard_to_action(raw(dx=1, dy=-1, dz=1), 10.0, False)
        self.assertEqual(action, {"dx.mm": 10.0, "dy.mm": -10.0, "dz.mm": 10.0, "gripper.pos": 0.0})
        self.assertEqual(tuple(action), KEYS)

    def test_no_key_means_no_move(self):
        action, _ = keyboard_to_action(raw(), 10.0, False)
        self.assertEqual([action[key] for key in KEYS[:3]], [0.0, 0.0, 0.0])

    def test_gripper_stay_keeps_the_last_state(self):
        self.assertEqual(keyboard_to_action(raw(gripper=1), 10.0, False), (dict(zip(KEYS, (0.0, 0.0, 0.0, 0.0))), False))
        action, now_open = keyboard_to_action(raw(gripper=1), 10.0, True)
        self.assertEqual((action["gripper.pos"], now_open), (1.0, True))

    def test_gripper_open_and_close(self):
        """keyboard_ee sends 0 = close (left ctrl), 1 = stay, 2 = open (right ctrl)."""
        action, now_open = keyboard_to_action(raw(gripper=2), 10.0, False)
        self.assertEqual((action["gripper.pos"], now_open), (1.0, True))
        action, now_open = keyboard_to_action(raw(gripper=0), 10.0, True)
        self.assertEqual((action["gripper.pos"], now_open), (0.0, False))

    def test_no_gripper_key_in_the_raw_action_means_stay(self):
        without = {"delta_x": 1, "delta_y": 0, "delta_z": 0}
        self.assertEqual(keyboard_to_action(without, 10.0, True)[1], True)
        self.assertEqual(keyboard_to_action(without, 10.0, False)[1], False)

    def test_step_size_is_limited_to_the_robot_cap(self):
        for value in (0, -1, 50.1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                check_step(value)
        check_step(0.5)
        check_step(50)


if __name__ == "__main__":
    unittest.main()
