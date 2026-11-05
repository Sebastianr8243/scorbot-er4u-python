"""ST14: a USB error part-way through a stream (order 21) stops, switches the motors off and fails. No USB.

``stream_targets`` must handle a failed write the way ``vendor_home_run`` does: no further setpoint,
the stop sequence and motors-off if the link still answers, at most ``IO_ABORT_TRIES`` failures, result
1, and the command worker carries on. The session then latches a fault and refuses motion.
"""

from importlib.util import find_spec
import queue
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from scorbot import ScorbotError
from scorbot.robot import Scorbot
from scorbot.state import ENCODER_OFFSETS

STEPS = 20          # setpoints the source asks for
FAIL_AT = 6         # the write that fails (1-based, counted from the stream's first write)


def answer():
    reply = bytearray(64)
    reply[0] = 9
    for offset in ENCODER_OFFSETS:
        reply[offset + 2] = 128            # a valid sign byte per joint; counts stay put
    return reply


class FlakyWire:
    """Both endpoints. Fails write ``fail_at`` (1-based) and, with ``dead``, every write after it."""

    def __init__(self, fail_at=None, dead=False):
        self.fail_at, self.dead, self.writes, self.sent = fail_at, dead, 0, []

    def write(self, data, timeout=None):
        self.writes += 1
        if self.fail_at is not None and (self.writes == self.fail_at
                                         or (self.dead and self.writes > self.fail_at)):
            raise OSError("write timed out")
        self.sent.append(bytes(data))

    def read(self, buffer, timeout=None):
        buffer[:64] = answer()
        return 64


class Source:
    """Sends ``count`` small setpoints, then ends."""

    def __init__(self, count=STEPS):
        self.count, self.calls = count, 0

    def __call__(self, reply):
        self.calls += 1
        if self.calls > self.count:
            return ("end",)
        return ("send", (1000 + self.calls, 1000, 1000))


@unittest.skipUnless(find_spec("usb"), "PyUSB is needed to import the legacy modules")
class LegacyStreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        robot = Scorbot()
        robot._legacy("conf").setup()
        cls.comm = robot._legacy("libcomm")
        cls.libdef = robot._legacy("libdef")
        cls.libhex = robot._legacy("libhex")

    def setUp(self):
        sleeping = mock.patch("time.sleep")
        sleeping.start()
        self.addCleanup(sleeping.stop)
        self.libdef.reset_count_guard()
        self.addCleanup(self.libdef.reset_count_guard)

    def matches(self, packet, text):
        """Whether ``packet`` is the message ``text`` (a libhex template), ignoring the sequence byte."""
        body = bytes.fromhex(text.format("00"))
        return packet[1:len(body)] == body[1:]

    def ends_with_stop_then_motors_off(self, packets):
        stop = [self.libhex.mov_comm(i) for i in range(2, 6)]
        off = [self.libhex.motorsoff(i) for i in range(1, 27)]
        tail = packets[-30:]
        return len(tail) == 30 and all(self.matches(p, t) for p, t in zip(tail, stop + off))

    def run_stream(self, wire, source=None):
        buffer = answer()
        reads, results = queue.Queue(), queue.Queue()
        reads.put([1000] * 6)
        returned = self.comm.stream_targets(1, wire, wire, buffer, reads, results, source or Source())
        return reads, results, returned

    def test_a_clean_stream_sends_every_setpoint_and_reports_nothing(self):
        wire, source = FlakyWire(), Source()
        reads, results, returned = self.run_stream(wire, source)
        self.assertEqual(source.calls, STEPS + 1)
        self.assertFalse(self.ends_with_stop_then_motors_off(wire.sent))
        self.assertTrue(results.empty())           # execute reports DONE; nothing else
        self.assertFalse(reads.empty())
        self.assertIsInstance(returned, int)

    def test_a_usb_error_mid_stream_stops_and_switches_the_motors_off(self):
        wire = FlakyWire(fail_at=FAIL_AT)
        reads, results, returned = self.run_stream(wire)   # must not raise
        self.assertIsInstance(returned, int)
        after = wire.sent[FAIL_AT - 1:]                     # the writes that got through after the failed one
        self.assertEqual(len(after), 30)                    # no further setpoint: only the stop and motors-off
        self.assertTrue(self.ends_with_stop_then_motors_off(after))   # mov_comm 2 to 5, then motors_off's 26
        self.assertEqual(results.get_nowait(), 1)           # failed, not STOPPED (14) and not clean
        self.assertTrue(results.empty())
        self.assertFalse(reads.empty())                     # the media is back for the next order

    def test_a_dead_link_gives_up_after_the_abort_tries(self):
        wire = FlakyWire(fail_at=FAIL_AT, dead=True)
        reads, results, _ = self.run_stream(wire)
        self.assertEqual(results.get_nowait(), 1)
        self.assertLessEqual(wire.writes - FAIL_AT, self.comm.IO_ABORT_TRIES + 1)
        self.assertFalse(reads.empty())

    def test_execute_survives_a_usb_error_mid_stream_and_takes_the_next_order(self):
        wire = FlakyWire(fail_at=FAIL_AT)
        buffer = answer()
        syncs, orders, reads, results = queue.Queue(), queue.Queue(), queue.Queue(), queue.Queue()
        reads.put([1000] * 6)
        syncs.put(10)
        orders.put([self.comm.STREAM, Source(), 0])
        orders.put([self.comm.EXIT, 1, 1])
        worker = threading.Thread(target=self.comm.execute, daemon=True,
                                  args=(syncs, orders, reads, wire, wire, buffer, results,
                                        threading.Event(), threading.Event()))
        worker.start()
        worker.join(timeout=60)
        self.assertFalse(worker.is_alive())                       # it ran the exit order: not dead mid-stream
        self.assertEqual(results.get_nowait(), 1)                 # the stream failed
        self.assertEqual(results.get_nowait(), self.comm.DONE)    # and the order was still completed
        self.assertEqual(results.get_nowait(), self.comm.DONE)    # then the exit


