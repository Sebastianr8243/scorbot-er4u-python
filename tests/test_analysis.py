"""Session analysis: defined measures, CSV export, catalogue and comparison."""

import json
from pathlib import Path
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
STATE = {"encoder_counts": {"base": 0}, "home_switch_bits": 0, "connected": True}


def record_sim_session(root, jogs=(("base", 1.0), ("base", -1.0)), data_source="simulated"):
    """Record home + jogs exactly the way the lab scripts do, with the simulator."""
    from scorbot.session import SessionWriter
    from scorbot.simulated import SimulatedScorbot
    with SessionWriter.create(root, data_source=data_source, robot_id="sim-arm") as rec, \
            SimulatedScorbot() as robot:
        rec.log_state(robot.get_state())
        robot.enable()
        cid = rec.log_command("home", {"start_position_confirmed": True})
        robot.home(start_position_confirmed=True)
        rec.log_command_result(cid, "completed")
        rec.log_state(robot.get_state())
        for joint, delta in jogs:
            before = robot.get_state()
            plan = robot.preview_jog(joint, delta,
                                     starting_signed_counts=before.signed_encoder_counts)
            rec.log_state(before)
            cid = rec.log_command("jog_joint", {"joint": joint, "delta_degrees": delta,
                                                "motor_count_deltas": plan["motor_count_deltas"]})
            after = robot.jog_joint(joint, delta)
            rec.log_command_result(cid, "completed")
            rec.log_state(after)
    return rec.path


def writer(root, data_source="synthetic"):
    from scorbot.session import SessionWriter
    return SessionWriter.create(root, data_source=data_source, robot_id="test-arm")


def state(base, **extra):
    return {"encoder_counts": {"base": base, **extra}, "home_switch_bits": 0,
            "connected": True}


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


