import queue
import threading
import time
import unittest
import importlib.util
from unittest.mock import patch

from scorbot import Scorbot
from scorbot.robot import ScorbotError
from scorbot.state import decode_state


class StateTests(unittest.TestCase):
    def test_decodes_six_little_endian_encoder_values(self):
        packet = bytearray(64)
        packet[5] = 0b10101
        for index, offset in enumerate((19, 24, 29, 34, 39, 44), start=1):
            packet[offset:offset + 2] = (index * 1000).to_bytes(2, "little")
        state = decode_state(bytes(packet), connected=True, enabled=False,
                             homed=False, fault=None)
        self.assertEqual(state.encoder_counts["base"], 1000)
        self.assertEqual(state.encoder_counts["gripper"], 6000)
        self.assertEqual(state.home_switch_bits, 0b10101)

    def test_rejects_short_packet(self):
        with self.assertRaises(ValueError):
            decode_state(bytes(48), connected=True, enabled=False,
                         homed=False, fault=None)


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.robot = Scorbot(command_timeout=0.5)
        self.robot._device = object()
        self.robot._buffer = bytearray(64)

    def test_command_and_reply_are_separate(self):
        def worker():
            self.assertEqual(self.robot._commands.get(timeout=0.5), [16, 1, 1])
            self.robot._results.put(0)

        thread = threading.Thread(target=worker)
        thread.start()
        self.robot._command([16, 1, 1])
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())

    def test_jog_requires_homing_and_bounded_delta(self):
        with self.assertRaises(ScorbotError):
            self.robot.jog_joint("base", 1)
        self.robot._enabled = self.robot._homed = True
        with self.assertRaises(ValueError):
            self.robot.jog_joint("base", 6)
        self.assertTrue(self.robot._commands.empty())

    def test_error_is_drained_and_faults_controller(self):
        self.robot._results.put(2)
        self.robot._results.put(1)
        self.robot._results.put(0)
        with self.assertRaisesRegex(ScorbotError, "error code 2"):
            self.robot._command([4, 10, 1])
        self.assertTrue(self.robot._results.empty())
        self.assertIsNotNone(self.robot._fault)

    def test_timeout_requests_homing_cancel_and_disable(self):
        self.robot.command_timeout = 0.01
        with self.assertRaisesRegex(ScorbotError, "timed out"):
            self.robot._command([18, 1, 1])
        self.assertTrue(self.robot._cancel_event.is_set())
        self.assertEqual(self.robot._commands.get_nowait(), [18, 1, 1])
        self.assertEqual(self.robot._commands.get_nowait(), [16, 1, 1])

    @unittest.skipUnless(importlib.util.find_spec("usb"), "PyUSB is not installed")
    def test_faulted_disconnect_keeps_usb_handle_if_worker_is_active(self):
        class StuckWorker:
            def is_alive(self):
                return True

            def join(self, timeout):
                pass

        self.robot._fault = "test fault"
        self.robot._command_thread = StuckWorker()
        with patch("usb.util.dispose_resources") as dispose:
            with self.assertRaisesRegex(ScorbotError, "still active"):
                self.robot.disconnect()
            dispose.assert_not_called()


@unittest.skipUnless(importlib.util.find_spec("usb"), "PyUSB is not installed")
class LegacyPacketTests(unittest.TestCase):
    def test_large_encoder_change_resets_running_mean(self):
        legacy = Scorbot()._legacy("libdef")
        packet = bytearray(64)
        packet[19:21] = (2000).to_bytes(2, "little")
        updated = legacy.get_media(packet, [0] * 6)
        self.assertEqual(updated[0], 2000)

    def test_homing_search_stops_on_cancel_or_deadline(self):
        homing = Scorbot()._legacy("setHome")
        result = queue.Queue()
        cancel = threading.Event()
        self.assertFalse(homing.search_must_stop(time.monotonic() + 5, cancel, result))
        cancel.set()
        self.assertTrue(homing.search_must_stop(time.monotonic() + 5, cancel, result))
        self.assertEqual(result.get_nowait(), 2)
        cancel.clear()
        self.assertTrue(homing.search_must_stop(time.monotonic() - 1, cancel, result))
        self.assertEqual(result.get_nowait(), 2)

    def test_handshake_acknowledgment_wait_has_deadline(self):
        sync = Scorbot()._legacy("libsync")
        with patch.object(sync, "HANDSHAKE_WAIT_TIMEOUT_S", 0):
            with self.assertRaisesRegex(TimeoutError, "handshake"):
                sync.send_wait(1, None, None, bytearray(64))


if __name__ == "__main__":
    unittest.main()
