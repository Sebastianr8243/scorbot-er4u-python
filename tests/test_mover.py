"""The Mover: degree targets on ``Scorbot.start_stream``, simulator only. No USB.

Design: docs/specs/2026-10-05-three-front-doors-design.md.
"""

from importlib.util import find_spec
import threading
import time
import unittest
from unittest import mock

from scorbot import MotionStopped, ScorbotError, source_model
from scorbot.mover import Mover, MoverRefused

try:
    from tests.sim_support import SimulatedRobotCase
except ImportError:      # discovered with -s tests: the folder itself is on sys.path
    from sim_support import SimulatedRobotCase

HOME = source_model.HOME_ANGLES


class Clock:
    """The real clock plus whatever a test adds, so idle rules need no real waiting."""

    def __init__(self):
        self.added = 0.0

    def __call__(self):
        return time.monotonic() + self.added


@unittest.skipUnless(find_spec("ruckig"), "ruckig not installed (pip install .[planning])")
class MoverTests(SimulatedRobotCase):
    CONTROLLER_DEFAULTS = {"step_delay_s": 0.001}

    def mover(self, **controller_kwargs):
        robot = self.robot(**controller_kwargs)
        self.clock = Clock()
        mover = Mover(robot, now=self.clock)
        self.addCleanup(mover.close)
        return mover, robot

    def drive(self, mover, seconds=5.0):
        """Poll until the arm has arrived (or time runs out)."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            mover.poll()
            if mover.arrived():
                return True
            time.sleep(0.005)
        return False

    def test_reaches_a_joint_target_and_closes_the_stream(self):
        mover, robot = self.mover()
        self.assertFalse(mover.moving())
        mover.set_target_angles(base=HOME["base"] + 5.0)
        self.assertTrue(mover.moving())
        self.assertTrue(self.drive(mover))
        self.assertAlmostEqual(mover.angles()["base"], HOME["base"] + 5.0, delta=0.1)
        self.assertAlmostEqual(mover.target_angles()["base"], HOME["base"] + 5.0)
        mover.poll()
        self.assertTrue(mover.moving(), "a stream that has just arrived stays for a moment")
        self.clock.added += Mover.IDLE_CLOSE_S + 0.1
        mover.poll()
        self.assertFalse(mover.moving())
        self.assertIsNone(robot._stream)
        self.assertIsNone(robot._fault)
        mover.poll()
        self.assertFalse(mover.moving(), "an arrived target does not reopen a stream")

    def test_three_joints_move_together(self):
        mover, robot = self.mover()
        wanted = {"base": HOME["base"] - 4.0, "shoulder": HOME["shoulder"] - 3.0,
                  "elbow": HOME["elbow"] + 2.0}
        mover.set_target_angles(**wanted)
        self.assertTrue(self.drive(mover))
        mover.close()
        for joint, value in wanted.items():
            self.assertAlmostEqual(mover.angles()[joint], value, delta=0.1, msg=joint)
        self.assertEqual(self.events().count("stream_start"), 1)
        self.assertIsNone(robot._fault)

    def test_a_joint_left_out_keeps_its_target(self):
        mover, _robot = self.mover()
        mover.set_target_angles(base=HOME["base"] + 3.0)
        mover.set_target_angles(elbow=HOME["elbow"] + 2.0)
        self.assertAlmostEqual(mover.target_angles()["base"], HOME["base"] + 3.0)
        self.assertTrue(self.drive(mover))
        self.assertAlmostEqual(mover.angles()["base"], HOME["base"] + 3.0, delta=0.1)

    def test_a_target_past_a_joint_limit_is_refused_and_names_the_range(self):
        mover, robot = self.mover()
        with self.assertRaises(MoverRefused) as caught:
            mover.set_target_angles(base=HOME["base"] + 175.0)       # the base ends at 174
        self.assertIn("base", str(caught.exception))
        self.assertIn("-132.0 to 174.0", str(caught.exception))
        self.assertFalse(mover.moving(), "a refused target opens nothing")
        self.assertIsNone(mover.target_angles())
        self.assertNotIn("stream_start", self.events())
        self.assertIsNone(robot._fault)

    def test_the_shoulder_is_refused_above_its_limit(self):
        mover, robot = self.mover()
        with self.assertRaises(MoverRefused) as caught:
            mover.set_target_angles(shoulder=HOME["shoulder"] + 4.0)
        self.assertIn("shoulder", str(caught.exception))
        mover.set_target_angles(shoulder=HOME["shoulder"] + 3.0)
        self.assertTrue(self.drive(mover))
        self.assertIsNone(robot._fault)

    def test_a_bad_target_during_a_move_leaves_the_move_running_and_no_fault(self):
        mover, robot = self.mover()
        mover.set_target_angles(base=HOME["base"] + 5.0)
        with self.assertRaises(MoverRefused):
            mover.set_target_angles(base=HOME["base"] + 175.0)
        with self.assertRaises(MoverRefused):
            mover.set_target_angles(base=float("nan"))
        self.assertTrue(mover.moving())
        self.assertTrue(self.drive(mover))
        self.assertAlmostEqual(mover.angles()["base"], HOME["base"] + 5.0, delta=0.1)
        self.assertIsNone(robot._fault)

    def test_a_stream_nobody_drives_is_closed_short_of_the_target(self):
        mover, robot = self.mover(step_delay_s=0.004)
        mover.set_target_angles(base=HOME["base"] + 9.0)
        mover.poll(driving=False)
        self.assertTrue(mover.moving(), "not yet: it may only be a slow browser")
        self.clock.added += Mover.IDLE_CLOSE_S + 0.1
        mover.poll(driving=False)
        self.assertFalse(mover.moving())
        self.assertFalse(mover.arrived())
        self.assertIsNone(robot._fault)
        mover.poll(driving=False)
        self.assertFalse(mover.moving(), "nobody is driving, so nothing reopens")

    def test_gripper_works_straight_after_a_target_nobody_polled(self):
        mover, robot = self.mover()
        mover.set_target_angles(base=HOME["base"] + 2.0)
        move = mover.gripper("close")
        self.assertEqual(move.direction, "close")
        self.assertFalse(mover.moving())
        self.assertIsNone(robot._fault)

    def test_a_move_longer_than_the_stream_limit_carries_on_in_a_new_stream(self):
        mover, robot = self.mover(step_delay_s=0.004)
        mover.MAX_STREAM_S = 0.2
        mover.set_target_angles(base=HOME["base"] + 9.0)
        self.assertTrue(self.drive(mover, seconds=20.0))
        mover.close()
        self.assertGreaterEqual(self.events().count("stream_start"), 2)
        self.assertAlmostEqual(mover.angles()["base"], HOME["base"] + 9.0, delta=0.1)
        self.assertIsNone(robot._fault)

    def test_stop_ends_the_move_and_forgets_the_target(self):
        mover, robot = self.mover(step_delay_s=0.004)
        mover.set_target_angles(base=HOME["base"] + 9.0)
        time.sleep(0.15)
        mover.stop()
        self.assertFalse(mover.moving())
        self.assertIsNone(mover.target_angles())
        self.assertIn("stream_stopped", self.events())
        self.assertLess(mover.angles()["base"], HOME["base"] + 9.0 - 0.5)
        mover.poll()
        self.assertFalse(mover.moving(), "a stopped move does not start again by itself")
        self.assertIsNone(robot._fault)
        mover.stop()                                    # with nothing moving: no error

    def test_a_fault_during_a_move_is_left_latched(self):
        mover, robot = self.mover()
        robot.sim.stream_stuck = True
        mover.set_target_angles(base=HOME["base"] + 9.0)
        deadline = time.monotonic() + 5
        while robot._fault is None and time.monotonic() < deadline:
            mover.poll()
            time.sleep(0.005)
        self.assertIn("not following", str(robot._fault))
        mover.poll()
        self.assertFalse(mover.moving())
        self.assertIsNone(mover.target_angles())
        with self.assertRaises(ScorbotError):
            mover.set_target_angles(base=HOME["base"] + 1.0)
        self.assertEqual(self.events().count("stream_fault"), 1)
        # The session drops its home after a fault; the picture must still work.
        self.assertLess(abs(mover.angles()["base"] - HOME["base"]), 9.0)

    def test_lost_feedback_during_a_move_stops_it_and_latches_the_session(self):
        mover, robot = self.mover(step_delay_s=0.004)
        mover.set_target_angles(base=HOME["base"] + 9.0)
        mover.poll()
        robot.sim.inject("stale_feedback")
        with self.assertRaises(ScorbotError):
            mover.poll()
        self.assertFalse(mover.moving())
        self.assertIsNone(robot._stream)
        self.assertIsNone(mover.target_angles())
        self.assertIn("feedback", str(robot._fault))

    def test_a_stop_that_cannot_end_the_stream_raises_and_leaves_the_fault(self):
        mover, robot = self.mover()
        mover.set_target_angles(base=HOME["base"] + 5.0)
        with mock.patch.object(type(robot), "_end_stream", autospec=True,
                               side_effect=ScorbotError("The stream did not end")):
            with self.assertRaises(ScorbotError):
                mover.stop()
        self.assertIsNone(mover.target_angles())
        robot._stream.stop()                                 # really end it for the cleanup

    def test_stop_reaches_the_stream_while_a_poll_is_closing_it(self):
        mover, _robot = self.mover(step_delay_s=0.004)
        mover.set_target_angles(base=HOME["base"] + 9.0)
        stream = mover._stream
        with mover._lock:                                    # a poll in the middle of a close
            asked = threading.Thread(target=mover.stop)
            asked.start()
            deadline = time.monotonic() + 2
            while not stream.core._stop_requested and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertTrue(stream.core._stop_requested, "the request did not wait for the lock")
        asked.join(timeout=10)
        self.assertFalse(mover.moving())

    def test_a_target_that_was_given_up_is_not_picked_up_by_the_next_command(self):
        mover, robot = self.mover(step_delay_s=0.004)
        mover.set_target_angles(base=HOME["base"] + 9.0)
        mover.poll(driving=False)
        self.clock.added += Mover.IDLE_CLOSE_S + 0.1
        mover.poll(driving=False)                            # given up, well short of 9
        left_at = mover.angles()["base"]
        mover.set_target_angles(elbow=HOME["elbow"] + 2.0)
        self.assertAlmostEqual(mover.target_angles()["base"], left_at, delta=0.05)
        self.assertTrue(self.drive(mover))
        self.assertAlmostEqual(mover.angles()["base"], left_at, delta=0.1)
        mover.gripper("close")                               # the same after a gripper move
        mover.set_target_angles(base=HOME["base"] + 1.0)
        self.assertAlmostEqual(mover.target_angles()["elbow"], HOME["elbow"] + 2.0, delta=0.05)
        self.assertIsNone(robot._fault)

    def test_a_stop_asked_for_while_the_stream_is_ending_keeps_the_gripper_still(self):
        mover, robot = self.mover()
        mover.set_target_angles(base=HOME["base"] + 2.0)
        end = mover._end

        def end_and_be_stopped(**kwargs):
            end(**kwargs)
            mover._stops += 1                   # stop() got in while the stream was closing

        with mock.patch.object(mover, "_end", side_effect=end_and_be_stopped):
            with self.assertRaises(MotionStopped):
                mover.gripper("close")
        self.assertNotIn("gripper_start", self.events())
        self.assertIsNone(robot._fault)
        self.assertEqual(mover.gripper("close").direction, "close")     # and it works after

    def test_a_stop_asked_for_while_home_is_opening_the_stream_still_ends_it(self):
        mover, robot = self.mover()
        mover.set_target_angles(base=HOME["base"] + 3.0)
        self.assertTrue(self.drive(mover))
        self.clock.added += Mover.IDLE_CLOSE_S + 0.1
        mover.poll()
        self.assertFalse(mover.moving())
        start_stream, stopper = robot.start_stream, []

        def stop_while_opening(*args, **kwargs):
            thread = threading.Thread(target=mover.stop)
            thread.start()
            stopper.append(thread)
            deadline = time.monotonic() + 5
            while mover._stops == 0 and time.monotonic() < deadline:
                time.sleep(0.001)           # stop() has been asked for and waits for the lock
            return start_stream(*args, **kwargs)

        with mock.patch.object(robot, "start_stream", side_effect=stop_while_opening):
            mover.home()
        stopper[0].join(timeout=10)
        self.assertFalse(stopper[0].is_alive())
        self.assertFalse(mover.moving())
        self.assertIsNone(robot._stream)
        self.assertIsNone(mover.target_angles())
        self.assertIsNone(robot._fault)

    def test_a_refusal_gives_the_range_in_the_angles_the_student_typed(self):
        mover, _robot = self.mover()
        with self.assertRaises(MoverRefused) as caught:
            mover.set_target_angles(shoulder=130.0)
        # alone from home: the elbow's -5.16 limit stops the shoulder 89.9 degrees down
        self.assertIn("30.4 to 124.0 degrees", str(caught.exception))
        with self.assertRaises(MoverRefused) as caught:
            mover.set_target_angles(elbow=-150.0)                   # the elbow ends at -140.8
        self.assertIn("elbow", str(caught.exception))
        self.assertIn("-140.8", str(caught.exception))
        self.assertNotIn("wrist", str(caught.exception), "a side effect is not the reason")

    def test_an_elbow_target_that_takes_the_wrist_pitch_past_its_limit_says_so(self):
        # Raising the elbow 35 degrees with the wrist motors still pushes the
        # pitch past -109.65: the elbow's own limit is not the reason.
        mover, robot = self.mover()
        with self.assertRaises(MoverRefused) as caught:
            mover.set_target_angles(elbow=HOME["elbow"] + 35.0)
        self.assertIn("pitch", str(caught.exception))
        self.assertFalse(mover.moving())
        self.assertIsNone(robot._fault)

    def test_only_a_simulated_robot_is_accepted(self):
        class NotSimulated:
            _home_counts = {"base": 0}

        with self.assertRaises(MoverRefused) as caught:
            Mover(NotSimulated())
        self.assertIn("simulat", str(caught.exception))

    def test_needs_a_homed_robot(self):
        self.HOMED = False
        robot = self.robot()
        with self.assertRaises(ScorbotError):
            Mover(robot)

    def test_home_returns_to_zero_counts(self):
        mover, robot = self.mover()
        mover.set_target_angles(base=HOME["base"] + 4.0, elbow=HOME["elbow"] - 3.0)
        self.assertTrue(self.drive(mover))
        mover.home()
        self.assertTrue(self.drive(mover))
        mover.close()
        counts = robot.get_state().signed_encoder_counts
        home = robot._home_counts
        for motor in ("base", "shoulder", "elbow"):
            self.assertAlmostEqual(counts[motor], home[motor], delta=Mover.ARRIVE_COUNTS)

    def test_home_is_where_the_toolbox_puts_the_tool(self):
        mover, _robot = self.mover()
        x, y, z, pitch, roll = mover.xyz()
        self.assertAlmostEqual(x, 169.3, delta=0.5)
        self.assertAlmostEqual(y, 0.0, delta=0.01)
        self.assertAlmostEqual(z, 504.3, delta=0.5)
        self.assertAlmostEqual(pitch, -63.55, delta=0.05)
        self.assertEqual(roll, 0.0)

    def test_the_tool_goes_to_a_point_and_back_and_keeps_its_angle(self):
        mover, robot = self.mover()
        start = mover.xyz()
        for dx, dy, dz in ((20.0, 0.0, 0.0), (15.0, 15.0, -15.0), (0.0, 0.0, 0.0)):
            mover.set_target_xyz(start[0] + dx, start[1] + dy, start[2] + dz)
            self.assertTrue(self.drive(mover))
            here = mover.xyz()
            for got, was, moved in zip(here[:3], start[:3], (dx, dy, dz)):
                self.assertAlmostEqual(got, was + moved, delta=1.0)
            self.assertAlmostEqual(here[3], start[3], delta=0.2, msg="tool pitch")
        self.assertIsNone(robot._fault)

    def test_a_point_outside_the_window_or_out_of_reach_is_refused(self):
        mover, robot = self.mover()
        x, y, z = mover.xyz()[:3]
        with self.assertRaises(MoverRefused) as caught:
            mover.set_target_xyz(x - 20.0, y, z)          # needs the shoulder above its limit
        self.assertIn("shoulder", str(caught.exception))
        with self.assertRaises(MoverRefused) as caught:
            mover.set_target_xyz(900.0, 0.0, 349.0)
        self.assertIn("reach", str(caught.exception))
        with self.assertRaises(MoverRefused):
            mover.set_target_xyz(x, y, None)
        self.assertFalse(mover.moving())
        self.assertIsNone(robot._fault)

    def test_stop_is_not_called_an_emergency_stop(self):
        self.assertIn("not an emergency stop", Mover.stop.__doc__.lower())


if __name__ == "__main__":
    unittest.main()
