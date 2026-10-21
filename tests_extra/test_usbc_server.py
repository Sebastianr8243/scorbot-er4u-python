"""tools/usbc_server and scorbot/bridge.Arm against a fake library: no DLL is loaded, no USB, no arm."""

import importlib.util
import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from scorbot import bridge

TOOL = Path(__file__).resolve().parent.parent / "tools" / "usbc_server" / "usbc_server.py"
spec = importlib.util.spec_from_file_location("usbc_server", TOOL)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
move, home = server.move, server.home

FORBIDDEN = ("SetHome", "SetJoint", "SetJoints", "MoveManual", "EnterManual", "MoveLinear", "MoveTorque", "Impact",
             "SetParameter", "SetEncoders")


class FakeExport:
    def __init__(self, action=None):
        self.action, self.calls = action, []

    def __call__(self, *args):
        self.calls.append(args)
        return self.action(*args) if self.action else 1


class FakeLib:
    def __init__(self, **actions):
        actions.setdefault("IsEmergency", lambda: 0)
        actions.setdefault("GetCurrentPosition", self._position)
        self.xyz = [300000, 0, 200000, -63000, 0]
        self.exports = {}
        for name, (mangled, _args, _ret) in server.SIGNATURES.items():
            self.exports[name] = FakeExport(actions.get(name))
            setattr(self, mangled, self.exports[name])

    def _position(self, encoder, joint, xyz):
        for index, value in enumerate(self.xyz):
            xyz.contents[index] = value
        encoder.contents[0] = self.xyz[0] // 100
        return 1

    def calls(self, name):
        return self.exports[name].calls


class Log:
    source = "vendor_dll_simulated"

    def __init__(self):
        self.events = []

    def event(self, name, **fields):
        json.dumps(fields, allow_nan=False)     # as strict as home.EventLog
        self.events.append((name, fields))

    def names(self):
        return [name for name, _ in self.events]


class Keys:
    def stop_pressed(self):
        return False


class Running:
    """The server's action on a thread, listening on a free port."""

    def __init__(self, lib, do_home=True):
        self._saved = (move.SETTLE_S, move.jog.CALL_S, home.STOP_CALL_S, move.MOVE_TIMEOUT_S, server.IDLE_POLL_S)
        move.SETTLE_S, move.jog.CALL_S, home.STOP_CALL_S, move.MOVE_TIMEOUT_S, server.IDLE_POLL_S = 0.002, 0.5, 0.5, 1.0, 0.02
        self.dir = tempfile.TemporaryDirectory()
        self.lib, self.log = lib, Log()
        self.state = {"busy": False, "stop_open": 0}
        ready = threading.Event()
        port = {}
        action = server.make_action(0, do_home, ready=lambda p: (port.update(p=p), ready.set()))
        self.result = {}
        self.thread = threading.Thread(
            target=lambda: self.result.update(code=action(server.ServerDll(lib), self.log, Keys(), self.state)),
            daemon=True)
        self.thread.start()
        assert ready.wait(5), "server did not start"
        self.port = port["p"]

    def arm(self):
        return bridge.Arm(port=self.port, timeout=5)

    def close(self):
        self.thread.join(5)
        (move.SETTLE_S, move.jog.CALL_S, home.STOP_CALL_S, move.MOVE_TIMEOUT_S, server.IDLE_POLL_S) = self._saved
        self.dir.cleanup()


