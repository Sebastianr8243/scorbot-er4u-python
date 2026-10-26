"""tools/usbc_home/usbc_home.py against a fake library: no DLL is loaded, no USB."""

import ctypes
import importlib.util
import io
import json
import os
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "tools" / "usbc_home" / "usbc_home.py"
spec = importlib.util.spec_from_file_location("usbc_home", TOOL)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class FakeExport:
    """Stands in for a ctypes function: takes argtypes/restype, records calls."""

    def __init__(self, action=None):
        self.action = action
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        return self.action(*args) if self.action else 1


class FakeLib:
    def __init__(self, **actions):
        self.exports = {}
        for name, (mangled, _args, _ret) in tool.SIGNATURES.items():
            self.exports[name] = FakeExport(actions.get(name))
            setattr(self, mangled, self.exports[name])


class FakeConsole:
    def __init__(self, stop_after=None):
        self.stop_after = stop_after
        self.polls = 0

    def stop_pressed(self):
        self.polls += 1
        return self.stop_after is not None and self.polls >= self.stop_after


class Recorder:
    def __init__(self, source="vendor_dll_simulated"):
        self.source = source
        self.events = []

    def event(self, name, **fields):
        self.events.append((name, fields))

    def names(self):
        return [name for name, _ in self.events]


def call_callback(callback, value=None):
    """Invoke a ctypes callback the way the DLL would: with a pointer to a byte."""
    cell = ctypes.c_ubyte(value or 0)
    callback(ctypes.addressof(cell) if value is not None else None)


def make_dll(emergency=0, init_calls_back=True, home_values=(0xFF, 1, 2, 3, 0x40), home_ok=1, on_home=None):
    def initialization(_mode, _system_type, on_done, _on_error):
        if init_calls_back:
            call_callback(on_done)
        return 1

    def home(_group, on_progress):
        for value in home_values:
            call_callback(on_progress, value)
        if on_home:
            on_home()
        return home_ok

    lib = FakeLib(Initialization=initialization, IsEmergency=lambda: emergency, Home=home)
    return lib, tool.VendorDll(lib)


class AllowlistTests(unittest.TestCase):
    def test_only_the_allowlisted_exports_are_bound(self):
        self.assertEqual(set(tool.ALLOWED), {"Initialization", "IsEmergency", "Control", "Home", "Stop", "CloseUSBC"})
        for name in ("MoveManual", "SetJoint", "MoveTorque", "MoveLinear", "SetHome", "IsOnlineOk"):
            self.assertNotIn(name, tool.SIGNATURES)
            self.assertFalse(any(name in mangled for mangled, _a, _r in tool.SIGNATURES.values()))

    def test_a_dll_missing_an_export_is_refused_by_name(self):
        lib = FakeLib()
        delattr(lib, tool.SIGNATURES["Home"][0])
        with self.assertRaises(tool.ToolError) as caught:
            tool.VendorDll(lib)
        self.assertIn("Home", str(caught.exception))


