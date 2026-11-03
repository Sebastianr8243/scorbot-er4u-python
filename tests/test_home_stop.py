"""A stop request ends the legacy Home's switch search (order 18), through the simulator and the legacy helper.

Like a jog: ``MotionStopped``, no latched fault, the session not homed, and the next Home runs. A stop asked
for before the Home starts refuses it once. Not an emergency stop; unverified on the arm.
"""

import queue
import threading
import time
import unittest
from importlib.util import find_spec
from pathlib import Path
from unittest import mock

from scorbot import MotionStopped, Scorbot
from scorbot.simulated import SimulatorProfile

try:
    from tests.sim_support import SimulatedRobotCase
except ImportError:      # discovered with -s tests
    from sim_support import SimulatedRobotCase

STOPPED = 14


class StopOnStep(list):
    """The simulator's homing trace; asks for a stop as the search reaches step ``step``."""

    def __init__(self, robot, step):
        super().__init__()
        self.robot, self.step = robot, step

    def append(self, item):
        super().append(item)
        if len(self) == self.step:
            self.robot.request_stop()


class SdkHomeStopTests(SimulatedRobotCase):
    HOMED = False

    def homing_robot(self):
        return self.robot(profile=SimulatorProfile(model_homing=True, homing_duration_s=0.0))

    def test_a_stop_mid_search_ends_the_home_without_a_fault(self):
        robot = self.homing_robot()
        robot.sim.homing_trace = StopOnStep(robot, 5)
        with self.assertRaises(MotionStopped):
            robot.home(start_position_confirmed=True)
        self.assertLess(len(robot.sim.homing_trace), 20, "the search ended early")
        self.assertIsNone(robot._fault)
        self.assertFalse(robot._homed)
        self.assertIn("home_stopped", self.events())
        robot.sim.homing_trace = []
        robot.home(start_position_confirmed=True)
        self.assertTrue(robot._homed)

    def test_a_stop_asked_before_the_home_refuses_it_once(self):
        robot = self.homing_robot()
        robot.request_stop()
        with self.assertRaises(MotionStopped):
            robot.home(start_position_confirmed=True)
        self.assertEqual(robot.sim.homing_trace, [], "nothing was sent")
        robot.home(start_position_confirmed=True)
        self.assertTrue(robot._homed)

    def test_a_stop_after_the_search_ended_is_cleared_not_kept(self):
        robot = self.homing_robot()
        search = robot.sim._model_homing

        def then_stop():
            code = search()
            robot.request_stop()     # lands after the last switch: the Home has finished
            return code

        robot.sim._model_homing = then_stop
        robot.home(start_position_confirmed=True)
        self.assertTrue(robot._homed)
        self.assertIn("stop_too_late", self.events())
        robot.jog_joint("base", 1.0)     # not refused by a stale stop


@unittest.skipUnless(find_spec("usb"), "PyUSB is needed to import the legacy modules")
class LegacyHomeStopTests(unittest.TestCase):

    def test_the_search_ends_on_a_stop_with_the_stopped_result(self):
        homing = Scorbot()._legacy("setHome")
        result, stop = queue.Queue(), threading.Event()
        self.assertFalse(homing.search_must_stop(time.monotonic() + 5, None, result, stop))
        stop.set()
        self.assertTrue(homing.search_must_stop(time.monotonic() + 5, None, result, stop))
        self.assertEqual(result.get_nowait(), STOPPED)

    def test_every_switch_search_and_execute_pass_the_stop_event(self):
        legacy = Path(__file__).resolve().parents[1] / "openScorbot"
        home = (legacy / "setHome.py").read_text(encoding="utf-8")
        self.assertEqual(home.count("search_must_stop(deadline, cancel_event, cola_orden)"), 0)
        self.assertEqual(home.count("search_must_stop(deadline, cancel_event, cola_orden, stop_event, stopped)"), 5)
        comm = (legacy / "libcomm.py").read_text(encoding="utf-8")
        self.assertIn("cola_read, cola_result, cancel_event, stop_event)", comm)

    def test_a_stopped_home_leaves_the_motors_on_and_reports_only_stopped(self):
        robot = Scorbot()
        comm, home = robot._legacy("libcomm"), robot._legacy("setHome")
        sync, orders, reads, results = (queue.Queue() for _ in range(4))
        sync.put(1)
        reads.put([0] * 6)

        def stopped_search(b_1, *args):
            args[4].put(STOPPED)     # cola_orden, as search_must_stop does
            return [b_1, STOPPED]

        with mock.patch.object(home, "homing", side_effect=stopped_search),                 mock.patch.object(comm, "motors_off") as motors_off:
            worker = threading.Thread(target=comm.execute, args=(sync, orders, reads, None, None, bytearray(64),
                                      results, threading.Event(), threading.Event()), daemon=True)
            worker.start()
            orders.put([18, 1, 1])
            self.assertEqual(results.get(timeout=10), STOPPED)
            self.assertEqual(results.get(timeout=10), 0)
            motors_off.assert_not_called()     # the exit below sends its own motors-off
            orders.put([528, 1, 1])
            worker.join(timeout=30)


if __name__ == "__main__":
    unittest.main()
