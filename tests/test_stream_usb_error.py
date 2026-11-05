"""ST14: a USB error part-way through a stream (order 21) stops, switches the motors off and fails. No USB.

``stream_targets`` must handle failed writes and partial reads: no further setpoint, recovery from
the last complete reply, fresh packet IDs across stop retries, motors-off if the link answers, at
most ``IO_ABORT_TRIES`` failures, result 1, and the worker carries on. The session latches a fault
and refuses motion even if the caller never closes the stream.
"""

from array import array
from importlib.util import find_spec
import json
from pathlib import Path
import queue
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from scorbot import ScorbotError
from scorbot.robot import Scorbot
from scorbot.state import ENCODER_OFFSETS
from scorbot.streaming import StreamRefused

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
        reply = answer()
        buffer[:64] = array("B", reply) if isinstance(buffer, array) else reply
        return 64


class ShortReadWire(FlakyWire):
    """Overwrite part of the shared reply before reporting a short read."""

    def __init__(self, *fail_reads):
        super().__init__()
        self.reads, self.fail_reads = 0, fail_reads

    def read(self, buffer, timeout=None):
        self.reads += 1
        if self.reads in self.fail_reads:
            buffer[ENCODER_OFFSETS[0] + 2] = 129
            return 22
        return super().read(buffer, timeout)


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

    def run_stream(self, wire, source=None, *, buffer=None, sequence=1):
        if buffer is None:
            buffer = answer()
        reads, results = queue.Queue(), queue.Queue()
        reads.put([1000] * 6)
        returned = self.comm.stream_targets(sequence, wire, wire, buffer, reads, results, source or Source())
        return reads, results, returned

    def test_a_short_read_stops_at_the_last_complete_reply(self):
        hold = bytes.fromhex(self.libdef.get_hold(answer()))
        for buffer in (answer(), array("B", answer())):
            with self.subTest(buffer=type(buffer).__name__):
                self.libdef.reset_count_guard()
                wire = ShortReadWire(FAIL_AT)
                reads, results, _ = self.run_stream(wire, buffer=buffer)
                recovery = wire.sent[FAIL_AT:]
                self.assertEqual(results.get_nowait(), 1)
                self.assertTrue(self.ends_with_stop_then_motors_off(recovery))
                self.assertFalse(reads.empty())
                self.assertEqual([p[12:36] for p in recovery], [hold] * 30)
                self.assertEqual(bytes(buffer), bytes(answer()))   # the caller's buffer was restored in place

    def test_a_short_motors_off_read_restores_the_last_complete_reply_and_media(self):
        for buffer in (answer(), array("B", answer())):
            with self.subTest(buffer=type(buffer).__name__):
                self.libdef.reset_count_guard()
                # The stream read fails, then four stop replies succeed before motors-off fails.
                wire = ShortReadWire(FAIL_AT, FAIL_AT + 5)
                reads, results, _ = self.run_stream(wire, buffer=buffer)
                self.assertEqual(results.get_nowait(), 1)
                self.assertEqual(wire.reads, FAIL_AT + 5)
                self.assertTrue(self.matches(wire.sent[-1], self.libhex.motorsoff(1)))
                media = reads.get_nowait()
                self.assertTrue(reads.empty())
                self.assertEqual(media, self.comm.fresh_media(answer()))
                echo = bytes.fromhex(self.libdef.get_encoder(buffer, media))
                self.assertEqual(int.from_bytes(echo[:4], "little", signed=True), 1)
                self.assertEqual(bytes(buffer), bytes(answer()))

    def test_a_failed_stop_read_consumes_its_packet_id_and_retries_that_message(self):
        for sequence in (1, 248):     # also cross 255 -> 1 during recovery
            with self.subTest(sequence=sequence):
                self.libdef.reset_count_guard()
                wire = ShortReadWire(FAIL_AT, FAIL_AT + 2)
                _, results, returned = self.run_stream(wire, sequence=sequence)
                self.assertEqual(results.get_nowait(), 1)
                packets = wire.sent[FAIL_AT - 1:]
                ids = [p[0] for p in packets]
                self.assertEqual(ids[1:], [n % 255 + 1 for n in ids[:-1]])
                self.assertEqual(returned, ids[-1])
                # mov_comm(3) failed: retry only it, then finish 4, 5 and motors-off.
                stop = [self.libhex.mov_comm(i) for i in (2, 3, 3, 4, 5)]
                self.assertTrue(all(self.matches(p, t) for p, t in zip(packets[1:6], stop)))
                self.assertEqual(len(packets), 1 + 5 + 26)
                hold = bytes.fromhex(self.libdef.get_hold(answer()))
                self.assertEqual([p[12:36] for p in packets[1:]], [hold] * 31)
                self.assertEqual(self.libdef.ultimo_hold, hold)

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
        # EXIT's scorbotoff waits for the controller's handshake ack, which this fake does not model.
        with mock.patch.object(self.comm, "scorbotoff", lambda *args: None):
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

    def test_a_usb_error_faults_without_close_and_is_reported_once(self):
        link = self.robot.dry_link
        out = link.endpoint_out
        real_write = out.write
        failing, failed = threading.Event(), threading.Event()

        def write(data, timeout=None):
            if failing.is_set():
                failing.clear()
                failed.set()
                raise OSError("write timed out")
            return real_write(data, timeout)

        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(self.robot, "log_path", Path(directory) / "stream.jsonl"), \
                mock.patch.object(out, "write", side_effect=write), \
                mock.patch.object(self.robot, "_latch_fault", wraps=self.robot._latch_fault) as latch, \
                mock.patch.object(self.robot._commands, "put", wraps=self.robot._commands.put) as orders:
            stream = self.robot.start_stream(travel_cap_deg=None)
            stream.set_target({"base": 20})
            # Fail only once the stream is sending setpoints: packets counted on the link
            # can still be idle ones from before the worker picked up the stream.
            deadline = time.monotonic() + 10
            while len(stream.steps) < 3 and time.monotonic() < deadline:
                time.sleep(0.001)
            self.assertGreaterEqual(len(stream.steps), 3)
            failing.set()
            deadline = time.monotonic() + 3
            while self.robot._fault is None and time.monotonic() < deadline:
                time.sleep(0.001)
            self.assertTrue(failed.is_set())
            self.assertIsNotNone(self.robot._fault)
            with stream.report_lock:   # reporting follows the latch on the worker
                self.assertIn("physical stop", self.robot._fault)
                self.assertIn("motor state is unverified", stream.core.fault)
                self.assertTrue(stream.fault_reported)
            with self.assertRaises(StreamRefused):
                stream.set_target({"base": 10})
            def events():
                return [json.loads(row)["event"]
                        for row in self.robot.log_path.read_text().splitlines()]

            self.assertEqual(events().count("stream_fault"), 1)
            with self.assertRaises(ScorbotError):
                stream.close()
            self.assertEqual(events().count("stream_fault"), 1)
            self.assertEqual(latch.call_count, 1)
            self.assertEqual([call.args[0] for call in orders.call_args_list].count([16, 1, 1]), 1)
            self.assertTrue(self.robot._link_alive())

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
            # Fail only once the stream is sending setpoints: packets counted on the link
            # can still be idle ones from before the worker picked up the stream.
            deadline = time.monotonic() + 10
            while len(stream.steps) < 3 and time.monotonic() < deadline:
                time.sleep(0.001)
            self.assertGreaterEqual(len(stream.steps), 3)
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
