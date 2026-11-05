"""Tests for streaming rows that are built but had no test (docs/requirements/streaming.md, #171, #172).

ST03: the default bounds are the vendor encoder window, both sides. ST12: the pendant Abort key mid-stream
is the normal stop, and a held key refuses a new stream. ST13: every stream fault cause queues motors-off
without the caller ending the stream. ST16: after the stream, the idle messages hold the last setpoint
(close) or the counts at the stop, and nothing after a fault's motors-off. Simulator, DryScorbot and fake
endpoints only; never the device.
"""

from importlib.util import find_spec
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from scorbot import ScorbotError
from scorbot.calibration import signed_count_delta
from scorbot.nominal import VENDOR_ENCODER_SOFT_LIMITS

try:
    from tests.sim_support import SimulatedRobotCase
    from tests import test_idle_hold as idle_hold   # a module, so its test class is not collected here too
except ImportError:      # discovered with -s tests: the folder itself is on sys.path
    from sim_support import SimulatedRobotCase
    import test_idle_hold as idle_hold

DriftingController, STEP = idle_hold.DriftingController, idle_hold.STEP

HAS_RUCKIG = find_spec("ruckig") is not None
STREAM_ORDER = 21
MOTORS_OFF = [16, 1, 1]
OPEN, MODE, CONTROL_OFF, MOTOR = 0x47, 0x4F, 0x73, 0x42


def wait_for(condition, seconds=3.0):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.002)
    return condition()


@unittest.skipUnless(HAS_RUCKIG, "needs the planning extra")
class SdkStreamTests(SimulatedRobotCase):
    CONTROLLER_DEFAULTS = {"step_delay_s": 0.001}

    @staticmethod
    def running(stream):
        return wait_for(lambda: len(stream.steps) >= 2)

    # ST03 --------------------------------------------------------------------------------------

    def test_the_default_shoulder_bounds_are_the_vendor_window_exactly(self):
        robot = self.robot()
        with robot.start_stream() as stream:
            for motor in ("base", "shoulder", "elbow"):
                with self.subTest(motor=motor):
                    home = signed_count_delta(robot._home_counts[motor], 0)
                    window = VENDOR_ENCODER_SOFT_LIMITS[motor]
                    self.assertEqual(stream.core.bounds[motor],
                                     (window.minimum - home, window.maximum - home))
        self.assertIsNone(robot._fault)

    # ST12 --------------------------------------------------------------------------------------

    def test_pendant_abort_mid_stream_stops_without_a_fault(self):
        robot = self.robot(step_delay_s=0.004)
        stream = robot.start_stream()
        stream.set_target({"base": 1400})
        self.assertTrue(self.running(stream))
        robot._pendant_abort()             # what the input endpoint calls on reply byte 3 = 0x21
        final = stream.stop()
        self.assertEqual(stream.steps[-1]["action"], "stop")
        self.assertLess(final.signed_encoder_counts["base"], 1400)
        self.assertIsNone(robot._fault)
        self.assertIsNone(stream.core.fault)
        self.assertTrue(robot.get_state().homed)
        self.assertIn("stream_stopped", self.events())
        self.assertNotIn(MOTORS_OFF, robot.sim.commands[1:])    # a stop, not a fault: motors stay on

    def test_abort_held_refuses_a_new_stream(self):
        robot = self.robot()
        robot.sim.pendant_abort_held = True     # the input endpoint's flag while reply byte 3 stays 0x21
        queued = len(robot.sim.commands)
        with self.assertRaises(ScorbotError):
            robot.start_stream()
        self.assertEqual(len(robot.sim.commands), queued, "nothing queued while the key is held")
        self.assertIsNone(robot._stream)
        robot.sim.pendant_abort_held = False
        with robot.start_stream():
            pass
        self.assertIsNone(robot._fault)

    # ST13 --------------------------------------------------------------------------------------

    def test_every_stream_fault_queues_motors_off(self):
        """Each cause in ST13 faults the stream, latches, and queues order 16, with no close()."""

        def emergency(robot, stream):
            robot.sim.stream_emergency = True

        def impact_error_word(robot, stream):
            robot.sim.stream_error_word = 0xFFB9          # -71: past ImpactDetect (vendor error 201)

        def collision_flag(robot, stream):
            robot.sim.stream_collision_bits = 0x02        # shoulder

        def lead_past_the_limit(robot, stream):
            robot.sim.stream_stuck = True
            stream.set_target({"base": 1400})

        def setpoint_leaving_the_bounds(robot, stream):
            stream.set_target({"base": 400})
            with stream.core._lock:
                stream.core.bounds["base"] = (-60, 60)    # the next setpoints cross 60

        def setpoint_past_a_vendor_limit(robot, stream):
            stream.set_target({"base": 400})
            stream.core._pose_check = lambda counts: "a test limit" if counts["base"] > 60 else None

        def corrupt_top_byte(robot, stream):
            robot.sim.stream_sign_override = {"shoulder": 0x81}

        def past_the_counter_range(robot, stream):
            counts = robot.get_state().signed_encoder_counts["base"]
            patch = mock.patch("scorbot.streaming.COUNT_MAX", counts + 60)
            patch.start()
            self.addCleanup(patch.stop)
            stream.set_target({"base": 400})

        def session_fault_from_elsewhere(robot, stream):
            robot._latch_fault("feedback was lost")       # as _motion_state does on another thread

        def trajectory_generator_failure(robot, stream):
            from ruckig import Result
            otg = stream.core._otg
            stream.core._otg = SimpleNamespace(update=lambda *args: Result.Error)
            self.addCleanup(setattr, stream.core, "_otg", otg)

        causes = (emergency, impact_error_word, collision_flag, lead_past_the_limit,
                  setpoint_leaving_the_bounds, setpoint_past_a_vendor_limit, corrupt_top_byte,
                  past_the_counter_range, session_fault_from_elsewhere, trajectory_generator_failure)
        for cause in causes:
            with self.subTest(cause=cause.__name__):
                robot = self.robot()
                stream = robot.start_stream()
                self.assertTrue(self.running(stream))
                start = len(robot.sim.commands)
                cause(robot, stream)
                self.assertTrue(wait_for(lambda robot=robot: robot._fault is not None), "the session must latch")
                self.assertTrue(wait_for(lambda robot=robot, start=start: MOTORS_OFF in robot.sim.commands[start:]),
                                "motors-off must be queued without close()")
                self.assertIsNotNone(stream.core.fault)
                sent = len(robot.sim.stream_sent)
                time.sleep(0.05)
                self.assertEqual(len(robot.sim.stream_sent), sent, "no setpoint after the fault")
                with self.assertRaises(ScorbotError):
                    stream.close()


