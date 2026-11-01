"""scorbot.pacing (pure lag arithmetic) and its opt-in hook in the legacy stream loop. No USB, no real sleeps."""

import queue
import threading
import time
import unittest
from importlib.util import find_spec
from unittest import mock

from scorbot import pacing
from scorbot.robot import Scorbot
from scorbot.state import ENCODER_OFFSETS
from scorbot.vendor_home_source import VendorHomeSource


def reply(echo):
    return bytes([echo]) + bytes(63)


class LagTests(unittest.TestCase):
    def test_lag_is_sent_minus_echoed(self):
        self.assertEqual(pacing.echo_lag(10, reply(10)), 0)
        self.assertEqual(pacing.echo_lag(10, reply(4)), 6)

    def test_lag_wraps_on_the_1_to_255_ring(self):
        self.assertEqual(pacing.echo_lag(3, reply(250)), 8)
        self.assertEqual(pacing.echo_lag(1, reply(255)), 1)

    def test_an_unusable_echo_has_no_lag(self):
        self.assertIsNone(pacing.echo_lag(10, b""))
        self.assertIsNone(pacing.echo_lag(10, reply(0)))

    def test_the_vendor_home_source_measures_with_the_same_function(self):
        source = VendorHomeSource.__new__(VendorHomeSource)
        source.lag_first = source.lag_max = None
        source.lag = 0
        self.assertEqual(source._measure_lag(reply(250), 4), pacing.echo_lag(3, reply(250)))
        self.assertEqual((source.lag_first, source.lag_max), (8, 8))
        self.assertIsNone(source._measure_lag(reply(0), 4))


class DelayTests(unittest.TestCase):
    def test_fast_below_the_limit_and_half_the_period_at_or_above(self):
        self.assertEqual(pacing.next_read_delay(0, 45, 0.016), 0.001)
        self.assertEqual(pacing.next_read_delay(44, 45, 0.016), 0.001)
        self.assertEqual(pacing.next_read_delay(45, 45, 0.016), 0.008)
        self.assertEqual(pacing.next_read_delay(200, 4, 0.016), 0.008)

    def test_an_unknown_lag_gets_the_slow_wait(self):
        self.assertEqual(pacing.next_read_delay(None, 45, 0.016), 0.008)

    def test_a_lag_already_at_the_limit_waits_the_slow_time(self):
        # The thesis idle capture shows a lag of about 45 (the Buffers limit) at rest: the vendor rule then
        # gives half a period. What motion does to the lag is unmeasured; this pins the rule, not a claim.
        pace = pacing.make_pace(limit=45, period_s=0.016)
        # (Our IDs skip 0, so the ring is 255 long; across the wrap the vendor's 256 ring reads one higher.)
        self.assertEqual(pace(100, reply(55)), 0.008)

    def test_make_pace_joins_the_two(self):
        pace = pacing.make_pace(limit=6, period_s=0.016)
        self.assertEqual(pace(10, reply(8)), 0.001)
        self.assertEqual(pace(10, reply(4)), 0.008)
        self.assertEqual(pace(3, reply(250)), 0.008)


@unittest.skipUnless(find_spec("usb"), "PyUSB is needed to import the legacy modules")
class HookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        robot = Scorbot()
        robot._legacy("conf").setup()
        cls.comm = robot._legacy("libcomm")
        cls.lib = robot._legacy("libdef")

    def run_stream(self, **kwargs):
        reads, results = queue.Queue(), queue.Queue()
        reads.put([1000] * 6)
        steps = iter([("send", (1010, 990, 1005)), ("end",)])
        read_args, sleeps = [], []

        def fill(buffer):
            buffer[0] = 9                      # the echoed ID of the message just sent
            for offset in ENCODER_OFFSETS:
                buffer[offset + 2] = 128       # a valid sign byte per joint

        # Both patches reach every thread (self.comm.time is the time module): record only this thread's calls,
        # and give a worker left running by another test the real ones, not a busy loop.
        me, real_sleep, real_set_msg = threading.get_ident(), time.sleep, self.lib.set_msg

        def fake_set_msg(cadena, epout, epin, buffer, write, read):
            if threading.get_ident() != me:
                return real_set_msg(cadena, epout, epin, buffer, write, read)
            read_args.append(read)
            fill(buffer)

        buffer = bytearray(64)
        fill(buffer)

        def sleep(seconds):
            if threading.get_ident() == me:
                sleeps.append(seconds)
            else:
                real_sleep(seconds)

        with mock.patch.object(self.lib, "set_msg", fake_set_msg), \
                mock.patch.object(self.comm.time, "sleep", sleep):
            self.comm.stream_targets(1, None, None, buffer, reads, results,
                                     lambda _reply: next(steps), **kwargs)
        return read_args, sleeps

    def test_by_default_every_message_keeps_the_fixed_read_delay_and_no_extra_sleep(self):
        read_args, sleeps = self.run_stream()
        base = self.comm.conf.readData("cadera", "read")
        self.assertEqual(set(read_args), {base})
        self.assertEqual(sleeps, [])

    def test_with_a_pace_the_setpoint_reply_is_followed_by_its_delay(self):
        seen = []

        def pace(sent_id, reply_bytes):
            seen.append((sent_id, reply_bytes[0]))
            return 0.0123

        read_args, sleeps = self.run_stream(pace=pace)
        base = self.comm.conf.readData("cadera", "read")
        self.assertEqual(read_args.count(0), 1)
        self.assertEqual(len(read_args) - 1, len([r for r in read_args if r == base]))
        self.assertEqual(sleeps, [0.0123])
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][1], 9)

    def test_start_stream_is_off_by_default(self):
        import inspect
        from scorbot.stream_control import StreamMixin
        self.assertFalse(inspect.signature(StreamMixin.start_stream).parameters["adaptive_pacing"].default)


if __name__ == "__main__":
    unittest.main()
