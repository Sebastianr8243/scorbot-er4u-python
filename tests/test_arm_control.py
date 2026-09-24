import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from scorbot.calibration import load_calibration, signed_count_delta
from scorbot.packet import TrackedInputEndpoint
from scorbot.robot import Scorbot, ScorbotError
from scripts.fit_calibration import fit


class FakeEndpoint:
    def read(self, buffer, timeout):
        packet = bytearray(len(buffer))
        packet[19:21] = (1234).to_bytes(2, "little")
        buffer[:] = packet
        return len(packet)


class PacketTests(unittest.TestCase):
    def test_tracks_new_usb_reads_and_rejects_reused_state(self):
        endpoint = TrackedInputEndpoint(FakeEndpoint())
        endpoint.read(bytearray(64), 100)
        first = endpoint.snapshot()
        self.assertEqual(first.index, 1)
        self.assertEqual(int.from_bytes(first.data[19:21], "little"), 1234)
        with self.assertRaises(TimeoutError):
            endpoint.snapshot(after_index=first.index, timeout=0.01)
        endpoint.read(bytearray(64), 100)
        self.assertEqual(endpoint.snapshot(after_index=first.index).index, 2)


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.csv_path = self.root / "physical.csv"
        self.limits_path = self.root / "limits.json"
        rows = [
            ("home", "", 1000, 0), ("home", "", 1001, 0),
            ("home", "", 999, 0),
            ("fit", "-", 900, -10), ("fit", "+", 950, -5),
            ("fit", "+", 1050, 5), ("fit", "-", 1100, 10),
            ("verify", "-", 925, -7.5), ("verify", "+", 1025, 2.5),
            ("verify", "-", 1075, 7.5),
            ("move_verify", "-", 950, -5),
            ("move_verify", "+", 1050, 5),
            ("move_verify", "-", 975, -2.5),
        ]
        with self.csv_path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(["robot_id", "joint", "role", "approach",
                             "reference_source", "encoder_count", "measured_angle_deg",
                             "requested_target_deg"])
            for role, direction, count, angle in rows:
                writer.writerow(["arm-1", "base", role, direction, "physical", count, angle,
                                 angle if role == "move_verify" else ""])
        self.limits_path.write_text(
            json.dumps({"base": {"soft_min_deg": -7, "soft_max_deg": 7}}),
            encoding="utf-8")

    def test_fit_and_load_use_holdout_and_provenance(self):
        result = fit(self.csv_path, self.limits_path, "arm-1")
        path = self.root / "calibration.json"
        path.write_text(json.dumps(result), encoding="utf-8")
        calibration = load_calibration(path, robot_id="arm-1")
        joint = calibration.joints["base"]
        self.assertAlmostEqual(joint.counts_per_degree, 10)
        self.assertAlmostEqual(joint.angle(1020, 1000), 2)
        self.assertEqual(joint.home_tolerance_counts, 6)
        with self.assertRaises(ValueError):
            load_calibration(path, robot_id="different-arm")
        result["status"] = "candidate"
        path.write_text(json.dumps(result), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_calibration(path, robot_id="arm-1")

    def test_vendor_display_cannot_validate_calibration(self):
        content = self.csv_path.read_text(encoding="utf-8").replace(
            "physical", "vendor_display")
        self.csv_path.write_text(content, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "physical"):
            fit(self.csv_path, self.limits_path, "arm-1")

    def test_count_wrap_and_ambiguity(self):
        self.assertEqual(signed_count_delta(2, 65534), 4)
        with self.assertRaises(ValueError):
            signed_count_delta(32768, 0)

    def test_calibrated_move_reuses_one_bounded_jog(self):
        path = self.root / "calibration.json"
        path.write_text(json.dumps(fit(self.csv_path, self.limits_path, "arm-1")),
                        encoding="utf-8")
        robot = Scorbot(robot_id="arm-1", calibration_path=path)
        robot._device = object()
        robot._enabled = robot._homed = True
        robot._home_counts = {"base": 1000}
        class Input:
            count = 1000
            index = 0
            def snapshot(self, **kwargs):
                self.index += 1
                packet = bytearray(64)
                packet[19:21] = self.count.to_bytes(2, "little")
                return type("Sample", (), {"data": bytes(packet), "index": self.index,
                                           "host_monotonic_ns": 1})()
        reader = Input()
        robot._input = reader
        calls = []
        def command(payload):
            calls.append(payload)
            reader.count += 10
        robot._command = command
        self.assertAlmostEqual(robot.move_joint("base", 1), 1)
        self.assertEqual(calls, [[5, 10, 1]])
        with self.assertRaises(ValueError):
            robot.move_joint("base", 8)
        self.assertEqual(len(calls), 1)

    def test_calibrated_jog_cannot_bypass_soft_limit(self):
        path = self.root / "calibration.json"
        path.write_text(json.dumps(fit(self.csv_path, self.limits_path, "arm-1")),
                        encoding="utf-8")
        robot = Scorbot(robot_id="arm-1", calibration_path=path)
        robot._device = object()
        robot._enabled = robot._homed = True
        robot._home_counts = {"base": 1000}
        class Input:
            index = 0
            def snapshot(self, **kwargs):
                self.index += 1
                packet = bytearray(64)
                packet[19:21] = (1060).to_bytes(2, "little")
                return type("Sample", (), {"data": bytes(packet), "index": self.index,
                                           "host_monotonic_ns": 1})()
        robot._input = Input()
        robot._command = Mock()
        with self.assertRaisesRegex(ValueError, "soft limits"):
            robot.jog_joint("base", 2)
        robot._command.assert_not_called()

    def test_jog_ceiling_cannot_be_raised_past_five_degrees(self):
        with self.assertRaises(ValueError):
            Scorbot(max_jog_degrees=6)

    def test_stale_feedback_faults_before_any_motion(self):
        robot = Scorbot(response_timeout=0.01)
        robot._device = object()
        robot._enabled = robot._homed = True
        reader = Mock()
        reader.snapshot.side_effect = TimeoutError("no packet")
        robot._input = reader
        robot._command = Mock()
        with self.assertRaisesRegex(ScorbotError, "feedback"):
            robot.jog_joint("base", 1)
        robot._command.assert_not_called()
        self.assertIsNotNone(robot._fault)
        self.assertFalse(robot._homed)


if __name__ == "__main__":
    unittest.main()
