"""SD01-SD07 (docs/requirements/safe-shutdown.md): motors-off after any failure. Fake endpoints only; never the arm."""

import queue
import time
import unittest
from importlib.util import find_spec
from unittest import mock

from scorbot import Scorbot
from scorbot.lab.dry_link import DryLink, DryScorbot
from tests.test_stream_usb_error import FailWritesWire, Source, answer


def wait_for(condition, seconds=3.0):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)
    return condition()


@unittest.skipUnless(find_spec("usb"), "PyUSB is needed to import the legacy modules")
class LegacyCase(unittest.TestCase):
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
        body = bytes.fromhex(text.format("00"))
        return packet[1:len(body)] == body[1:]

    def ends_with_motors_off(self, packets):
        tail = packets[-26:]
        return len(tail) == 26 and all(self.matches(p, self.libhex.motorsoff(i)) for i, p in enumerate(tail, 1))

    def run_order(self, call, wire):
        reads, results = queue.Queue(), queue.Queue()
        reads.put([1000] * 6)
        returned = call(wire, answer(), reads, results)
        return reads, results, returned

    def assert_recovered(self, reads, results, wire):
        self.assertIn(1, list(results.queue))
        self.assertTrue(self.ends_with_motors_off(wire.sent))
        self.assertFalse(reads.empty())


JOG_CALLS = {
    4: "move_hips", 5: "move_hips", 6: "move_shoulder", 7: "move_shoulder", 8: "move_elbow", 9: "move_elbow",
    10: "move_wrist", 11: "move_wrist", 12: "move_wrist", 13: "move_wrist",
}


class LegacyOrderErrorTests(LegacyCase):
    def jog(self, orden):
        move = getattr(self.comm, JOG_CALLS[orden])
        return lambda wire, buffer, reads, results: move(1, wire, wire, buffer, orden, reads, results, 5, 2)

    def test_a_usb_error_in_every_jog_order_switches_the_motors_off(self):
        for orden in JOG_CALLS:
            clean = FailWritesWire()
            self.run_order(self.jog(orden), clean)
            for fail_at in (1, 2, 5, clean.writes - 2):
                with self.subTest(order=orden, fail_at=fail_at):
                    self.libdef.reset_count_guard()
                    wire = FailWritesWire(fail_at)
                    reads, results, _ = self.run_order(self.jog(orden), wire)
                    self.assert_recovered(reads, results, wire)

    def clamp(self, orden):
        return lambda wire, buffer, reads, results: self.comm.clamp(1, wire, wire, buffer, orden, reads, results)

    def test_a_usb_error_in_the_clamp_switches_the_motors_off(self):
        for orden in (14, 15):
            clean = FailWritesWire()
            self.run_order(self.clamp(orden), clean)
            for fail_at in (1, 3, 6, clean.writes - 1):
                with self.subTest(order=orden, fail_at=fail_at):
                    self.libdef.reset_count_guard()
                    wire = FailWritesWire(fail_at)
                    reads, results, _ = self.run_order(self.clamp(orden), wire)
                    self.assert_recovered(reads, results, wire)

    def test_a_stop_during_the_clamp_start_never_sends_the_gripper_switch_on(self):
        import threading
        stop = threading.Event()
        stop.set()
        wire = FailWritesWire()
        reads, results = queue.Queue(), queue.Queue()
        reads.put([1000] * 6)
        self.comm.clamp(1, wire, wire, answer(), 14, reads, results, stop)
        self.assertFalse(any(self.matches(p, self.libhex.clamp(3)) for p in wire.sent))

    def test_a_usb_error_while_the_stream_closes_switches_the_motors_off(self):
        def stream(wire, buffer, reads, results):
            return self.comm.stream_targets(1, wire, wire, buffer, reads, results, Source(3))
        clean = FailWritesWire()
        self.run_order(stream, clean)
        for fail_at in (clean.writes, clean.writes - 1):    # inside closeMov
            with self.subTest(fail_at=fail_at):
                self.libdef.reset_count_guard()
                wire = FailWritesWire(fail_at)
                reads, results, _ = self.run_order(stream, wire)
                self.assert_recovered(reads, results, wire)


