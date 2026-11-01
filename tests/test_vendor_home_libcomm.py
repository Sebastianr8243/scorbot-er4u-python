"""openScorbot/libcomm.py: the vendor-home order (22) with fake endpoints. No USB.

``vendor_home_run`` only does I/O: it asks a ``source`` for each message, sends it with the legacy
delays and hands the reply back. These tests pin that, the sequence byte it continues, the rebuilt
``media`` (the counters jump to zero, so the old smoothed positions must not survive), and the failure
path (motors off, result 1).
"""

from importlib.util import find_spec
import json
import queue
import threading
import unittest
from pathlib import Path
from unittest import mock

from scorbot import vendor_home as vh
from scorbot.robot import Scorbot
from scorbot.vendor_home_source import VENDOR_HOME_ORDER, VendorHomeSource
from tests.vendor_home_plant import Plant

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "vendor_home_trace05.json").read_text(encoding="utf-8"))


class Wire:
    """Both endpoints of the fake controller: ``write`` feeds the arm model, ``read`` returns its reply."""

    def __init__(self, plant, first_id=1):
        """``first_id`` is the ID of the last message before the home: the first reply echoes it, as the real one does."""
        self.plant = plant
        self.sent = []
        self.reply = plant.exchange(vh.encode_out(vh.Out(vh.NOP), first_id))

    def write(self, data, timeout=None):
        self.sent.append(bytes(data))
        self.reply = self.plant.exchange(bytes(data))

    def read(self, buffer, timeout=None):
        buffer[:len(self.reply)] = self.reply


def non_nop(packets):
    items = [[p[4], p[5], int.from_bytes(p[6:8], "little"), int.from_bytes(p[8:12], "little", signed=True)]
             for p in packets if p[4] != vh.NOP]
    return [item for i, item in enumerate(items) if i == 0 or item != items[i - 1]]


class FlakyWire(Wire):
    """Fails the write numbered ``fail_at`` (1-based) and, with ``dead``, every write after that."""

    def __init__(self, plant, fail_at, dead=False):
        super().__init__(plant, 10)
        self.fail_at, self.dead, self.writes = fail_at, dead, 0

    def write(self, data, timeout=None):
        self.writes += 1
        if self.writes == self.fail_at or (self.dead and self.writes > self.fail_at):
            raise OSError("write timed out")
        super().write(data, timeout)


class Scripted:
    """A source that sends ``count`` NOP packets and then ends with ``finish``."""

    def __init__(self, count, finish):
        self.count, self.finish, self.calls = count, finish, []

    def __call__(self, reply, sequence):
        self.calls.append((bytes(reply), sequence))
        if len(self.calls) > self.count:
            return self.finish
        return ("send", vh.encode_out(vh.Out(vh.NOP), sequence))


