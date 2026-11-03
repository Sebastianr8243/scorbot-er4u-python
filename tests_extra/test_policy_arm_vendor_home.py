"""PolicyArm.connect homes with the vendor Home, and only after the three confirmations (simulator, no arm)."""

from importlib.util import find_spec
from pathlib import Path
import tempfile
import unittest

from scorbot import SimulatedScorbot
from scorbot.policy_arm import PolicyArm
from scorbot.vendor_home_source import VENDOR_HOME_ORDER

CONFIRMED = dict(operator_at_stop=True, jaws_empty=True, wrist_clear=True)
LEGACY_HOME = [18, 1, 1]


@unittest.skipUnless(find_spec("ruckig"), "needs the planning extra")
class PolicyArmVendorHomeTests(unittest.TestCase):
    def robot(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return SimulatedScorbot(log_path=Path(tmp.name) / "e.jsonl", robot_id="t")

    def test_connect_sends_the_vendor_home_not_the_legacy_home(self):
        robot = self.robot()
        arm = PolicyArm(robot)
        arm.connect(**CONFIRMED)
        self.addCleanup(arm.close)
        orders = [c[0] for c in robot.sim.commands]
        self.assertIn(VENDOR_HOME_ORDER, orders)
        self.assertNotIn(LEGACY_HOME, robot.sim.commands)
        self.assertTrue(robot._homed)
        self.assertTrue(robot._gripper_homed)     # the vendor Home zeroes the gripper; the legacy one cleared it

    def test_connect_sends_the_same_home_as_vendor_home(self):
        direct = self.robot().connect()
        self.addCleanup(direct.disconnect)
        direct.enable()
        direct.vendor_home(**CONFIRMED)
        robot = self.robot()
        arm = PolicyArm(robot)
        arm.connect(**CONFIRMED)
        self.addCleanup(arm.close)
        home = [c for c in robot.sim.commands if c[0] == VENDOR_HOME_ORDER]
        self.assertEqual(len(home), 1)
        self.assertEqual(direct.sim.commands[-1][0], VENDOR_HOME_ORDER)
        self.assertEqual([c[0] for c in robot.sim.commands[:len(direct.sim.commands)]],
                         [c[0] for c in direct.sim.commands])

    def test_connect_without_all_three_confirmations_sends_nothing(self):
        cases = [{}] + [{k: v for k, v in CONFIRMED.items() if k != left_out} for left_out in CONFIRMED]
        for given in cases:
            with self.subTest(given=sorted(given)):
                robot = self.robot()
                arm = PolicyArm(robot)
                with self.assertRaises(ValueError):
                    arm.connect(**given)
                self.assertEqual(robot.sim.commands, [])
                self.assertFalse(arm.is_connected)
                self.assertFalse(robot._homed)


if __name__ == "__main__":
    unittest.main()
