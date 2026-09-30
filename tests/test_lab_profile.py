"""Lab profile and the scripted operator; no hardware."""

from pathlib import Path
import tempfile
import unittest

from scorbot.lab.operator import ENTER, ScriptedOperator, StatusLine
from scorbot.lab.profile import (LabProfile, ProfileError, ensure_profile, load_profile,
                                 save_profile)

GOOD = dict(robot_id="lab-er4u-1", arm_label="A-12", controller_label="C-3",
            driver="WinUSB", operator="SR")


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "lab.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_validation(self):
        LabProfile(**GOOD)
        for field, bad in (("robot_id", " "), ("arm_label", "Arm nameplate"),
                           ("operator", "your initials")):
            with self.subTest(field=field), self.assertRaises(ProfileError):
                LabProfile(**{**GOOD, field: bad})
        for speed in (0, 21, True, 5.0):
            with self.subTest(speed=speed), self.assertRaises(ProfileError):
                LabProfile(**GOOD, speed=speed)

    def test_save_and_load_round_trip_without_temp_leftovers(self):
        save_profile(LabProfile(**GOOD, speed=7), self.path)
        self.assertEqual(load_profile(self.path), LabProfile(**GOOD, speed=7))
        self.assertEqual(sorted(p.name for p in self.path.parent.iterdir()), ["lab.json"])
        self.assertIsNone(load_profile(self.path.parent / "missing.json"))
        self.path.write_text('{"robot_id": "x"}', encoding="utf-8")
        with self.assertRaises(ProfileError):
            load_profile(self.path)

    def test_first_run_asks_every_field_then_saves(self):
        op = ScriptedOperator(["lab-er4u-1", "A-12", "C-3", "WinUSB", "SR", "", "y"])
        profile = ensure_profile(self.path, op)
        self.assertEqual(profile, LabProfile(**GOOD))          # blank speed keeps 10
        self.assertEqual(load_profile(self.path), profile)

    def test_existing_profile_kept_with_enter(self):
        save_profile(LabProfile(**GOOD), self.path)
        op = ScriptedOperator([""])
        self.assertEqual(ensure_profile(self.path, op), LabProfile(**GOOD))
        self.assertIn("lab-er4u-1", " ".join(op.shown))

    def test_change_keeps_blank_fields_and_rejects_placeholders(self):
        save_profile(LabProfile(**GOOD), self.path)
        op = ScriptedOperator(["c", "", "", "", "", "your initials", "",   # rejected
                               "", "", "", "", "JD", "12", "y"])
        profile = ensure_profile(self.path, op)
        self.assertEqual((profile.operator, profile.speed), ("JD", 12))
        self.assertEqual(load_profile(self.path).operator, "JD")
        self.assertTrue(any("your initials" in m.lower() or "example" in m.lower()
                            for m in op.shown))

    def test_not_saved_when_operator_says_no(self):
        op = ScriptedOperator(["lab-er4u-1", "A-12", "C-3", "WinUSB", "SR", "", "n"])
        self.assertEqual(ensure_profile(self.path, op), LabProfile(**GOOD))
        self.assertFalse(self.path.exists())


class ScriptedOperatorTests(unittest.TestCase):
    def test_answers_and_fallbacks(self):
        op = ScriptedOperator(["home", "x", "x", "x", "", lambda: "q", "note"])
        self.assertTrue(op.confirm("Type HOME: ", "HOME"))
        self.assertEqual(op.choose("LED? ", {"y": "lit"}), "unsure")    # 3 invalid
        self.assertEqual(op.choose("Keep? ", {ENTER: "keep"}), "keep")  # blank = Enter
        self.assertEqual(op.key("key: "), "q")
        self.assertEqual(op.text("note: "), "note")
        self.assertEqual(op.key("key: "), "")           # end of input
        self.assertEqual(op.choose("LED? ", {"y": "lit"}), "unsure")
        self.assertFalse(op.confirm("Type ARM: ", "ARM"))
        self.assertEqual(op.text("x"), "")

    def test_status_line_render(self):
        line = StatusLine("SIMULATED", True, False, "base", 0.5, 10, None).render()
        self.assertEqual(line, "SIMULATED | HOMED | DISARMED | base | step 0.5 deg | "
                               "speed 10 | fault: none")
        self.assertIn("HOME NEEDED", StatusLine("REAL", False, False, "base", 1.0, 10,
                                                "x").render())


if __name__ == "__main__":
    unittest.main()
