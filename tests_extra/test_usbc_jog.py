"""tools/usbc_jog/usbc_jog.py against a fake library: no DLL is loaded, no USB, no arm."""

import importlib.util
import io
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

TOOL = Path(__file__).resolve().parent.parent / "tools" / "usbc_jog" / "usbc_jog.py"
spec = importlib.util.spec_from_file_location("usbc_jog", TOOL)
jog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jog)

FORBIDDEN = ("Home", "SetHome", "SetJoint", "SetJoints", "Impact", "MoveJoint", "MoveLinear", "MoveTorque",
             "Speed", "Velocity", "OpenGripper", "CloseGripper")


class FakeExport:
    def __init__(self, action=None):
        self.action = action
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        return self.action(*args) if self.action else 1


class FakeLib:
    def __init__(self, **actions):
        actions.setdefault("IsEmergency", lambda: 0)
        self.exports = {}
        for name, (mangled, _args, _ret) in jog.SIGNATURES.items():
            self.exports[name] = FakeExport(actions.get(name))
            setattr(self, mangled, self.exports[name])

    def calls(self, name):
        return self.exports[name].calls


class Recorder:
    source = "vendor_dll_simulated"

    def __init__(self):
        self.events = []

    def event(self, name, **fields):
        self.events.append((name, fields))

    def names(self):
        return [name for name, _ in self.events]

    def first(self, name):
        return next(fields for event, fields in self.events if event == name)


class FakeConsole:
    def __init__(self, stop_after=None):
        self.stop_after, self.polls = stop_after, 0

    def stop_pressed(self):
        self.polls += 1
        return self.stop_after is not None and self.polls >= self.stop_after


def moving_position():
    """GetCurrentPosition fake: the encoder of axis 0 reads 100 the first time and 130 after."""
    reads = []

    def read(encoder, joint, xyz):
        reads.append(1)
        encoder.contents[0] = 100 if len(reads) == 1 else 130
        return 1
    return read


