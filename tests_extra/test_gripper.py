"""The gripper: the legacy ``clamp`` loop against fake endpoints (exact messages),
then ``Scorbot.open_gripper`` / ``close_gripper`` through the simulator.

Never run on the arm. No USB, no hardware.
"""

from importlib.util import find_spec
import queue
import threading
import unittest
from unittest import mock

from scorbot import Scorbot
from scorbot.state import JOINTS

try:
    from tests.test_software_stop import FollowingController, START
except ImportError:      # discovered with -s tests: the folder itself is on sys.path
    from test_software_stop import FollowingController, START

try:
    from tests.sim_support import SimulatedRobotCase
except ImportError:      # discovered with -s tests: the folder itself is on sys.path
    from sim_support import SimulatedRobotCase

STEP, MODE, SLAVE, MOTOR, CONTROL_OFF = 0x0D, 0x4F, 0x4C, 0x42, 0x73
GRIPPER_MASK = 0x20
GRIPPER, GRIPPER_REGION = JOINTS.index("gripper"), 12 + 4 * JOINTS.index("gripper")
OPEN_ORDER, CLOSE_ORDER = 14, 15
# Far enough from zero that a full close does not cross the counter's sign seam,
# which the fake controller (unsigned two-byte targets) does not model.
GRIP_START = 20000
STOPPED = 14