class SequenceTests(unittest.TestCase):
    def test_simulated_run_goes_init_control_home_control_off_close(self):
        lib, dll = make_dll()
        log = Recorder()
        self.assertEqual(tool.run(dll, log, True, FakeConsole()), 0)
        self.assertEqual(log.names(), ["init_done", "init_returned", "emergency", "control_on", "home_start"]
                         + ["home_progress"] * 5 + ["home_returned", "control_off", "closed"])
        self.assertEqual(lib.exports["Initialization"].calls[0][:2], (tool.INIT_SIMULATE, tool.ER4USB_SYSTEM_TYPE))
        self.assertEqual(lib.exports["Control"].calls, [(ord("&"), 1), (ord("&"), 0)])
        self.assertEqual(lib.exports["Home"].calls[0][0], ord("A"))

    def test_online_run_forces_online_mode(self):
        lib, dll = make_dll()
        tool.run(dll, Recorder("vendor_dll"), False, FakeConsole())
        self.assertEqual(lib.exports["Initialization"].calls[0][0], tool.INIT_ONLINE)

    def test_progress_values_are_decoded(self):
        _lib, dll = make_dll()
        log = Recorder()
        tool.run(dll, log, True, FakeConsole())
        meanings = [f["meaning"] for n, f in log.events if n == "home_progress"]
        self.assertEqual(meanings, ["homing started", "axis 1", "axis 2", "axis 3", "homing ended"])

    def test_no_init_callback_times_out_without_homing_or_control(self):
        lib, dll = make_dll(init_calls_back=False)
        old, tool.INIT_TIMEOUT_S = tool.INIT_TIMEOUT_S, 0.05
        try:
            log = Recorder()
            self.assertEqual(tool.run(dll, log, True, FakeConsole()), 2)
        finally:
            tool.INIT_TIMEOUT_S = old
        self.assertEqual(log.names(), ["init_returned", "init_timeout", "closed"])
        self.assertEqual(lib.exports["Home"].calls, [])
        self.assertEqual(lib.exports["Control"].calls, [])

    def test_initialization_returning_zero_is_refused_at_once(self):
        lib = FakeLib(Initialization=lambda _m, _s, _done, _err: 0)
        log = Recorder()
        self.assertEqual(tool.run(tool.VendorDll(lib), log, True, FakeConsole()), 2)
        self.assertEqual(log.names(), ["init_returned", "init_refused", "closed"])
        self.assertEqual(lib.exports["Control"].calls, [])

    def test_an_active_emergency_stops_before_control_on(self):
        lib, dll = make_dll(emergency=1)
        self.assertEqual(tool.run(dll, Recorder(), True, FakeConsole()), 3)
        self.assertEqual(lib.exports["Control"].calls, [])
        self.assertEqual(lib.exports["Home"].calls, [])

    def test_no_home_connects_and_controls_but_never_calls_home(self):
        lib, dll = make_dll()
        log = Recorder("vendor_dll")
        self.assertEqual(tool.run(dll, log, False, FakeConsole(), do_home=False), 0)
        self.assertEqual(lib.exports["Home"].calls, [])
        self.assertEqual(lib.exports["Control"].calls, [(ord("&"), 1), (ord("&"), 0)])
        self.assertEqual(log.names(), ["init_done", "init_returned", "emergency", "control_on", "home_skipped",
                                       "control_off", "closed"])

    def test_control_that_will_not_turn_on_still_gets_a_control_off(self):
        def initialization(_mode, _system_type, on_done, _on_error):
            call_callback(on_done)
            return 1
        lib = FakeLib(Initialization=initialization, IsEmergency=lambda: 0, Control=lambda _g, on: 1 - on)
        self.assertEqual(tool.run(tool.VendorDll(lib), Recorder(), True, FakeConsole()), 4)
        self.assertEqual(lib.exports["Home"].calls, [])
        self.assertEqual(lib.exports["Control"].calls, [(ord("&"), 1), (ord("&"), 0)])   # a half-done on is switched off

    def test_a_failed_control_off_is_reported_and_close_still_runs(self):
        def initialization(_mode, _system_type, on_done, _on_error):
            call_callback(on_done)
            return 1
        lib = FakeLib(Initialization=initialization, IsEmergency=lambda: 0, Control=lambda _g, on: on)
        log = Recorder()
        self.assertEqual(tool.run(tool.VendorDll(lib), log, True, FakeConsole()), 8)
        self.assertIn("control_off_failed", log.names())
        self.assertEqual(len(lib.exports["CloseUSBC"].calls), 1)

    def test_an_exception_in_initialization_still_closes_the_dll(self):
        def initialization(*_args):
            raise RuntimeError("boom")
        lib = FakeLib(Initialization=initialization)
        with self.assertRaises(RuntimeError):
            tool.run(tool.VendorDll(lib), Recorder(), True, FakeConsole())
        self.assertEqual(len(lib.exports["CloseUSBC"].calls), 1)

    def test_ctrl_c_while_home_runs_requests_stop_and_skips_cleanup_if_it_is_still_running(self):
        release = threading.Event()
        lib, dll = make_dll(on_home=lambda: release.wait(5))

        class Interrupting(FakeConsole):
            def stop_pressed(self):
                raise KeyboardInterrupt

        old, tool.STOP_GRACE_S = tool.STOP_GRACE_S, 0.05
        try:
            log = Recorder()
            with self.assertRaises(KeyboardInterrupt):
                tool.run(dll, log, True, Interrupting())
        finally:
            tool.STOP_GRACE_S = old
            release.set()
        self.assertEqual(len(lib.exports["Stop"].calls), 1)
        self.assertIn("home_interrupted", log.names())
        self.assertIn("shutdown_skipped", log.names())
        self.assertEqual(lib.exports["CloseUSBC"].calls, [])

    def test_a_stop_that_blocks_inside_the_dll_does_not_hide_the_timeout_path(self):
        release = threading.Event()
        lib, dll = make_dll(on_home=lambda: release.wait(5))
        lib.exports["Stop"].action = lambda _group: release.wait(5)
        old = tool.HOME_TIMEOUT_S, tool.STOP_GRACE_S, tool.STOP_CALL_S
        tool.HOME_TIMEOUT_S, tool.STOP_GRACE_S, tool.STOP_CALL_S = 0.05, 0.05, 0.05
        try:
            log = Recorder()
            self.assertEqual(tool.run(dll, log, True, FakeConsole()), 6)
        finally:
            tool.HOME_TIMEOUT_S, tool.STOP_GRACE_S, tool.STOP_CALL_S = old
            release.set()
        self.assertIn(("stop_requested", {"ok": None, "returned": False}), log.events)
        self.assertIn("shutdown_skipped", log.names())

    def test_ctrl_c_while_waiting_for_initialization_does_not_close_under_it(self):
        release = threading.Event()
        lib = FakeLib(Initialization=lambda *_a: release.wait(5))
        original = tool.bounded

        def interrupted(fn, seconds):
            threading.Thread(target=fn, daemon=True).start()     # the DLL call is running...
            raise KeyboardInterrupt                              # ...when Ctrl+C arrives
        tool.bounded = interrupted
        try:
            with self.assertRaises(KeyboardInterrupt):
                tool.run(tool.VendorDll(lib), Recorder(), True, FakeConsole())
        finally:
            tool.bounded = original
            release.set()
        self.assertEqual(lib.exports["CloseUSBC"].calls, [])
        self.assertEqual(lib.exports["Control"].calls, [])

    def test_a_failure_inside_the_interrupt_handler_still_skips_cleanup(self):
        release = threading.Event()
        lib, dll = make_dll(on_home=lambda: release.wait(5))
        lib.exports["Stop"].action = lambda _group: (_ for _ in ()).throw(RuntimeError("stop failed"))

        class Interrupting(FakeConsole):
            def stop_pressed(self):
                raise KeyboardInterrupt

        old, tool.STOP_GRACE_S = tool.STOP_GRACE_S, 0.05
        try:
            with self.assertRaises(KeyboardInterrupt):
                tool.run(dll, Recorder(), True, Interrupting())
        finally:
            tool.STOP_GRACE_S = old
            release.set()
        self.assertEqual(lib.exports["CloseUSBC"].calls, [])

    def test_a_stop_still_running_in_the_dll_keeps_the_session_busy_even_if_home_finishes(self):
        release = threading.Event()
        lib, dll = make_dll(on_home=lambda: release.wait(0.3))      # Home ends on its own shortly after Stop
        lib.exports["Stop"].action = lambda _group: release.wait(5)  # but Stop never returns
        old = tool.STOP_CALL_S
        tool.STOP_CALL_S = 0.05
        try:
            log = Recorder()
            tool.run(dll, log, True, FakeConsole(stop_after=1))
        finally:
            tool.STOP_CALL_S = old
            release.set()
        self.assertIn("shutdown_skipped", log.names())
        self.assertEqual(lib.exports["CloseUSBC"].calls, [])
        self.assertEqual(lib.exports["Control"].calls, [(ord("&"), 1)])

    def test_ctrl_c_while_waiting_on_stop_keeps_the_session_busy(self):
        release = threading.Event()
        lib, dll = make_dll(on_home=lambda: release.wait(0.2))     # Home ends by itself shortly after
        lib.exports["Stop"].action = lambda _group: release.wait(5)
        original = tool.bounded
        calls = []

        def interrupted_on_stop(fn, seconds):
            calls.append(1)
            if len(calls) == 1:                                    # the Initialization call: normal
                return original(fn, seconds)
            threading.Thread(target=fn, daemon=True).start()       # the Stop call is running...
            raise KeyboardInterrupt                                # ...when a second Ctrl+C arrives
        tool.bounded = interrupted_on_stop
        try:
            with self.assertRaises(KeyboardInterrupt):
                tool.run(dll, Recorder(), True, FakeConsole(stop_after=1))
        finally:
            tool.bounded = original
            release.set()
        self.assertEqual(lib.exports["CloseUSBC"].calls, [])
        self.assertEqual(lib.exports["Control"].calls, [(ord("&"), 1)])

    def test_a_hung_initialization_is_reported_and_nothing_else_is_called(self):
        release = threading.Event()
        lib = FakeLib(Initialization=lambda *_a: release.wait(5))
        old, tool.INIT_TIMEOUT_S = tool.INIT_TIMEOUT_S, 0.05
        try:
            log = Recorder()
            self.assertEqual(tool.run(tool.VendorDll(lib), log, True, FakeConsole()), 2)
        finally:
            tool.INIT_TIMEOUT_S = old
            release.set()
        self.assertIn("init_hang", log.names())
        self.assertEqual(lib.exports["Control"].calls, [])
        self.assertEqual(lib.exports["CloseUSBC"].calls, [])

    def test_control_off_failure_is_reported_before_a_close_that_raises(self):
        def initialization(_mode, _system_type, on_done, _on_error):
            call_callback(on_done)
            return 1

        def close(*_a):
            raise RuntimeError("close failed")
        lib = FakeLib(Initialization=initialization, IsEmergency=lambda: 0, Control=lambda _g, on: on,
                      CloseUSBC=close)
        log = Recorder()
        with self.assertRaises(RuntimeError):
            tool.run(tool.VendorDll(lib), log, True, FakeConsole(), do_home=False)
        self.assertIn("control_off_failed", log.names())

    def test_a_home_that_will_not_stop_skips_control_off_and_close(self):
        release = threading.Event()
        lib, dll = make_dll(on_home=lambda: release.wait(5))
        old = tool.HOME_TIMEOUT_S, tool.STOP_GRACE_S
        tool.HOME_TIMEOUT_S, tool.STOP_GRACE_S = 0.05, 0.05
        try:
            log = Recorder()
            self.assertEqual(tool.run(dll, log, True, FakeConsole()), 6)
        finally:
            tool.HOME_TIMEOUT_S, tool.STOP_GRACE_S = old
            release.set()
        self.assertIn("shutdown_skipped", log.names())
        self.assertEqual(lib.exports["Control"].calls, [(ord("&"), 1)])      # never switched off under a running Home
        self.assertEqual(lib.exports["CloseUSBC"].calls, [])
        self.assertEqual(len(lib.exports["Stop"].calls), 1)

    def test_a_failed_home_is_a_nonzero_exit(self):
        _lib, dll = make_dll(home_ok=0)
        self.assertEqual(tool.run(dll, Recorder(), True, FakeConsole()), 7)

    def test_stop_key_calls_stop_and_reports_it(self):
        release = threading.Event()
        lib, dll = make_dll(on_home=lambda: release.wait(2))
        lib.exports["Stop"].action = lambda _group: (release.set(), 1)[1]
        log = Recorder()
        self.assertEqual(tool.run(dll, log, True, FakeConsole(stop_after=2)), 7)
        self.assertEqual(lib.exports["Stop"].calls, [(ord("&"),)])
        self.assertIn("stop_requested", log.names())


