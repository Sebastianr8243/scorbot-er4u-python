"""The first calibration recorder must never request robot motion."""

from dataclasses import asdict
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from examples import record_raw_state
from scorbot.preflight import Check
from scorbot.session import load_session
from scorbot.state import RobotState
from scripts.review_lab_logs import review_idle


class RawCaptureTests(unittest.TestCase):
    def test_recording_only_reads_state(self):
        calls = []
        state = RobotState(
            timestamp_utc="2026-01-01T00:00:00+00:00",
            encoder_counts={"base": 123},
            controller_error_counts={"base": 0},
            home_switch_bits=0,
            connected=True,
            enabled=False,
            homed=False,
            fault=None,
        )

        class FakeRobot:
            def __init__(self, *, log_path, robot_id):
                self.robot_id = robot_id
                calls.append("construct")
                Path(log_path).write_text("", encoding="utf-8")

            def __enter__(self):
                calls.append("connect")
                return self

            def __exit__(self, *_args):
                calls.append("disconnect")

            def get_state(self):
                calls.append("get_state")
                return state

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "idle.jsonl"
            argv = ["record_raw_state.py", "--output", str(output),
                    "--robot-id", "test-arm", "--arm-label", "arm-plate",
                    "--controller-label", "controller-plate", "--driver", "WinUSB",
                    "--operator", "tester", "--pose-note", "known idle pose",
                    "--seconds", "1", "--hz", "0.2",
                    "--acknowledge-connect-handshake"]
            with patch.object(sys, "argv", argv), \
                    patch.object(record_raw_state, "run_checks",
                                 return_value=[Check("USB", True, "mocked")]), \
                    patch.object(record_raw_state, "Scorbot", FakeRobot), \
                    patch.object(record_raw_state.time, "sleep"), \
                    patch("builtins.input", side_effect=["n", "g", "n", "o"]):
                self.assertEqual(record_raw_state.main(), 0)

            rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(calls, ["construct", "connect", "get_state", "disconnect"])
            self.assertEqual(rows[0]["robot_id"], "test-arm")
            self.assertEqual(rows[0]["arm_label"], "arm-plate")
            self.assertEqual(len(rows[0]["motion_source_sha256"]), 64)
            samples = [row for row in rows if row["type"] == "sample"]
            self.assertEqual(samples[0]["state"], asdict(state))

    def test_interrupt_during_sampling_records_failure_without_final_led_answer(self):
        calls = []
        state = RobotState(
            timestamp_utc="2026-01-01T00:00:00+00:00",
            encoder_counts={"base": 123},
            controller_error_counts={"base": 0},
            home_switch_bits=0, connected=True, enabled=False, homed=False, fault=None,
        )

        class FakeRobot:
            def __init__(self, *, log_path, robot_id):
                Path(log_path).write_text("", encoding="utf-8")

            def __enter__(self):
                calls.append("connect")
                return self

            def __exit__(self, *_args):
                calls.append("disconnect")

            def get_state(self):
                calls.append("get_state")
                if calls.count("get_state") == 2:
                    raise KeyboardInterrupt()
                return state

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "idle.jsonl"
            argv = ["record_raw_state.py", "--output", str(output),
                    "--robot-id", "test-arm", "--arm-label", "arm-plate",
                    "--controller-label", "controller-plate", "--driver", "WinUSB",
                    "--operator", "tester", "--pose-note", "known idle pose",
                    "--seconds", "2", "--hz", "1",
                    "--acknowledge-connect-handshake"]
            with patch.object(sys, "argv", argv), \
                    patch.object(record_raw_state, "run_checks",
                                 return_value=[Check("USB", True, "mocked")]), \
                    patch.object(record_raw_state, "Scorbot", FakeRobot), \
                    patch.object(record_raw_state.time, "sleep"), \
                    patch("builtins.input", side_effect=["n", "g"]):
                with self.assertRaises(KeyboardInterrupt):
                    record_raw_state.main()

            rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(calls, ["connect", "get_state", "get_state", "disconnect"])
            self.assertEqual(rows[-1]["type"], "session_failed")
            self.assertEqual(rows[-1]["error_type"], "KeyboardInterrupt")
            self.assertEqual(rows[-1]["error"], "")
            self.assertEqual([row["step"] for row in rows if row["type"] == "led_observation"],
                             ["after_connect"])
            self.assertIn("session reported failure", review_idle(output)["problems"])
            self.assertIn("led observation missing after_exit", review_idle(output)["problems"])
            [session_dir] = (output.parent / "sessions").iterdir()
            session = load_session(session_dir)
            self.assertEqual(session.errors, [])
            faults = [event for event in session.events if event["topic"] == "/session/fault"]
            self.assertEqual(len(faults), 1)


if __name__ == "__main__":
    unittest.main()