@unittest.skipUnless(find_spec("usb"), "PyUSB is needed to import the legacy modules")
class LegacyGripperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        robot = Scorbot()
        cls.conf = robot._legacy("conf")
        cls.conf.setup()
        cls.comm = robot._legacy("libcomm")
        cls.lib = robot._legacy("libdef")

    def setUp(self):
        # Which messages are sent is the subject, not when (tests/CLAUDE.md).
        sleeping = mock.patch("time.sleep")
        sleeping.start()
        self.addCleanup(sleeping.stop)

    def travel(self):
        """Counts the legacy ramp asks the gripper to move, from its own settings."""
        speed, steps = (self.conf.readData("pinza", key) for key in ("vel", "ite_clamp"))
        return sum(self.lib.incremento(i + 1, speed, steps) for i in range(steps))

    def move(self, order, *, rate=None, stop_after=None):
        stop_event = threading.Event()
        controller = FollowingController(stop_event, stop_after, rate)
        controller.position[GRIPPER] = GRIP_START
        controller._report()
        buffer = bytearray(controller.reply)
        reads, results = queue.Queue(), queue.Queue()
        reads.put([START] * GRIPPER + [GRIP_START])
        self.comm.clamp(1, controller, controller, buffer, order, reads, results, stop_event)
        self.assertEqual(reads.qsize(), 1, "the smoothed position must go back on its queue")
        return controller, list(results.queue)

    def test_a_move_is_gripper_on_then_setpoints_then_gripper_off(self):
        controller, results = self.move(OPEN_ORDER)
        packets = controller.packets
        self.assertEqual(results, [], "a gripper that arrives reports no error")
        self.assertEqual([(p[4], p[5]) for p in packets[:3]],
                         [(MODE, GRIPPER_MASK), (SLAVE, GRIPPER_MASK), (MOTOR, GRIPPER_MASK)])
        self.assertEqual((packets[0][6], packets[2][6]), (0x53, 0x01))
        self.assertEqual([(p[4], p[5], p[6]) for p in packets[-2:]],
                         [(CONTROL_OFF, GRIPPER_MASK, 0), (MOTOR, GRIPPER_MASK, 0)])
        self.assertEqual({p[4] for p in packets[3:-2]}, {STEP})
        self.assertEqual({len(p) for p in packets}, {64})
        self.assertEqual([p[0] for p in packets], list(range(2, 2 + len(packets))),
                         "sequence bytes stay consecutive")

    def test_open_raises_the_gripper_setpoint_and_close_lowers_it_by_the_same_amount(self):
        for order, sign in ((OPEN_ORDER, 1), (CLOSE_ORDER, -1)):
            with self.subTest(order=order):
                controller, _ = self.move(order)
                steps = [p for p in controller.packets if p[4] == STEP]
                targets = [int.from_bytes(p[GRIPPER_REGION:GRIPPER_REGION + 2], "little")
                           for p in steps]
                moves = [b - a for a, b in zip([GRIP_START] + targets, targets)]
                self.assertTrue(all(move * sign >= 0 for move in moves), "one direction only")
                self.assertEqual(targets[-1], GRIP_START + sign * self.travel())
                self.assertEqual(controller.position[GRIPPER], GRIP_START + sign * self.travel())

    def test_no_arm_joint_is_ever_asked_to_move(self):
        controller, _ = self.move(CLOSE_ORDER)
        for packet in controller.packets:
            for joint in range(GRIPPER):
                region = 12 + 4 * joint
                self.assertEqual(int.from_bytes(packet[region:region + 2], "little"), START)
        self.assertEqual(controller.position[:GRIPPER], [START] * GRIPPER)

    def test_a_gripper_that_reaches_its_target_ends_normally(self):
        # The old loop waited only while the gripper WAS at its target, then
        # reported an error through a name that did not exist in the function.
        controller, results = self.move(OPEN_ORDER)
        self.assertEqual(results, [])
        self.assertLess(len(controller.packets), 3 + 30 + 15 + 12 + 2)

    def test_a_gripper_blocked_by_an_object_is_a_grasp_not_an_error(self):
        controller, results = self.move(CLOSE_ORDER, rate=0)       # it cannot move at all
        self.assertEqual(results, [])
        self.assertEqual(controller.position[GRIPPER], GRIP_START)
        self.assertEqual([(p[4], p[5]) for p in controller.packets[-2:]],
                         [(CONTROL_OFF, GRIPPER_MASK), (MOTOR, GRIPPER_MASK)])

    def test_a_gripper_still_moving_at_the_end_is_reported(self):
        controller, results = self.move(OPEN_ORDER, rate=3)        # far too slow to settle
        self.assertEqual(results, [2], "legacy code 2: the joint did not respond in time")
        self.assertEqual([(p[4], p[5]) for p in controller.packets[-2:]],
                         [(CONTROL_OFF, GRIPPER_MASK), (MOTOR, GRIPPER_MASK)])
        self.assertLessEqual(len(controller.packets), 3 + 30 + 15 + 101 + 2)

    def test_a_gripper_creeping_one_count_a_message_is_still_moving(self):
        # The smoothed reading halves small changes, so it must not be what
        # decides that the gripper has come to rest.
        controller, results = self.move(OPEN_ORDER, rate=1)
        self.assertEqual(results, [2])

    def test_a_stop_during_the_hold_ends_it_at_once(self):
        ramp_done = 3 + 30
        controller, results = self.move(CLOSE_ORDER, stop_after=ramp_done + 4)
        self.assertEqual(results, [STOPPED])
        self.assertLessEqual(len(controller.packets), ramp_done + 4 + 2,
                             "no further hold messages after the stop")
        self.assertEqual([(p[4], p[5]) for p in controller.packets[-2:]],
                         [(CONTROL_OFF, GRIPPER_MASK), (MOTOR, GRIPPER_MASK)])

    def test_a_usb_failure_mid_move_still_tries_to_switch_the_gripper_off(self):
        class Failing(FollowingController):
            fail_at = None

            def write(self, data, timeout):
                if self.fail_at is not None and len(self.packets) + 1 == self.fail_at:
                    self.fail_at = None            # the link comes back for the next write
                    raise OSError("USB write failed")
                return super().write(data, timeout)

        for fail_at, stage in ((10, "ramp"), (3 + 30 + 5, "hold"), (3 + 30 + 15 + 1, "settle")):
            with self.subTest(stage=stage):
                controller = Failing()
                controller.fail_at = fail_at
                controller.position[GRIPPER] = GRIP_START
                controller._report()
                buffer = bytearray(controller.reply)
                reads, results = queue.Queue(), queue.Queue()
                reads.put([START] * GRIPPER + [GRIP_START])
                with self.assertRaises(RuntimeError):
                    self.comm.clamp(1, controller, controller, buffer, CLOSE_ORDER, reads,
                                    results)
                self.assertEqual([(p[4], p[5], p[6]) for p in controller.packets[-2:]],
                                 [(CONTROL_OFF, GRIPPER_MASK, 0), (MOTOR, GRIPPER_MASK, 0)],
                                 "the gripper must not be left switched on")

    def test_a_dead_link_does_not_hide_the_first_error_behind_the_cleanup(self):
        class Dead(FollowingController):
            def write(self, data, timeout):
                if len(self.packets) >= 9:
                    raise OSError("USB gone")
                return super().write(data, timeout)

        controller = Dead()
        buffer = bytearray(controller.reply)
        reads = queue.Queue()
        reads.put([START] * len(JOINTS))
        with self.assertRaises(RuntimeError) as caught:
            self.comm.clamp(1, controller, controller, buffer, OPEN_ORDER, reads, queue.Queue())
        self.assertIn("USB write failed", str(caught.exception))

    def test_a_stop_request_ends_the_move_on_the_measured_position(self):
        controller, results = self.move(CLOSE_ORDER, stop_after=10)
        self.assertEqual(results, [STOPPED])
        packets = controller.packets
        self.assertLess(len(packets), 20)
        self.assertEqual([(p[4], p[5]) for p in packets[-2:]],
                         [(CONTROL_OFF, GRIPPER_MASK), (MOTOR, GRIPPER_MASK)])
        moved = GRIP_START - controller.position[GRIPPER]
        self.assertGreater(moved, 0)
        self.assertLess(moved, self.travel())
        # The two closing messages ask for the (smoothed) measured position, which
        # trails the gripper: never for anywhere beyond where it has got to.
        last_step = [p for p in packets if p[4] == STEP][-1]
        target = int.from_bytes(last_step[GRIPPER_REGION:GRIPPER_REGION + 2], "little")
        for packet in packets[-2:]:
            asked = int.from_bytes(packet[GRIPPER_REGION:GRIPPER_REGION + 2], "little")
            self.assertGreaterEqual(asked, target, "never further closed than the ramp got")
            self.assertLessEqual(asked, GRIP_START)

    def test_execute_runs_the_gripper_for_orders_14_and_15(self):
        controller = FollowingController()
        buffer = bytearray(controller.reply)
        sync, orders, reads, results = (queue.Queue() for _ in range(4))
        reads.put([START] * len(JOINTS))
        sync.put(1)
        self.addCleanup(lambda: orders.put([528, 1, 1]))
        orders.put([OPEN_ORDER, 1, 1])
        worker = threading.Thread(
            target=self.comm.execute,
            args=(sync, orders, reads, controller, controller, buffer, results), daemon=True)
        worker.start()
        self.assertEqual(results.get(timeout=10), 0)
        self.assertEqual(controller.position[GRIPPER], START + self.travel())


