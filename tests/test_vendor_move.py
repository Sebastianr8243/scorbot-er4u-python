"""``vendor_move.setpoints`` against the lab USBC.dll's own ``MoveJoint`` setpoints on the pipe fake.

The golden fixture (``fixtures/dll_lab_move_joint.json``, made by ``scripts/make_move_joint_fixture.py``) holds
slots 0-4 of every message of 26 recorded moves: base alone at speeds 1 to 100, shoulder, elbow, pitch and roll,
all five joints at once, and a ``Time(3000)`` move. Each must come out of the pure function message for message from the first change to the last. The lead
(messages that still hold the start counts) and the total message count are the model's own and cannot be
seen on the wire: they are pinned here as a regression check, not as evidence.
The fake arm is our model, so this says what the DLL sends, not how the arm follows it.
"""

import ast
import json
import math
import time
import unittest
from pathlib import Path
from unittest import mock

from scorbot import Scorbot, ScorbotError, MotionStopped, vendor_home as vh, vendor_move as vm
from scorbot.lab.dry_link import DryScorbot
from scorbot.vendor_home_source import VendorHomeSource

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "dll_lab_move_joint.json").read_text(encoding="utf-8"))
MOVES = FIXTURE["moves"]


def starts():
    """Each move's start and target in the DLL's joint radians: home after a Home, else the previous target."""
    start = None
    for move in MOVES:
        target = vm.joints_from_millidegrees(move["target_millideg"])
        if move["start"] == "home":
            start = vm.home_joints()
        yield move, start, target
        start = target


class Setpoints(unittest.TestCase):
    def test_every_recorded_move_message_for_message(self):
        self.assertEqual(len(MOVES), 26)
        for move, start, target in starts():
            with self.subTest(move["recording"], label=move["label"], speed=move["speed_percent"]):
                sent = vm.setpoints(start, target, move["total_ms"])
                self.assertEqual(len(sent), move["messages"])
                self.assertTrue(all(s == sent[0] for s in sent[:move["lead"]]))
                body = [list(s) for s in sent[move["lead"]:]]
                recorded = move["setpoints"]
                self.assertEqual(body[:len(recorded)], recorded)
                self.assertTrue(all(s == recorded[-1] for s in body[len(recorded):]))   # then it holds
                self.assertEqual(sent[-1], tuple(recorded[-1]))

    def test_one_tick_more_or_less_does_not_fit(self):
        move, start, target = next(starts())
        for total_ms in (move["total_ms"] - 24, move["total_ms"] + 24):
            body = [list(s) for s in vm.setpoints(start, target, total_ms)[move["lead"]:]]
            self.assertNotEqual(body[:len(move["setpoints"])], move["setpoints"])

    def test_the_last_message_is_the_target_and_the_first_the_start(self):
        start, target = vm.home_joints(), vm.joints_from_millidegrees([20000, -115278, 90021, 98805, 20000])
        sent = vm.setpoints(start, target, 1623)
        self.assertEqual(sent[0], (0, 0, 0, 0, 0))
        self.assertEqual(sent[-1], tuple(MOVES[18]["setpoints"][-1]))

    def test_bad_arguments_are_refused(self):
        start = vm.home_joints()
        for total_ms in (0, -5, 1.5, True):
            with self.assertRaises(ValueError):
                vm.setpoints(start, start, total_ms)
        with self.assertRaises(ValueError):
            vm.setpoints(start[:4], start, 100)
        with self.assertRaises(ValueError):
            vm.joints_from_millidegrees([0, 0, 0])


class Duration(unittest.TestCase):
    def test_time_mode_is_the_time_given(self):
        timed = [m for m in MOVES if m["time_ms"] is not None]
        self.assertEqual([(m["time_ms"], m["total_ms"]) for m in timed], [(3000, 3000)])

    def test_the_acceleration_check_sets_fast_moves_and_bounds_all(self):
        for move, start, target in starts():
            with self.subTest(move["label"], speed=move["speed_percent"]):
                bound = vm.accel_bound_ms(start, target)
                self.assertEqual(bound, move["accel_bound_ms"])
                self.assertGreaterEqual(move["total_ms"], bound)
                if move["time_ms"] is None and (move["speed_percent"] or 0) >= 80:
                    self.assertEqual(move["total_ms"], bound)
        self.assertEqual(sum(m["total_ms"] == m["accel_bound_ms"] for m in MOVES), 15)

    def test_no_motor_moving_has_no_duration(self):
        self.assertEqual(vm.accel_bound_ms(vm.home_joints(), vm.home_joints()), 0)


class OptIn(unittest.TestCase):
    def test_no_other_module_imports_it(self):
        users = []
        for path in (ROOT / "scorbot").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imports = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
                       and (getattr(n, "module", "") or "").endswith("vendor_move")
                       or isinstance(n, (ast.Import, ast.ImportFrom))
                       and any(a.name.split(".")[-1] == "vendor_move" for a in n.names)]
            for node in imports:
                users.append(path.name)
                method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                              and n.name == "move_joint_vendor")
                self.assertIn(node, list(ast.walk(method)), "only the opt-in method may import the profile")
        self.assertEqual(users, ["robot.py"])


