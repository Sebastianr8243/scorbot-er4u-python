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


PACKET = bytes(range(64))


def _with_packet(robot_class):
    """Give a fake robot the accessor the idle recorder uses."""
    robot_class.get_state_and_packet = lambda self: (self.get_state(), PACKET)
    return robot_class


class RawCaptureTests(unittest.TestCase):
    def test_literal_example_metadata_is_rejected_before_preflight_or_usb(self):
        examples = {
            "--arm-label": "arm nameplate",
            "--controller-label": "controller nameplate",
            "--driver": "current Windows driver",
            "--operator": "your initials",
            "--pose-note": "photo/sketch of known start pose",
        }
        for option, example in examples.items():
            with self.subTest(option=option):
                with tempfile.TemporaryDirectory() as directory:
                    output = Path(directory) / "idle.jsonl"
                    values = {
                        "--robot-id": "test-arm", "--arm-label": "arm-plate",
                        "--controller-label": "controller-plate", "--driver": "WinUSB",
                        "--operator": "tester", "--pose-note": "known idle pose",
                    }
                    values[option] = example
                    argv = ["record_raw_state.py", "--output", str(output)]
                    for key, value in values.items():
                        argv.extend((key, value))
                    argv.append("--acknowledge-connect-handshake")
                    with patch.object(sys, "argv", argv), \
                            patch.object(record_raw_state, "run_checks",
                                         side_effect=AssertionError("preflight must not run")), \
                            patch.object(record_raw_state, "Scorbot",
                                         side_effect=AssertionError("USB must not open")), \
                            patch.object(record_raw_state.SessionWriter, "create",
                                         side_effect=AssertionError("recorder must not start")):
                        with self.assertRaises(SystemExit) as error:
                            record_raw_state.main()
                    self.assertEqual(error.exception.code, 2)
                    self.assertFalse(output.exists())
                    self.assertFalse((output.parent / "sessions").exists())

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

        @_with_packet
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
            self.assertEqual(samples[0]["raw_hex"], PACKET.hex())

    def test_mcap_write_failure_is_persisted_and_fails_review(self):
        from scorbot.session.record import SessionError, SessionWriter

        state = RobotState(
            timestamp_utc="2026-01-01T00:00:00+00:00",
            encoder_counts={"base": 123}, controller_error_counts={"base": 0},
            home_switch_bits=0, connected=True, enabled=False, homed=False, fault=None,
        )

        @_with_packet
        class FakeRobot:
            def __init__(self, *, log_path, robot_id):
                Path(log_path).write_text("", encoding="utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            def get_state(self):
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
                    patch.object(SessionWriter, "log_state",
                                 side_effect=SessionError("disk lost")), \
                    patch("builtins.input", side_effect=["n", "g", "n", "o"]):
                self.assertEqual(record_raw_state.main(), 1)

            rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["type"] for row in rows].count("sample"), 1)
            self.assertEqual(rows[-1]["type"], "recorder_failed")
            self.assertIn("disk lost", rows[-1]["error"])
            self.assertIn("MCAP recorder reported failure", review_idle(output)["problems"])

    def test_interrupt_during_sampling_records_failure_without_final_led_answer(self):
        calls = []
        state = RobotState(
            timestamp_utc="2026-01-01T00:00:00+00:00",
            encoder_counts={"base": 123},
            controller_error_counts={"base": 0},
            home_switch_bits=0, connected=True, enabled=False, homed=False, fault=None,
        )

        @_with_packet
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

    def test_unsafe_after_connect_led_stops_before_sampling(self):
        for motors_answer in ("y", "u"):
            with self.subTest(motors_answer=motors_answer):
                calls = []

                @_with_packet
                class FakeRobot:
                    log = calls          # bound per iteration (B023)

                    def __init__(self, *, log_path, robot_id):
                        Path(log_path).write_text("", encoding="utf-8")

                    def __enter__(self):
                        self.log.append("connect")
                        return self

                    def __exit__(self, *_args):
                        self.log.append("disconnect")

                    def get_state(self):
                        self.log.append("get_state")
                        raise AssertionError("unsafe LED check must stop before sampling")

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
                            patch("builtins.input", side_effect=[motors_answer, "g"]):
                        with self.assertRaises(RuntimeError):
                            record_raw_state.main()

                    rows = [json.loads(line) for line in output.read_text(
                        encoding="utf-8").splitlines()]
                    self.assertEqual(calls, ["connect", "disconnect"])
                    self.assertEqual(rows[-1]["type"], "session_failed")
                    self.assertEqual([row["type"] for row in rows].count("sample"), 0)
                    self.assertEqual([row["step"] for row in rows
                                      if row["type"] == "led_observation"], ["after_connect"])
                    self.assertIn("session reported failure", review_idle(output)["problems"])

    def test_mcap_flush_interrupt_preserves_jsonl_failure_and_readable_fault(self):
        calls = []
        state = RobotState(
            timestamp_utc="2026-01-01T00:00:00+00:00",
            encoder_counts={"base": 123},
            controller_error_counts={"base": 0},
            home_switch_bits=0, connected=True, enabled=False, homed=False, fault=None,
        )

        @_with_packet
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
                return state

        class InterruptingFlush:
            def __init__(self, stream):
                self.stream = stream
                self.calls = 0

            def flush(self):
                self.stream.flush()
                self.calls += 1
                if self.calls == 2:
                    raise KeyboardInterrupt()

            def __getattr__(self, name):
                return getattr(self.stream, name)

        create = record_raw_state.SessionWriter.create

        def create_with_interrupt(*args, **kwargs):
            writer = create(*args, **kwargs)
            writer._stream = InterruptingFlush(writer._stream)
            return writer

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
                    patch.object(record_raw_state.SessionWriter, "create",
                                 side_effect=create_with_interrupt), \
                    patch.object(record_raw_state.time, "sleep"), \
                    patch("builtins.input", side_effect=["n", "g"]):
                with self.assertRaises(KeyboardInterrupt):
                    record_raw_state.main()

            rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(calls, ["connect", "get_state", "disconnect"])
            self.assertEqual([row["type"] for row in rows].count("sample"), 1)
            self.assertEqual(rows[-1]["type"], "session_failed")
            self.assertEqual(rows[-1]["error_type"], "KeyboardInterrupt")
            self.assertIn("session reported failure", review_idle(output)["problems"])
            [session_dir] = (output.parent / "sessions").iterdir()
            session = load_session(session_dir)
            self.assertEqual(session.errors, [])
            faults = [event for event in session.events if event["topic"] == "/session/fault"]
            self.assertEqual(len(faults), 1)


if __name__ == "__main__":
    unittest.main()