class GripperPlanTests(unittest.TestCase):
    @unittest.skipUnless(find_spec("usb"), "PyUSB is needed to import the legacy modules")
    def test_the_planned_travel_is_what_the_legacy_ramp_sends(self):
        # motion_profile.py is pure, so the SDK and the simulator can plan a
        # gripper move without importing the USB code. It must match that code.
        robot = Scorbot()
        profile, lib = robot._legacy("motion_profile"), robot._legacy("libdef")
        for speed, steps in ((150, 30), (150, 24), (40, 30), (1, 30), (150, 60)):
            with self.subTest(speed=speed, steps=steps):
                self.assertEqual(profile.gripper_increments(speed, steps),
                                 [lib.incremento(i + 1, speed, steps) for i in range(steps)])


class SdkGripperTests(SimulatedRobotCase):
    HOMED = False          # the gripper needs motors on, not a homed arm

    def robot(self, **controller_kwargs):
        robot = super().robot(**controller_kwargs)
        self.travel = sum(robot._legacy("motion_profile").gripper_increments(150, 30))
        return robot

    def assert_latched(self, robot):
        from scorbot import ScorbotError
        self.assertIsNotNone(robot._fault)
        self.assertIsNone(robot._enabled)
        queued = len(robot.sim.commands)
        for call in (robot.open_gripper, robot.close_gripper):
            with self.assertRaises(ScorbotError):
                call()
        self.assertEqual(len(robot.sim.commands), queued, "a latched session queues nothing")

    def test_open_then_close_moves_only_the_gripper(self):
        robot = self.robot()
        before = robot.get_state().signed_encoder_counts
        opened = robot.open_gripper()
        self.assertGreater(self.travel, 1000)
        self.assertEqual((opened.direction, opened.planned_counts, opened.moved_counts,
                          opened.full_travel), ("open", self.travel, self.travel, True))
        closed = robot.close_gripper()
        self.assertEqual((closed.direction, closed.moved_counts, closed.full_travel),
                         ("close", -self.travel, True))
        self.assertEqual(closed.state.signed_encoder_counts, before)
        self.assertEqual([c for c in robot.sim.commands if c[0] in (14, 15)],
                         [[14, 1, 1], [15, 1, 1]])
        events = self.events()
        self.assertEqual(events.count("gripper_start"), 2)
        self.assertEqual(events.count("gripper_complete"), 2)
        self.assertIsNone(robot._fault)
        self.assertTrue(robot.get_state().enabled)

    def test_it_does_not_need_homing_but_does_need_motors_on(self):
        from scorbot import ScorbotError
        from scorbot.simulated import SimulatedScorbot
        robot = SimulatedScorbot()
        with self.assertRaises(ScorbotError):
            robot.close_gripper()                              # not connected
        robot.connect()
        self.addCleanup(self.disconnect, robot)
        queued = list(robot.sim.commands)
        with self.assertRaises(ScorbotError) as caught:
            robot.close_gripper()                              # motors not enabled
        self.assertIn("Enable", str(caught.exception))
        self.assertEqual(robot.sim.commands, queued)
        robot.enable()
        self.assertFalse(robot.get_state().homed)
        self.assertTrue(robot.close_gripper().full_travel)     # no home needed
        with self.assertRaises(ValueError):
            robot.move_gripper("squeeze")

    @unittest.skipUnless(find_spec("ruckig"), "ruckig not installed (pip install .[planning])")
    def test_it_is_refused_while_a_stream_is_active(self):
        from scorbot import ScorbotError
        robot = self.robot()
        robot.home(start_position_confirmed=True)
        with robot.start_stream():
            queued = len(robot.sim.commands)
            with self.assertRaises(ScorbotError) as caught:
                robot.close_gripper()
            self.assertIn("stream", str(caught.exception).lower())
            self.assertEqual(len(robot.sim.commands), queued)

    def test_closing_on_an_object_is_a_grasp_not_a_fault(self):
        robot = self.robot()
        robot.open_gripper()
        robot.sim.gripper_blocked_at = self.travel - 1600      # an object 1600 counts into the close
        result = robot.close_gripper()
        self.assertEqual((result.moved_counts, result.full_travel), (-1600, False))
        self.assertIsNone(robot._fault)
        self.assertTrue(robot.get_state().enabled)
        self.assertIn("gripper_complete", self.events())
        robot.sim.gripper_blocked_at = None
        self.assertEqual(robot.open_gripper().moved_counts, self.travel)

    def test_a_stop_request_ends_a_gripper_move_without_a_fault(self):
        from scorbot import MotionStopped
        try:
            from tests.test_software_stop import StopAtStep
        except ImportError:
            from test_software_stop import StopAtStep
        robot = self.robot(step_delay_s=0.02)
        hook = StopAtStep(robot.request_stop, robot.sim.step_delay_s, step=5)
        hook.start()
        self.addCleanup(hook.cancel)
        with self.assertRaises(MotionStopped) as caught:
            robot.open_gripper()
        moved = caught.exception.state.signed_encoder_counts["gripper"]
        self.assertGreater(moved, 0)
        self.assertLess(moved, self.travel)
        self.assertIsNone(robot._fault)
        self.assertIn("gripper_stopped", self.events())
        self.assertFalse(robot._stop_event.is_set())

    def test_a_pending_stop_refuses_the_next_gripper_move_once(self):
        from scorbot import MotionStopped
        robot = self.robot()
        robot.request_stop()
        queued = list(robot.sim.commands)
        with self.assertRaises(MotionStopped) as caught:
            robot.close_gripper()
        self.assertFalse(caught.exception.started)
        self.assertEqual(robot.sim.commands, queued)
        self.assertTrue(robot.open_gripper().full_travel)

    def test_a_gripper_that_goes_the_wrong_way_faults_the_session(self):
        # Which legacy order opens and which closes is inherited, not measured.
        from scorbot import ScorbotError
        robot = self.robot()
        robot.sim.gripper_reversed = True
        with self.assertRaises(ScorbotError) as caught:
            robot.open_gripper()
        self.assertIn("wrong way", str(caught.exception))
        self.assert_latched(robot)
        self.assertIn("gripper_fault", self.events())

    def test_the_arm_must_be_at_rest_before_the_gripper_is_moved(self):
        from scorbot import ScorbotError
        robot = self.robot()
        robot.STOP_SETTLE_TIMEOUT_S = 0.2
        controller = robot.sim
        plain = controller.snapshot

        def drifting(**kwargs):
            with controller._lock:
                controller.counts["shoulder"] += 30
            return plain(**kwargs)

        controller.snapshot = drifting
        queued = [c for c in controller.commands if c[0] in (14, 15)]
        with self.assertRaises(ScorbotError) as caught:
            robot.close_gripper()
        controller.snapshot = plain
        self.assertIn("not at rest", str(caught.exception))
        self.assertEqual([c for c in controller.commands if c[0] in (14, 15)], queued,
                         "no gripper order may be sent to a moving arm")
        self.assertIsNotNone(robot._fault)

    def test_a_gripper_that_never_settles_faults_the_session(self):
        from scorbot import ScorbotError
        robot = self.robot()
        robot.sim.gripper_never_settles = True
        with self.assertRaises(ScorbotError):
            robot.open_gripper()
        self.assert_latched(robot)

    def test_an_arm_joint_moving_during_a_gripper_move_faults_the_session(self):
        from scorbot import ScorbotError
        robot = self.robot()
        robot.sim.gripper_disturbs = {"elbow": 45}
        with self.assertRaises(ScorbotError) as caught:
            robot.close_gripper()
        self.assertIn("elbow", str(caught.exception))
        self.assert_latched(robot)
        self.assertIn("gripper_fault", self.events())

    def test_a_gripper_move_is_logged_with_its_plan_and_its_result(self):
        robot = self.robot()
        robot.open_gripper()
        rows = self.rows()
        start = next(row for row in rows if row["event"] == "gripper_start")
        self.assertEqual((start["direction"], start["planned_counts"]), ("open", self.travel))
        done = next(row for row in rows if row["event"] == "gripper_complete")
        self.assertEqual((done["moved_counts"], done["full_travel"]), (self.travel, True))
        self.assertTrue(all(row.get("simulated") for row in rows))

    def test_the_gripper_call_says_what_it_does_not_do(self):
        doc = Scorbot.move_gripper.__doc__.lower()
        self.assertIn("never run on the arm", doc)
        self.assertIn("force", doc)
        self.assertIn("not an emergency stop", doc)


if __name__ == "__main__":
    unittest.main()
