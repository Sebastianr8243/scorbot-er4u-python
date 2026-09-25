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
            self.assertEqual(record.count_error, {"base": 0})
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
        self.assertEqual(result.pooled["jog_joint"]["max_abs_error"], {"base": 0})

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


if __name__ == "__main__":
    unittest.main()