class ServerTests(unittest.TestCase):
    def start(self, lib=None, **kwargs):
        running = Running(lib or FakeLib(), **kwargs)
        self.addCleanup(running.close)
        return running

    def test_only_the_listed_exports_are_bound(self):
        self.assertFalse(set(FORBIDDEN) & set(server.SIGNATURES))
        self.assertEqual(server.SIGNATURES["OpenGripper"][0], "?OpenGripper@@YAHXZ")

    def test_it_listens_on_localhost_only(self):
        self.assertEqual(server.HOST, "127.0.0.1")

    def test_it_homes_then_serves_and_quit_ends_with_zero(self):
        running = self.start()
        with running.arm() as arm:
            self.assertTrue(arm.ping()["homed"])
            arm.quit()
        running.thread.join(5)
        self.assertEqual(running.result["code"], 0)
        self.assertEqual(len(running.lib.calls("Home")), 1)

    def test_move_by_teaches_micrometres_relative_and_defines_the_vector_once(self):
        running = self.start()
        with running.arm() as arm:
            position = arm.move_by(dx=5, dz=-2.5)
            arm.move_by(dy=1)
            arm.quit()
        self.assertEqual(len(running.lib.calls("DefineVector")), 1)
        first, second = running.lib.calls("Teach")
        self.assertEqual(list(first[2].contents), [5000, 0, -2500, 0, 0])
        self.assertEqual(first[4], move.RELATIVE_FLAG)
        self.assertEqual(list(second[2].contents), [0, 1000, 0, 0, 0])
        self.assertEqual(len(running.lib.calls("MoveJoint")), 2)
        self.assertAlmostEqual(position.x, 300.0)       # the fake's xyz, read after the move

    def test_bad_steps_are_refused_without_a_dll_call(self):
        running = self.start()
        with running.arm() as arm:
            for fields in ({"dx": 0}, {"dx": 50.5}, {"dx": "5"}, {"dy": True}, {"dz": None}):
                with self.assertRaises(bridge.ArmError):
                    arm.send("move_by", **fields)
            with self.assertRaises(bridge.ArmError):
                arm.send("fly")
        with socket.create_connection(("127.0.0.1", running.port), timeout=5) as raw:
            raw.sendall(b'{"op": "move_by", "dx": NaN}\n{"op": "quit"}\n')     # Python's json reads NaN
            reader = raw.makefile("rb")
            self.assertIn("finite", json.loads(reader.readline())["error"])
            reader.readline()
        self.assertEqual(running.lib.calls("Teach"), [])

    def test_move_to_needs_a_home_and_has_no_box(self):
        running = self.start(do_home=False)
        with running.arm() as arm:
            with self.assertRaisesRegex(bridge.ArmError, "Home"):
                arm.move_to(300, 0, 100)
            arm.home()
            arm.move_to(310, 20, 100)
            arm.move_to(900, -500, 10)          # no box: only the DLL's own workspace decides
            arm.quit()
        (_name, _point, array, _count, flag), _second = running.lib.calls("Teach")
        self.assertEqual(flag, move.ABSOLUTE_FLAG)
        self.assertEqual(list(array.contents), [310000, 20000, 100000, -63000, 0])   # pitch and roll kept

    def test_a_stop_line_during_a_move_calls_stop(self):
        release = threading.Event()
        running = self.start(FakeLib(MoveJoint=lambda *args: release.wait(0.8) and 1))
        with running.arm() as arm:
            threading.Timer(0.1, arm.stop).start()
            with self.assertRaises(bridge.ArmError) as caught:
                arm.move_by(dx=5)
            release.set()
            self.assertEqual(caught.exception.reply["code"], 7)
            arm.quit()
        self.assertEqual(running.lib.calls("Stop"), [(ord("&"),)])

    def test_a_failed_move_says_what_the_code_means(self):
        running = self.start(FakeLib(MoveJoint=lambda *args: 0))
        with running.arm() as arm:
            with self.assertRaises(bridge.ArmError) as caught:
                arm.move_by(dx=5)
            arm.quit()
        self.assertEqual(caught.exception.reply["code"], 7)
        self.assertRegex(str(caught.exception), "move_by failed.*refused or stopped")

    def test_an_unknown_failure_code_is_still_reported(self):
        self.assertEqual(bridge.describe_failure("move_by", {"code": 99}), "move_by failed (code 99)")

    def test_the_emergency_stop_refuses_moves(self):
        running = self.start(FakeLib(IsEmergency=lambda: 1))
        with running.arm() as arm:
            with self.assertRaisesRegex(bridge.ArmError, "emergency"):
                arm.move_by(dx=5)
            with self.assertRaisesRegex(bridge.ArmError, "emergency"):
                arm.gripper(open=True)
            arm.quit()
        self.assertEqual(running.lib.calls("MoveJoint"), [])

    def test_gripper_and_speed_call_their_exports(self):
        running = self.start()
        with running.arm() as arm:
            arm.gripper(open=False)
            arm.gripper(open=True)
            arm.speed(20)
            with self.assertRaises(bridge.ArmError):
                arm.speed(150)
            arm.quit()
        self.assertEqual((len(running.lib.calls("CloseGripper")), len(running.lib.calls("OpenGripper"))), (1, 1))
        self.assertEqual(running.lib.calls("Speed"), [(ord("A"), 20)])

    def test_a_client_that_disconnects_leaves_the_session_for_the_next(self):
        running = self.start()
        with running.arm() as arm:
            arm.where()
        with running.arm() as arm:
            arm.move_by(dx=1)
            arm.quit()
        running.thread.join(5)
        self.assertEqual(running.result["code"], 0)
        self.assertEqual(len(running.lib.calls("Home")), 1)

    def test_a_move_call_that_never_returns_ends_serving_busy(self):
        release = threading.Event()
        saved = home.STOP_GRACE_S
        home.STOP_GRACE_S = 0.05
        self.addCleanup(lambda: setattr(home, "STOP_GRACE_S", saved))
        running = self.start(FakeLib(MoveJoint=lambda *args: release.wait(5)))
        with running.arm() as arm:
            with self.assertRaises(bridge.ArmError):
                arm.move_by(dx=5)
        running.thread.join(5)
        release.set()
        self.assertEqual(running.result["code"], 8)
        self.assertTrue(running.state["busy"])

    def test_odd_fields_cannot_end_the_session(self):
        running = self.start()
        with socket.create_connection(("127.0.0.1", running.port), timeout=5) as raw:
            reader = raw.makefile("rb")
            raw.sendall(b'{"op": "where", "name": 1, "event": 2}\n{"op": "move_by", "dx": NaN}\n')
            self.assertTrue(json.loads(reader.readline())["ok"])
            self.assertIn("finite", json.loads(reader.readline())["error"])
            raw.sendall(b'{"op": "ping", "id": 7}\n')
            self.assertEqual(json.loads(reader.readline())["id"], 7)
            raw.sendall(b'{"op": "quit"}\n')
            reader.readline()
        running.thread.join(5)
        self.assertEqual(running.result["code"], 0)

    def test_a_client_that_vanishes_mid_move_gets_the_arm_stopped(self):
        release = threading.Event()
        running = self.start(FakeLib(MoveJoint=lambda *args: release.wait(0.8) and 1))
        raw = socket.create_connection(("127.0.0.1", running.port), timeout=5)
        raw.sendall(b'{"op": "move_by", "dx": 5}\n')
        threading.Timer(0.1, raw.close).start()
        for _ in range(100):
            if running.lib.calls("Stop"):
                break
            threading.Event().wait(0.02)
        release.set()
        self.assertEqual(running.lib.calls("Stop"), [(ord("&"),)])
        with running.arm() as arm:      # the session is still there for the next client
            self.assertTrue(arm.ping()["homed"])
            arm.quit()

    def test_garbage_lines_get_an_error_reply_and_serving_goes_on(self):
        running = self.start()
        with socket.create_connection(("127.0.0.1", running.port), timeout=5) as raw:
            raw.sendall(b"not json\n[1]\n")
            reader = raw.makefile("rb")
            self.assertFalse(json.loads(reader.readline())["ok"])
            self.assertFalse(json.loads(reader.readline())["ok"])
            raw.sendall(b'{"op": "quit", "id": 1}\n')
            self.assertTrue(json.loads(reader.readline())["ok"])


if __name__ == "__main__":
    unittest.main()
