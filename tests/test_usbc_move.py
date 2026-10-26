"""tools/usbc_move/usbc_move.py against a fake library: no DLL is loaded, no USB, no arm."""

import importlib.util
import io
import threading
import unittest
from contextlib import redirect_stderr
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "tools" / "usbc_move" / "usbc_move.py"
spec = importlib.util.spec_from_file_location("usbc_move", TOOL)
move = importlib.util.module_from_spec(spec)
spec.loader.exec_module(move)

FORBIDDEN = ("Home", "SetHome", "SetJoint", "SetJoints", "MoveManual", "EnterManual", "CloseManual",
             "MoveLinear", "MoveTorque", "Impact", "OpenGripper", "CloseGripper")


class FakeExport:
    def __init__(self, action=None):
        self.action, self.calls = action, []

    def __call__(self, *args):
        self.calls.append(args)
        return self.action(*args) if self.action else 1


class FakeLib:
    def __init__(self, **actions):
        actions.setdefault("IsEmergency", lambda: 0)
        self.exports = {}
        for name, (mangled, _args, _ret) in move.SIGNATURES.items():
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


def position(before=(100, 0, 0), after=(130, 0, 0)):
    """GetCurrentPosition fake: the encoder reads `before` once, then `after` from then on."""
    reads = []

    def read(encoder, joint, xyz):
        reads.append(1)
        for index, value in enumerate(before if len(reads) == 1 else after):
            encoder.contents[index] = value
        return 1
    return read


class MoveTests(unittest.TestCase):
    def setUp(self):
        self._saved = (move.SETTLE_S, move.jog.CALL_S, move.home.STOP_CALL_S, move.MOVE_TIMEOUT_S)
        move.SETTLE_S, move.jog.CALL_S, move.home.STOP_CALL_S, move.MOVE_TIMEOUT_S = 0.002, 0.2, 0.2, 0.3

    def tearDown(self):
        move.SETTLE_S, move.jog.CALL_S, move.home.STOP_CALL_S, move.MOVE_TIMEOUT_S = self._saved

    def go(self, lib, console=None, axis="x", mm=5.0):
        log, state = Recorder(), {"busy": False, "stop_open": 0}
        code = move.make_action(axis, mm)(move.MoveDll(lib), log, console or FakeConsole(), state)
        return code, log, state

    def test_only_the_point_move_exports_are_bound(self):
        self.assertEqual(set(move.SIGNATURES), {"Initialization", "IsEmergency", "Control", "Stop", "CloseUSBC",
                                                "GetCurrentPosition", "DefineVector", "Teach", "MoveJoint"})
        self.assertFalse(set(FORBIDDEN) & set(move.SIGNATURES))

    def test_the_call_order_is_define_teach_move_then_the_position_delta(self):
        lib = FakeLib(GetCurrentPosition=position())
        code, log, state = self.go(lib, axis="y", mm=-5)
        self.assertEqual(code, 0)
        self.assertEqual(lib.calls("DefineVector"), [(ord("A"), b"OSC", 2)])
        (name, point, array, count, flag), = lib.calls("Teach")
        self.assertEqual((name, point, count, flag), (b"OSC", 1, 5, move.RELATIVE_FLAG))
        self.assertEqual(list(array.contents), [0, -5000, 0, 0, 0])      # micrometres, y only
        self.assertEqual(lib.calls("MoveJoint"), [(b"OSC", 1, None, 0)])
        names = log.names()
        self.assertLess(names.index("point_taught"), names.index("move_start"))
        self.assertLess(names.index("move_returned"), names.index("position_after"))
        self.assertEqual(log.first("position_delta")["encoder"][0], 30)
        self.assertFalse(state["busy"])

    def test_a_refused_define_or_teach_sends_no_move(self):
        for refuse in ("DefineVector", "Teach"):
            lib = FakeLib(**{refuse: lambda *args: 0})
            code, _, state = self.go(lib)
            self.assertEqual(code, 4)
            self.assertEqual(lib.calls("MoveJoint"), [])
            self.assertFalse(state["busy"])

    def test_a_refused_move_is_reported_and_leaves_the_session_clean(self):
        lib = FakeLib(MoveJoint=lambda *args: 0)
        code, log, state = self.go(lib)
        self.assertEqual(code, 7)
        self.assertFalse(log.first("move_returned")["ok"])
        self.assertFalse(state["busy"])

    def test_the_stop_key_calls_stop_and_ends_with_a_nonzero_code(self):
        release = threading.Event()
        lib = FakeLib(MoveJoint=lambda *args: release.wait(0.5) and 1)
        try:
            code, log, _ = self.go(lib, FakeConsole(stop_after=2))
        finally:
            release.set()
        self.assertEqual(code, 7)
        self.assertEqual(lib.calls("Stop"), [(ord("&"),)])
        self.assertIn("stop_requested", log.names())

    def test_a_move_that_runs_past_the_timeout_is_stopped_and_stays_busy_while_it_runs(self):
        release = threading.Event()
        lib = FakeLib(MoveJoint=lambda *args: release.wait(5))
        saved = move.home.STOP_GRACE_S
        move.home.STOP_GRACE_S = 0.05
        try:
            code, log, state = self.go(lib)
        finally:
            release.set()
            move.home.STOP_GRACE_S = saved
        self.assertEqual(code, 6)
        self.assertIn("move_timeout", log.names())
        self.assertEqual(len(lib.calls("Stop")), 1)
        self.assertTrue(state["busy"])

    def test_a_position_call_that_never_returns_leaves_the_session_busy(self):
        release = threading.Event()
        lib = FakeLib(GetCurrentPosition=lambda *args: release.wait(5))
        try:
            code, log, state = self.go(lib)
        finally:
            release.set()
        self.assertEqual(code, 8)
        self.assertTrue(state["busy"])
        self.assertIn("PHYSICAL STOP", state["busy_note"])
        self.assertEqual(lib.calls("MoveJoint"), [])

    def test_a_whole_simulated_run_turns_control_on_then_off(self):
        done_calls = []
        lib = FakeLib(Initialization=lambda mode, kind, done, error: (done_calls.append(mode), done(None), 1)[2],
                      GetCurrentPosition=position())
        log = Recorder()
        code = move.home.run(move.MoveDll(lib), log, True, FakeConsole(), action=move.make_action("z", 5))
        self.assertEqual((code, done_calls), (0, [2]))
        self.assertEqual([call[1] for call in lib.calls("Control")], [1, 0])
        self.assertEqual(log.names()[-1], "closed")


class ArgumentTests(unittest.TestCase):
    def refused(self, *argv):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            move.main(["--dll-dir", "x", *argv])
        self.assertEqual(caught.exception.code, 2)

    def test_axis_and_step_are_bounded(self):
        for bad in (("--axis", "w"), ("--axis", "x", "--mm", "0"), ("--axis", "x", "--mm", "20.5"),
                    ("--axis", "x", "--mm", "-21")):
            self.refused("--simulate", "--output", "o.jsonl", *bad)

    def test_a_run_needs_an_axis(self):
        self.refused("--simulate", "--output", "o.jsonl")


if __name__ == "__main__":
    unittest.main()