class JogTests(unittest.TestCase):
    def setUp(self):
        self._saved = (jog.MOVE_PERIOD_S, jog.POLL_S, jog.CALL_S, jog.home.STOP_CALL_S)
        jog.MOVE_PERIOD_S, jog.POLL_S, jog.CALL_S, jog.home.STOP_CALL_S = 0.02, 0.002, 0.2, 0.2

    def tearDown(self):
        jog.MOVE_PERIOD_S, jog.POLL_S, jog.CALL_S, jog.home.STOP_CALL_S = self._saved

    def go(self, lib, console=None, seconds=0.1, axis=2, velocity=-10):
        log, state = Recorder(), {"busy": False, "stop_open": 0}
        code = jog.make_action(axis, velocity, seconds)(jog.JogDll(lib), log, console or FakeConsole(), state)
        return code, log, state

    def test_only_the_jog_exports_are_bound(self):
        self.assertEqual(set(jog.SIGNATURES), {"Initialization", "IsEmergency", "Control", "Stop", "CloseUSBC",
                                               "EnterManual", "MoveManual", "CloseManual", "GetCurrentPosition"})
        self.assertFalse(set(FORBIDDEN) & set(jog.SIGNATURES))

    def test_timed_jog_call_order(self):
        lib = FakeLib(GetCurrentPosition=moving_position())
        code, log, state = self.go(lib)
        self.assertEqual(code, 0)
        self.assertEqual(lib.calls("EnterManual"), [(0,)])
        moves = lib.calls("MoveManual")
        self.assertGreaterEqual(len(moves), 3)
        self.assertTrue(all(call == (2, -10) for call in moves))
        self.assertEqual(len(lib.calls("CloseManual")), 1)
        self.assertEqual(lib.calls("Stop"), [(ord("&"),)])
        names = log.names()
        self.assertLess(names.index("jog_start"), names.index("close_manual"))
        self.assertLess(names.index("close_manual"), names.index("stop_requested"))
        self.assertLess(names.index("stop_requested"), names.index("position_after"))
        self.assertEqual(log.first("position_delta")["encoder"][0], 30)
        self.assertFalse(state["busy"])

    def test_stop_key_ends_the_jog(self):
        lib = FakeLib()
        code, log, _ = self.go(lib, FakeConsole(stop_after=5), seconds=5)
        self.assertEqual(code, 7)
        self.assertEqual(log.first("jog_end")["reason"], "stop_key")
        self.assertEqual(len(lib.calls("CloseManual")), 1)
        self.assertEqual(len(lib.calls("Stop")), 1)

    def test_emergency_ends_the_jog(self):
        lib = FakeLib(IsEmergency=lambda: 1)
        code, log, _ = self.go(lib, seconds=5)
        self.assertEqual(code, 7)
        self.assertEqual(log.first("jog_end")["reason"], "emergency")
        self.assertEqual(lib.calls("MoveManual"), [])
        self.assertEqual(len(lib.calls("Stop")), 1)

    def test_a_dll_error_callback_ends_the_jog(self):
        log, state = Recorder(), {"busy": False, "stop_open": 0}
        lib = FakeLib()
        console = FakeConsole()
        original = console.stop_pressed

        def stop_pressed():
            log.event("dll_error_callback", code=1)     # what the DLL's error callback does
            return original()
        console.stop_pressed = stop_pressed
        code = jog.make_action(0, 10, 5)(jog.JogDll(lib), log, console, state)
        self.assertEqual((code, log.first("jog_end")["reason"]), (7, "dll_error"))

    def test_enter_manual_refused(self):
        lib = FakeLib(EnterManual=lambda kind: 0)
        code, log, state = self.go(lib)
        self.assertEqual(code, 4)
        self.assertEqual(lib.calls("MoveManual"), [])
        self.assertEqual(len(lib.calls("CloseManual")), 1)
        self.assertEqual(len(lib.calls("Stop")), 1)
        self.assertFalse(state["busy"])

    def test_hung_move_call(self):
        import threading
        release = threading.Event()
        lib = FakeLib(MoveManual=lambda axis, velocity: release.wait(5))
        try:
            code, log, state = self.go(lib)
        finally:
            release.set()
        self.assertEqual(code, 8)
        self.assertTrue(state["busy"])
        self.assertIn("jog_call_hang", log.names())
        self.assertIn("PHYSICAL STOP", state["busy_note"])

    def test_move_refused(self):
        lib = FakeLib(MoveManual=lambda axis, velocity: 0)
        code, log, _ = self.go(lib, seconds=5)
        self.assertEqual((code, log.first("jog_end")["reason"]), (7, "move_refused"))
        self.assertEqual(len(lib.calls("MoveManual")), 1)
        self.assertEqual(len(lib.calls("CloseManual")), 1)
        self.assertEqual(len(lib.calls("Stop")), 1)

    def test_close_manual_failure(self):
        lib = FakeLib(CloseManual=lambda: 0)
        code, log, state = self.go(lib)
        self.assertEqual(code, 8)
        self.assertTrue(state["busy"])
        self.assertIn("PHYSICAL STOP", state["busy_note"])

    def test_hung_close_manual(self):
        import threading
        release = threading.Event()
        lib = FakeLib(CloseManual=lambda: release.wait(5))
        try:
            code, log, state = self.go(lib)
        finally:
            release.set()
        self.assertEqual(code, 8)
        self.assertTrue(state["busy"])
        self.assertEqual(lib.calls("Stop"), [(ord("&"),)])      # Stop is still sent when CloseManual hangs

    def test_ctrl_c_cleans_up(self):
        class Interrupt(KeyboardInterrupt):
            pass
        console = FakeConsole()
        console.stop_pressed = lambda: (_ for _ in ()).throw(Interrupt())
        lib, log, state = FakeLib(), Recorder(), {"busy": False, "stop_open": 0}
        with self.assertRaises(Interrupt):
            jog.make_action(0, 10, 5)(jog.JogDll(lib), log, console, state)
        self.assertEqual((len(lib.calls("CloseManual")), len(lib.calls("Stop"))), (1, 1))
        self.assertFalse(state["busy"])

    def test_ctrl_c_with_failed_close_manual(self):
        console = FakeConsole()
        console.stop_pressed = lambda: (_ for _ in ()).throw(KeyboardInterrupt())
        lib, log, state = FakeLib(CloseManual=lambda: 0), Recorder(), {"busy": False, "stop_open": 0}
        with self.assertRaises(KeyboardInterrupt):
            jog.make_action(0, 10, 5)(jog.JogDll(lib), log, console, state)
        self.assertTrue(state["busy"])
        self.assertIn("CLOSEMANUAL FAILED", state["busy_note"])

    def test_stop_returns_false(self):
        lib = FakeLib(Stop=lambda group: 0)
        code, log, state = self.go(lib)
        self.assertEqual(code, 8)
        self.assertTrue(state["busy"])
        self.assertIn("STOP FAILED", state["busy_note"])

    def test_ctrl_c_during_a_move_call(self):
        import threading
        release, real = threading.Event(), jog.home.bounded
        lib = FakeLib(MoveManual=lambda axis, velocity: release.wait(5))

        def bounded(fn, seconds):       # Ctrl+C arrives while the MoveManual call is still inside the DLL
            finished, value, error = real(fn, 0.05)
            if not finished and lib.calls("MoveManual"):
                raise KeyboardInterrupt()
            return finished, value, error
        log, state = Recorder(), {"busy": False, "stop_open": 0}
        try:
            with mock.patch.object(jog.home, "bounded", bounded), self.assertRaises(KeyboardInterrupt):
                jog.make_action(0, 10, 5)(jog.JogDll(lib), log, FakeConsole(), state)
        finally:
            release.set()
        self.assertEqual((len(lib.calls("CloseManual")), len(lib.calls("Stop"))), (1, 1))
        self.assertTrue(state["busy"])
        self.assertIn("PHYSICAL STOP", state["busy_note"])

    def test_dll_error_in_the_last_move(self):
        import time
        log, state = Recorder(), {"busy": False, "stop_open": 0}

        def move(axis, velocity):       # the error callback fires just as the time runs out
            time.sleep(0.06)
            log.event("dll_error_callback", code=1)
            return 1
        lib = FakeLib(MoveManual=move)
        code = jog.make_action(0, 10, 0.05)(jog.JogDll(lib), log, FakeConsole(), state)
        self.assertEqual((code, log.first("jog_end")["reason"]), (7, "dll_error"))

    def test_dll_error_during_cleanup(self):
        log, state = Recorder(), {"busy": False, "stop_open": 0}
        lib = FakeLib(CloseManual=lambda: log.event("dll_error_callback", code=1) or 1)
        code = jog.make_action(0, 10, 0.05)(jog.JogDll(lib), log, FakeConsole(), state)
        self.assertEqual((code, log.first("jog_end")["reason"]), (7, "time"))

    def test_a_failed_first_position_read_moves_nothing(self):
        lib = FakeLib(GetCurrentPosition=lambda encoder, joint, xyz: 0)
        code, log, state = self.go(lib)
        self.assertEqual((code, log.first("jog_end")["reason"]), (4, "position_read_failed"))
        self.assertEqual((lib.calls("EnterManual"), lib.calls("MoveManual")), ([], []))
        self.assertNotIn("position_delta", log.names())
        self.assertFalse(state["busy"])

    def test_last_position_read_fails(self):
        reads = []

        def position(encoder, joint, xyz):
            reads.append(1)
            return 1 if len(reads) == 1 else 0
        lib = FakeLib(GetCurrentPosition=position)
        code, log, state = self.go(lib)
        self.assertEqual(code, 4)
        self.assertEqual(log.first("jog_end")["reason"], "time")
        self.assertNotIn("position_delta", log.names())
        self.assertFalse(state["busy"])

    def test_hung_position_read_after_stop(self):
        import threading
        release, reads = threading.Event(), []

        def position(encoder, joint, xyz):
            reads.append(1)
            if len(reads) > 1:
                release.wait(5)
            return 1
        lib = FakeLib(GetCurrentPosition=position)
        try:
            code, log, state = self.go(lib)
        finally:
            release.set()
        self.assertEqual(code, 0)
        self.assertFalse(state["busy"])
        self.assertIn("position_after_hang", log.names())

    def test_hung_first_position_read(self):
        import threading
        release = threading.Event()
        lib = FakeLib(GetCurrentPosition=lambda encoder, joint, xyz: release.wait(5))
        try:
            code, log, state = self.go(lib)
        finally:
            release.set()
        self.assertEqual(code, 8)
        self.assertTrue(state["busy"])
        self.assertEqual(len(lib.calls("GetCurrentPosition")), 1)
        self.assertEqual(len(lib.calls("Stop")), 1)

    def test_hung_stop(self):
        import threading
        release = threading.Event()
        lib = FakeLib(Stop=lambda group: release.wait(5))
        try:
            code, log, state = self.go(lib)
        finally:
            release.set()
        self.assertEqual(code, 8)
        self.assertTrue(state["busy"])
        self.assertIn("STOP DID NOT RETURN", state["busy_note"])

    def test_exception_before_the_loop(self):
        order = []
        lib = FakeLib(EnterManual=lambda kind: (_ for _ in ()).throw(RuntimeError("boom")),
                      CloseManual=lambda: order.append("close") or 1, Stop=lambda group: order.append("stop") or 1)
        with self.assertRaises(RuntimeError):
            self.go(lib)
        self.assertEqual(order, ["stop", "close"])

    def test_cleanup_order(self):
        order = []
        lib = FakeLib(CloseManual=lambda: order.append("close") or 1, Stop=lambda group: order.append("stop") or 1)
        self.go(lib, FakeConsole(stop_after=3), seconds=5)
        self.assertEqual(order, ["stop", "close"])
        order.clear()
        self.go(lib)
        self.assertEqual(order, ["close", "stop"])

    def test_simulated_run(self):
        done_calls = []
        lib = FakeLib(Initialization=lambda mode, kind, done, error: (done_calls.append(mode), done(None), 1)[2],
                      GetCurrentPosition=moving_position())
        log = Recorder()
        code = jog.home.run(jog.JogDll(lib), log, True, FakeConsole(),
                            action=jog.make_action(1, 10, 0.1))
        self.assertEqual(code, 0)
        self.assertEqual(done_calls, [2])
        self.assertEqual([call[1] for call in lib.calls("Control")], [1, 0])
        self.assertEqual(len(lib.calls("CloseUSBC")), 1)
        self.assertEqual(log.names()[-1], "closed")


class ArgumentTests(unittest.TestCase):
    def refused(self, *argv):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            jog.main(["--dll-dir", "x", *argv])
        self.assertEqual(caught.exception.code, 2)

    def test_axis_velocity_and_time_are_bounded(self):
        for bad in (("--axis", "3"), ("--axis", "5"), ("--axis", "0", "--velocity", "0"),
                    ("--axis", "0", "--velocity", "101"), ("--axis", "0", "--seconds", "0"),
                    ("--axis", "0", "--seconds", "5.1")):
            self.refused("--simulate", "--output", "o.jsonl", *bad)

    def test_a_run_needs_an_axis(self):
        self.refused("--simulate", "--output", "o.jsonl")


if __name__ == "__main__":
    unittest.main()