@unittest.skipUnless(find_spec("usb"), "PyUSB is needed to import the legacy modules")
class LegacyStreamTests(idle_hold.LegacyIdleHoldTests):
    """ST16 through ``execute`` and ``syncro``, with the arm drifting after the stream ends.

    Inherits the idle-hold harness (``run_order``, ``idle``); its own tests are not re-run here.
    """

    def stream_order(self, script):
        steps = iter(script)
        return [STREAM_ORDER, lambda reply: next(steps), 0]

    def test_after_close_the_idle_messages_hold_the_last_setpoint(self):
        controller = DriftingController()
        last = (1150, 1080, 1020)          # controller counts
        script = [("send", (1050, 1030, 1010)), ("send", last), ("end",)]
        hold, media = self.run_order(controller, self.stream_order(script))
        self.assertEqual(controller.commands()[-3:], [MODE, CONTROL_OFF, MOTOR], "ended with closeMov")
        self.assertEqual(hold, controller.packets[-1][12:36])
        self.assertEqual([int.from_bytes(hold[4 * i:4 * i + 4], "little", signed=True) for i in range(3)],
                         list(last))
        before = list(controller.position)
        for packet in self.idle(controller, media, hold):
            self.assertEqual(packet[12:36], hold)
        self.assertEqual([p - b for p, b in zip(controller.position, before)], [60] * len(controller.position))

    def test_after_a_stop_the_idle_messages_hold_the_stop_counts(self):
        controller = DriftingController(rate=3)             # the arm lags the setpoints
        script = [("send", (1300, 1001, 1001))] * 4 + [("stop",)]
        hold, media = self.run_order(controller, self.stream_order(script))
        self.assertEqual(controller.commands()[-4:], [OPEN, MODE, CONTROL_OFF, MOTOR], "ended with the stop")
        at_stop = controller.reported[-5]                    # the reply the stop was built from
        # The fake reports L with sign byte 128: controller count L + 1, held as a signed 32-bit slot.
        self.assertEqual(hold, b"".join((value + 1).to_bytes(4, "little") for value in at_stop))
        self.assertLess(int.from_bytes(hold[0:4], "little", signed=True), 1300, "the counts, not the target")
        for packet in self.idle(controller, media, hold):
            self.assertEqual(packet[12:36], hold)

    def test_after_a_fault_no_hold_is_left(self):
        # A stream fault ends the order with the stop; the session then queues motors-off (order 16),
        # after which the idle messages follow the encoders again.
        controller = DriftingController(rate=3)
        script = [("send", (1300, 1001, 1001))] * 3 + [("stop",)]
        stop_hold, _ = self.run_order(controller, self.stream_order(script))
        self.assertIsNotNone(stop_hold)
        hold, media = self.run_order(controller, MOTORS_OFF)
        self.assertIsNone(hold)
        fields = {bytes(packet[12:36]) for packet in self.idle(controller, media, hold)}
        self.assertGreater(len(fields), 1, "with no hold the idle setpoint follows the drift")
        self.assertTrue(all(packet[4] == STEP for packet in controller.packets[-12:]))


# LegacyIdleHoldTests' own tests run in tests/test_idle_hold.py; do not run them twice from here.
for _name in [n for n in vars(idle_hold.LegacyIdleHoldTests) if n.startswith("test_")]:
    setattr(LegacyStreamTests, _name, None)
del _name


if __name__ == "__main__":
    unittest.main()
