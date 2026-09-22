"""Preflight tests never touch a real USB device."""

import types
import unittest
from unittest.mock import patch

from scorbot import preflight


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.backend = object()
        self.find_calls = []

        def fake_import(name):
            if name == "libusb_package":
                return types.SimpleNamespace(get_libusb1_backend=lambda: self.backend)
            if name == "usb.core":
                return types.SimpleNamespace(find=self.fake_find)
            return types.SimpleNamespace()

        self.import_patch = patch.object(preflight, "import_module", side_effect=fake_import)
        self.version_patch = patch.object(preflight.metadata, "version", return_value="test")
        self.platform_patch = patch.object(preflight.sys, "platform", "win32")
        for item in (self.import_patch, self.version_patch, self.platform_patch):
            item.start()
            self.addCleanup(item.stop)

    def fake_find(self, **kwargs):
        self.find_calls.append(kwargs)
        return object()

    def test_finds_expected_device_with_explicit_backend(self):
        checks = preflight.run_checks()
        self.assertTrue(all(check.passed for check in checks))
        self.assertEqual(self.find_calls, [{"idVendor": 0x09F1,
                                            "idProduct": 0x0007,
                                            "backend": self.backend}])

    def test_missing_backend_never_enumerates(self):
        self.backend = None
        checks = preflight.run_checks()
        self.assertFalse(checks[-1].passed)
        self.assertEqual(checks[-1].name, "libusb backend")
        self.assertEqual(self.find_calls, [])

    def test_missing_device_reports_driver_ambiguity(self):
        with patch.object(self, "fake_find", return_value=None):
            checks = preflight.run_checks()
        self.assertFalse(checks[-1].passed)
        self.assertIn("driver", checks[-1].detail)

    def test_usb_error_reports_enumeration_failure(self):
        with patch.object(self, "fake_find", side_effect=OSError("access denied")):
            checks = preflight.run_checks()
        self.assertFalse(checks[-1].passed)
        self.assertIn("access denied", checks[-1].detail)


if __name__ == "__main__":
    unittest.main()
