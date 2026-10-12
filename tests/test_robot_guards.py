"""Refusals in Scorbot that must happen before anything is queued or sent."""

import unittest
from unittest import mock

from scorbot import Scorbot, ScorbotError

try:
    from tests.sim_support import SimulatedRobotCase
except ImportError:
    from sim_support import SimulatedRobotCase


class UnconnectedTests(unittest.TestCase):
    def setUp(self):
        self.robot = Scorbot(command_timeout=0.2)

    def assertNothingQueued(self):
        self.assertTrue(self.robot._commands.empty())

    def test_a_command_before_connect_is_refused(self):
        with self.assertRaisesRegex(ScorbotError, "Not connected"):
            self.robot._command([16, 1, 1])
        self.assertNothingQueued()

    def test_a_stream_before_connect_is_refused(self):
        with self.assertRaisesRegex(ScorbotError, "Not connected"):
            self.robot.start_stream()
        self.assertNothingQueued()

    def test_calibration_needs_a_robot_id(self):
        with self.assertRaisesRegex(ValueError, "robot_id is required"):
            Scorbot(calibration_path="calibration.json")

    def test_preview_jog_rejects_bad_input_without_opening_anything(self):
        for args, message in (
                (("nowhere", 1), "Unknown joint"),
                (("base", 0), "nonzero"),
                (("base", float("nan")), "finite"),
                (("base", 99), "jog ceiling"),
                (("base", True), "finite")):
            with self.subTest(args=args):
                with self.assertRaisesRegex(ValueError, message):
                    self.robot.preview_jog(*args)
        with self.assertRaisesRegex(ValueError, "mapping"):
            self.robot.preview_jog("base", 1, starting_signed_counts=[1, 2])
        with self.assertRaisesRegex(ValueError, "Missing or invalid signed count"):
            self.robot.preview_jog("base", 1, starting_signed_counts={})
        self.assertNothingQueued()

    def test_calibrated_angles_need_a_loaded_calibration(self):
        with self.assertRaisesRegex(ScorbotError, "No validated physical calibration"):
            self.robot.get_joint_angles()
        with self.assertRaisesRegex(ScorbotError, "No validated physical calibration"):
            self.robot.move_joint("base", 1)
        self.assertNothingQueued()


class ConnectedArgumentTests(SimulatedRobotCase):
    def assertQuiet(self, robot, before):
        self.assertEqual(len(robot.sim.commands), before, "a refused call must send nothing")

    def test_park_at_home_checks_its_arguments_before_sending(self):
        robot = self.robot()
        before = len(robot.sim.commands)
        with self.assertRaisesRegex(ValueError, "operator is at the physical stop"):
            robot.park_at_home()
        with self.assertRaisesRegex(ValueError, "speed must be an integer"):
            robot.park_at_home(operator_at_stop=True, speed=0)
        with self.assertRaisesRegex(ValueError, "speed must be an integer"):
            robot.park_at_home(operator_at_stop=True, speed=True)
        self.assertQuiet(robot, before)

    def test_park_at_home_needs_a_home(self):
        self.HOMED = False
        robot = self.robot()
        with self.assertRaisesRegex(ScorbotError, "Home first"):
            robot.park_at_home(operator_at_stop=True)

    def test_a_jog_with_a_bad_speed_is_refused(self):
        robot = self.robot()
        before = len(robot.sim.commands)
        for speed in (0, 21, True, 1.5):
            with self.subTest(speed=speed):
                with self.assertRaisesRegex(ValueError, "speed must be an integer"):
                    robot.jog_joint("base", 1, speed=speed)
        self.assertQuiet(robot, before)

    def test_the_private_jog_checks_the_speed_itself(self):
        # pre_home_jog and home_inch reach _jog_joint without going through jog_joint
        robot = self.robot()
        before = len(robot.sim.commands)
        for speed in (0, 21, True):
            with self.subTest(speed=speed):
                # the planner downstream words its refusal the same way, so also prove the check came first:
                # nothing may read the arm's state before the speed is judged
                with mock.patch.object(robot, "_motion_state", side_effect=AssertionError("state read first")):
                    with self.assertRaisesRegex(ValueError, "speed must be an integer"):
                        robot._jog_joint("base", 1, speed)
        self.assertQuiet(robot, before)

    def test_a_jog_before_home_is_refused(self):
        self.HOMED = False
        robot = self.robot()
        before = len(robot.sim.commands)
        with self.assertRaisesRegex(ScorbotError, "Enable and home"):
            robot.jog_joint("base", 1)
        self.assertQuiet(robot, before)

    def test_a_second_connect_is_refused(self):
        robot = self.robot()
        with self.assertRaisesRegex(ScorbotError, "Already connected"):
            robot.connect()


if __name__ == "__main__":
    unittest.main()
