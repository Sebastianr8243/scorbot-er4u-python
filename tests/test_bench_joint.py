"""The bench script must stop before a jog when home looks wrong."""

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from examples import bench_joint
from scorbot.preflight import Check
from scorbot.state import RobotState


class BenchGateTests(unittest.TestCase):
    def run_bench(self, answers, expected_reason):
        calls = self.calls = []
        stdout = io.StringIO()
        state = RobotState(
            timestamp_utc="2026-01-01T00:00:00+00:00",
            encoder_counts={"base": 100},
            controller_error_counts={"base": 0},
            home_switch_bits=0,
            connected=True, enabled=False, homed=False, fault=None,
            signed_encoder_counts={"base": 100},
        )

        class FakeRobot:
            def __init__(self, **_kwargs):
                pass

            def __enter__(self):
                calls.append("connect")
                return self

            def __exit__(self, *_args):
                calls.append("disconnect")

            def get_state(self):
                return state

            def enable(self):
                calls.append("enable")

            def home(self, **_kwargs):
                calls.append("home")

            def jog_joint(self, *_args, **_kwargs):
                calls.append("jog")

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "bench.jsonl"
            argv = ["bench_joint.py", "--output", str(output),
                    "--robot-id", "test-arm", "--arm-label", "arm-plate",
                    "--controller-label", "controller-plate", "--driver", "WinUSB",
                    "--operator", "tester", "--start-pose-note", "known pose",
                    "--joint", "base", "--delta", "1",
                    "--acknowledge-supervised-motion"]
            with patch.object(sys, "argv", argv), \
                    patch.object(bench_joint, "run_checks",
                                 return_value=[Check("USB", True, "mocked")]), \
                    patch.object(bench_joint, "Scorbot", FakeRobot), \
                    patch("builtins.input", side_effect=answers), \
                    contextlib.redirect_stdout(stdout):
                self.assertEqual(bench_joint.main(), bench_joint.EXIT_DECLINED)
            rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows[-1]["type"], "operator_declined")
        self.assertIn(expected_reason, rows[-1]["text"])
        self.assertNotIn("Traceback", stdout.getvalue())
        return rows, stdout.getvalue()

    def test_operator_declining_home_prevents_jog(self):
        rows, _ = self.run_bench(["n", "g", "HOME", "y", "g", "looked wrong", "STOP"],
                                 "no jog requested")
        self.assertEqual(self.calls, ["connect", "enable", "home", "disconnect"])
        self.assertTrue(any(row.get("type") == "home_observation" for row in rows))
        self.assertEqual(rows[-1], {**rows[-1], "type": "operator_declined",
                                    "text": "stopped after homing; no jog requested"})
        self.assertFalse(any(row.get("type") == "session_failed" for row in rows))
        self.assertEqual([r["step"] for r in rows if r["type"] == "led_observation"],
                         ["after_connect", "after_enable"])
        self.assertFalse(any(r["type"] == "led_mismatch" for r in rows))

    def test_motors_led_off_after_enable_warns_and_lets_the_operator_stop(self):
        rows, out = self.run_bench(["n", "g", "HOME", "n", "g", "stop"],
                                   "LED mismatch after_enable")
        self.assertEqual(self.calls, ["connect", "enable", "disconnect"])  # no home
        [mismatch] = [r for r in rows if r["type"] == "led_mismatch"]
        self.assertEqual((mismatch["led"], mismatch["observed"], mismatch["expected"]),
                         ("motors", "off", "lit"))
        self.assertIn("!!! WARNING (after enable): Software says motors are ENABLED", out)


class LedPromptTests(unittest.TestCase):
    def observe(self, answers, **expect):
        rows, decisions, notes = [], [], []

        class Rec:
            def log_decision(self, choice, reason=None):
                decisions.append(choice)

            def log_note(self, text):
                notes.append(text)

        remaining = iter(answers)

        def ask(_prompt):
            try:
                return next(remaining)
            except StopIteration:
                raise EOFError from None

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            bench_joint.observe_leds("after_disable", lambda kind, **f: rows.append(
                dict(type=kind, **f)), Rec(), ask=ask, **expect)
        return rows, decisions, notes, stdout.getvalue()

    def test_keys_are_case_insensitive_and_words_work(self):
        rows, decisions, notes, out = self.observe(["N", "green"], expect_motors="off",
                                                   expect_power="green")
        self.assertEqual(rows, [dict(type="led_observation", step="after_disable",
                                     motors_led="off", power_led="green",
                                     expected_motors_led="off", expected_power_led="green")])
        self.assertEqual(decisions, ["motors_led=off power_led=green"])
        self.assertEqual(notes, [])
        self.assertNotIn("WARNING", out)

    def test_invalid_input_is_asked_again_then_recorded_unsure(self):
        rows, _, _, out = self.observe(["maybe", "Y", "x", "x", "x"], expect_motors="off")
        self.assertEqual(rows[0]["motors_led"], "lit")
        self.assertEqual(rows[0]["power_led"], "unsure")
        self.assertEqual(rows[0]["defaulted_to_unsure"], ["power_led"])
        self.assertIn("Type one key: y/n/u", out)
        self.assertEqual(rows[1]["type"], "led_mismatch")  # lit while expected off

    def test_end_of_input_records_unsure_without_blocking(self):
        def closed(_prompt):
            raise EOFError
        rows, _, _, _ = self.observe([], expect_motors="off")  # input ends at once
        self.assertEqual(rows[0]["motors_led"], "unsure")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(bench_joint.ask_key("?", bench_joint.POWER_KEYS, closed),
                             ("unsure", False))

    def test_unsure_is_never_a_mismatch(self):
        rows, _, _, _ = self.observe(["u", "u"], expect_motors="off", expect_power="green")
        self.assertEqual([r["type"] for r in rows], ["led_observation"])

    def test_power_orange_while_connected_is_a_mismatch(self):
        rows, _, notes, out = self.observe(["n", "o"], expect_motors="off",
                                           expect_power="green")
        self.assertEqual(rows[1]["led"], "power")
        self.assertIn("ORANGE", out)
        self.assertTrue(notes[0].startswith("LED MISMATCH after_disable"))


if __name__ == "__main__":
    unittest.main()
