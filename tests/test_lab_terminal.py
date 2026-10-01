"""Terminal front end and the lab command; no hardware, no real console."""

from datetime import date
import contextlib
import io
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scorbot.lab.__main__ import next_log_path
from scorbot.lab.operator import ENTER
from scorbot.lab.terminal import TerminalOperator, termination_as_interrupt


class TerminalOperatorTests(unittest.TestCase):
    def keys(self, *sequence):
        chars = iter(sequence)
        return TerminalOperator(getwch=lambda: next(chars), isatty=lambda: True)

    def test_single_keys_enter_and_special_keys(self):
        op = self.keys("Q", "\r", "\xe0", "H", "x")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(op.key("k: "), "q")
            self.assertEqual(op.key("k: "), ENTER)
            self.assertEqual(op.key("k: "), "<special>")   # arrow key: unknown, never a jog
            self.assertEqual(op.choose("? ", {"x": "done"}), "done")

    def test_ctrl_c_key_raises(self):
        op = self.keys("\x03")
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(KeyboardInterrupt):
            op.key("k: ")

    def test_non_tty_falls_back_to_input(self):
        op = TerminalOperator(getwch=None, isatty=lambda: False)
        with patch("builtins.input", side_effect=["y", "", "BASE -1", EOFError()]), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(op.key("k: "), "y")
            self.assertEqual(op.key("k: "), ENTER)
            self.assertTrue(op.confirm("Type: ", "base -1"))
            self.assertEqual(op.key("k: "), "")

    def test_line_mode_word_is_not_a_single_key(self):
        op = TerminalOperator(getwch=None, isatty=lambda: False)
        with patch("builtins.input", side_effect=["quit", "Exit"]),                 contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(op.key("k: "), "quit")        # never 'q' (base minus)
            self.assertEqual(op.key("k: "), "exit")        # never 'e' (elbow minus)

    def test_pending_keys_are_discarded(self):
        pending = ["a", "d"]
        op = TerminalOperator(getwch=lambda: pending.pop(0), isatty=lambda: True,
                              kbhit=lambda: bool(pending))
        self.assertEqual(op.discard_pending_keys(), 2)
        self.assertEqual(pending, [])
        self.assertEqual(TerminalOperator(getwch=None, isatty=lambda: False)
                         .discard_pending_keys(), 0)

    def test_alarm_lines_are_marked(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            TerminalOperator(getwch=None, isatty=lambda: False).show("stop", "alarm")
        self.assertIn("!!! stop", out.getvalue())

    def test_termination_signals_become_keyboard_interrupt(self):
        before = signal.getsignal(signal.SIGTERM)
        with termination_as_interrupt():
            with self.assertRaises(KeyboardInterrupt):
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        self.assertIs(signal.getsignal(signal.SIGTERM), before)


class LogPathTests(unittest.TestCase):
    def test_next_free_number_and_safe_name(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            day = date(2026, 9, 30)
            first = next_log_path(folder, "lab er4u/1", day)
            self.assertEqual(first.name, "20260930-lab-er4u-1-session-01.jsonl")
            first.write_text("", encoding="utf-8")
            (folder / "20260930-lab-er4u-1-session-02.controller.jsonl").write_text(
                "", encoding="utf-8")
            self.assertEqual(next_log_path(folder, "lab er4u/1", day).name,
                             "20260930-lab-er4u-1-session-03.jsonl")


class CommandSmokeTests(unittest.TestCase):
    def test_simulated_session_end_to_end_then_review(self):
        answers = ["", "y", "y", "y", "y", "n", "g", "pose ok", "HOME", "y", "g",
                   "homed", "y", "a", "door", "y", "g", "ARM", "q", "BASE -1", "t", "n", "",
                   "q", "t", "n", "", "x", "n", "g"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / "lab.json"
            profile.write_text(json.dumps({"robot_id": "lab-er4u-1", "arm_label": "A-12",
                                           "controller_label": "C-3", "driver": "none",
                                           "operator": "SR", "speed": 10}), encoding="utf-8")
            run = subprocess.run(
                [sys.executable, "-m", "scorbot.lab", "--simulate", "--profile", str(profile),
                 "--logs", str(root / "rehearsal")],
                input="\n".join(answers) + "\n", capture_output=True, text=True,
                encoding="utf-8", timeout=120)
            self.assertEqual(run.returncode, 0, run.stdout[-2000:] + run.stderr[-2000:])
            self.assertIn("SIMULATED", run.stdout)
            self.assertIn("LOG CHECK: 0 problems", run.stdout)
            logs = sorted((root / "rehearsal").glob("*-session-*.jsonl"))
            session_log = [p for p in logs if not p.name.endswith(".controller.jsonl")][0]
            review = subprocess.run(
                [sys.executable, "scripts/review_lab_logs.py", "--session", str(session_log)],
                capture_output=True, text=True, encoding="utf-8", timeout=60)
            self.assertEqual(review.returncode, 0, review.stdout + review.stderr)


if __name__ == "__main__":
    unittest.main()
