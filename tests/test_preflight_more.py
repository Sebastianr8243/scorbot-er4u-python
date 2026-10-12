"""The preflight branches test_preflight.py does not reach. Never touches USB."""

import io
import types
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from scorbot import preflight


def importer(find=None, backend=object(), raise_for=()):
    def fake_import(name):
        if name in raise_for:
            raise ImportError(f"no {name}")
        if name == "libusb_package":
            return types.SimpleNamespace(get_libusb1_backend=lambda: backend)
        if name == "usb.backend.libusb1":
            return types.SimpleNamespace(get_backend=lambda: backend)
        if name == "usb.core":
            return types.SimpleNamespace(find=find or (lambda **kw: object()))
        return types.SimpleNamespace()
    return fake_import


class PreflightMore(unittest.TestCase):
    def run_with(self, platform, **kwargs):
        with patch.object(preflight, "import_module", side_effect=importer(**kwargs)), \
                patch.object(preflight.metadata, "version", return_value="t"), \
                patch.object(preflight.sys, "platform", platform):
            return preflight.run_checks()

    def test_a_missing_dependency_stops_the_checks_early(self):
        checks = self.run_with("win32", raise_for=("usb.core",))
        self.assertEqual([c.name for c in checks][-1], "pyusb")
        self.assertFalse(checks[-1].passed)

    def test_the_posix_path_uses_the_system_libusb(self):
        checks = self.run_with("linux")
        backend = [c for c in checks if c.name == "libusb backend"][0]
        self.assertTrue(backend.passed)
        self.assertIn("libusb-1.0", backend.detail)

    def test_main_prints_pass_and_returns_zero(self):
        out = io.StringIO()
        with patch.object(preflight, "run_checks", return_value=[preflight.Check("x", True, "ok")]), \
                redirect_stdout(out):
            self.assertEqual(preflight.main(), 0)
        self.assertIn("PASS x: ok", out.getvalue())
        self.assertIn("Preflight passed", out.getvalue())

    def test_main_prints_fail_and_returns_one(self):
        out = io.StringIO()
        with patch.object(preflight, "run_checks", return_value=[preflight.Check("x", False, "bad")]), \
                redirect_stdout(out):
            self.assertEqual(preflight.main(), 1)
        self.assertIn("FAIL x: bad", out.getvalue())
        self.assertIn("Preflight failed", out.getvalue())


if __name__ == "__main__":
    unittest.main()
