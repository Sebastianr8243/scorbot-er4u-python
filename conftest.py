"""Pytest-only safety net for tests/ and tests_extra/: the suite never reaches the real arm.

Real USB discovery raises. Tests that stand in a fake usb package through
sys.modules are not affected; anything that reaches the real pyusb is. It does
not cover a ctypes DLL load or a plain `python -m unittest` run (no conftest there).
"""

import pytest


def pytest_configure(config):
    # Create the git-ignored data.json once, before xdist starts its workers: workers that
    # create it together can read a half-written file.
    from openScorbot import conf
    conf.setup()


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
