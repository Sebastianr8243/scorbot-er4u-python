"""The hardware switch: real Scorbot.connect() refuses unless SCORBOT_HARDWARE=1. No USB, no arm."""

import os
import sys
import unittest
from unittest import mock

from scorbot import Scorbot, ScorbotError
from scorbot.robot import HARDWARE_ENV


class HardwareSwitchTests(unittest.TestCase):
    def connect_with(self, env):
        robot = Scorbot()
        legacy = mock.Mock()
        no_usb = {"usb": None, "usb.core": None, "usb.util": None}     # importing usb now raises ImportError
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.dict(sys.modules, no_usb), \
                mock.patch.object(robot, "_legacy", legacy):
            with self.assertRaises(ScorbotError) as caught:
                robot.connect()
        return str(caught.exception), legacy

    def test_switch_off_refuses_before_any_usb_import_or_legacy_setup(self):
        for env in ({}, {HARDWARE_ENV: "0"}, {HARDWARE_ENV: "yes"}, {HARDWARE_ENV: ""}):
            with self.subTest(env=env):
                message, legacy = self.connect_with(env)
                self.assertIn(HARDWARE_ENV, message)
                legacy.assert_not_called()

    def test_switch_on_gets_past_the_gate(self):
        message, _ = self.connect_with({HARDWARE_ENV: "1"})
        self.assertNotIn(HARDWARE_ENV, message)
        self.assertIn("Install the package dependencies", message)     # reached the usb import

    @unittest.skipUnless("pytest" in sys.modules, "conftest.py clears the switch only under pytest")
    def test_the_test_suite_runs_with_the_switch_off(self):
        self.assertNotEqual(os.environ.get(HARDWARE_ENV), "1")


if __name__ == "__main__":
    unittest.main()
