"""tools/usbc_home/usbc_home.py against a fake library: no DLL is loaded, no USB."""

import ctypes
import importlib.util
import io
import json
import os
import tempfile
import threading
import unittest
import unittest.mock
import contextlib
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
    def test_simulated_run_order(self):
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

    def test_init_timeout(self):
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

    def test_init_refused(self):
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

    def test_no_home_skips_home(self):
        lib, dll = make_dll()
        log = Recorder("vendor_dll")
        self.assertEqual(tool.run(dll, log, False, FakeConsole(), do_home=False), 0)
        self.assertEqual(lib.exports["Home"].calls, [])
        self.assertEqual(lib.exports["Control"].calls, [(ord("&"), 1), (ord("&"), 0)])
        self.assertEqual(log.names(), ["init_done", "init_returned", "emergency", "control_on", "home_skipped",
                                       "control_off", "closed"])

    def test_control_on_failure_still_turns_off(self):
        def initialization(_mode, _system_type, on_done, _on_error):
            call_callback(on_done)
            return 1
        lib = FakeLib(Initialization=initialization, IsEmergency=lambda: 0, Control=lambda _g, on: 1 - on)
        self.assertEqual(tool.run(tool.VendorDll(lib), Recorder(), True, FakeConsole()), 4)
        self.assertEqual(lib.exports["Home"].calls, [])
        self.assertEqual(lib.exports["Control"].calls, [(ord("&"), 1), (ord("&"), 0)])   # a half-done on is switched off

    def test_control_off_failure_still_closes(self):
        def initialization(_mode, _system_type, on_done, _on_error):
            call_callback(on_done)
            return 1
        lib = FakeLib(Initialization=initialization, IsEmergency=lambda: 0, Control=lambda _g, on: on)
        log = Recorder()
        self.assertEqual(tool.run(tool.VendorDll(lib), log, True, FakeConsole()), 8)
        self.assertIn("control_off_failed", log.names())
        self.assertEqual(len(lib.exports["CloseUSBC"].calls), 1)

    def test_init_exception_still_closes(self):
        def initialization(*_args):
            raise RuntimeError("boom")
        lib = FakeLib(Initialization=initialization)
        with self.assertRaises(RuntimeError):
            tool.run(tool.VendorDll(lib), Recorder(), True, FakeConsole())
        self.assertEqual(len(lib.exports["CloseUSBC"].calls), 1)

    def test_ctrl_c_during_home(self):
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

    def test_blocked_stop_keeps_the_timeout(self):
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

    def test_ctrl_c_during_init(self):
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

    def test_interrupt_handler_failure(self):
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

    def test_running_stop_keeps_session_busy(self):
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

    def test_ctrl_c_during_stop(self):
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

    def test_hung_init(self):
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

    def test_control_off_failure_then_close_raises(self):
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

    def test_unstoppable_home_skips_cleanup(self):
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
    def test_log_is_strict_json_and_not_overwritten(self):
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
    def test_default_log_path(self):
        online = tool.default_output("home", False)
        self.assertEqual(os.path.dirname(online), "logs")
        self.assertRegex(os.path.basename(online), r"^home-\d{8}-\d{6}\.jsonl$")
        self.assertEqual(os.path.dirname(tool.default_output("home", True)), "rehearsal")

    def test_no_home_needs_online(self):
        with self.assertRaises(SystemExit):
            tool.main(["--dll-dir", ".", "--simulate", "--no-home", "--output", "x.jsonl"])


class FakeStdin:
    """What the pause reads: a terminal (or not) that yields these lines, then end of input."""

    def __init__(self, lines, tty=True, raises=None):
        self.lines = list(lines)
        self.tty = tty
        self.raises = raises

    def isatty(self):
        return self.tty

    def readline(self):
        if self.raises:
            raise self.raises
        return self.lines.pop(0) if self.lines else ""


