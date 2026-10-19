"""Pytest-only safety net for tests/ and tests_extra/: the suite never reaches the real arm.

1. The hardware switch is off for the whole run, even in a terminal where the
   operator set SCORBOT_HARDWARE=1 for a lab session (child processes inherit that).
   A test that needs the real connect path sets it itself, with a fake usb package.
2. Real USB discovery raises. Tests that stand in a fake usb package through
   sys.modules are not affected; anything that reaches the real pyusb is.
This is a backstop under the switch, not a guarantee: it does not cover a
ctypes DLL load or a plain `python -m unittest` run (no conftest there).
"""

import os

import pytest

os.environ.pop("SCORBOT_HARDWARE", None)


def _no_real_usb(*args, **kwargs):
    raise AssertionError("a test reached real USB discovery (conftest.py backstop)")


@pytest.fixture(autouse=True)
def _block_real_usb(monkeypatch):
    for module in ("usb.core", "libusb_package"):
        try:
            mod = __import__(module, fromlist=["find"])
        except ImportError:
            continue
        monkeypatch.setattr(mod, "find", _no_real_usb)
