"""Synthetic first-visit logs; reviewer never opens USB."""

from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest

from scripts.review_lab_logs import review_idle, review_bench
from scorbot.simulated import encode_packet
from scorbot.state import JOINTS, decode_state


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

    def test_idle_range_is_wrap_aware_at_the_zero_seam(self):
        # Raw 65535/sign 128 and raw 65534/sign 127 are one count apart, but their
        # signed values (+65535 and -1) differ by 65536.
        def seam_state(index, signed_base):
            decoded = decode_state(encode_packet({"base": signed_base}), connected=True,
                                   enabled=False, homed=False, fault=None,
                                   packet_index=index)
            return asdict(decoded)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "idle.jsonl"
            write_rows(path, [
                {"type": "session", "robot_id": "arm-1", "motion_source_sha256": "a" * 64},
                {"type": "sample", "state": seam_state(1, 65535)},
                {"type": "sample", "state": seam_state(2, -1)},
            ])
            self.assertEqual(review_idle(path)["count_range_at_rest"]["base"], 1)

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

    def test_led_observations_are_reported_and_checked_only_for_new_logs(self):
        def led(step, motors="off"):
            return {"type": "led_observation", "step": step, "motors_led": motors,
                    "power_led": "green"}

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "idle.jsonl"
            base = [{"type": "sample", "state": state(1)}, {"type": "sample", "state": state(2)}]
            session = {"type": "session", "robot_id": "arm-1", "motion_source_sha256": "a" * 64}
            write_rows(path, [session, led("after_connect"), *base])
            # Old logs have no led_prompts flag, so a missing observation is fine.
            result = review_idle(path)
            self.assertEqual(result["problems"], [])
            self.assertEqual(result["led_observations"],
                             [{"step": "after_connect", "motors_led": "off",
                               "power_led": "green"}])
            write_rows(path, [dict(session, led_prompts=True), led("after_connect"), *base])
            self.assertEqual(review_idle(path)["problems"],
                             ["led observation missing after_exit"])
            write_rows(path, [dict(session, led_prompts=True), led("after_connect"), *base,
                              led("after_exit", "lit"),
                              {"type": "led_mismatch", "step": "after_exit", "led": "motors",
                               "observed": "lit", "expected": "off", "message": "stop"}])
            self.assertEqual(review_idle(path)["problems"],
                             ["LED mismatch after_exit: motors LED reported lit, "
                              "software expected off"])

    def test_bench_with_led_prompts_needs_every_step(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bench.jsonl"
            write_rows(path, [{"type": "session", "robot_id": "arm-1", "led_prompts": True},
                              {"type": "led_observation", "step": "after_connect",
                               "motors_led": "off", "power_led": "green"}])
            problems = review_bench(path)["problems"]
            for step in ("after_enable", "after_jog", "after_disable"):
                self.assertIn(f"led observation missing {step}", problems)
            self.assertNotIn("led observation missing after_connect", problems)

    def test_idle_required_led_answers_cannot_be_unsure_or_invalid(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "idle.jsonl"
            write_rows(path, [
                {"type": "session", "robot_id": "arm-1", "led_prompts": True},
                {"type": "led_observation", "step": "after_connect",
                 "motors_led": "unsure", "power_led": "green"},
                {"type": "sample", "state": state(1)},
                {"type": "sample", "state": state(2)},
                {"type": "led_observation", "step": "after_exit",
                 "motors_led": "off", "power_led": "unknown"},
            ])
            self.assertEqual(review_idle(path)["problems"], [
                "LED observation after_connect: motors_led is unsure or invalid",
                "LED observation after_exit: power_led is unsure or invalid",
            ])

    def test_bench_required_led_answers_cannot_be_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bench.jsonl"
            write_rows(path, [
                {"type": "session", "robot_id": "arm-1", "led_prompts": True},
                {"type": "led_observation", "step": "after_connect",
                 "motors_led": "off"},
                {"type": "led_observation", "step": "after_enable",
                 "power_led": "green"},
            ])
            problems = review_bench(path)["problems"]
            self.assertIn("LED observation after_connect: power_led is unsure or invalid",
                          problems)
            self.assertIn("LED observation after_enable: motors_led is unsure or invalid",
                          problems)

    def test_missing_operator_note_is_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bench.jsonl"
            write_rows(path, [{"type": "session", "robot_id": "arm-1"}])
            result = review_bench(path)
            self.assertIn("missing home_observation", result["problems"])
            self.assertIn("missing before_jog", result["problems"])


class LabSessionReviewTests(unittest.TestCase):
    def rows(self, *, measured_base=-142, shoulder=0, extra=()):
        return [
            {"type": "session", "kind": "lab_session", "schema_version": 2,
             "robot_id": "arm-1", "data_source": "simulated"},
            {"type": "led_observation", "step": "after_connect", "motors_led": "off",
             "power_led": "green"},
            {"type": "led_observation", "step": "after_enable", "motors_led": "lit",
             "power_led": "green"},
            {"type": "jog_preview", "n": 1, "joint": "base",
             "plan": {"motor_count_deltas": {"base": -142}}},
            {"type": "jog_confirmed", "n": 1, "how": "typed", "move": "BASE -1"},
            {"type": "before_jog", "n": 1, "state": {}},
            {"type": "after_jog", "n": 1, "state": {}},
            {"type": "jog_observation", "n": 1, "direction": "toward",
             "other_joint_moved": "no"},
            {"type": "jog_result", "n": 1, "planned": {"base": -142},
             "measured": {"base": measured_base, "shoulder": shoulder, "elbow": 0}},
            *extra,
        ]

    def test_clean_session_has_no_problems_and_a_table(self):
        from scorbot.lab.review import format_session_review, review_session_rows
        report = review_session_rows(self.rows())
        self.assertEqual(report["problems"], [])
        self.assertEqual(report["jogs"][0]["move"], "BASE -1")
        text = format_session_review(report)
        self.assertIn("SIMULATED", text)
        self.assertIn("BASE -1", text)
        self.assertTrue(text.rstrip().endswith("LOG CHECK: 0 problems (not a safety verdict)"))

    def test_problems_are_counted(self):
        from scorbot.lab.review import review_session_rows
        report = review_session_rows(self.rows(measured_base=140, shoulder=35, extra=[
            {"type": "jog_preview", "n": 2, "joint": "base", "plan": {}},
            {"type": "jog_confirmed", "n": 2, "how": "repeat", "move": "BASE -1"},
            {"type": "led_mismatch", "step": "after_enable", "led": "motors",
             "observed": "off", "expected": "lit"},
            {"type": "session_failed", "error": "boom"}]))
        text = " | ".join(report["problems"])
        self.assertIn("jog 1: base moved opposite to the plan", text)
        self.assertIn("jog 1: shoulder moved 35 counts but was not jogged", text)
        self.assertIn("jog 2: started but has no after_jog", text)
        self.assertIn("LED mismatch after_enable", text)
        self.assertIn("session failed: boom", text)

    def test_missing_required_led_and_session_row(self):
        from scorbot.lab.review import review_session_rows
        report = review_session_rows([r for r in self.rows()
                                      if r.get("step") != "after_enable"
                                      and r["type"] != "session"])
        self.assertIn("missing session row", report["problems"])
        self.assertIn("LED observation missing after_enable", report["problems"])

    def test_plan_steps_appear_in_the_table_without_problems(self):
        from scorbot.lab.review import format_session_review, review_session_rows
        report = review_session_rows(self.rows(extra=[
            {"type": "plan_shown", "name": "start", "moves": ["BASE +1"]},
            {"type": "jog_preview", "n": 2, "joint": "base",
             "plan": {"motor_count_deltas": {"base": 142}}},
            {"type": "jog_confirmed", "n": 2, "how": "back", "move": "BASE +1"},
            {"type": "before_jog", "n": 2, "state": {}},
            {"type": "after_jog", "n": 2, "state": {}},
            {"type": "jog_result", "n": 2, "planned": {"base": 142},
             "measured": {"base": 142, "shoulder": 0, "elbow": 0}},
            {"type": "plan_complete", "name": "start", "answer": "yes", "steps": 1,
             "count_differences": {"base": 0}}]))
        self.assertEqual(report["problems"], [])
        self.assertEqual(report["jogs"][1]["how"], "back")
        self.assertIn("back", format_session_review(report))

    def test_counts_drift_is_a_problem(self):
        from scorbot.lab.review import review_session_rows
        report = review_session_rows(self.rows(extra=[
            {"type": "counts_drift", "differences": {"base": 25}, "limit": 20}]))
        self.assertTrue(any(p.startswith("counts drift") for p in report["problems"]))

    def test_cli_session_mode_and_json(self):
        import contextlib
        import io
        import sys
        from unittest.mock import patch
        from scripts import review_lab_logs
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "s.jsonl"
            path.write_text("".join(json.dumps(r) + "\n" for r in self.rows()),
                            encoding="utf-8")
            for extra, check in (([], "LOG CHECK: 0 problems"), (["--json"], '"jogs"')):
                out = io.StringIO()
                with patch.object(sys, "argv", ["review", "--session", str(path), *extra]), \
                        contextlib.redirect_stdout(out):
                    self.assertEqual(review_lab_logs.main(), 0)
                self.assertIn(check, out.getvalue())


if __name__ == "__main__":
    unittest.main()
