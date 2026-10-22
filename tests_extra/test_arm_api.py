"""scorbot.bridge's FakeArm, taught points and box, and the two arm-server example scripts. No server, no DLL."""

import json
import math
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from examples import arm_characterise, arm_latency, pick_place
from scorbot import bridge


class TaughtPointTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.positions = Path(self.dir.name) / "positions.json"
        self.box = Path(self.dir.name) / "arm.json"

    def test_teach_saves_the_point_and_grows_the_box_and_go_returns_there(self):
        arm = bridge.FakeArm(start=(300, 10, 150))
        arm.teach("above_a", self.positions, self.box)
        arm.move_by(dz=-40)
        arm.teach("at_a", self.positions, self.box)
        self.assertEqual(json.loads(self.box.read_text())["box_mm"]["z"], [110, 150])
        arm.go("above_a", self.positions)
        self.assertEqual((arm.x, arm.y, arm.z), (300, 10, 150))

    def test_go_to_an_unknown_name_is_refused(self):
        with self.assertRaisesRegex(bridge.ArmError, "no taught position"):
            bridge.FakeArm().go("nowhere", self.positions)

    def test_widen_box_keeps_other_keys(self):
        self.box.write_text(json.dumps({"note": "kept"}))
        bridge.widen_box(self.box, [(1, 2, 3), (-1, 5, 0)])
        data = json.loads(self.box.read_text())
        self.assertEqual(data["note"], "kept")
        self.assertEqual(data["box_mm"], {"x": [-1, 1], "y": [2, 5], "z": [0, 3]})

    def test_position_from_a_reply_converts_xyz_to_mm(self):
        position = bridge.Position.from_reply({"xyz": [300000, -5000, 200000, -63000, 10], "encoder": [1]})
        self.assertEqual((position.x, position.y, position.z, position.pitch, position.roll), (300, -5, 200, -63000, 10))
        self.assertIsNone(bridge.Position.from_reply({"ok": True}))

    def test_fake_move_to_needs_a_home(self):
        arm = bridge.FakeArm(homed=False)
        with self.assertRaises(bridge.ArmError):
            arm.move_to(1, 2, 3)
        arm.home()
        arm.move_to(1, 2, 3)


