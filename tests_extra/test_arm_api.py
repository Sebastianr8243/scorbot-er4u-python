"""scorbot.bridge's FakeArm, taught points and box, and the two arm-server example scripts. No server, no DLL."""

import json
import math
import tempfile
import unittest
from pathlib import Path

from examples import arm_characterise, pick_place
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

    def test_summarise_turns_mat_numbers_into_a_scale_and_worst_errors(self):
        report = {"step_mm": 20.0,
                  "frame": {"x": {"units_per_mm": 1000.0, "mat_mm": 19.0}, "y": {"units_per_mm": 1000.0, "mat_mm": 20.0},
                            "z": {"units_per_mm": 1000.0, "mat_mm": None}},
                  "repeatability": {"spread_mm": 0.0, "readings": [{"mat_mm": 0.5}, {"mat_mm": None}, {"mat_mm": 1.5}]},
                  "grid": [{"mat_error_mm": 2.0, "dll_error_mm": 0.0}, {"mat_error_mm": None, "dll_error_mm": 0.0}]}
        result = arm_characterise.summarise(report)
        self.assertAlmostEqual(result["mat_mm_per_commanded_mm"]["x"], 0.95)
        self.assertAlmostEqual(result["suggested_units_per_mm"]["x"], 1000 * 20 / 19)
        self.assertNotIn("z", result["mat_mm_per_commanded_mm"])
        self.assertEqual(result["worst_repeat_mat_mm"], 1.5)
        self.assertEqual(result["worst_grid_mat_mm"], 2.0)

    def test_summarise_with_no_mat_answers_says_so(self):
        report = {"step_mm": 20.0, "frame": {"x": {"units_per_mm": 1000.0, "mat_mm": None}},
                  "repeatability": {"readings": []}, "grid": []}
        result = arm_characterise.summarise(report)
        self.assertEqual(result["mat_mm_per_commanded_mm"], {})
        self.assertIsNone(result["worst_repeat_mat_mm"])

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


if __name__ == "__main__":
    unittest.main()
