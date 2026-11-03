"""PolicyArm.connect homes with the vendor Home, and only after the three confirmations (simulator, no arm)."""

from importlib.util import find_spec
import json
from pathlib import Path
import tempfile
import unittest

from scorbot import SimulatedScorbot, vendor_home as vh
from scorbot.policy_arm import PolicyArm
from scorbot.vendor_home_source import VENDOR_HOME_ORDER

CONFIRMED = dict(operator_at_stop=True, jaws_empty=True, wrist_clear=True)
LEGACY_HOME = [18, 1, 1]
# Every command Intelitek's USBC.dll sent in its Home against the fake controller (tests/test_vendor_home.py).
FIXTURE = json.loads((Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "vendor_home_trace05.json")
                     .read_text(encoding="utf-8"))


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

    def test_connect_sends_the_same_home_packets_as_vendor_home(self):
        direct = self.robot().connect()
        self.addCleanup(direct.disconnect)
        direct.enable()
        direct.vendor_home(**CONFIRMED)
        robot = self.robot()
        arm = PolicyArm(robot)
        arm.connect(**CONFIRMED)
        self.addCleanup(arm.close)
        self.assertEqual(len([c for c in robot.sim.commands if c[0] == VENDOR_HOME_ORDER]), 1)
        self.assertEqual([c[0] for c in robot.sim.commands[:len(direct.sim.commands)]],
                         [c[0] for c in direct.sim.commands])
        sent = [bytes(p) for p in robot.sim.vendor_packets]    # what reached the simulated controller
        self.assertGreater(len(sent), 100)
        self.assertEqual(sent, [bytes(p) for p in direct.sim.vendor_packets])

    def test_the_home_packets_are_the_vendor_dlls_recorded_sequence(self):
        robot = self.robot()
        arm = PolicyArm(robot)
        arm.connect(**CONFIRMED)
        self.addCleanup(arm.close)
        # Same reduction as tests/test_vendor_home_robot.py: order, motor, word, value; NOPs dropped, repeats collapsed.
        items = [[p[4], p[5], int.from_bytes(p[6:8], "little"), int.from_bytes(p[8:12], "little", signed=True)]
                 for p in robot.sim.vendor_packets if p[4] != vh.NOP]
        collapsed = [item for i, item in enumerate(items) if i == 0 or item != items[i - 1]]
        self.assertEqual(collapsed, [list(s) for s in FIXTURE["steps"]])

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