class ClientTests(unittest.TestCase):
    """The real Arm client against a one-connection stand-in server on localhost. No DLL."""

    def serve(self, replies):
        """Answer each received line with the next canned reply (a callable gets the request); returns the port."""
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        self.addCleanup(listener.close)
        self.requests = []

        def run():
            connection, _ = listener.accept()
            with connection, connection.makefile("rwb") as stream:
                for reply in replies:
                    request = json.loads(stream.readline())
                    self.requests.append(request)
                    for line in reply(request) if callable(reply) else [reply]:
                        stream.write((json.dumps(line) + "\n").encode())
                    stream.flush()

        threading.Thread(target=run, daemon=True).start()
        return listener.getsockname()[1]

    def test_where_sends_one_line_and_returns_mm(self):
        port = self.serve([lambda r: [{"id": r["id"], "ok": True, "xyz": [300000, 0, 200000, 1, 2]}]])
        with bridge.Arm(port=port, timeout=5) as arm:
            position = arm.where()
        self.assertEqual(self.requests[0]["op"], "where")
        self.assertEqual((position.x, position.y, position.z, position.pitch, position.roll), (300, 0, 200, 1, 2))

    def test_a_reply_for_an_earlier_request_is_skipped(self):
        port = self.serve([lambda r: [{"id": r["id"] - 1, "ok": False, "error": "stale"},
                                      {"id": r["id"], "ok": True, "xyz": [1000, 2000, 3000, 0, 0]}]])
        with bridge.Arm(port=port, timeout=5) as arm:
            self.assertEqual(arm.where().z, 3)

    def test_a_refusal_raises_with_the_servers_whole_reply(self):
        port = self.serve([lambda r: [{"id": r["id"], "ok": False, "error": "each of dx, dy, dz must be at most 50 mm"}]])
        with bridge.Arm(port=port, timeout=5) as arm, self.assertRaisesRegex(bridge.ArmError, "at most 50") as caught:
            arm.move_by(dx=60)
        self.assertEqual(self.requests[0], {"op": "move_by", "id": 1, "dx": 60, "dy": 0.0, "dz": 0.0})
        self.assertFalse(caught.exception.reply["ok"])

    def test_a_closed_connection_raises(self):
        port = self.serve([lambda r: []])
        with bridge.Arm(port=port, timeout=5) as arm, self.assertRaisesRegex(bridge.ArmError, "closed the connection"):
            arm.where()

    def test_no_server_says_to_start_it(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        with self.assertRaisesRegex(bridge.ArmError, "start tools/usbc_server first"):
            bridge.Arm(port=port)


class ScaledArm(bridge.FakeArm):
    """A fake whose DLL xyz has a known scale, to check the characterisation math."""

    def __init__(self, units_per_mm):
        super().__init__()
        self.units = units_per_mm

    def where(self):
        xyz = [round(v * self.units) for v in (self.x, self.y, self.z)] + [0, 0]
        return bridge.Position(*(v / bridge.XYZ_UNITS_PER_MM for v in xyz[:3]), 0, 0, [], [], xyz)

    def move_by(self, dx=0.0, dy=0.0, dz=0.0):
        super().move_by(dx, dy, dz)
        return self.where()


class CharacteriseTests(unittest.TestCase):
    def test_scale_and_spread_on_known_numbers(self):
        before = bridge.Position(0, 0, 0, xyz=[0, 0, 0, 0, 0])
        after = bridge.Position(0, 0, 0, xyz=[20000, 500, 0, 0, 0])
        self.assertEqual(arm_characterise.scale(before, after, "x", 20), (1000.0, 0.5))
        self.assertAlmostEqual(arm_characterise.spread_mm([(0, 0, 0), (2, 0, 0)]), 1.0)
        self.assertEqual(len(arm_characterise.grid_offsets(40)), 9)

    def test_a_run_measures_the_scale_and_comes_back_to_the_start(self):
        arm = ScaledArm(units_per_mm=1000)
        report, reached = arm_characterise.characterise(arm, grid_mm=30, repeats=4, read=lambda _prompt: "")
        for axis in "xyz":
            self.assertAlmostEqual(report["frame"][axis]["units_per_mm"], 1000)
        self.assertAlmostEqual(report["repeatability"]["spread_mm"], 0)
        self.assertEqual((arm.x, arm.y, arm.z), (300, 0, 200))
        self.assertLess(max(g["dll_error_mm"] for g in report["grid"]), 1e-9)
        self.assertIn((330, 30, 200), [tuple(round(v) for v in p) for p in reached])

    def test_a_wrong_unit_shows_up_as_a_scale_away_from_the_assumption(self):
        report, _ = arm_characterise.characterise(ScaledArm(units_per_mm=100), grid_mm=10, repeats=2,
                                                  read=lambda _prompt: "")
        self.assertAlmostEqual(report["frame"]["z"]["units_per_mm"], 100)
        self.assertTrue(math.isclose(report["grid"][0]["dll_error_mm"], math.hypot(9, 9), rel_tol=1e-6))

    def test_mat_answers_are_recorded_and_bad_ones_skipped(self):
        self.assertEqual(arm_characterise.ask("q", read=lambda _p: "1.5"), 1.5)
        self.assertIsNone(arm_characterise.ask("q", read=lambda _p: "abc"))


class PickPlaceTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.positions = Path(self.dir.name) / "positions.json"
        self.positions.write_text(json.dumps({name: {"x": 300, "y": i * 10, "z": 100}
                                              for i, name in enumerate(pick_place.POINTS)}))

    def arm(self):
        arm = bridge.FakeArm()
        arm.go = lambda name: bridge.go(arm, name, self.positions)
        return arm

    def test_runs_alternate_direction_and_grip_in_order(self):
        arm, rows = self.arm(), []
        answers = iter(["y", "1.0", "y", ""])
        pick_place.run(arm, 2, rows.append, read=lambda _p: next(answers))
        self.assertEqual([(r["from"], r["to"]) for r in rows], [("a", "b"), ("b", "a")])
        grips = [c[1] for c in arm.commands if c[0] == "gripper"]
        self.assertEqual(grips, [True, False, True] * 2)
        self.assertEqual(pick_place.summary(rows), {"runs": 2, "placed": 2, "success_rate": 1.0, "mean_error_mm": 1.0})

    def test_a_refused_move_ends_the_runs_and_is_recorded(self):
        arm = self.arm()
        arm.homed = False
        rows = pick_place.run(arm, 3, lambda _row: None, read=lambda _p: "y")
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["placed"])
        self.assertIn("Home", rows[0]["failure"])


class ArmLatencyTests(unittest.TestCase):
    def test_every_call_is_timed_in_order_and_the_arm_ends_where_it_started(self):
        arm, rows = bridge.FakeArm(), []
        ticks = iter(range(0, 1000, 2))          # a fake clock: every call takes 2 s
        result = arm_latency.run(arm, rows.append, speed=30, pairs=2, sizes=(5, 10), reads=3,
                                 clock=lambda: next(ticks))
        self.assertEqual(result, rows)
        self.assertEqual([r["kind"] for r in rows],
                         ["where"] * 3 + ["move_by_5mm"] * 4 + ["move_by_10mm"] * 4 + ["gripper"] * 4)
        self.assertTrue(all(r["seconds"] == 2 for r in rows))
        self.assertEqual(arm.commands[0], ("speed", 30))
        self.assertEqual((arm.x, arm.y, arm.z), (300.0, 0.0, 200.0))

    def test_summary_gives_median_worst_and_calls_per_second(self):
        rows = [{"kind": "move_by_5mm", "seconds": s} for s in (0.5, 1.0, 4.0)]
        self.assertEqual(arm_latency.summary(rows),
                         {"move_by_5mm": {"n": 3, "median_s": 1.0, "worst_s": 4.0, "per_second": 1.0}})

    def test_main_does_not_start_without_go(self):
        self.assertEqual(arm_latency.main([], read=lambda _p: "no"), 1)


if __name__ == "__main__":
    unittest.main()
