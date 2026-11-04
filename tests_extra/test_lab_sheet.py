"""scripts/lab_summary.py --sheet: the record-sheet rows, filled from logs the simulator wrote. No USB."""

import contextlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from scorbot.simulated import encode_packet
from scorbot.state import ERROR_OFFSETS
from scripts import lab_sheet, lab_summary
from tests_extra.test_bench_stream import HAS_RUCKIG
from tests.test_simulated import BENCH_ANSWERS, IDLE_ANSWERS, LABELS, run_script

GRIP = ("n\ng\n" "EMPTY\nCLEAR\nHOME\n" "y\ng\n" "HOME_OK\nGRIP\nGRIP\n" "y\ng\n"
        "jaws opened then closed\nnone\nno sounds\nnone\n" "n\ng\n")
STREAM = ("n\ng\n" "HOME\n" "y\ng\n" "home looked normal\nHOME_OK\nSTREAM\n" "y\ng\n"
          "toward door\nnone\nno sounds\nnone\n" "n\ng\n")


def setUpModule():
    global LOGS, _tmp
    _tmp = tempfile.TemporaryDirectory()
    LOGS = Path(_tmp.name)
    runs = (("examples/bench_joint.py", "base.jsonl", BENCH_ANSWERS,
             ("--joint", "base", "--delta", "1")),
            ("examples/bench_gripper.py", "gripper.jsonl", GRIP, ("--moves", "open", "close")),
            ("examples/bench_stream.py", "stream.jsonl", STREAM,
             ("--motor", "base", "--delta", "1", "--hold-s", "0.2")))
    idle = run_script("examples/record_raw_state.py", "--output", LOGS / "idle.jsonl", *LABELS,
                      "--pose-note", "desk rehearsal", "--seconds", "1", "--hz", "4", "--simulate",
                      "--acknowledge-connect-handshake", stdin=IDLE_ANSWERS)
    assert idle.returncode == 0, idle.stderr[-500:]
    for script, name, answers, extra in runs:
        if name == "stream.jsonl" and not HAS_RUCKIG:
            continue                     # bench_stream needs the optional planning extra
        result = run_script(script, "--output", LOGS / name, *LABELS, "--start-pose-note", "desk rehearsal",
                            *extra, "--simulate", "--acknowledge-supervised-motion", stdin=answers)
        assert result.returncode == 0, (script, result.stdout[-500:], result.stderr[-500:])


def tearDownModule():
    _tmp.cleanup()


class SheetTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.dir = Path(folder.name)

    def copy(self, *names):
        names = [name for name in names if (LOGS / name).exists()]
        for name in names:
            for path in LOGS.glob(name.replace(".jsonl", "*.jsonl")):
                shutil.copy(path, self.dir / path.name)
        return [self.dir / name for name in names]

    def sheet(self, paths, phone=None):
        rows, problems = lab_sheet.sheet(paths, phone or {})
        return {row["row"]: row for row in rows}, problems

    def test_rows_come_out_in_sheet_order_and_each_says_value_or_by_hand(self):
        rows, problems = self.sheet(self.copy("base.jsonl", "gripper.jsonl", "stream.jsonl", "idle.jsonl"))
        self.assertEqual(problems, [])
        self.assertEqual(list(rows)[:10], [str(n) for n in range(1, 11)])
        for row in rows.values():
            self.assertTrue(row["by_hand"] or (row["value"] and row["source"] and row["file"]), row)
        for number in ("1", "3", "4", "5", "6", "9"):
            self.assertTrue(rows[number]["by_hand"], number)

    def test_row_2_counts_right_after_home(self):
        row = self.sheet(self.copy("base.jsonl"))[0]["2"]
        self.assertIn("largest 0 counts", row["value"])
        self.assertEqual(row["source"], "home_complete")
        self.assertEqual(row["file"], "base.jsonl")

    def test_row_2_keeps_the_largest_count_over_every_home(self):
        first, = self.copy("base.jsonl")
        second = self.dir / "base-02.jsonl"
        for path, joint, counts in ((first, "base", 100), (second, "shoulder", 1)):
            rows = [json.loads(line) for line in first.read_text(encoding="utf-8").splitlines()]
            home = next(r for r in rows if r["type"] == "home_complete")["state"]["signed_encoder_counts"]
            home.update(dict.fromkeys(home, 0), **{joint: counts})
            path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        self.assertIn("largest 100 counts (base)", self.sheet([first, second])[0]["2"]["value"])

    def test_row_2_needs_a_vendor_home(self):
        path, = self.copy("base.jsonl")
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        next(r for r in rows if r["type"] == "home_complete")["method"] = "legacy"
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        self.assertIn("vendor Home", self.sheet([path])[0]["2"]["by_hand"])

    def test_row_5_is_measured_only_with_a_phone_angle(self):
        paths = self.copy("base.jsonl")
        row = self.sheet(paths)[0]["5"]
        self.assertIn("--phone base=", row["by_hand"])
        self.assertIn("142 counts", row["by_hand"])          # the commanded figure, as context only
        row = self.sheet(paths, {"base": 0.8})[0]["5"]
        self.assertIn("177.5 counts per measured degree", row["value"])
        self.assertEqual(row["source"], "bench_joint before_jog/after_jog")

    def test_row_7_drift_with_motors_on_between_home_and_the_move(self):
        path, = self.copy("base.jsonl")
        row = self.sheet([path])[0]["7"]
        self.assertIn("largest 0 counts", row["value"])
        self.assertIn("home_complete", row["source"])
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        before = next(r for r in rows if r["type"] == "before_jog")["state"]["encoder_counts"]
        before["shoulder"] += 3
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        self.assertIn("largest 3 counts (shoulder)", self.sheet([path])[0]["7"]["value"])

    def test_row_8_takes_the_largest_opening_over_every_file(self):
        full, = self.copy("gripper.jsonl")
        partial = self.dir / "a-partial.jsonl"
        rows = [json.loads(line) for line in full.read_text(encoding="utf-8").splitlines()]
        next(r for r in rows if r["type"] == "after_gripper")["result"].update(moved_counts=1200, full_travel=False)
        partial.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        row = self.sheet([partial, full])[0]["8"]
        self.assertIn("4999 counts", row["value"])
        self.assertEqual(row["file"], "gripper.jsonl")

    def test_a_run_that_failed_before_its_plan_is_still_reviewed(self):
        path, = self.copy("gripper.jsonl")
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        cut = next(i for i, r in enumerate(rows) if r["type"] == "gripper_plan")
        path.write_text("".join(json.dumps(r) + "\n" for r in rows[:cut] + [{"type": "session_failed"}]),
                        encoding="utf-8")
        self.assertTrue(any("gripper.jsonl" in p for p in self.sheet([path])[1]))

    def test_a_simulated_controller_log_beside_a_real_log_is_a_mix(self):
        path, = self.copy("base.jsonl")
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        rows[0]["data_source"] = "real"
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        self.assertTrue(any("mixed" in p for p in self.sheet([path])[1]))

    def test_row_8_gripper_stroke_closed_to_open(self):
        row = self.sheet(self.copy("gripper.jsonl"))[0]["8"]
        self.assertIn("4999 counts closed to open", row["value"])
        self.assertEqual(row["file"], "gripper.jsonl")

    @unittest.skipUnless(HAS_RUCKIG, "bench_stream needs ruckig")
    def test_row_10_largest_following_error_reads_the_stream_trace(self):
        path, = self.copy("stream.jsonl")
        rows = self.sheet([path])[0]
        self.assertIn("of 70", rows["10"]["value"])
        self.assertIn("stream.controller.jsonl", rows["10"]["file"])
        trace = self.dir / "stream.controller.jsonl"
        lines = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
        reply = bytearray(encode_packet({}))
        reply[ERROR_OFFSETS[2]:ERROR_OFFSETS[2] + 2] = (0x10000 - 60).to_bytes(2, "little")   # elbow -60
        traced = next(r for r in lines if r.get("event") == "stream_trace")
        traced["packets"] = [{"direction": "in", "host_monotonic_ns": 1, "hex": reply.hex()}]
        trace.write_text("".join(json.dumps(r) + "\n" for r in lines), encoding="utf-8")
        row = self.sheet([path])[0]["10"]
        self.assertIn("largest 60 counts (elbow)", row["value"])
        self.assertIn("within 15 of 70", row["value"])

    @unittest.skipUnless(HAS_RUCKIG, "bench_stream needs ruckig")
    def test_extra_rows_stream_tracking_and_gripper_times(self):
        rows = self.sheet(self.copy("gripper.jsonl", "stream.jsonl"))[0]
        self.assertRegex(rows["stream tracking"]["value"], r"base \d+% lag \d+ counts, overshoot \d+")
        self.assertIn("follow_report", rows["stream tracking"]["source"])
        self.assertRegex(rows["gripper times"]["value"], r"open \d+\.\d\d s, close \d+\.\d\d s")

    def test_missing_logs_leave_rows_by_hand_with_what_to_read(self):
        rows, problems = self.sheet([])
        self.assertEqual(problems, [])
        self.assertTrue(rows["8"]["by_hand"])
        self.assertIn("bench_gripper", rows["8"]["by_hand"])
        missing = self.dir / "nope.jsonl"
        rows, problems = self.sheet([missing])
        self.assertTrue(any("nope.jsonl" in p for p in problems))

    def test_a_corrupt_line_is_a_problem_and_the_other_files_still_count(self):
        bad = self.dir / "bad.jsonl"
        bad.write_text('{"type": "session"}\n{not json\n', encoding="utf-8")
        paths = self.copy("gripper.jsonl") + [bad]
        rows, problems = self.sheet(paths)
        self.assertTrue(any("bad.jsonl" in p and "invalid JSON" in p for p in problems))
        self.assertIn("4999", rows["8"]["value"])

    def test_a_flagged_run_says_so_on_the_rows_it_fills(self):
        path, = self.copy("gripper.jsonl")
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        path.write_text("".join(json.dumps(r) + "\n" for r in rows if r["type"] != "operator_observation"),
                        encoding="utf-8")
        self.assertIn("review problems", self.sheet([path])[0]["8"]["value"])

    def test_simulated_logs_get_a_banner_and_mixing_with_real_is_a_problem(self):
        paths = self.copy("base.jsonl")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            lab_summary.main([str(paths[0]), "--sheet"])
        self.assertTrue(out.getvalue().startswith("SIMULATED"))
        real, = self.copy("gripper.jsonl")
        rows = [json.loads(line) for line in real.read_text(encoding="utf-8").splitlines()]
        rows[0]["data_source"] = "real"
        real.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        self.assertTrue(any("mixed" in p for p in self.sheet(paths + [real])[1]))

    def test_cli_sheet_mode_prints_rows_and_exit_code(self):
        paths = self.copy("base.jsonl")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = lab_summary.main([str(p) for p in paths] + ["--sheet", "--phone", "base=0.8"])
        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertLess(text.index("\n2 "), text.index("\n5 "))
        self.assertIn("by hand:", text)
        self.assertIn("not a safety", text)
        bad = self.dir / "bad.jsonl"
        bad.write_text("{not json\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(lab_summary.main([str(bad), "--sheet"]), 1)


if __name__ == "__main__":
    unittest.main()
