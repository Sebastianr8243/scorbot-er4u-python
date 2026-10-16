"""scripts/lab_summary.py: the one-page read of lab-day logs, on hand-made records. No USB."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from scripts import lab_summary
from scorbot.state import JOINTS


def counts(**moved):
    raw = {j: 1000 for j in JOINTS}
    raw.update({j: 1000 + c for j, c in moved.items()})
    return {"packet_index": 1, "fault": None, "enabled": True, "encoder_counts": raw,
            "signed_encoder_counts": raw.copy(), "encoder_sign_bytes": {j: 128 for j in JOINTS}}


def bench(folder, name, joint="base", delta=1.0, moved=142, others=None):
    rows = [{"type": "session", "robot_id": "a", "joint": joint, "requested_delta_deg": delta,
             "motion_source_sha256": "f" * 64},
            {"type": "motion_preview", "plan": {"motor_count_deltas": {}}},
            {"type": "before_jog", "state": counts()},
            {"type": "after_jog", "state": counts(**{joint: moved}, **(others or {}))}]
    return write(folder, name, rows)


def write(folder, name, rows):
    path = Path(folder) / name
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


class SummaryTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.dir = folder.name

    def text(self, path, phone=None):
        return "\n".join(lab_summary.summarize(path, phone or {})[0])

    def test_a_positive_request_that_raises_counts_says_up_with_the_scale(self):
        out = self.text(bench(self.dir, "b.jsonl", moved=142))
        self.assertIn("counts went UP by 142", out)
        self.assertIn("142.0 counts per commanded degree", out)
        self.assertNotIn("far from the prior", out)

    def test_falling_counts_say_down(self):
        self.assertIn("counts went DOWN by 142", self.text(bench(self.dir, "b.jsonl", moved=-142)))

    def test_a_negative_request_that_lowers_counts_says_up(self):
        out = self.text(bench(self.dir, "b.jsonl", delta=-1.0, moved=-142))
        self.assertIn("counts went UP", out)          # + request = counts UP, stated for +1

    def test_no_change_says_it_did_not_move(self):
        self.assertIn("did not move", self.text(bench(self.dir, "b.jsonl", moved=2)))

    def test_a_scale_far_from_the_prior_is_flagged(self):
        self.assertIn("far from the prior", self.text(bench(self.dir, "b.jsonl", moved=20)))

    def test_phone_angle_gives_counts_per_measured_degree(self):
        out = self.text(bench(self.dir, "b.jsonl", moved=142), {"base": 0.8})
        self.assertIn("177.5 counts per degree you measured", out)

    def test_other_joints_that_moved_are_listed(self):
        self.assertIn("other joints also changed: shoulder +30",
                      self.text(bench(self.dir, "b.jsonl", others={"shoulder": 30})))

    def test_an_incomplete_log_reports_problems_and_exit_code_1(self):
        path = bench(self.dir, "b.jsonl")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = lab_summary.main([str(path)])
        self.assertEqual(code, 1)
        self.assertIn("PROBLEM:", out.getvalue())

    def test_policy_run_line_and_failed_run(self):
        steps = [{"type": "policy_step", "measured_deg": {"base": 0.2 * i, "shoulder": 0, "elbow": 0},
                  "clamped": i == 1} for i in range(5)]
        ok = write(self.dir, "p.jsonl", [{"type": "session", "procedure": "policy_run"}, *steps,
                                         {"type": "after_policy", "result": {"passed": True, "policy_errors": 0,
                                                                              "problems": []}}])
        lines, problems = lab_summary.summarize(ok, {})
        self.assertEqual(problems, [])
        self.assertIn("5 steps, 1 clamped", lines[0])
        crashed = write(self.dir, "q.jsonl", [{"type": "session", "procedure": "policy_run"}, *steps])
        self.assertTrue(lab_summary.summarize(crashed, {})[1])

    def test_a_wildcard_expands_and_skips_controller_event_logs(self):
        a = bench(self.dir, "base-01.jsonl")
        write(self.dir, "base-01.controller.jsonl", [{"type": "event"}])
        found = lab_summary.expand([Path(self.dir) / "*.jsonl"])
        self.assertEqual([p.name for p in found], [a.name])
        with self.assertRaises(SystemExit):
            lab_summary.main([str(Path(self.dir) / "nothing-*.jsonl")])

    def test_unknown_file_and_bad_phone_argument(self):
        self.assertTrue(lab_summary.summarize(write(self.dir, "x.jsonl", [{"type": "hi"}]), {})[1])
        with self.assertRaises(SystemExit):
            lab_summary.parse_phone(["wrist=1"])


if __name__ == "__main__":
    unittest.main()
