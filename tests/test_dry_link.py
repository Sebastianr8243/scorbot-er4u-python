"""SIMULATED endpoint contract and unchanged legacy workers, without a device."""

from array import array
import unittest
from unittest import mock

from scorbot.lab.dry_link import DryLink, DryScorbot
from scorbot.state import decode_state
from scorbot.transport.codec import IDLE, build_out
from scorbot.transport.device_file import DeviceLinkError
from scorbot.vendor_home_model import Plant


class DryLinkTests(unittest.TestCase):
    def test_endpoint_contract_uses_plant_and_fills_both_buffer_types(self):
        plant = Plant(start=(123, 0, 0, 0, 0, 0), bands={0: (100, 200)})
        link = DryLink(plant=plant)
        packet = build_out(1, IDLE)
        for buffer in (array("B", bytes(64)), bytearray(64)):
            self.assertEqual(link.endpoint_out.wMaxPacketSize, 64)
            self.assertEqual(link.endpoint_in.wMaxPacketSize, 64)
            self.assertEqual(link.endpoint_out.write(packet, 1500), 64)
            self.assertEqual(link.endpoint_in.read(buffer, timeout=1500), 64)
            self.assertEqual(bytes(buffer), plant.reply(packet))
            state = decode_state(bytes(buffer), connected=True, enabled=None, homed=False, fault=None)
            self.assertEqual(state.home_switch_bits, 1)
            self.assertEqual(state.signed_encoder_counts["base"], 122)
        self.assertEqual(link.sent, [packet, packet])

    def test_size_close_and_use_after_close_match_device_link(self):
        link = DryLink()
        with self.assertRaises(ValueError):
            link.endpoint_out.write(bytes(63), 1500)
        self.assertEqual(link.sent, [])
        link.close()
        link.close()
        with self.assertRaises(DeviceLinkError):
            link.endpoint_out.write(bytes(64), 1500)
        with self.assertRaises(DeviceLinkError):
            link.endpoint_in.read(bytearray(64), 1500)

    def test_latency_is_configurable_bounded_by_read_timeout_and_not_a_measurement(self):
        for bad in (-1, float("inf"), float("nan")):
            with self.assertRaises(ValueError):
                DryLink(latency_ms=bad)
        link = DryLink(latency_ms=20)
        link.endpoint_out.write(build_out(1, IDLE), 100)
        link.endpoint_out.write(build_out(2, IDLE), 100)
        with self.assertRaises(TimeoutError):
            link.endpoint_in.read(bytearray(64), 1)
        buffer = bytearray(64)
        self.assertEqual(link.endpoint_in.read(buffer, 100), 64)
        self.assertEqual(buffer[0], 1)
        self.assertEqual(link.endpoint_in.read(buffer, 100), 64)
        self.assertEqual(buffer[0], 2)

    def test_real_connect_and_legacy_threads_use_only_the_injected_link(self):
        robot = DryScorbot()
        with mock.patch("scorbot.transport.device_file.Win32Api", side_effect=AssertionError("device")):
            with robot:
                self.assertTrue(robot._sync_thread.is_alive())
                self.assertTrue(robot._command_thread.is_alive())
                self.assertTrue(robot.get_state().simulated)
                self.assertFalse(robot.get_state().enabled)
                self.assertEqual(robot.transport_used, "SIMULATED")
        self.assertTrue(robot.dry_link.closed)
        self.assertGreater(len(robot.dry_link.sent), 145)