class CommandRecordTests(TempDirCase):
    def records(self, path):
        from scorbot.session import load_session
        from scorbot.session.analysis import command_records
        return command_records(load_session(path))

    def test_simulated_jogs_match_the_plan_exactly(self):
        records = self.records(record_sim_session(self.root))
        jogs = [r for r in records if r.kind == "jog_joint"]
        self.assertEqual(len(jogs), 2)
        for record in jogs:
            self.assertEqual(record.status, "completed")
            self.assertEqual(record.observed_counts["base"], record.planned_counts["base"])
            self.assertIn("base", record.count_error)
            self.assertEqual(set(record.count_error.values()), {0})  # no motor off plan
            self.assertGreaterEqual(record.recorded_duration_ms, 0)
        self.assertEqual({r.planned_counts["base"] for r in jogs}, {142, -142})

    def test_command_without_result(self):
        with writer(self.root) as rec:
            rec.log_state(state(10))
            rec.log_command("jog_joint", {"motor_count_deltas": {"base": 5}})
        [record] = self.records(rec.path)
        self.assertEqual(record.status, "no_result")
        self.assertIsNone(record.recorded_duration_ms)
        self.assertEqual(record.observed_counts, {})
        self.assertIn("no result", record.note)

    def test_command_without_states_explains_why(self):
        with writer(self.root) as rec:
            cid = rec.log_command("jog_joint", {"motor_count_deltas": {"base": 5}})
            rec.log_command_result(cid, "completed")
        [record] = self.records(rec.path)
        self.assertEqual(record.observed_counts, {})
        self.assertIn("no state before", record.note)

    def test_back_to_back_commands_share_the_state_between_them(self):
        with writer(self.root) as rec:
            rec.log_state(state(0))
            first = rec.log_command("jog_joint", {"motor_count_deltas": {"base": 5}})
            rec.log_command_result(first, "completed")
            rec.log_state(state(5))
            second = rec.log_command("jog_joint", {"motor_count_deltas": {"base": 5}})
            rec.log_command_result(second, "completed")
            rec.log_state(state(10))
        one, two = self.records(rec.path)
        self.assertEqual(one.state_after_seq, two.state_before_seq)
        self.assertEqual(one.count_error, {"base": 0})
        self.assertEqual(two.count_error, {"base": 0})

    def test_states_from_another_command_are_never_borrowed(self):
        with writer(self.root) as rec:
            rec.log_state(state(0))
            first = rec.log_command("jog_joint", {})
            rec.log_command_result(first, "completed")
            second = rec.log_command("jog_joint", {})  # no state between the two
            rec.log_command_result(second, "completed")
            rec.log_state(state(10))
        one, two = self.records(rec.path)
        self.assertIn("no state after", one.note)
        self.assertIn("no state before", two.note)

    def test_empty_session_has_no_records(self):
        with writer(self.root) as rec:
            pass
        self.assertEqual(self.records(rec.path), [])

    def test_joints_missing_from_a_state_are_skipped(self):
        with writer(self.root) as rec:
            rec.log_state(state(0, elbow=3))
            cid = rec.log_command("jog_joint", {"motor_count_deltas": {"base": 2}})
            rec.log_command_result(cid, "completed")
            rec.log_state(state(2))  # no elbow this time
        [record] = self.records(rec.path)
        self.assertEqual(record.observed_counts, {"base": 2})

    def test_wrapped_counts_use_the_lab_review_arithmetic(self):
        with writer(self.root) as rec:
            rec.log_state(state(65530))
            cid = rec.log_command("jog_joint", {"motor_count_deltas": {"base": 10}})
            rec.log_command_result(cid, "completed")
            rec.log_state(state(5))  # wrapped past 65535
        [record] = self.records(rec.path)
        self.assertEqual(record.observed_counts["base"], 10)
        self.assertEqual(record.count_error, {"base": 0})

    def test_ambiguous_difference_is_reported_not_guessed(self):
        with writer(self.root) as rec:
            rec.log_state(state(0))
            cid = rec.log_command("jog_joint", {"motor_count_deltas": {"base": 1}})
            rec.log_command_result(cid, "completed")
            rec.log_state(state(32767))
        [record] = self.records(rec.path)
        self.assertEqual(record.observed_counts["base"], "ambiguous")
        self.assertEqual(record.count_error, {})

    def test_malformed_planned_counts_are_ignored(self):
        with writer(self.root) as rec:
            rec.log_state(state(0))
            cid = rec.log_command("jog_joint", {"motor_count_deltas": [1, 2]})
            rec.log_command_result(cid, "completed")
            rec.log_state(state(1))
        [record] = self.records(rec.path)
        self.assertEqual(record.planned_counts, {})
        self.assertEqual(record.count_error, {})


class SummaryAndComparisonTests(TempDirCase):
    def summary(self, path):
        from scorbot.session import load_session
        from scorbot.session.analysis import summarize
        return summarize(load_session(path))

    def test_identical_sources_pool(self):
        from scorbot.session.analysis import compare
        a = self.summary(record_sim_session(self.root / "a"))
        b = self.summary(record_sim_session(self.root / "b"))
        result = compare([a, b])
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.pooled["jog_joint"]["n"], 4)
        errors = result.pooled["jog_joint"]["max_abs_error"]
        self.assertIn("base", errors)
        self.assertEqual(set(errors.values()), {0})
        self.assertEqual(result.pooled_source, "simulated")

    def test_mixed_sources_never_pool(self):
        from scorbot.session.analysis import compare
        sim = self.summary(record_sim_session(self.root / "a"))
        with writer(self.root / "b") as rec:
            rec.log_note("synthetic")
        result = compare([sim, self.summary(rec.path)])
        self.assertIsNone(result.pooled)
        self.assertEqual(result.exit_code, 1)
        self.assertIn("mixed", result.messages[0].lower())

    def test_errored_session_is_left_out_unless_asked(self):
        from scorbot.session.analysis import compare
        good = self.summary(record_sim_session(self.root / "a"))
        bad_path = record_sim_session(self.root / "b")
        meta = bad_path / "metadata.json"
        data = json.loads(meta.read_text())
        data["event_count"] = 999  # integrity error
        meta.write_text(json.dumps(data))
        bad = self.summary(bad_path)
        self.assertTrue(bad.errors)
        result = compare([good, bad])
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.pooled["jog_joint"]["n"], 2)
        self.assertIn(bad.session_id, result.excluded)
        included = compare([good, bad], include_damaged=True)
        self.assertEqual(included.pooled["jog_joint"]["n"], 4)

    def test_warning_only_session_is_pooled(self):
        from scorbot.session.analysis import compare
        good = self.summary(record_sim_session(self.root / "a"))
        path = record_sim_session(self.root / "b")
        meta = path / "metadata.json"
        data = json.loads(meta.read_text())
        del data["closed_cleanly"]  # "not closed cleanly" is only a warning
        meta.write_text(json.dumps(data))
        warned = self.summary(path)
        self.assertFalse(warned.errors)
        self.assertTrue(warned.warnings)
        self.assertEqual(compare([good, warned]).pooled["jog_joint"]["n"], 4)


