"""Scorbot.connect and disconnect with a fake usb package, fake legacy modules and no worker threads."""

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from scorbot import Scorbot, ScorbotError
from scorbot.state import ENCODER_OFFSETS, decode_state


class FakeEndpoint:
    def __init__(self, address):
        self.bEndpointAddress = address
        self.wMaxPacketSize = 64


def fake_usb(device, in_endpoint=True, out_endpoint=True):
    class USBError(Exception):
        pass

    core = types.SimpleNamespace(find=lambda **kw: device, USBError=USBError)
    endpoints = {0x83: FakeEndpoint(0x83) if in_endpoint else None,
                 0x02: FakeEndpoint(0x02) if out_endpoint else None}

    def find_descriptor(interface, custom_match):
        for endpoint in (e for e in endpoints.values() if e is not None):
            if custom_match(endpoint):
                return endpoint
        return None

    util = types.SimpleNamespace(
        ENDPOINT_IN=0x80, ENDPOINT_OUT=0x00,
        endpoint_direction=lambda address: address & 0x80,
        find_descriptor=find_descriptor,
        create_buffer=lambda size: bytearray(size),
        dispose_resources=lambda dev: None)
    package = types.SimpleNamespace(core=core, util=util)
    return {"usb": package, "usb.core": core, "usb.util": util}


class FakeDevice:
    def __init__(self):
        self.resets = 0

    def reset(self):
        self.resets += 1

    def __getitem__(self, index):
        return types.SimpleNamespace(interfaces=lambda: [types.SimpleNamespace(bInterfaceNumber=0)])

    def is_kernel_driver_active(self, number):
        return False

    def get_active_configuration(self):
        return {(0, 0): object()}


def fake_legacy(name):
    if name == "conf":
        return types.SimpleNamespace(setup=lambda: None)
    if name == "libsync":
        return types.SimpleNamespace(msg_start=lambda out, inp, buf: (1, [0] * 6))
    return types.SimpleNamespace()


class ConnectTests(unittest.TestCase):
    def robot(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        robot = Scorbot(log_path=Path(folder.name) / "run.controller.jsonl", command_timeout=0.2)
        patches = [
            mock.patch.object(robot, "_legacy", side_effect=fake_legacy),
            mock.patch.object(robot, "_run_sync_worker"),
            mock.patch.object(robot, "_run_command_worker"),
            mock.patch.object(robot, "_command"),
            mock.patch("scorbot.robot.sys.platform", "linux"),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        packet = bytearray(64)
        for offset in ENCODER_OFFSETS:
            packet[offset + 2] = 128                      # every joint needs a valid sign byte
        state = decode_state(bytes(packet), connected=True, enabled=False, homed=False, fault=None)
        patch = mock.patch.object(robot, "get_state", return_value=state)
        patch.start()
        self.addCleanup(patch.stop)
        return robot

    def test_a_good_connect_leaves_the_motors_disabled_and_logs_it(self):
        robot, device = self.robot(), FakeDevice()
        with mock.patch.dict(sys.modules, fake_usb(device)):
            robot.connect()
        self.assertIs(robot._device, device)
        self.assertEqual(device.resets, 1)
        self.assertFalse(robot._enabled)
        robot._command.assert_called_with([16, 1, 1])     # the handshake energises, so it disables at once
        events = robot.log_path.read_text(encoding="utf-8").splitlines()
        self.assertTrue(any('"connect"' in line for line in events))

    def test_no_controller_latches_a_fault_and_says_motors_are_unverified(self):
        robot = self.robot()
        with mock.patch.dict(sys.modules, fake_usb(None)):
            with self.assertRaisesRegex(ScorbotError, "09F1:0007 was not found.*unverified"):
                robot.connect()
        self.assertIsNotNone(robot._fault)
        self.assertTrue(robot._cancel_event.is_set())     # disconnect() returns early with no device, so connect must set it
        with self.assertRaisesRegex(ScorbotError, "new Scorbot instance"):
            robot.connect()

    def test_missing_endpoints_latch_a_fault(self):
        for kwargs in ({"in_endpoint": False}, {"out_endpoint": False}):
            with self.subTest(kwargs=kwargs):
                robot = self.robot()
                with mock.patch.dict(sys.modules, fake_usb(FakeDevice(), **kwargs)):
                    with self.assertRaisesRegex(ScorbotError, "endpoints were not found"):
                        robot.connect()
                self.assertIsNotNone(robot._fault)

    def test_a_failed_disable_after_the_handshake_latches_cleans_up_and_refuses_a_retry(self):
        # The handshake energises the motors; if the disable that follows fails, the session must say so.
        robot, device = self.robot(), FakeDevice()
        robot._command.side_effect = ScorbotError("disable failed")
        with mock.patch.dict(sys.modules, fake_usb(device)):
            with self.assertRaisesRegex(ScorbotError, "Connection failed: disable failed.*unverified"):
                robot.connect()
        self.assertIn("disable failed", robot._fault)
        self.assertTrue(robot._cancel_event.is_set())
        self.assertIsNone(robot._device)                  # the cleanup disconnect released it
        events = [json.loads(line)["event"] for line in robot.log_path.read_text(encoding="utf-8").splitlines()]
        self.assertIn("connect_failed", events)
        self.assertNotIn("connect", events)
        with self.assertRaisesRegex(ScorbotError, "new Scorbot instance"):
            robot.connect()

    def test_on_windows_the_controller_is_found_through_libusb_package(self):
        robot, device = self.robot(), FakeDevice()
        modules = fake_usb(None)                          # usb.core.find would find nothing
        modules["libusb_package"] = types.SimpleNamespace(find=lambda **kw: device)
        with mock.patch.dict(sys.modules, modules), mock.patch("scorbot.robot.sys.platform", "win32"):
            robot.connect()
        self.assertIs(robot._device, device)

    def test_a_second_connect_is_refused_without_touching_the_device(self):
        robot, device = self.robot(), FakeDevice()
        with mock.patch.dict(sys.modules, fake_usb(device)):
            robot.connect()
            with self.assertRaisesRegex(ScorbotError, "Already connected"):
                robot.connect()
        self.assertEqual(device.resets, 1)


if __name__ == "__main__":
    unittest.main()