class OffGroupTests(LegacyCase):
    def test_motors_off_tries_every_exchange_after_a_failure(self):
        for failing in ((1,), (2, 3), (26,)):
            with self.subTest(failing=failing):
                wire = FailWritesWire(*failing)
                reads = queue.Queue()
                reads.put([1000] * 6)
                with self.assertRaises(self.comm.MotorsOffError) as caught:
                    self.comm.motors_off(10, wire, wire, answer(), reads)
                self.assertEqual(wire.writes, 26)
                self.assertEqual(caught.exception.sequence, 10 + 26)
                self.assertTrue(self.matches(wire.attempted[-1], self.libhex.motorsoff(26)))

    def test_clamp_off_tries_both_exchanges_after_a_failure(self):
        wire = FailWritesWire(1)
        with self.assertRaises(RuntimeError):
            self.comm._clamp_off(1, [1000] * 6, wire, wire, answer(), 0.0, 0.0, 14, "", True)
        self.assertEqual(wire.writes, 2)


@unittest.skipUnless(find_spec("usb"), "PyUSB is needed to import the legacy modules")
class SessionFaultTests(unittest.TestCase):
    def queued_offs(self, spy):
        return [c for c in spy.call_args_list if c.args[0] == [16, 1, 1]]

    def test_every_latched_fault_queues_one_motors_off_and_disable_still_works(self):
        for cause in ("latch", "emergency"):
            with self.subTest(cause=cause), DryScorbot() as robot:
                robot.enable()
                with mock.patch.object(robot._commands, "put", wraps=robot._commands.put) as spy:
                    if cause == "latch":
                        robot._latch_fault("something went wrong")
                        robot._latch_fault("again", keep_first=True)
                    else:
                        robot._emergency_input()
                    self.assertEqual(len(self.queued_offs(spy)), 1)
                self.assertTrue(wait_for(lambda: robot._results.qsize() >= 1))
                robot.disable()                      # must not raise while faulted
                self.assertIsNotNone(robot._fault)

    def test_an_order_17_queued_before_an_emergency_never_turns_the_motors_on(self):
        with DryScorbot() as robot:
            comm = robot._legacy("libcomm")
            with mock.patch.object(comm, "motors_on", wraps=comm.motors_on) as motors_on:
                robot._emergency_input()
                robot._commands.put([17, 1, 1])      # the order that passed the fault check just before
                results = []

                def drained():
                    while not robot._results.empty():
                        results.append(robot._results.get_nowait())
                    return len(results) >= 3
                self.assertTrue(wait_for(drained))
                self.assertIn(1, results)
                motors_on.assert_not_called()


class ConnectInterruptTests(unittest.TestCase):
    def test_ctrl_c_after_motors_on_attempts_motors_off_and_cleans_up(self):
        link = DryLink()
        robot = DryScorbot(link=link)
        read, write = link.endpoint_in.read, link.endpoint_out.write
        state = {"on": False}
        off_attempts = []

        def interrupting_read(buffer, timeout=None):
            if state["on"]:
                raise KeyboardInterrupt
            return read(buffer, timeout)

        def watching_write(data, timeout=None):
            if state["on"] and data[4:7] == b"\x42\xff\x00":
                off_attempts.append(bytes(data))
            result = write(data, timeout)
            if data[4:7] == b"\x42\xff\x01":
                state["on"] = True
            return result

        with mock.patch.object(link.endpoint_in, "read", side_effect=interrupting_read), \
                mock.patch.object(link.endpoint_out, "write", side_effect=watching_write):
            with self.assertRaises(KeyboardInterrupt):
                robot.connect()
        self.assertEqual(len(off_attempts), 1)
        self.assertTrue(link.closed)
        self.assertIsNone(robot._device)
        self.assertIsNotNone(robot._fault)
        self.assertTrue(robot._cancel_event.is_set())


if __name__ == "__main__":
    unittest.main()
