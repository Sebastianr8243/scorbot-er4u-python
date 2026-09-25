"""Synthetic first-visit logs; reviewer never opens USB."""

import json
from pathlib import Path
import tempfile
import unittest

from scripts.review_lab_logs import review_idle, review_bench
from scorbot.state import JOINTS


def state(index, base=100):
    raw = {joint: 100 for joint in JOINTS}
    raw["base"] = base
    return {
        "packet_index": index, "fault": None, "enabled": False,
        "encoder_counts": raw,
        "signed_encoder_counts": raw.copy(),
        "encoder_sign_bytes": {joint: 128 for joint in JOINTS},
    }


def write_rows(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class LabLogReviewTests(unittest.TestCase):
    def test_idle_reports_rest_variation_and_fresh_packets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "idle.jsonl"
            write_rows(path, [
                {"type": "session", "robot_id": "arm-1", "motion_source_sha256": "a" * 64},
                {"type": "sample", "state": state(1)},
                {"type": "sample", "state": state(2, base=101)},
            ])
            result = review_idle(path)
            self.assertEqual(result["problems"], [])
            self.assertEqual(result["count_range_at_rest"]["base"], 1)
            self.assertTrue(result["physical_review_required"])

    def test_bench_reports_count_mismatch_without_claiming_motion_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bench.jsonl"
            write_rows(path, [
                {"type": "session", "robot_id": "arm-1", "joint": "base",
                 "requested_delta_deg": 1, "motion_source_sha256": "a" * 64},
                {"type": "connected", "state": state(1)},
                {"type": "home_complete", "state": state(2)},
                {"type": "home_observation", "text": "same pose as prior ScorBot home"},
                {"type": "motion_preview", "plan": {"motor_count_deltas": {"base": 142}}},
                {"type": "before_jog", "state": state(3)},
                {"type": "after_jog", "state": state(4, base=236)},
                {"type": "operator_observation", "direction_and_displacement": "clockwise, small",
                 "other_motion": "none seen", "controller_indicators": "observed",
                 "issue": "none"},
                {"type": "disabled", "state": state(5, base=236)},
            ])
            result = review_bench(path)
            self.assertEqual(result["problems"], [])
            self.assertEqual(result["count_deltas"]["base"]["observed"], 136)
            self.assertEqual(result["count_deltas"]["base"]["difference"], -6)
            self.assertTrue(result["physical_review_required"])

    def test_missing_operator_note_is_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bench.jsonl"
            write_rows(path, [{"type": "session", "robot_id": "arm-1"}])
            result = review_bench(path)
            self.assertIn("missing home_observation", result["problems"])
            self.assertIn("missing before_jog", result["problems"])


if __name__ == "__main__":
    unittest.main()
