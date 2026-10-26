"""scorbot/policy_arm.py: PolicyArm on the simulated controller (no USB, no arm)."""

from importlib.util import find_spec
from pathlib import Path
import tempfile
import unittest

from scorbot import SimulatedScorbot
from scorbot.policy_arm import STEP_MAX_DEG, PolicyArm


@unittest.skipUnless(find_spec("ruckig"), "needs the planning extra")
class PolicyArmTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        robot = SimulatedScorbot(log_path=Path(tmp.name) / "e.jsonl", robot_id="t")
        self.arm = PolicyArm(robot, travel_cap_deg=2.0)
        self.arm.connect()
        self.addCleanup(self.arm.close)

    def test_starts_at_home(self):
        self.assertTrue(self.arm.is_connected)
        self.assertEqual(self.arm.observe(), {"base": 0.0, "shoulder": 0.0, "elbow": 0.0})

    def test_a_far_goal_moves_one_clamped_step(self):
        self.assertEqual(self.arm.act({"base": 90.0})["base"], STEP_MAX_DEG)

    def test_goal_is_held_inside_the_travel_cap(self):
        for _ in range(10):
            target = self.arm.act({"base": 90.0})
        self.assertLessEqual(target["base"], 2.0)

    def test_bad_values_and_unknown_motors_hold(self):
        target = self.arm.act({"base": float("nan"), "shoulder": "x", "wrist": 1.0, "elbow": True})
        self.assertEqual(target, {"base": 0.0, "shoulder": 0.0, "elbow": 0.0})

    def test_close_is_safe_twice_and_act_after_close_fails(self):
        self.arm.close()
        self.arm.close()
        self.assertFalse(self.arm.is_connected)
        with self.assertRaises(ConnectionError):
            self.arm.act({"base": 1.0})


if __name__ == "__main__":
    unittest.main()
