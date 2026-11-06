"""Pytest-only safety net for tests/ and tests_extra/: the suite never reaches the real arm.

Real USB discovery raises. Tests that stand in a fake usb package through
sys.modules are not affected; anything that reaches the real pyusb is. It does
not cover a ctypes DLL load or a plain `python -m unittest` run (no conftest there).

A child process escapes that backstop, so starting a hardware tool with ``--online`` (``bring_up.py``,
``lab_day.py``, ``read_timeout_check.py``, ``python -m scorbot.lab``, ``scorbot.transport.probe`` or the ``scorbot``
CLI) through ``subprocess`` fails the test before the process starts. ``--simulate`` children are untouched.

A test that leaves a legacy worker thread running fails: the worker keeps sending
and sleeping into the next test on the same xdist worker, where a patched
`time.sleep` or `set_msg` records its calls.
"""

import os
import subprocess
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


HARDWARE_TOOLS = ("bring_up.py", "lab_day.py", "read_timeout_check.py", "scorbot.lab", "scorbot.transport.probe")


def _hardware_online(args):
    """Whether a subprocess argv would start one of our hardware tools on the real controller."""
    words = args.split() if isinstance(args, (str, bytes)) else [os.fsdecode(a) for a in args]
    words = [os.fsdecode(w).replace("\\", "/") for w in words]
    if "--online" not in words:
        return False
    names = [w.rsplit("/", 1)[-1] for w in words]
    return any(n in HARDWARE_TOOLS or n in ("scorbot", "scorbot.exe") for n in names)


@pytest.fixture(autouse=True)
def _block_online_children(monkeypatch):
    real_init = subprocess.Popen.__init__

    def guarded(self, args, *rest, **kwargs):
        if _hardware_online(args):
            raise AssertionError(f"a test started a hardware tool with --online (conftest.py backstop): {args!r}")
        real_init(self, args, *rest, **kwargs)

    monkeypatch.setattr(subprocess.Popen, "__init__", guarded)


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
