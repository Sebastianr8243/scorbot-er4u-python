"""The bench script must stop before a jog when home looks wrong."""

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
    def test_operator_declining_home_prevents_jog(self):
        calls = []
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
                    patch("builtins.input", side_effect=["HOME", "looked wrong", "STOP"]):
                with self.assertRaisesRegex(RuntimeError, "no jog requested"):
                    bench_joint.main()
            self.assertEqual(calls, ["connect", "enable", "home", "disconnect"])
            rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertTrue(any(row.get("type") == "home_observation" for row in rows))
            self.assertTrue(any(row.get("type") == "session_failed" for row in rows))


if __name__ == "__main__":
    unittest.main()
