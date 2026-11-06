"""#172 (streaming part C): every way a stream ends is ended the documented way and logged (ST11, ST15, ST18).

ST18: the session log holds exactly one of ``stream_complete``, ``stream_stopped`` (with the reason) or
``stream_fault``. ST11 and ST15 list the stop paths: ``Stream.stop()``, ``Stream.request_stop()``,
``Scorbot.request_stop()``, the pendant Abort key (ST12), an exception in the caller's ``with`` block and a
disconnect with the stream open; and the fault paths: emergency input, impact, a stream that does not end
in time and an interrupt while it ends. The teach-mode reason is already tested in tests/test_teach_mode.py.
Simulator only; never the device.
"""

from importlib.util import find_spec
import time
import unittest
from unittest import mock

from scorbot import ScorbotError

try:
    from tests.sim_support import SimulatedRobotCase
except ImportError:      # discovered with -s tests: the folder itself is on sys.path
    from sim_support import SimulatedRobotCase

HAS_RUCKIG = find_spec("ruckig") is not None
ENDINGS = ("stream_complete", "stream_stopped", "stream_fault")


def wait_for(condition, seconds=3.0):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.002)
    return condition()


@unittest.skipUnless(HAS_RUCKIG, "needs the planning extra")
class StreamEndLogTests(SimulatedRobotCase):
    CONTROLLER_DEFAULTS = {"step_delay_s": 0.004}

    def moving(self, robot):
        stream = robot.start_stream()
        stream.set_target({"base": 1400})
        self.assertTrue(wait_for(lambda: len(stream.steps) >= 2), "the worker must take the stream up")
        return stream

    def endings(self):
        return [row for row in self.rows() if row["event"] in ENDINGS]

    def assert_stopped_with_reason(self, *words):
        """Exactly one ending row, ``stream_stopped``, whose reason is a sentence naming the cause."""
        endings = self.endings()
        self.assertEqual([row["event"] for row in endings], ["stream_stopped"])
        reason = endings[0].get("reason")
        self.assertIsInstance(reason, str, "ST18: stream_stopped carries the reason")
        self.assertTrue(reason.strip())
        for word in words:
            self.assertIn(word.lower(), reason.lower())

    # -- stop paths: stream_stopped with the reason ----------------------------------------------

    def test_stream_stop_logs_stream_stopped_with_its_reason(self):
        robot = self.robot()
        self.moving(robot).stop()
        self.assertIsNone(robot._fault)
        self.assert_stopped_with_reason("stop")

    def test_stream_request_stop_logs_stream_stopped_with_its_reason(self):
        robot = self.robot()
        stream = self.moving(robot)
        stream.request_stop()
        stream.close()
        self.assertIsNone(robot._fault)
        self.assert_stopped_with_reason("stop")

    def test_robot_request_stop_logs_stream_stopped_with_its_reason(self):
        robot = self.robot()
        stream = self.moving(robot)
        robot.request_stop()
        stream.close()
        self.assertIsNone(robot._fault)
        self.assert_stopped_with_reason("stop")

    def test_pendant_abort_logs_stream_stopped_naming_the_abort_key(self):
        robot = self.robot()
        stream = self.moving(robot)
        robot._pendant_abort()             # what the input endpoint calls on reply byte 3 = 0x21
        stream.close()
        self.assertIsNone(robot._fault)
        self.assert_stopped_with_reason("abort")

    def test_an_abort_before_the_stream_is_up_keeps_its_cause_after_the_key_is_released(self):
        robot = self.robot()
        def abort_after_the_check():       # the Abort lands after start_stream took the last stop, before the stream is published
            robot._pendant_abort()
            robot._input.pendant_abort_held = False
            return False

        with mock.patch.object(robot, "_consume_stop", side_effect=abort_after_the_check):
            stream = robot.start_stream()
        stream.set_target({"base": 1400})
        stream.close()
        self.assertIsNone(robot._fault)
        self.assert_stopped_with_reason("abort")

    def test_an_exception_in_the_callers_block_logs_stream_stopped_naming_it(self):
        robot = self.robot()
        with self.assertRaises(KeyError):
            with self.moving(robot):
                raise KeyError("caller bug")
        self.assertIsNone(robot._fault)
        self.assert_stopped_with_reason("KeyError")

    def test_disconnect_with_the_stream_open_logs_stream_stopped_naming_it(self):
        robot = self.robot()
        stream = self.moving(robot)
        robot.disconnect()
        self.assertIsNotNone(stream.final_state)
        self.assert_stopped_with_reason("disconnect")

    # -- the normal end and the fault paths: one ending row each -----------------------------------

    def test_close_logs_one_stream_complete(self):
        robot = self.robot()
        with robot.start_stream() as stream:
            stream.set_target({"base": 40})
            self.assertTrue(wait_for(lambda: robot.get_state().signed_encoder_counts["base"] == 40))
        self.assertEqual([row["event"] for row in self.endings()], ["stream_complete"])

    def test_every_fault_path_logs_one_stream_fault_and_no_other_ending(self):
        def emergency(robot, stream):
            robot.sim.stream_emergency = True
            wait_for(lambda: robot._fault is not None)
            with self.assertRaises(ScorbotError):
                stream.close()

        def impact(robot, stream):
            robot.sim.stream_error_word = 0xFFB9          # -71: past ImpactDetect (vendor error 201)
            wait_for(lambda: robot._fault is not None)
            with self.assertRaises(ScorbotError):
                stream.close()

        def not_ended_in_time(robot, stream):
            robot.command_timeout = 0.02                  # slowing to rest takes longer than this
            with self.assertRaises(ScorbotError):
                stream.close()

        def interrupt_while_ending(robot, stream):
            with mock.patch.object(robot, "_next_result", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    stream.stop()

        for cause in (emergency, impact, not_ended_in_time, interrupt_while_ending):
            with self.subTest(cause=cause.__name__):
                robot = self.robot()
                stream = self.moving(robot)
                cause(robot, stream)
                self.assertIsNotNone(robot._fault)
                self.assertEqual([row["event"] for row in self.endings()], ["stream_fault"])
                self.assertTrue(self.endings()[0].get("error"))


if __name__ == "__main__":
    unittest.main()