class FingerprintTests(unittest.TestCase):
    def test_an_unknown_build_is_logged_and_allowed(self):
        log = Recorder()
        with redirect_stdout(io.StringIO()):
            for sha in tool.KNOWN_DLL_SHA256:
                tool.check_fingerprint(sha, log)
            self.assertEqual(log.names(), [])
            tool.check_fingerprint("0" * 64, log)
        self.assertEqual(log.names(), ["unknown_dll"])


class LogTests(unittest.TestCase):
    def test_log_is_strict_json_with_its_source_and_refuses_to_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "run.jsonl")
            log = tool.EventLog(path, "vendor_dll_simulated")
            log.event("x", value=1)
            log.close()
            with open(path, encoding="utf-8") as handle:
                line = json.loads(handle.read())
            self.assertEqual((line["event"], line["source"], line["value"]), ("x", "vendor_dll_simulated", 1))
            with self.assertRaises(FileExistsError):
                tool.EventLog(path, "vendor_dll")

    def test_log_creates_a_missing_folder(self):
        # A fresh clone on the lab PC has no logs\ folder; the first run used to crash on it.
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "logs", "run.jsonl")
            log = tool.EventLog(path, "vendor_dll_simulated")
            log.close()
            self.assertTrue(os.path.isfile(path))


