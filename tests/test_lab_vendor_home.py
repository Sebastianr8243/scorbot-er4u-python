"""The guided lab session with --vendor-home: prompts, confirmations, the home, and what a failure does.

The simulated controller runs the vendor home against the arm model. Nothing here touches USB.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scorbot import SimulatedScorbot
from scorbot.lab import __main__ as lab_main
from scorbot.lab.operator import ScriptedOperator
from scorbot.lab.profile import LabProfile
from scorbot.lab.session import EXIT_DECLINED, EXIT_FAILED, EXIT_OK, LabSession
from scorbot.simulated import SimulatedController
from scorbot.vendor_home_source import VENDOR_HOME_ORDER

PROFILE = LabProfile("lab-er4u-1", "A-12", "C-3", "WinUSB", "SR")
CHECKLIST = ["y", "y", "y", "y"]
BEFORE_HOME = CHECKLIST + ["n", "g", "pose matches photo"]
HOME_OK = ["HOME", "y", "g", "all axes homed", "y"]       # typed HOME, enable, LEDs, observation, home ok
END = ["x", "n", "g"]


class VendorHomeSessionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.ctrl = SimulatedController()

    def run_session(self, answers, **kwargs):
        self.op = ScriptedOperator(answers)
        self.op.stop_on_key = True
        session = LabSession(
            profile=PROFILE, operator=self.op,
            robot_factory=lambda **kw: SimulatedScorbot(controller=self.ctrl, **kw),
            data_source="simulated", log_path=self.root / "s.jsonl",
            session_root=self.root / "sessions", clock=lambda: 0.0, sleep=lambda s: None,
            vendor_home=True, **kwargs)
        code = session.run()
        self.rows = [json.loads(line) for line in
                     (self.root / "s.jsonl").read_text(encoding="utf-8").splitlines()]
        return code

    def of(self, kind):
        return [row for row in self.rows if row["type"] == kind]

    def orders(self):
        return {c[0] for c in self.ctrl.commands if c}

    def test_a_full_vendor_home_session(self):
        code = self.run_session(BEFORE_HOME + ["EMPTY", "CLEAR"] + HOME_OK + END)
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(self.rows[0]["home_method"], "vendor")
        self.assertEqual([row.get("method") for row in self.of("home_complete")], ["vendor"])
        self.assertIn(VENDOR_HOME_ORDER, self.orders())
        self.assertNotIn(18, self.orders())                                   # not the legacy home
        shown = " ".join(self.op.shown)
        for text in ("Vendor home", "CLOSES THE GRIPPER", "37 degrees", "physical stop", "1 degree jog"):
            self.assertIn(text, shown)

    def test_the_jaws_must_be_confirmed_empty_before_anything_is_enabled(self):
        code = self.run_session(BEFORE_HOME + ["no", "CLEAR", "HOME"])
        self.assertEqual(code, EXIT_DECLINED)
        self.assertNotIn(VENDOR_HOME_ORDER, self.orders())
        self.assertNotIn(17, self.orders())                                   # motors never switched on
        self.assertFalse(self.ctrl.motors_on)

    def test_the_wrist_path_must_be_confirmed_clear_before_anything_is_enabled(self):
        code = self.run_session(BEFORE_HOME + ["EMPTY", "no", "HOME"])
        self.assertEqual(code, EXIT_DECLINED)
        self.assertNotIn(VENDOR_HOME_ORDER, self.orders())
        self.assertNotIn(17, self.orders())

    def test_typing_anything_but_home_declines(self):
        code = self.run_session(BEFORE_HOME + ["EMPTY", "CLEAR", "go"])
        self.assertEqual(code, EXIT_DECLINED)
        self.assertNotIn(VENDOR_HOME_ORDER, self.orders())
        self.assertNotIn(17, self.orders())

    def test_a_home_that_fails_ends_the_session_with_the_motors_off(self):
        self.ctrl.vendor_hook = lambda n, plant: setattr(plant, "emergency", n > 150)
        code = self.run_session(BEFORE_HOME + ["EMPTY", "CLEAR", "HOME", "y", "g"] + END)
        self.assertEqual(code, EXIT_FAILED)
        self.assertFalse(self.ctrl.motors_on)
        self.assertEqual(self.of("home_complete"), [])
        self.assertIn("error 300", json.dumps(self.rows))

    def test_the_two_homes_cannot_be_combined(self):
        with self.assertRaises(ValueError):
            LabSession(profile=PROFILE, operator=ScriptedOperator([]), robot_factory=None,
                       data_source="simulated", log_path=self.root / "x.jsonl",
                       session_root=self.root, inch_home=True, vendor_home=True)
        with self.assertRaises(SystemExit):
            with mock.patch("sys.stderr"):
                lab_main.main(["--simulate", "--vendor-home", "--inch-home"])


if __name__ == "__main__":
    unittest.main()