class CsvSafetyTests(unittest.TestCase):
    def test_formula_like_text_is_quoted_and_numbers_are_not(self):
        from scorbot.session.analysis import csv_safe
        for text in ("=SUM(A1)", "+1", "-1 deg toward door", "@cmd"):
            self.assertEqual(csv_safe(text), "'" + text)
        self.assertEqual(csv_safe("base moved"), "base moved")
        self.assertEqual(csv_safe(-142), -142)
        self.assertEqual(csv_safe(-1.5), -1.5)
        self.assertIsNone(csv_safe(None))


def cli(*args):
    import subprocess
    import sys
    return subprocess.run([sys.executable, "-m", "scorbot.session", *map(str, args)],
                          cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=120)


def read_csv(path):
    import csv
    with open(path, encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


class CliTests(TempDirCase):
    def test_bare_path_still_replays(self):
        path = record_sim_session(self.root)
        result = cli(path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SIMULATED DATA", result.stdout)

    def test_list_finds_nested_sessions_and_flags_broken_ones(self):
        sim = record_sim_session(self.root / "week1" / "sessions")
        with writer(self.root / "week2", data_source="real") as rec:
            rec.log_note("real")
        broken = record_sim_session(self.root / "week2")
        meta = json.loads((broken / "metadata.json").read_text())
        meta["event_count"] = 999
        (broken / "metadata.json").write_text(json.dumps(meta))
        (self.root / "stray.mcap").write_bytes(b"not an mcap file")
        (self.root / "empty" / "sessions").mkdir(parents=True)
        result = cli("list", self.root)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        for text in (sim.name, rec.path.name, broken.name, "SIMULATED", "REAL", "ERROR",
                     "stray.mcap"):
            self.assertIn(text, result.stdout)
        self.assertNotIn("Traceback", result.stdout + result.stderr)

    def test_export_writes_labelled_excel_safe_csvs(self):
        with writer(self.root, data_source="simulated") as rec:
            rec.log_state({**STATE, "simulated": True})
            cid = rec.log_command("jog_joint", {"motor_count_deltas": {"base": 1}})
            rec.log_command_result(cid, "completed")
            rec.log_state({"encoder_counts": {"base": 1}, "home_switch_bits": 0,
                           "connected": True, "simulated": True})
            rec.log_note("-1 deg toward door, déjà vu ✓")
        result = cli("export", rec.path)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = rec.path / "csv"
        events, states, commands = (read_csv(out / f"{name}.csv")
                                    for name in ("events", "states", "commands"))
        from scorbot.session.analysis import COMMAND_COLUMNS, EVENT_COLUMNS, STATE_COLUMNS
        self.assertEqual(list(events[0]), EVENT_COLUMNS)
        self.assertEqual(list(states[0]), STATE_COLUMNS)
        self.assertEqual(list(commands[0]), COMMAND_COLUMNS)
        for row in events + states + commands:
            self.assertEqual(row["data_source"], "simulated")
            self.assertEqual(row["session_id"], rec.path.name)
        self.assertEqual(events[-1]["summary"], "'-1 deg toward door, déjà vu ✓")
        self.assertEqual(json.loads(commands[0]["count_error"]), {"base": 0})
        again = cli("export", rec.path)
        self.assertEqual(again.returncode, 2)
        self.assertEqual(cli("export", rec.path, "--force").returncode, 0)

    def test_export_of_a_crashed_session_keeps_what_was_read(self):
        from scorbot.session import SessionWriter
        rec = SessionWriter.create(self.root, data_source="synthetic", robot_id="x")
        rec.log_note("before crash")
        rec._stream.close()  # crash: never finished
        result = cli("export", rec.path, "--out", self.root / "out")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("not closed cleanly", result.stdout)
        self.assertEqual(len(read_csv(self.root / "out" / "events.csv")), 1)

    def test_compare_prints_writes_csv_and_counts_duplicates_once(self):
        a = record_sim_session(self.root / "a")
        b = record_sim_session(self.root / "b")
        table = self.root / "compare.csv"
        result = cli("compare", a, b, a, "--csv", table)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("POOLED", result.stdout)
        self.assertIn("duplicate", result.stdout.lower())
        rows = read_csv(table)
        pooled = [r for r in rows if r["session_id"] == "POOLED" and r["kind"] == "jog_joint"]
        self.assertEqual(pooled[0]["n"], "4")

    def test_compare_refuses_to_pool_mixed_sources(self):
        a = record_sim_session(self.root / "a")
        with writer(self.root / "b", data_source="real") as rec:
            rec.log_note("real")
        result = cli("compare", a, rec.path)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Mixed data sources", result.stdout)
        self.assertNotIn("POOLED", result.stdout)

    def test_compare_agrees_with_the_lab_review_on_a_bench_run(self):
        import subprocess
        import sys
        logs = self.root / "rehearsal"
        labels = ["--robot-id", "arm", "--arm-label", "a", "--controller-label", "c",
                  "--driver", "none", "--operator", "t"]
        bench = subprocess.run(
            [sys.executable, str(REPO_ROOT / "examples" / "bench_joint.py"),
             "--output", str(logs / "bench.jsonl"), *labels, "--start-pose-note", "desk",
             "--joint", "base", "--delta", "1", "--simulate",
             "--acknowledge-supervised-motion"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=120,
            input="HOME\nok\nHOME_OK\nMOVE\nleft\nnone\nnone\nnone\n")
        self.assertEqual(bench.returncode, 0, bench.stderr)
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        try:
            import review_lab_logs
        finally:
            sys.path.remove(str(REPO_ROOT / "scripts"))
        review = review_lab_logs.review_bench(logs / "bench.jsonl")["count_deltas"]
        session = json.loads((logs / "bench.jsonl").read_text().splitlines()[0])["mcap_session"]
        self.assertEqual(cli("export", logs / "sessions" / session).returncode, 0)
        [jog] = [r for r in read_csv(logs / "sessions" / session / "csv" / "commands.csv")
                 if r["kind"] == "jog_joint"]
        observed = json.loads(jog["observed_counts"])
        planned = json.loads(jog["planned_counts"])
        for motor, values in review.items():
            self.assertEqual(observed.get(motor), values["observed"], motor)
            self.assertEqual(planned.get(motor, 0), values["planned"], motor)


class SecondReviewFixTests(TempDirCase):
    def damage(self, path):
        meta = json.loads((path / "metadata.json").read_text())
        meta["event_count"] = 999
        (path / "metadata.json").write_text(json.dumps(meta))
        return path

    def test_pooled_row_is_labelled_with_the_pooled_source_not_the_first_argument(self):
        bad_sim = self.damage(record_sim_session(self.root / "a"))
        real = []
        for name in ("b", "c"):
            with writer(self.root / name, data_source="real") as rec:
                rec.log_state(state(0))
                cid = rec.log_command("jog_joint", {"motor_count_deltas": {"base": 142}})
                rec.log_command_result(cid, "completed")
                rec.log_state(state(140))
            real.append(rec.path)
        table = self.root / "c.csv"
        result = cli("compare", bad_sim, *real, "--csv", table)
        self.assertEqual(result.returncode, 1)
        pooled_lines = [l for l in result.stdout.splitlines() if l.startswith("POOLED")]
        self.assertTrue(pooled_lines, result.stdout)
        for line in pooled_lines:
            self.assertIn("REAL", line)
            self.assertNotIn("SIMULATED", line)
        pooled = [r for r in read_csv(table) if r["session_id"] == "POOLED"]
        self.assertTrue(pooled)
        self.assertEqual({r["data_source"] for r in pooled}, {"real"})

    def test_unplanned_motor_motion_shows_up_as_count_error(self):
        from scorbot.session import load_session
        from scorbot.session.analysis import command_records
        with writer(self.root) as rec:
            rec.log_state(state(0, shoulder=100))
            cid = rec.log_command("jog_joint", {"motor_count_deltas": {"base": 142}})
            rec.log_command_result(cid, "completed")
            rec.log_state(state(142, shoulder=400))  # shoulder moved uncommanded
        [record] = command_records(load_session(rec.path))
        self.assertEqual(record.count_error, {"base": 0, "shoulder": 300})

    def test_only_session_mcap_files_are_sessions(self):
        path = record_sim_session(self.root / "sessions")
        (path / "a_trimmed.mcap").write_bytes((path / "session.mcap").read_bytes())
        listing = cli("list", self.root)
        session_rows = [line for line in listing.stdout.splitlines()
                        if line.startswith("OK") and path.name in line]
        self.assertEqual(len(session_rows), 1, listing.stdout)
        self.assertIn("not a session", listing.stdout)
        result = cli("compare", self.root)
        self.assertNotIn("duplicate", result.stdout.lower())
        self.assertIn("POOLED", result.stdout)

    def test_a_path_matching_no_session_is_an_error(self):
        path = record_sim_session(self.root / "a")
        result = cli("compare", path, self.root / "runB_typo")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("runB_typo", result.stderr)

    def test_null_encoder_counts_do_not_crash_replay_or_export(self):
        with writer(self.root) as rec:
            rec.log_state({"encoder_counts": None, "home_switch_bits": 0, "connected": True})
        self.assertEqual(cli(rec.path).returncode, 0)
        self.assertEqual(cli("export", rec.path).returncode, 0)

    def test_export_rows_are_labelled_even_without_metadata(self):
        from scorbot.session import load_session
        from scorbot.session.analysis import event_rows
        with writer(self.root, data_source="simulated") as rec:
            rec.log_note("x")
        session = load_session(rec.path)
        session.metadata = {}  # as if no metadata survived at all
        [row] = event_rows(session)
        self.assertEqual(row["session_id"], str(session.path))
        self.assertEqual(row["data_source"], "unknown")


class CrashAuditTests(unittest.TestCase):
    def test_worker_crash_latches_and_leaves_an_audit_record(self):
        from scorbot import ScorbotError
        from scorbot.simulated import SimulatedScorbot
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "events.jsonl"
            robot = SimulatedScorbot(log_path=log, command_timeout=5.0).connect()
            try:
                robot.enable()
                robot.home(start_position_confirmed=True)
                robot.sim.inject("worker_crash")
                with self.assertRaises(ScorbotError) as caught:
                    robot.jog_joint("base", 1.0)
                self.assertIn("physical stop", str(caught.exception))
                self.assertIsNotNone(robot._fault)
                with self.assertRaises(ScorbotError):
                    robot.enable()
            finally:
                robot.disconnect()
            events = [json.loads(line)["event"] for line in log.read_text().splitlines()]
        self.assertIn("disable_skipped_worker_crashed", events)


if __name__ == "__main__":
    unittest.main()
