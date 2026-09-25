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
from scorbot.state import RobotState


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
                    patch.object(record_raw_state.time, "sleep"):
                self.assertEqual(record_raw_state.main(), 0)

            rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(calls, ["construct", "connect", "get_state", "disconnect"])
            self.assertEqual(rows[0]["robot_id"], "test-arm")
            self.assertEqual(rows[0]["arm_label"], "arm-plate")
            self.assertEqual(len(rows[0]["motion_source_sha256"]), 64)
            self.assertEqual(rows[1]["state"], asdict(state))


if __name__ == "__main__":
    unittest.main()
