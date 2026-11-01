"""Pytest-only safety net for tests/ and tests_extra/: the suite never reaches the real arm.

Real USB discovery raises. Tests that stand in a fake usb package through
sys.modules are not affected; anything that reaches the real pyusb is. It does
not cover a ctypes DLL load or a plain `python -m unittest` run (no conftest there).

A test that leaves a legacy worker thread running fails: the worker keeps sending
and sleeping into the next test on the same xdist worker, where a patched
`time.sleep` or `set_msg` records its calls.
"""

import threading
import time

import pytest

# How long a test's own workers get to finish their exit after it returns.
WORKER_GRACE_S = 2.0


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


def _is_legacy_worker(thread):
    # Scorbot and SimulatedScorbot name theirs; a bare Thread(target=libcomm.execute) is "Thread-N (execute)".
    return thread.name.startswith("scorbot-") or thread.name.endswith(("(execute)", "(syncro)"))


@pytest.fixture(autouse=True)
def _no_leftover_workers():
    before = set(threading.enumerate())
    yield
    deadline = time.monotonic() + WORKER_GRACE_S
    started = [t for t in threading.enumerate() if t not in before and _is_legacy_worker(t)]
    for thread in started:
        thread.join(max(0.0, deadline - time.monotonic()))
    alive = [t.name for t in started if t.is_alive()]
    if alive:
        pytest.fail(f"the test left legacy worker threads running: {alive}; "
                    "disconnect the robot or send EXIT and join the worker")
