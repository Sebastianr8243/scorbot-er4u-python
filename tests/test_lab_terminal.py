"""Terminal front end and the lab command; no hardware, no real console."""

from datetime import date
import contextlib
import dataclasses
import functools
import io
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scorbot import simulated
from scorbot.lab import __main__ as lab_main
from scorbot.lab.__main__ import next_log_path
from scorbot.lab.operator import ENTER
from scorbot.lab.session import LabSession
from scorbot.lab.terminal import TerminalOperator, termination_as_interrupt
from scripts import review_lab_logs


class CameraFlagTests(unittest.TestCase):
    def test_fake_camera_needs_simulate(self):
        from scorbot.lab.__main__ import main
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(["--camera", "fake"])

    def test_camera_index_must_be_a_number(self):
        from scorbot.lab.__main__ import main
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(["--simulate", "--camera", "front"])


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

    def held(self, polls_down, repeats_at=()):
        """Key physically down for `polls_down` polls; repeats queue at given polls."""
        state = {"polls": 0}
        pending = []

        def key_down(char):
            state["polls"] += 1
            if state["polls"] in repeats_at:
                pending.append(char)
            return state["polls"] <= polls_down
        op = TerminalOperator(getwch=lambda: pending.pop(0), isatty=lambda: True,
                              kbhit=lambda: bool(pending), key_down=key_down,
                              sleep=lambda s: None, quiet_s=0.0)
        return op, pending, state

    def test_silent_key_state_failure_still_waits_out_the_repeats(self):
        # GetAsyncKeyState returns 0 ("up") when it fails (Microsoft docs), so the
        # gate must also wait until Windows' own repeat timing has gone quiet.
        clock = {"t": 0.0}
        schedule = [0.5, 0.53, 0.56, 0.59]          # repeats of a key still held
        pending = []

        def sleep(seconds):
            clock["t"] += seconds
            while schedule and schedule[0] <= clock["t"]:
                pending.append("q")
                schedule.pop(0)
        op = TerminalOperator(getwch=lambda: pending.pop(0), isatty=lambda: True,
                              kbhit=lambda: bool(pending), key_down=lambda c: False,
                              sleep=sleep, clock=lambda: clock["t"], quiet_s=1.1)
        op._last_key_s = 0.0                       # the press that started the step
        with contextlib.redirect_stdout(io.StringIO()):
            discarded = op.wait_for_release("q")
        self.assertEqual(discarded, 4)
        self.assertGreaterEqual(clock["t"], 0.59 + 1.1)

    def test_quiet_window_from_windows_settings(self):
        from scorbot.lab.terminal import quiet_from_settings
        # Delay setting 3 = 1 s, slowest repeat speed 0 = about 2.5 per second.
        self.assertAlmostEqual(quiet_from_settings(3, 0), 1.1, places=2)
        self.assertAlmostEqual(quiet_from_settings(0, 31), 0.35, places=2)

    def test_wait_for_release_waits_for_key_up_and_drops_repeats(self):
        op, pending, state = self.held(polls_down=40, repeats_at=range(20, 40))
        with contextlib.redirect_stdout(io.StringIO()):
            discarded = op.wait_for_release("q")
        self.assertEqual(discarded, 20)
        self.assertEqual(pending, [])
        self.assertEqual(state["polls"], 41)

    def test_long_repeat_delay_still_one_step(self):
        # No repeat has arrived yet when the gate starts, but the key is still down.
        op, pending, _ = self.held(polls_down=60, repeats_at=(55, 58))
        with contextlib.redirect_stdout(io.StringIO()):
            op.wait_for_release("q")
            self.assertIsNone(op.key_or_tick("k: ", 0.0))
        self.assertEqual(pending, [])

    def test_release_gate_needs_key_state(self):
        op = TerminalOperator(getwch=lambda: "q", isatty=lambda: True, kbhit=lambda: False,
                              key_down=None)
        op._key_down = None
        self.assertFalse(op.can_wait_for_release())

    def test_key_or_tick_returns_none_without_a_key(self):
        now = iter([0.0, 0.1, 0.3])
        op = TerminalOperator(getwch=lambda: "q", isatty=lambda: True, kbhit=lambda: False,
                              key_down=lambda c: False, sleep=lambda s: None,
                              clock=lambda: next(now))
        self.assertIsNone(op.key_or_tick("k: ", 0.2))

    def test_key_or_tick_returns_a_waiting_key(self):
        pending = ["Q"]
        op = TerminalOperator(getwch=lambda: pending.pop(0), isatty=lambda: True,
                              kbhit=lambda: bool(pending), key_down=lambda c: False,
                              sleep=lambda s: None)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(op.key_or_tick("k: ", 0.2), "q")

    def test_every_teleop_key_has_a_release_gate(self):
        from scorbot.lab.session import JOG_KEYS
        from scorbot.lab.terminal import _virtual_key
        for key in [*JOG_KEYS, "t", "r", "n"]:
            with self.subTest(key=key):
                self.assertIsNotNone(_virtual_key(key))

    def test_virtual_key_codes(self):
        from scorbot.lab.terminal import _virtual_key
        self.assertEqual((_virtual_key("q"), _virtual_key("3"), _virtual_key("?")),
                         (ord("Q"), ord("3"), None))

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
            code, stdout, stderr = self.run_main(
                lab_main.main, ["--simulate", "--profile", str(profile),
                                "--logs", str(root / "rehearsal")], answers)
            self.assertEqual(code, 0, stdout[-2000:] + stderr[-2000:])
            self.assertIn("SIMULATED", stdout)
            self.assertIn("LOG CHECK: 0 problems", stdout)
            logs = sorted((root / "rehearsal").glob("*-session-*.jsonl"))
            session_log = [p for p in logs if not p.name.endswith(".controller.jsonl")][0]
            with patch.object(sys, "argv", ["review_lab_logs.py", "--session",
                                            str(session_log)]):
                code, stdout, stderr = self.run_main(lambda argv: review_lab_logs.main(),
                                                     None, [])
            self.assertEqual(code, 0, stdout + stderr)

    def run_main(self, main, argv, answers):
        """Call an entry point as a piped child would see it: exit code, stdout, stderr.

        The rehearsal's real waits are removed (the modeled 3 s homing, as in
        test_lab_session.py, and the 1 s between idle samples); nothing else differs
        from `python -m scorbot.lab --simulate`.
        """
        pending = iter(answers)

        def piped_input(prompt=""):
            print(prompt, end="")
            try:
                return next(pending)
            except StopIteration:
                raise EOFError from None          # what a closed pipe gives
        out, err = io.StringIO(), io.StringIO()
        # stdin is replaced too: with a real console TerminalOperator would read
        # the keyboard instead of calling input().
        with patch("builtins.input", piped_input), patch.object(sys, "stdin", io.StringIO()), \
                patch.object(lab_main, "LabSession",
                             functools.partial(LabSession, sleep=lambda seconds: None)), \
                patch.object(simulated, "REHEARSAL_PROFILE", dataclasses.replace(
                    simulated.REHEARSAL_PROFILE, homing_duration_s=0.0)), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = main(argv)
            except SystemExit as stop:
                code = stop.code
        return code, out.getvalue(), err.getvalue()

    def test_module_starts_as_a_process(self):
        # The one real child: `python -m scorbot.lab` resolves and parses its
        # arguments. --help exits before any controller, simulated or real, is made.
        run = subprocess.run([sys.executable, "-m", "scorbot.lab", "--help"],
                             capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(run.returncode, 0, run.stdout[-2000:] + run.stderr[-2000:])
        self.assertIn("--simulate", run.stdout)


if __name__ == "__main__":
    unittest.main()