class PauseBeforeInitTests(unittest.TestCase):
    """--pause-before-init stops after the DLL is loaded and before Initialization, so a hook can attach first.

    These run the real main() and the real run() against a fake library, so "never reaches the controller"
    means no export of the library was called at all.
    """

    def run_main(self, extra, stdin):
        lib, _ = make_dll()
        with tempfile.TemporaryDirectory() as folder, \
                unittest.mock.patch.object(tool, "load_library", return_value=(lib, str(Path(folder) / "USBC.dll"))), \
                unittest.mock.patch.object(tool, "file_facts", return_value={"sha256": "0" * 64}), \
                unittest.mock.patch("sys.stdin", stdin), \
                redirect_stdout(io.StringIO()) as out:
            code = tool.main(["--dll-dir", folder, "--simulate", "--output", str(Path(folder) / "log.jsonl")] + extra)
            events = [json.loads(line)["event"]
                      for line in (Path(folder) / "log.jsonl").read_text(encoding="utf-8").splitlines()]
        called = sorted(name for name, export in lib.exports.items() if export.calls)
        return code, called, events, out.getvalue()

    def assert_cancelled_before_the_controller(self, result):
        code, called, events, _ = result
        self.assertEqual((code, called), (1, []))                  # no export of the DLL was touched
        self.assertIn("pause_cancelled", events)
        self.assertNotIn("pause_released", events)
        self.assertNotIn("init_returned", events)

    def test_enter_releases_the_pause_and_initialization_follows_it(self):
        code, called, events, out = self.run_main(["--pause-before-init"], FakeStdin(["\n"]))
        self.assertEqual(code, 0)
        self.assertIn("Initialization", called)
        self.assertLess(events.index("dll"), events.index("paused"))
        self.assertLess(events.index("paused"), events.index("pause_released"))
        self.assertLess(events.index("pause_released"), events.index("init_returned"))
        self.assertIn(str(os.getpid()), out)

    def test_ctrl_c_at_the_pause_never_reaches_the_controller(self):
        self.assert_cancelled_before_the_controller(
            self.run_main(["--pause-before-init"], FakeStdin([], raises=KeyboardInterrupt())))

    def test_end_of_input_at_the_pause_never_reaches_the_controller(self):
        self.assert_cancelled_before_the_controller(self.run_main(["--pause-before-init"], FakeStdin([])))

    def test_text_that_ends_without_a_newline_does_not_release_the_pause(self):
        # input() returns such a partial line at end of input instead of raising, which once released the gate.
        self.assert_cancelled_before_the_controller(self.run_main(["--pause-before-init"], FakeStdin(["cancel"])))

    def test_typed_text_is_not_an_enter_press(self):
        self.assert_cancelled_before_the_controller(self.run_main(["--pause-before-init"], FakeStdin(["go\n"])))

    def test_input_that_is_not_a_terminal_never_releases_the_pause(self):
        self.assert_cancelled_before_the_controller(
            self.run_main(["--pause-before-init"], FakeStdin(["\n"], tty=False)))

    def test_without_the_flag_nothing_waits_and_stdin_is_not_read(self):
        code, called, events, _ = self.run_main([], FakeStdin([], raises=AssertionError("stdin was read")))
        self.assertEqual(code, 0)
        self.assertIn("Initialization", called)
        self.assertNotIn("paused", events)

    def test_the_pause_does_not_apply_to_probe(self):
        err = io.StringIO()
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(err):
            tool.main(["--dll-dir", ".", "--probe", "--pause-before-init"])
        self.assertIn("--pause-before-init only applies", err.getvalue())


class ControlOnHangTests(unittest.TestCase):
    def test_hung_control_on(self):
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
    def test_hung_control_off(self):
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
    def test_home_deadline_allows_a_slow_home(self):
        # scorbot/vendor_home_source.TOTAL_SECONDS (800 s) is the sum of every axis' max_time in both
        # passes; a shorter deadline here would Stop a healthy home part-way (found by review, 2026-10-11).
        self.assertGreaterEqual(tool.HOME_TIMEOUT_S, 800.0)


if __name__ == "__main__":
    unittest.main()