@unittest.skipUnless(find_spec("usb") and find_spec("ruckig"), "needs PyUSB and the planning extra")
class SdkStreamTests(unittest.TestCase):
    def setUp(self):
        from scorbot.lab.dry_link import DryScorbot
        real_sleep = time.sleep
        patch = mock.patch("time.sleep", lambda seconds: real_sleep(0))
        patch.start()
        self.addCleanup(patch.stop)
        self.robot = DryScorbot(command_timeout=30)
        for target in (mock.patch.object(self.robot._legacy("libdef"), "time",
                                         SimpleNamespace(sleep=lambda seconds: None)),
                       mock.patch("scorbot.lab.dry_link.time", SimpleNamespace(
                           monotonic=time.monotonic, sleep=lambda seconds: None))):
            target.start()
            self.addCleanup(target.stop)
        self.robot.connect()
        self.addCleanup(self.robot.disconnect)
        self.robot.STOP_SETTLE_INTERVAL_S = 0.001
        self.robot.enable()
        self.robot.vendor_home(operator_at_stop=True, jaws_empty=True, wrist_clear=True)

    def test_a_usb_error_mid_stream_latches_and_the_worker_survives(self):
        link = self.robot.dry_link
        out = link.endpoint_out
        real_write = out.write
        failing = threading.Event()

        def write(data, timeout=None):
            if failing.is_set():
                failing.clear()                # one failed write, then the link answers again
                raise OSError("write timed out")
            return real_write(data, timeout)

        with mock.patch.object(out, "write", side_effect=write):
            stream = self.robot.start_stream(travel_cap_deg=None)
            stream.set_target({"base": 20})
            begin = len(link.sent)
            deadline = time.monotonic() + 10
            while len(link.sent) < begin + 5 and time.monotonic() < deadline:
                time.sleep(0.001)
            failing.set()
            with self.assertRaises(ScorbotError):
                stream.close()
        self.assertIsNotNone(self.robot._fault)
        self.assertIn("physical stop", self.robot._fault)      # motor state is unverified (ST14)
        self.assertNotIn("worker stopped", self.robot._fault)  # the stream failed; the worker did not die
        self.robot._command_thread.join(timeout=1)             # a dying worker latches before it exits
        self.assertTrue(self.robot._link_alive())
        with self.assertRaises(ScorbotError):
            self.robot.jog_joint("base", 1)


if __name__ == "__main__":
    unittest.main()
