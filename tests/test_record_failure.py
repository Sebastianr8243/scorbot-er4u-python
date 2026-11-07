"""A failed log write after a motion latches a fault instead of escaping as a raw OSError.

The post-motion rows (the packet trace, a command's completion) were written with no
isolation, so a full disk or a locked log file skipped the settle checks and left the
session unlatched. ``_end_stream`` already did this right; every other motion now does too.
"""

import math
import time

from scorbot import ScorbotError
from scorbot import vendor_move
from scorbot.packet import PacketTrace

from tests.sim_support import SimulatedRobotCase


class _FailOn:
    """Wraps ``robot._record`` and raises OSError for one event name."""

    def __init__(self, robot, event):
        self.original = robot._record
        self.event = event
        robot._record = self

    def __call__(self, event, **fields):
        if event == self.event:
            raise OSError(28, "No space left on device")
        return self.original(event, **fields)


class RecordFailureLatches(SimulatedRobotCase):
    def traced_robot(self):
        robot = self.robot()
        robot._trace = PacketTrace()
        return robot

    def assert_latched(self, robot, call):
        with self.assertRaises(ScorbotError) as raised:
            call()
        self.assertNotIsInstance(raised.exception, OSError)
        self.assertIsNotNone(robot._fault)
        self.assertIn("could not be written", robot._fault)

    def test_a_jog_trace_write_failure_latches(self):
        robot = self.traced_robot()
        _FailOn(robot, "motion_trace")
        self.assert_latched(robot, lambda: robot.jog_joint("base", 1.0))

    def test_a_gripper_trace_write_failure_latches(self):
        robot = self.traced_robot()
        _FailOn(robot, "motion_trace")
        self.assert_latched(robot, robot.close_gripper)

    def test_a_velocity_jog_trace_write_failure_latches(self):
        robot = self.traced_robot()
        _FailOn(robot, "motion_trace")

        def jog():
            handle = robot.start_velocity_jog("elbow", 10)
            time.sleep(0.2)
            handle.stop()
        self.assert_latched(robot, jog)

    def test_a_vendor_joint_trace_write_failure_latches(self):
        robot = self.traced_robot()
        _FailOn(robot, "motion_trace")
        target = list(vendor_move.home_joints())
        target[0] += math.radians(1)
        self.assert_latched(robot, lambda: robot.move_joint_vendor(target, total_ms=3000))

    def test_a_command_complete_write_failure_latches(self):
        robot = self.robot()
        _FailOn(robot, "command_complete")
        self.assert_latched(robot, lambda: robot.jog_joint("base", 1.0))

    def test_a_clean_motion_is_unchanged(self):
        robot = self.traced_robot()
        robot.jog_joint("base", 1.0)
        self.assertIsNone(robot._fault)
        self.assertIn("motion_trace", self.events())
