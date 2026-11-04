"""examples/bench_gripper.py: the supervised first gripper trial (vendor Home, then the gripper), rehearsed
through the simulator. No USB, no hardware."""

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from examples import bench_gripper
from scorbot import ScorbotError
from scorbot.preflight import Check
from scorbot.simulated import SimulatedController, SimulatedScorbot

TO_ENABLE = "n\ng\n" "EMPTY\nCLEAR\nHOME\n" "y\ng\n" "HOME_OK\n"
AFTER = "y\ng\n" "jaws opened then closed\nnone\nno sounds\nnone\n" "n\ng\n"
# The Home leaves the gripper at count 0 and RangeEnc 5000 reads back as 5000 (until 2026-11-04 the old
# decode read both one low on the positive side; docs/requirements/count-frame.md).
OPEN_PLANNED, TRAVEL, CLOSE_PLANNED = 5000, 5000, 5000


class BenchGripperTests(unittest.TestCase):
    def run_script(self, answers, *extra, moves=("open", "close"), simulate=True, sim=None):
        made = self.made = []

        class Arm(SimulatedScorbot):
            def __init__(self, **kwargs):
                kwargs["controller"] = SimulatedController()
                super().__init__(**kwargs)
                for name, value in (sim or {}).items():
                    setattr(self.sim, name, value)
                made.append(self)

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.output = Path(folder.name) / "gripper.jsonl"
        argv = ["bench_gripper.py", "--output", str(self.output), "--robot-id", "rehearsal-arm",
                "--arm-label", "arm-plate", "--controller-label", "controller-plate",
                "--driver", "none (simulated)", "--operator", "tester",
                "--start-pose-note", "desk rehearsal", "--moves", *moves,
                "--acknowledge-supervised-motion", *extra]
        if simulate:
            argv.append("--simulate")
        self.printed = io.StringIO()
        replies = iter(answers.splitlines())
        with mock.patch.object(sys, "argv", argv), \
                mock.patch.object(bench_gripper, "SimulatedScorbot", Arm), \
                mock.patch.object(bench_gripper, "run_checks", return_value=[
                    Check("USB", False, "mocked: a test never reaches the arm")]) \
                as self.checks, \
                mock.patch("builtins.input", lambda _question="": next(replies)), \
                contextlib.redirect_stdout(self.printed), \
                contextlib.redirect_stderr(self.printed):
            return bench_gripper.main()

    def rows(self):
        return [json.loads(line) for line in self.output.read_text(encoding="utf-8").splitlines()]

    def test_empty_jaws_open_then_close(self):
        self.assertEqual(self.run_script(TO_ENABLE + "GRIP\nGRIP\n" + AFTER), 0)
        rows = self.rows()
        kinds = [row["type"] for row in rows]
        self.assertEqual(kinds.count("before_gripper"), 2)
        self.assertEqual(kinds.count("after_gripper"), 2)
        for kind in ("session", "connected", "gripper_plan", "operator_observation", "disabled"):
            self.assertIn(kind, kinds)
        self.assertIn("home_complete", kinds, "the gripper is refused before a vendor Home")
        session = rows[0]
        self.assertEqual((session["procedure"], session["data_source"], session["moves"]),
                         ("gripper_trial", "simulated", ["open", "close"]))
        self.assertTrue(session["motion_source_sha256"])
        results = [row["result"] for row in rows if row["type"] == "after_gripper"]
        self.assertEqual([(r["direction"], r["moved_counts"], r["full_travel"]) for r in results],
                         [("open", TRAVEL, True), ("close", -TRAVEL, True)])
        self.assertEqual([r["step"] for r in rows if r["type"] == "led_observation"],
                         ["after_connect", "after_enable", "after_gripper", "after_disable"])
        out = self.printed.getvalue()
        self.assertIn("SIMULATED", out)
        self.assertIn("no force limit", out)
        self.assertIn("NOT an emergency stop", out)
        self.assertIn(f"open: moved +{TRAVEL} of {OPEN_PLANNED} counts (full travel)", out)
        commands = self.made[0].sim.commands
        self.assertEqual([c[0] for c in commands if c[0] in (14, 15, 22, 23)], [22, 23, 23])
        self.assertFalse(any(c[0] == 18 or 4 <= c[0] <= 13 for c in commands),
                         "no legacy home and no arm jog")
        self.checks.assert_not_called()
        from scripts.review_lab_logs import review_bench
        report = review_bench(self.output)
        self.assertEqual((report["kind"], report["problems"]), ("gripper", []))
        self.assertEqual(len(report["moves"]), 2)

    def test_closing_on_an_object_is_reported_as_stopped_short_not_as_a_failure(self):
        code = self.run_script(TO_ENABLE + "GRIP\nGRIP\n" + AFTER,
                               sim={"gripper_blocked_at": 1000})
        self.assertEqual(code, 0)
        close = [row["result"] for row in self.rows() if row["type"] == "after_gripper"][1]
        self.assertEqual((close["moved_counts"], close["full_travel"]), (1000 - TRAVEL, False))
        self.assertIn(f"close: moved {1000 - TRAVEL} of {CLOSE_PLANNED} counts (stopped short",
                      self.printed.getvalue())

    def test_declining_a_move_sends_nothing_more(self):
        self.assertEqual(self.run_script(TO_ENABLE + "GRIP\nno\n"), bench_gripper.EXIT_DECLINED)
        self.assertEqual(self.rows()[-1]["type"], "operator_declined")
        self.assertEqual([c[0] for c in self.made[0].sim.commands if c[0] in (14, 15, 23)], [23])

    def test_declining_before_the_home_moves_nothing(self):
        for answers in ("n\ng\nno\n", "n\ng\nEMPTY\nno\n", "n\ng\nEMPTY\nCLEAR\nno\n"):
            with self.subTest(answers=answers):
                self.assertEqual(self.run_script(answers), bench_gripper.EXIT_DECLINED)
                commands = self.made[-1].sim.commands
                self.assertFalse(any(c[0] in (14, 15, 17, 22, 23) for c in commands))

    def test_declining_after_the_home_moves_no_gripper(self):
        answers = "n\ng\n" "EMPTY\nCLEAR\nHOME\n" "y\ng\n" "no\n"
        self.assertEqual(self.run_script(answers), bench_gripper.EXIT_DECLINED)
        commands = [c[0] for c in self.made[0].sim.commands]
        self.assertIn(22, commands)
        self.assertNotIn(23, commands)

    def test_an_arm_motor_moving_during_the_grip_fails_the_run(self):
        with self.assertRaises(ScorbotError) as caught:
            self.run_script(TO_ENABLE + "GRIP\n", sim={"gripper_disturbs": {"shoulder": 60}})
        self.assertIn("shoulder", str(caught.exception))
        rows = self.rows()
        self.assertEqual(rows[-1]["type"], "session_failed")
        self.assertIn("physical stop", self.printed.getvalue())
        self.assertEqual([c[0] for c in self.made[0].sim.commands if c[0] in (14, 15, 23)], [23])

    def test_bad_arguments_are_refused_before_preflight_or_any_connection(self):
        for moves in (("open",) * 5, ("squeeze",), ()):
            with self.subTest(moves=moves):
                with self.assertRaises(SystemExit) as caught:
                    self.run_script("", moves=moves, simulate=False)
                self.assertEqual(caught.exception.code, 2)
                self.assertFalse(self.output.exists())
                self.assertEqual(self.made, [])
                self.checks.assert_not_called()


if __name__ == "__main__":
    unittest.main()