class Wiring(unittest.TestCase):
    def setUp(self):
        real_sleep = time.sleep
        patch = mock.patch("time.sleep", lambda seconds: real_sleep(seconds / 1000))
        patch.start()
        self.addCleanup(patch.stop)
        self.robot = DryScorbot(command_timeout=30).connect()
        self.addCleanup(self.robot.disconnect)
        self.robot.STOP_SETTLE_INTERVAL_S = 0.001
        self.robot.enable()

    def home(self):
        self.robot.vendor_home(operator_at_stop=True, jaws_empty=True, wrist_clear=True)

    def move(self, target, total_ms):
        packets = []
        send = VendorHomeSource._send

        def record(out, sequence):
            step = send(out, sequence)
            packets.append(step[1])
            return step

        begin = len(self.robot.dry_link.sent)
        with mock.patch.object(VendorHomeSource, "_send", side_effect=record):
            result = self.robot.move_joint_vendor(target, total_ms=total_ms)
        wire = self.robot.dry_link.sent[begin:]
        index = wire.index(packets[0])
        self.assertEqual(wire[index:index + len(packets)], packets)
        return packets, result

    def refused(self, target=None, total_ms=3000, error=ScorbotError):
        before = len(self.robot.dry_link.sent)
        with mock.patch.object(self.robot, "_command", wraps=self.robot._command) as command:
            with self.assertRaises(error):
                self.robot.move_joint_vendor(vm.home_joints() if target is None else target, total_ms=total_ms)
        command.assert_not_called()
        # Idle status polls are allowed, but no command or changed setpoint may be sent.
        packets = self.robot.dry_link.sent[before:]
        self.assertTrue(all(p[4] == vh.NOP for p in packets))
        self.assertEqual(len({p[12:44] for p in packets}), min(1, len(packets)))
        self.assertTrue(self.robot._commands.empty())

    def test_fixture_messages_and_consecutive_targets(self):
        self.home()
        start = vm.home_joints()
        # Recording c has consecutive moves through the five-joint move; b ends with Time(3000).
        selected = [(m, a, b) for m, a, b in starts() if m["recording"] == "c"]
        selected += [(m, a, b) for m, a, b in starts() if m["recording"] == "b"]
        for move, expected_start, target in selected:
            if move["start"] == "home":
                self.home()
                start = vm.home_joints()
            self.assertEqual(start, expected_start)
            with self.subTest(label=move["label"], time_ms=move["time_ms"]):
                packets, _ = self.move(target, move["total_ms"])
                slots = [tuple(int.from_bytes(p[12 + 4*n:16 + 4*n], "little", signed=True)
                               for n in range(5)) for p in packets]
                self.assertEqual(slots, vm.setpoints(start, target, move["total_ms"]))
                recorded = move["setpoints"]
                self.assertEqual([list(s) for s in slots[move["lead"]:move["lead"] + len(recorded)]], recorded)
                self.assertTrue(all(s == tuple(recorded[-1]) for s in slots[move["lead"] + len(recorded):]))
                self.assertTrue(all(p[4:12] == bytes([vh.NOP]) + bytes(7) for p in packets))
                self.assertTrue(all(p[32:44] == bytes(12) for p in packets))
                # The sync worker must continue holding the target after the source finishes.
                self.robot.get_state(after_index=self.robot.get_state().packet_index + 2)
                self.assertEqual(self.robot.dry_link.sent[-1][12:44], packets[-1][12:44])
            start = target

    def test_refusals_send_nothing(self):
        with self.assertRaises(ScorbotError):
            Scorbot().move_joint_vendor(vm.home_joints(), total_ms=3000)
        self.refused()  # no Home
        self.robot._homed, self.robot._gripper_homed = True, False
        self.refused()  # legacy Home
        self.home()
        self.robot._enabled = False
        self.refused()
        self.robot._enabled = True
        self.robot._stream = object()
        self.refused()
        self.robot._stream = None
        for total_ms in (0, -1, True, 1.5):
            self.refused(total_ms=total_ms, error=ValueError)
        target = vm.joints_from_millidegrees([20000, -120278, 95021, 88805, 0])
        self.refused(target, vm.accel_bound_ms(vm.home_joints(), target) - 1, ValueError)
        for index in range(5):
            target = list(vm.home_joints())
            target[index] = math.radians(1000)
            self.refused(target, error=ValueError)
        for target in ([0]*4, [0]*6, [float("nan")]*5, [True]*5):
            self.refused(target, error=ValueError)
        self.robot.request_stop()
        self.refused(error=MotionStopped)

    def test_other_motion_invalidates_until_home(self):
        for motion in (lambda: self.robot.jog_joint("base", 1), self.robot.open_gripper,
                       lambda: self.robot._command([4, 10, 1]),
                       lambda: self.robot.start_stream().stop()):
            with self.subTest(motion=motion):
                self.home()
                motion()
                self.refused()
                self.home()
                self.move(vm.home_joints(), 24)

    def test_stop_holds_and_switches_off(self):
        self.home()
        send = VendorHomeSource._send
        packets = []

        def stop(out, sequence):
            step = send(out, sequence)
            packets.append(step[1])
            if len(packets) == 10:
                self.robot.request_stop()
            return step

        with mock.patch.object(VendorHomeSource, "_send", side_effect=stop):
            with self.assertRaises(MotionStopped) as caught:
                self.robot.move_joint_vendor(vm.joints_from_millidegrees(
                    [20000, -120278, 95021, 88805, 0]), total_ms=3000)
        self.assertTrue(caught.exception.started)
        self.assertEqual([p[4:7].hex() for p in packets[-2:]], ["731f00", "421f00"])
        self.assertEqual(packets[-1][12:32], packets[-2][12:32])
        self.assertIsNone(self.robot._fault)
        self.robot.enable()
        self.refused()

    def test_faults_latch(self):
        self.home()
        self.robot.dry_link.plant.frozen = {0}
        with self.assertRaises(ScorbotError):
            self.robot.move_joint_vendor(vm.joints_from_millidegrees(
                [20000, -120278, 95021, 88805, 0]), total_ms=3000)
        self.assertIsNotNone(self.robot._fault)
        self.assertFalse(self.robot._enabled)
        self.assertFalse(self.robot._homed)
        self.refused()


if __name__ == "__main__":
    unittest.main()
