"""``vendor_move.setpoints`` against the lab USBC.dll's own ``MoveJoint`` setpoints on the pipe fake.

The golden fixture (``fixtures/dll_lab_move_joint.json``, made by ``scripts/make_move_joint_fixture.py``) holds
slots 0-4 of every message of 26 recorded moves: base alone at speeds 1 to 100, shoulder, elbow, pitch and roll,
all five joints at once, and a ``Time(3000)`` move. Each must come out of the pure function message for message from the first change to the last. The lead
(messages that still hold the start counts) and the total message count are the model's own and cannot be
seen on the wire: they are pinned here as a regression check, not as evidence.
The fake arm is our model, so this says what the DLL sends, not how the arm follows it.
"""

import json
import unittest
from pathlib import Path

from scorbot import vendor_move as vm

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
        users = [p.name for p in (ROOT / "scorbot").rglob("*.py")
                 if p.name != "vendor_move.py" and "vendor_move" in p.read_text(encoding="utf-8")]
        self.assertEqual(users, [])


if __name__ == "__main__":
    unittest.main()
