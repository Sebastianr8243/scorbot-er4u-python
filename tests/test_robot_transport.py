"""Scorbot(transport=...): pyusb stays the default; device_file and auto use the fake kernel32. No USB."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scorbot import Scorbot, ScorbotError, SimulatedScorbot
from scorbot.transport import device_file as df
from tests.test_robot_connect import FakeDevice, fake_legacy, fake_usb
from tests.test_transport_device_file import FakeApi


class TransportTests(unittest.TestCase):
    def robot(self, **kwargs):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        robot = Scorbot(log_path=Path(folder.name) / "run.controller.jsonl", command_timeout=0.2, **kwargs)
        for patch in (mock.patch.object(robot, "_legacy", side_effect=fake_legacy),
                      mock.patch.object(robot, "_run_sync_worker"),
                      mock.patch.object(robot, "_run_command_worker"),
                      mock.patch.object(robot, "_command"),
                      mock.patch.object(robot, "get_state"),
                      mock.patch("scorbot.robot.asdict", return_value={}),
                      mock.patch("scorbot.robot.sys.platform", "linux")):
            patch.start()
            self.addCleanup(patch.stop)
        return robot

    def link_api(self, api):
        patch = mock.patch.object(df, "Win32Api", return_value=api)
        patch.start()
        self.addCleanup(patch.stop)
        return api

    def events(self, robot):
        return [json.loads(line) for line in robot.log_path.read_text(encoding="utf-8").splitlines()]

    def test_the_default_is_pyusb_and_never_opens_the_device_file(self):
        robot, device = self.robot(), FakeDevice()
        self.assertEqual(robot.transport, "pyusb")
        with mock.patch.object(df, "Win32Api", side_effect=AssertionError("device file opened")):
            with mock.patch.dict(sys.modules, fake_usb(device)):
                robot.connect()
        self.assertIs(robot._device, device)
        self.assertEqual(robot.transport_used, "pyusb")

    def test_an_unknown_transport_is_refused(self):
        with self.assertRaises(ValueError):
            Scorbot(transport="serial")

    def test_device_file_feeds_the_legacy_loops_through_the_link_and_never_imports_usb(self):
        robot, api = self.robot(transport="device_file"), self.link_api(FakeApi())
        with mock.patch.dict(sys.modules, {"usb": None, "usb.core": None, "usb.util": None}):
            robot.connect()
            self.assertEqual(robot.transport_used, "device_file")
            self.assertEqual(len(robot._buffer), 64)
            robot.disconnect()
        self.assertIn(("close", 7), api.calls)
        self.assertIsNone(robot._device)

    def test_device_file_not_found_fails_the_connect_without_falling_back(self):
        robot = self.robot(transport="device_file")
        self.link_api(FakeApi(create_error=df.Win32Error(df.ERROR_FILE_NOT_FOUND)))
        with mock.patch.dict(sys.modules, fake_usb(FakeDevice())):
            with self.assertRaisesRegex(ScorbotError, "does not exist.*unverified"):
                robot.connect()
        self.assertIsNone(robot.transport_used)

    def test_auto_uses_the_device_file_when_it_opens(self):
        robot, api = self.robot(transport="auto"), self.link_api(FakeApi())
        with mock.patch.dict(sys.modules, {"usb": None, "usb.core": None, "usb.util": None}):
            robot.connect()
        self.assertEqual(robot.transport_used, "device_file")
        self.assertEqual(api.calls[0], ("create", df.DEFAULT_PATH))

    def test_auto_falls_back_to_pyusb_when_the_device_does_not_exist_and_says_so(self):
        robot, device = self.robot(transport="auto"), FakeDevice()
        self.link_api(FakeApi(create_error=df.Win32Error(df.ERROR_PATH_NOT_FOUND)))
        with mock.patch.dict(sys.modules, fake_usb(device)):
            robot.connect()
        self.assertIs(robot._device, device)
        self.assertEqual(robot.transport_used, "pyusb")
        fallback = [e for e in self.events(robot) if e["event"] == "transport_fallback"]
        self.assertEqual(fallback[0]["used"], "pyusb")

    def test_auto_does_not_swallow_other_open_errors(self):
        for code in (df.ERROR_SHARING_VIOLATION, df.ERROR_ACCESS_DENIED, 123):
            with self.subTest(code=code):
                robot = self.robot(transport="auto")
                self.link_api(FakeApi(create_error=df.Win32Error(code)))
                with mock.patch.dict(sys.modules, fake_usb(FakeDevice())):
                    with self.assertRaisesRegex(ScorbotError, "Connection failed"):
                        robot.connect()
                self.assertIsNone(robot._device)
                self.assertIsNotNone(robot._fault)

    def test_simulated_accepts_the_argument_and_is_unchanged(self):
        robot = SimulatedScorbot(transport="auto")
        self.assertEqual(robot.transport, "auto")
        self.assertEqual(SimulatedScorbot().transport, "pyusb")


if __name__ == "__main__":
    unittest.main()
