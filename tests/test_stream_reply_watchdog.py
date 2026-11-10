"""SO11/SO12: reply age on the real Stream adapter; controlled host time, no I/O."""

from types import SimpleNamespace
import unittest
from unittest import mock

from scorbot import Scorbot
from scorbot.simulated import encode_packet
from scorbot.state import decode_state
from scorbot.streaming import MOTORS, Stream, StreamCore, StreamRefused


class ReplyWatchdogTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.0
        clock = mock.patch("scorbot.streaming.time", SimpleNamespace(
            monotonic=lambda: self.now, monotonic_ns=lambda: round(self.now * 1e9),
            sleep=lambda delay: None))
        clock.start()
        self.addCleanup(clock.stop)
        self.robot = Scorbot()
        self.robot._record = mock.Mock()
        self.robot._link_alive = lambda: True  # queue-only fake; no workers or handles
        self.counts = dict.fromkeys(MOTORS, 0)
        self.core = StreamCore(self.counts, travel_cap=dict.fromkeys(MOTORS, 1000),
                               lead_limit=dict.fromkeys(MOTORS, 100), now=self.now)
        home = decode_state(encode_packet(self.counts), connected=True, enabled=True,
                            homed=True, fault=None).encoder_counts
        self.stream = Stream(self.robot, self.core, home, None, home_signed=self.counts)

    def reply(self, echo, age):
        self.now = 10.0 + age
        self.stream.set_target({"base": 60})
        packet = bytearray(encode_packet(self.counts))
        packet[0] = echo
        answer = self.stream.source(bytes(packet))
        if answer[0] == "send":
            self.counts = dict(zip(MOTORS, answer[1]))
        return answer

    def assert_fault(self):
        self.assertEqual(self.core.state, "faulted")
        self.assertIn("no fresh controller reply for", self.core.fault)
        self.assertIn(self.core.fault, self.robot._fault)
        self.assertTrue(self.robot._fault_off_queued)
        self.assertEqual(self.robot._commands.get_nowait(), [16, 1, 1])
        self.assertTrue(self.robot._commands.empty())
        self.assertFalse(self.robot._homed)
        with self.assertRaises(StreamRefused):
            self.stream.set_target({"base": 0})

    def test_stale_echo_faults_even_while_targets_and_other_reply_fields_change(self):
        self.assertEqual(self.reply(7, 0.0)[0], "send")
        for age in (0.1, 0.2, 0.3, 0.4, 0.5):
            self.assertEqual(self.reply(7, age)[0], "send")
        self.assertEqual(self.reply(7, 0.501), ("stop",))
        self.assert_fault()
        self.assertEqual(self.stream.source(encode_packet(self.counts)), ("stop",))
        self.assertTrue(self.robot._commands.empty())  # latch reports once

    def test_four_repeats_and_longer_runs_below_timeout_keep_tracking(self):
        packet = bytearray(encode_packet(self.counts))
        packet[0] = 255
        for i in range(50):  # many more than the four identical replies allowed in lab step P
            self.now = 10.0 + i / 100
            self.stream.set_target({"base": 60})
            self.assertEqual(self.stream.source(bytes(packet))[0], "send")
        self.assertEqual(self.core.state, "tracking")
        self.assertIsNone(self.robot._fault)

    def test_echo_advance_including_wrap_refreshes_the_deadline(self):
        for echo, age in ((255, 0.0), (255, 0.49), (1, 0.499), (1, 0.98), (2, 0.99)):
            self.assertEqual(self.reply(echo, age)[0], "send")
        self.assertEqual(self.core.state, "tracking")
        self.assertIsNone(self.robot._fault)

    def test_one_late_step_faults_even_if_the_arriving_echo_advanced(self):
        self.assertEqual(self.reply(7, 0.0)[0], "send")
        self.assertEqual(self.reply(8, 0.6), ("stop",))
        self.assert_fault()

    def test_a_delayed_first_worker_call_faults(self):
        self.assertEqual(self.reply(7, 0.6), ("stop",))
        self.assert_fault()

    def test_an_invalid_echo_does_not_refresh_the_deadline(self):
        self.assertEqual(self.reply(7, 0.0)[0], "send")
        self.assertEqual(self.reply(0, 0.49)[0], "send")
        self.assertEqual(self.reply(0, 0.501), ("stop",))
        self.assert_fault()

    def test_a_custom_timeout_controls_reply_age(self):
        self.core.reply_timeout_s = 0.1
        self.assertEqual(self.reply(7, 0.0)[0], "send")
        self.assertEqual(self.reply(7, 0.099)[0], "send")
        self.assertEqual(self.reply(8, 0.101), ("stop",))
        self.assert_fault()

    def test_fault_hold_survives_collection_and_ignores_measured_idle_holds(self):
        self.reply(7, 0.0)
        self.reply(8, 0.6)
        hold = bytes.fromhex("01000000" * 6)
        self.stream._hold_after_fault(hold)
        self.robot._stream = None  # collecting close must not restore smoothed idle setpoints
        self.robot._set_idle_hold(None)
        self.assertEqual(self.robot._idle_setpoint(), hold)


if __name__ == "__main__":
    unittest.main()