@unittest.skipUnless(find_spec("usb"), "PyUSB is needed to import the legacy modules")
class VendorHomeRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        robot = Scorbot()
        robot._legacy("conf").setup()
        cls.comm = robot._legacy("libcomm")
        cls.libdef = robot._legacy("libdef")

    def setUp(self):
        sleeping = mock.patch("time.sleep")
        sleeping.start()
        self.addCleanup(sleeping.stop)

    def run_order(self, source, plant=None, b_1=10, stale=999):
        wire = Wire(plant or Plant(), b_1)
        buffer = bytearray(wire.reply)
        reads, results = queue.Queue(), queue.Queue()
        reads.put([stale] * 6)
        returned = self.comm.vendor_home_run(b_1, wire, wire, buffer, reads, results, source)
        return wire, buffer, reads, results, returned

    def test_the_order_code_matches_the_sdk_side(self):
        self.assertEqual(self.comm.VENDOR_HOME, VENDOR_HOME_ORDER)

    def test_a_whole_home_through_the_legacy_loop(self):
        # The one full run (about 5,400 messages through the legacy helpers): everything the source asks
        # for is sent, in order; the sequence byte continues from 250 across the wrap and is handed back;
        # and the media is rebuilt from the final reply, not kept stale.
        source = VendorHomeSource()
        wire, buffer, reads, results, returned = self.run_order(source, b_1=250, stale=4321)
        self.assertTrue(source.ok)
        self.assertEqual(non_nop(wire.sent), [list(s) for s in FIXTURE["steps"]])
        self.assertTrue(results.empty())                       # success: no error code from this function
        expected, b_1 = [], 250
        for _ in wire.sent:
            b_1 = self.libdef.countByte1(b_1)
            expected.append(b_1)
        self.assertEqual([p[0] for p in wire.sent], expected)
        self.assertEqual(returned, expected[-1])               # the sync thread carries on from here
        self.assertIn(1, expected)                             # and the count wrapped the way the legacy one does
        media = reads.get_nowait()
        self.assertEqual(media, [buffer[pos] + 256 * buffer[pos + 1] for pos in self.comm.VEC_POS])
        self.assertNotIn(4321, media)
        self.assertTrue(reads.empty())

    def test_the_media_is_rebuilt_even_when_the_home_ends_at_once(self):
        _, buffer, reads, _, _ = self.run_order(Scripted(2, ("end",)), stale=4321)
        media = reads.get_nowait()
        self.assertEqual(media, [buffer[pos] + 256 * buffer[pos + 1] for pos in self.comm.VEC_POS])

    def test_what_the_next_legacy_packet_commands_is_where_the_arm_is(self):
        wire, buffer, reads, _, _ = self.run_order(Scripted(3, ("end",)), stale=4321)
        media = self.libdef.get_media(buffer, reads.get_nowait())
        region = bytes.fromhex(self.libdef.get_encoder(buffer, media))
        for index, offset in enumerate((19, 24, 29, 34, 39, 44)):
            self.assertEqual(region[4 * index:4 * index + 2], bytes(buffer[offset:offset + 2]))

    def test_a_failed_home_switches_the_motors_off_and_reports_failure(self):
        wire, buffer, reads, results, _ = self.run_order(Scripted(25, ("stop",)))
        self.assertEqual(results.get_nowait(), 1)              # not 14: a half home is a failure, not a stop
        self.assertTrue(results.empty())
        motors_off = [p for p in wire.sent[25:]]
        self.assertEqual(len(motors_off), 26)                  # libcomm.motors_off sends 26 messages
        media = reads.get_nowait()                              # motors_off smooths it by at most a count
        for value, pos in zip(media, self.comm.VEC_POS):
            self.assertLessEqual(abs(value - (buffer[pos] + 256 * buffer[pos + 1])), 2)
        self.assertNotIn(999, media)

    def test_a_usb_error_mid_home_still_sends_the_stop_and_switches_the_motors_off(self):
        # set_msg raises on any USB failure; the worker must not die with a motor still driving.
        wire = FlakyWire(Plant(), fail_at=300)
        buffer = bytearray(wire.reply)
        reads, results = queue.Queue(), queue.Queue()
        reads.put([0] * 6)
        source = VendorHomeSource()
        returned = self.comm.vendor_home_run(10, wire, wire, buffer, reads, results, source)
        self.assertIsInstance(returned, int)                      # no exception: execute() carries on
        self.assertEqual(source.error.code, vh.ERR_USB)
        self.assertIn("USB write failed", str(source.error))
        after = wire.sent[299:]                                   # the writes that got through after the failed one
        self.assertTrue(any(p[4] == vh.CLEAR for p in after))     # 47: the vendor's stop
        zero_drives = [p for p in after if p[4] == vh.MOVE and int.from_bytes(p[8:12], "little", signed=True) == 0]
        self.assertGreaterEqual(len(zero_drives), 6)              # 4D 0 to every motor
        self.assertGreaterEqual(len(after), 26)                   # and libcomm.motors_off's 26 messages
        self.assertEqual(results.get_nowait(), 1)

    def test_a_dead_link_is_given_up_on_after_a_few_tries_and_the_first_error_is_raised(self):
        wire = FlakyWire(Plant(), fail_at=300, dead=True)
        buffer = bytearray(wire.reply)
        reads, results = queue.Queue(), queue.Queue()
        reads.put([0] * 6)
        source = VendorHomeSource()
        self.comm.vendor_home_run(10, wire, wire, buffer, reads, results, source)
        self.assertEqual(source.error.code, vh.ERR_USB)
        self.assertTrue(source.abandoned)                         # the stop messages never got through
        self.assertEqual(results.get_nowait(), 1)
        self.assertLess(wire.writes, 300 + 40)                    # it did not keep trying for ever
        self.assertFalse(reads.empty())                           # the media is back in the queue for the next order

    def test_execute_hands_the_sequence_back_after_a_usb_error_so_the_sync_thread_is_not_stranded(self):
        wire = FlakyWire(Plant(), fail_at=300)
        buffer = bytearray(wire.reply)
        syncs, orders, reads, results = queue.Queue(), queue.Queue(), queue.Queue(), queue.Queue()
        reads.put([0] * 6)
        syncs.put(10)
        orders.put([VENDOR_HOME_ORDER, VendorHomeSource(), 0])
        orders.put([self.comm.EXIT, 1, 1])
        worker = threading.Thread(target=self.comm.execute, daemon=True,
                                  args=(syncs, orders, reads, wire, wire, buffer, results,
                                        threading.Event(), threading.Event()))
        worker.start()
        worker.join(timeout=120)
        self.assertFalse(worker.is_alive())                       # the worker did not die on the error
        self.assertEqual(results.get_nowait(), 1)                 # the home failed
        self.assertEqual(results.get_nowait(), self.comm.DONE)    # and the order was still completed
        self.assertEqual(results.get_nowait(), self.comm.DONE)    # then the exit

    def test_an_unknown_answer_from_the_source_is_a_failure_too(self):
        _, _, _, results, _ = self.run_order(Scripted(3, ("surprise",)))
        self.assertEqual(results.get_nowait(), 1)

    def test_the_first_call_gets_the_last_reply_from_the_shared_buffer(self):
        source = Scripted(2, ("end",))
        wire, buffer, _, _, _ = self.run_order(source)
        self.assertEqual(source.calls[0][0], bytes(wire.plant.reply(vh.encode_out(vh.Out(vh.NOP), 10))))    # echoes b_1, the last ID sent

    def test_the_legacy_delays_are_the_idle_pair(self):
        sleeps = []
        me = threading.get_ident()
        # time.sleep is patched for every thread, so a worker left running by an earlier test would add to the list.
        with mock.patch("time.sleep", side_effect=lambda s: sleeps.append(s) if threading.get_ident() == me else None):
            self.run_order(Scripted(3, ("end",)))
        self.assertEqual(sleeps, [self.comm.WRITE, self.comm.READ] * 3)

    def test_execute_dispatches_the_order_and_reports_done(self):
        source = Scripted(4, ("end",))
        wire = Wire(Plant())
        buffer = bytearray(wire.reply)
        syncs, orders, reads, results = queue.Queue(), queue.Queue(), queue.Queue(), queue.Queue()
        reads.put([0] * 6)
        syncs.put(10)                                           # what the sync thread would hand over
        orders.put([VENDOR_HOME_ORDER, source, 0])
        orders.put([self.comm.EXIT, 1, 1])
        worker = threading.Thread(target=self.comm.execute, daemon=True,
                                  args=(syncs, orders, reads, wire, wire, buffer, results,
                                        threading.Event(), threading.Event()))
        worker.start()
        worker.join(timeout=120)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(source.calls), 5)                  # four sends, then the end
        self.assertEqual(results.get_nowait(), self.comm.DONE)  # the home
        self.assertEqual(results.get_nowait(), self.comm.DONE)  # the exit


if __name__ == "__main__":
    unittest.main()
