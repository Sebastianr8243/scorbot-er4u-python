"""The teach pendant's Abort key stops motion, as the vendor DLL's ``Stop('&')`` does (LEDGER.md PL64, PL67).

Reply byte 3 is the pendant: key code in bits 0-5 (Abort is 0x21), bit 6 set = released, bit 7 the
DLL's "In Home". So 0x21 and 0xA1 are Abort pressed. The DLL ignores repeats of the same code, so a
held key stops once. No USB, no hardware.
"""

import queue
import threading
import unittest
from unittest import mock

from scorbot import Scorbot
from scorbot.packet import TrackedInputEndpoint
from scorbot.state import JOINTS

try:
    from tests.test_software_stop import FollowingController, MOVERS, START, STOPPED, OPEN, MODE
except ImportError:      # discovered with -s tests
    from test_software_stop import FollowingController, MOVERS, START, STOPPED, OPEN, MODE


class Replies:
    def __init__(self, pendant_bytes):
        self.packets = []
        for value in pendant_bytes:
            packet = bytearray(64)
            packet[3] = value
            self.packets.append(bytes(packet))

    def read(self, buffer, timeout=None):
        packet = self.packets.pop(0)
        buffer[:64] = packet
        return 64


def presses(pendant_bytes):
    calls = []
    endpoint = TrackedInputEndpoint(Replies(pendant_bytes), on_pendant_abort=lambda: calls.append(1))
    buffer = bytearray(64)
    for _ in pendant_bytes:
        endpoint.read(buffer)
    return len(calls)


class PendantByteTests(unittest.TestCase):

    def test_abort_pressed_calls_back_once_per_press(self):
        # pressed, held, released (0x61), pressed with In Home (0xA1), held
        self.assertEqual(presses([0x00, 0x21, 0x21, 0x61, 0xA1, 0xA1]), 2)

    def test_abort_held_at_connect_counts_as_a_press(self):
        self.assertEqual(presses([0x21, 0x21]), 1)

    def test_other_keys_and_modes_do_nothing(self):
        # released Abort, Plus/Minus pressed, teach and auto mode codes, dead man
        self.assertEqual(presses([0x61, 0xE1, 0x0C, 0x0D, 0x40, 0xC0, 0x7E, 0xFE, 0x4F, 0xF9]), 0)

    def test_a_failing_callback_never_breaks_the_read(self):
        def boom():
            raise RuntimeError("log disk full")
        endpoint = TrackedInputEndpoint(Replies([0x21, 0x00]), on_pendant_abort=boom)
        buffer = bytearray(64)
        self.assertEqual(endpoint.read(buffer), 64)
        self.assertEqual(endpoint.read(buffer), 64)

    def test_without_a_callback_nothing_changes(self):
        endpoint = TrackedInputEndpoint(Replies([0x21]))
        self.assertEqual(endpoint.read(bytearray(64)), 64)


class AbortController(FollowingController):
    """Reports Abort pressed in reply byte 3 from message ``abort_after`` on."""

    def __init__(self, abort_after, rate):
        super().__init__(rate=rate)
        self.abort_after = abort_after

    def write(self, data, timeout):
        result = super().write(data, timeout)
        if len(self.packets) >= self.abort_after:
            self.reply[3] = 0x21
        return result


class LegacyJogAbortTests(unittest.TestCase):
    """The legacy jog loop, with the reply passing through the same endpoint proxy ``connect`` uses."""

    @classmethod
    def setUpClass(cls):
        robot = Scorbot()
        robot._legacy("conf").setup()
        cls.comm = robot._legacy("libcomm")

    def setUp(self):
        sleeping = mock.patch("time.sleep")
        sleeping.start()
        self.addCleanup(sleeping.stop)

    def test_abort_mid_jog_sends_the_stop_sequence(self):
        mover, order, _, _ = MOVERS[0]
        stop_event = threading.Event()
        controller = AbortController(abort_after=6, rate=3)
        endpoint = TrackedInputEndpoint(controller, on_pendant_abort=stop_event.set)
        buffer = bytearray(controller.reply)
        reads, results = queue.Queue(), queue.Queue()
        reads.put([START] * len(JOINTS))
        getattr(self.comm, mover)(1, controller, endpoint, buffer, order, reads, results, 10, 1.0, stop_event)
        commands = controller.commands()
        self.assertEqual(list(results.queue), [STOPPED])
        stop = commands.index(OPEN, 1)
        self.assertLess(stop, 12, "the jog must stop soon after Abort, not run to its end")
        self.assertEqual(commands[stop + 1], MODE)


class ScorbotWiringTests(unittest.TestCase):

    def test_abort_sets_the_stop_and_is_logged(self):
        robot = Scorbot()
        logged = threading.Event()
        robot._record = lambda event, **fields: logged.set() if event == "pendant_abort" else None
        robot._pendant_abort()
        self.assertTrue(robot._stop_event.is_set())
        self.assertTrue(logged.wait(2.0))

    def test_a_blocked_log_never_delays_the_stop(self):
        robot = Scorbot()
        release = threading.Event()
        robot._record = lambda event, **fields: release.wait(5.0)
        caller = threading.Thread(target=robot._pendant_abort)
        caller.start()
        caller.join(1.0)
        self.assertFalse(caller.is_alive(), "the USB read must return while the log write is stuck")
        self.assertTrue(robot._stop_event.is_set())
        release.set()

    def test_abort_reaches_an_active_stream(self):
        robot = Scorbot()
        robot._record = lambda event, **fields: None
        robot._stream = mock.Mock()
        robot._pendant_abort()
        robot._stream.request_stop.assert_called_once()

    def test_motion_is_refused_while_abort_is_held(self):
        robot = Scorbot()
        robot._input = mock.Mock(pendant_abort_held=True)
        self.assertTrue(robot._consume_stop())
        self.assertTrue(robot._consume_stop(), "a held key refuses every start, not only the first")
        robot._input.pendant_abort_held = False
        self.assertFalse(robot._consume_stop())

    def test_the_endpoint_reports_a_held_key(self):
        endpoint = TrackedInputEndpoint(Replies([0x21, 0x61]), on_pendant_abort=lambda: None)
        buffer = bytearray(64)
        endpoint.read(buffer)
        self.assertTrue(endpoint.pendant_abort_held)
        endpoint.read(buffer)
        self.assertFalse(endpoint.pendant_abort_held)


class StreamStartTests(unittest.TestCase):

    def test_a_stop_set_before_the_stream_was_published_still_stops_it(self):
        from scorbot.streaming import Stream
        stream = Stream.__new__(Stream)
        stream.robot = mock.Mock(_fault=None)
        stream.robot._cancel_event.is_set.return_value = False
        stream.robot._stop_event.is_set.return_value = True
        stream.core = mock.Mock()
        stream._pace = lambda: None
        stream._step(bytes(64))
        stream.core.request_stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