class ArgumentTests(unittest.TestCase):
    def test_the_default_log_goes_to_logs_or_rehearsal_with_a_time_stamp(self):
        online = tool.default_output("home", False)
        self.assertEqual(os.path.dirname(online), "logs")
        self.assertRegex(os.path.basename(online), r"^home-\d{8}-\d{6}\.jsonl$")
        self.assertEqual(os.path.dirname(tool.default_output("home", True)), "rehearsal")

    def test_no_home_needs_online(self):
        with self.assertRaises(SystemExit):
            tool.main(["--dll-dir", ".", "--simulate", "--no-home", "--output", "x.jsonl"])


class ControlOnHangTests(unittest.TestCase):
    def test_a_control_on_that_never_returns_is_reported_and_nothing_runs_after_it(self):
        release = threading.Event()
        lib, dll = make_dll()
        lib.exports["Control"].action = lambda _group, on: (release.wait(5), 1)[1] if on else 1
        old = tool.CONTROL_ON_CALL_S
        tool.CONTROL_ON_CALL_S = 0.05
        try:
            log = Recorder()
            code = tool.run(dll, log, True, FakeConsole())
        finally:
            tool.CONTROL_ON_CALL_S = old
            release.set()
        self.assertEqual(code, 8)
        self.assertIn("control_on_hang", log.names())
        self.assertEqual(lib.exports["Home"].calls, [])           # Home never started
        self.assertEqual(lib.exports["CloseUSBC"].calls, [])      # and the DLL was not closed under a running call


class ControlOffHangTests(unittest.TestCase):
    def test_a_control_off_that_never_returns_is_reported_and_not_closed_over(self):
        release = threading.Event()
        lib, dll = make_dll()
        lib.exports["Control"].action = lambda _group, on: 1 if on else (release.wait(5), 1)[1]
        old = tool.CONTROL_OFF_CALL_S
        tool.CONTROL_OFF_CALL_S = 0.05
        try:
            log = Recorder()
            code = tool.run(dll, log, True, FakeConsole())
        finally:
            tool.CONTROL_OFF_CALL_S = old
            release.set()
        self.assertEqual(code, 8)
        self.assertIn("control_off_hang", log.names())
        self.assertEqual(lib.exports["CloseUSBC"].calls, [])      # never closed under a running DLL call


class DeadlineTests(unittest.TestCase):
    def test_the_home_deadline_covers_a_slow_but_healthy_vendor_home(self):
        # scorbot/vendor_home_source.TOTAL_SECONDS (800 s) is the sum of every axis' max_time in both
        # passes; a shorter deadline here would Stop a healthy home part-way (found by review, 2026-10-11).
        self.assertGreaterEqual(tool.HOME_TIMEOUT_S, 800.0)


if __name__ == "__main__":
    unittest.main()
