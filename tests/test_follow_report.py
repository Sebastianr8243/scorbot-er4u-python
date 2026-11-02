"""scripts/follow_report.py: how far the arm lagged its streamed target, read from a controller event log.

Hand-made logs with known answers. No USB, no arm.
"""

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts import follow_report

PER_DEGREE = {"base": 142, "shoulder": 115, "elbow": 113}
MS = 1_000_000


def step(t_ms, commanded, measured, action="send", lead=0):
    zero = {"base": 0, "shoulder": 0, "elbow": 0}
    return {"host_monotonic_ns": t_ms * MS, "action": action, "state": "tracking",
            "target": {**zero, "base": float(commanded)}, "commanded": {**zero, "base": commanded},
            "measured": {**zero, "base": measured}, "lead": {**zero, "base": lead}}


def stream(steps, speed=0.25, extra=()):
    return [{"event": "stream_start", "host_monotonic_ns": 0, "speed_fraction": speed, "lead_limit_deg": 2.0,
             "period_s": 0.024, "simulated": True},
            *extra,
            {"event": "stream_trace", "host_monotonic_ns": steps[-1]["host_monotonic_ns"] + MS, "steps": steps},
            {"event": "stream_complete", "host_monotonic_ns": steps[-1]["host_monotonic_ns"] + 2 * MS}]


def ramp(n, rate, lag, start_ms=0, hold=20):
    """Commanded rises ``rate`` counts per 24 ms step for ``n`` steps, then holds; measured trails by ``lag``."""
    out, top = [], rate * n
    for i in range(n + hold):
        commanded = min(top, rate * (i + 1))
        out.append(step(start_ms + 24 * i, commanded, max(0, commanded - lag) if i < n else top))
    return out


class FollowReportTests(unittest.TestCase):
    def one(self, rows, **kw):
        found = follow_report.analyse(rows, PER_DEGREE, **kw)
        self.assertEqual(len(found), 1, found)
        return found[0]

    def test_lag_is_the_worst_commanded_minus_measured(self):
        r = self.one(stream(ramp(30, 10, 71)))
        self.assertEqual(r["motor"], "base")
        self.assertEqual(r["worst_lag_counts"], 71)
        self.assertAlmostEqual(r["worst_lag_deg"], 0.5)
        self.assertEqual(r["speed_pct"], 25)

    def test_the_streams_own_lead_counts_too(self):
        steps = ramp(30, 10, 5)
        steps[10]["lead"]["base"] = -200
        self.assertEqual(self.one(stream(steps))["worst_lag_counts"], 200)

    def test_peak_speed_is_measured_and_set_against_the_vendor_limit(self):
        r = self.one(stream(ramp(40, 24, 0)))       # 24 counts per 24 ms is 1000 counts/s
        self.assertAlmostEqual(r["peak_counts_s"], 1000, delta=1)
        self.assertEqual(r["limit_counts_s"], 6500 * 0.25)
        self.assertAlmostEqual(r["peak_pct_of_limit"], 100 * 1000 / 1625, delta=0.1)

    def test_overshoot_and_settling(self):
        steps = ramp(10, 10, 0, hold=0)                         # commanded ends at 100
        for i, m in enumerate((112, 106, 101, 100, 100, 100)):  # past it, then back within 5 counts
            steps.append(step(24 * (10 + i), 100, m))
        r = self.one(stream(steps))
        self.assertEqual(r["overshoot_counts"], 12)
        self.assertAlmostEqual(r["settle_s"], 0.072, delta=1e-9)   # 3 steps after the command stopped moving

    def test_never_settled_is_none(self):
        steps = ramp(10, 10, 0, hold=0) + [step(240 + 24 * i, 100, 80) for i in range(5)]
        self.assertIsNone(self.one(stream(steps))["settle_s"])

    def test_a_fault_is_reported(self):
        rows = stream(ramp(10, 10, 0))
        rows.insert(-1, {"event": "stream_fault", "host_monotonic_ns": 300 * MS, "error": "lead past the limit"})
        self.assertEqual(self.one(rows)["fault"], "lead past the limit")

    def test_a_live_speed_change_starts_a_new_segment(self):
        first, second = ramp(10, 10, 20, hold=2), ramp(10, -10, 30, start_ms=24 * 12, hold=2)
        for s in second:      # back down from 100
            s["commanded"]["base"] += 100
            s["measured"]["base"] = s["commanded"]["base"] + 30 if s["commanded"]["base"] < 100 else 100
        rows = stream(first + second, extra=[{"event": "stream_speed", "host_monotonic_ns": 24 * 12 * MS,
                                              "speed_fraction": 1.0}])
        found = follow_report.analyse(rows, PER_DEGREE)
        self.assertEqual([(r["speed_pct"], r["worst_lag_counts"]) for r in found], [(25, 20), (100, 30)])

    def test_a_motor_that_did_not_move_is_left_out(self):
        self.assertEqual({r["motor"] for r in follow_report.analyse(stream(ramp(10, 10, 0)), PER_DEGREE)}, {"base"})

    def test_only_send_steps_count_for_lag(self):
        steps = ramp(10, 10, 0)
        steps[3] = step(72, 999, 0, action="hold")
        self.assertEqual(self.one(stream(steps))["worst_lag_counts"], 0)

    def test_no_stream_is_no_result(self):
        self.assertEqual(follow_report.analyse([{"event": "connect"}], PER_DEGREE), [])

    def test_main_reads_files_and_prints_a_line_per_segment(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.controller.jsonl"
            path.write_text("\n".join(json.dumps(r) for r in stream(ramp(30, 10, 71))) + "\n", encoding="utf-8")
            with mock.patch("builtins.print") as printed:
                code = follow_report.main([str(path)])
        text = "\n".join(" ".join(map(str, c.args)) for c in printed.call_args_list)
        self.assertEqual(code, 0)
        self.assertIn("base", text)
        self.assertIn("SIMULATED", text)
        self.assertIn("0.50", text)

    def test_main_exits_2_on_a_file_with_no_stream(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.jsonl"
            path.write_text('{"event": "connect"}\n', encoding="utf-8")
            with mock.patch("builtins.print"):
                self.assertEqual(follow_report.main([str(path)]), 2)


if __name__ == "__main__":
    unittest.main()
