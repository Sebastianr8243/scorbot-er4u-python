"""Incomplete USB transfers must not turn a reused buffer into fresh feedback. Desk only."""

from importlib.util import find_spec
import unittest
from unittest import mock

from scorbot import Scorbot
from scorbot.packet import PacketTrace, TrackedInputEndpoint
from scorbot.transport.device_file import PACKET_SIZE


class PartialEndpoint:
    wMaxPacketSize = PACKET_SIZE

    def __init__(self, size):
        self.size = size

    def read(self, buffer, timeout=None):
        buffer[:self.size] = bytes(self.size)
        return self.size


@unittest.skipUnless(find_spec("usb"), "PyUSB is needed to import the legacy modules")
class LegacyReadLengthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = Scorbot()._legacy("libdef")

    def test_incomplete_read_raises_before_the_caller_decodes_reused_bytes(self):
        for size in (0, 1, 48, 49, PACKET_SIZE - 1):
            with self.subTest(size=size):
                raw = PartialEndpoint(size)
                buffer = bytearray([0xAA] * raw.wMaxPacketSize)
                with mock.patch.object(self.lib.time, "sleep"), \
                        mock.patch.object(self.lib, "get_media") as decode:
                    with self.assertRaisesRegex(RuntimeError, "USB read failed:.*expected.*got"):
                        self.lib.set_msg("0D", mock.Mock(), raw, buffer, 0.008, 0.012)
                        self.lib.get_media(buffer, [0] * 6)
                decode.assert_not_called()

    def test_complete_read_preserves_packet_timeouts_and_sleeps(self):
        raw = PartialEndpoint(PACKET_SIZE)
        buffer = bytearray([0xAA] * raw.wMaxPacketSize)
        epout = mock.Mock()
        with mock.patch.object(self.lib.time, "sleep") as sleep:
            self.lib.set_msg("0D", epout, raw, buffer, 0.008, 0.012)
        self.assertEqual(buffer, bytes(PACKET_SIZE))
        self.assertEqual(epout.write.call_args.args,
                         (bytes.fromhex("0D" + "0" * (self.lib.MSG_LEN - 2)), self.lib.TIME_OUT_W))
        self.assertEqual(sleep.call_args_list, [mock.call(0.008), mock.call(0.012)])

    def test_failed_read_keeps_the_existing_runtime_error(self):
        raw = mock.Mock()
        raw.read.side_effect = OSError("pipe failed")
        with mock.patch.object(self.lib.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "USB read failed: pipe failed"):
                self.lib.check(bytes(PACKET_SIZE), mock.Mock(), raw, bytearray(PACKET_SIZE), 0, 0)


class TrackedReadLengthTests(unittest.TestCase):
    def test_incomplete_read_never_publishes_or_decodes_a_partial_reply(self):
        for size in (0, 1, 48, 49, PACKET_SIZE - 1):
            with self.subTest(size=size):
                raw = PartialEndpoint(PACKET_SIZE)
                trace, abort = PacketTrace(), mock.Mock()
                endpoint = TrackedInputEndpoint(raw, trace, abort)
                buffer = bytearray(raw.wMaxPacketSize)
                endpoint.read(buffer)
                first = endpoint.snapshot()
                # Old switches, counts and emergency bytes remain past the short transfer.
                buffer[:] = bytes([0xAA] * len(buffer))
                raw.size = size
                trace.start()
                with mock.patch("scorbot.packet.teach_mode_after") as decode:
                    with self.assertRaisesRegex(ValueError, "USB response length"):
                        endpoint.read(buffer)
                decode.assert_not_called()
                abort.assert_not_called()
                self.assertIs(endpoint.snapshot(), first)
                with self.assertRaises(TimeoutError):
                    endpoint.snapshot(after_index=first.index, timeout=0)
                self.assertEqual(trace.stop(), ([], 0))

    def test_failed_read_does_not_publish_a_snapshot(self):
        raw = mock.Mock()
        raw.read.side_effect = OSError("pipe failed")
        endpoint = TrackedInputEndpoint(raw)
        with self.assertRaisesRegex(OSError, "pipe failed"):
            endpoint.read(bytearray(PACKET_SIZE))
        with self.assertRaises(TimeoutError):
            endpoint.snapshot(timeout=0)
