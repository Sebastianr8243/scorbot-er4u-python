"""The hand-filled observation sheet (notes.md): parsing, validation, CLI reporting."""

import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]

FILLED = """# Session x observation sheet
notes_schema: 1

Date / time: 2026-10-01 14:30
Stop operator: Alice
Recorder: Bob
E-stop tested before start: yes
Start pose matches photo: yes
Observed direction: toward the door, about 1 degree
Other joints moved: no
Python returned normally: yes
How run ended: normal return
Discrepancies: none
Reviewed by:

## Free notes
Base moved smoothly. Note: LEDs green the whole time.
"""


def new_session(root, data_source="simulated"):
    from scorbot.session import SessionWriter
    with SessionWriter.create(root, data_source=data_source, robot_id="arm") as rec:
        rec.log_note("x")
    return rec.path


def cli(*args):
    return subprocess.run([sys.executable, "-m", "scorbot.session", *map(str, args)],
                          cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=120)


class NotesParsingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def parse(self, text=None, name="notes.md", raw=None):
        from scorbot.session.notes import parse_notes
        path = self.root / name
        if raw is not None:
            path.write_bytes(raw)
        elif text is not None:
            path.write_text(text, encoding="utf-8")
        return parse_notes(path)

    def test_a_new_session_gets_a_versioned_blank_sheet(self):
        from scorbot.session.notes import parse_notes
        report = parse_notes(new_session(self.root) / "notes.md")
        self.assertEqual(report.status, "untouched")
        self.assertEqual(report.schema, 1)
        self.assertEqual(report.unknown, [])  # instruction lines are not answers

    def test_a_filled_sheet_is_complete(self):
        report = self.parse(FILLED)
        self.assertEqual(report.status, "complete", report.problems)
        self.assertEqual(report.fields["stop_operator"], "Alice")
        self.assertEqual(report.fields["run_ended"], "normal return")
        self.assertFalse(report.reviewed)
        self.assertEqual(report.problems, [])

    def test_free_notes_with_colons_are_not_fields(self):
        report = self.parse(FILLED)
        self.assertEqual(report.unknown, [])
        self.assertIn("LEDs green", report.free_notes)

    def test_time_values_stay_text(self):
        self.assertEqual(self.parse(FILLED).fields["date_time"], "2026-10-01 14:30")

    def test_missing_required_answer_is_incomplete(self):
        report = self.parse(FILLED.replace("Stop operator: Alice", "Stop operator:"))
        self.assertEqual(report.status, "incomplete")
        self.assertTrue(any("Stop operator" in p for p in report.problems))

    def test_run_end_must_be_a_known_value_or_other_with_text(self):
        bad = self.parse(FILLED.replace("normal return", "stopped"))
        self.assertEqual(bad.status, "incomplete")
        self.assertTrue(any("How run ended" in p for p in bad.problems))
        other = self.parse(FILLED.replace("normal return", "other: cable snagged"))
        self.assertEqual(other.status, "complete", other.problems)
        bare_other = self.parse(FILLED.replace("normal return", "other:"))
        self.assertEqual(bare_other.status, "incomplete")

    def test_yes_no_questions_accept_only_known_answers(self):
        bad = self.parse(FILLED.replace("E-stop tested before start: yes",
                                        "E-stop tested before start: maybe"))
        self.assertEqual(bad.status, "incomplete")
        for answer in ("YES", "No", "n/a", "not sure"):
            ok = self.parse(FILLED.replace("E-stop tested before start: yes",
                                           f"E-stop tested before start: {answer}"))
            self.assertEqual(ok.status, "complete", (answer, ok.problems))

    def test_labels_ignore_case_and_extra_spaces(self):
        report = self.parse(FILLED.replace("Stop operator: Alice", "stop   OPERATOR :  Alice"))
        self.assertEqual(report.fields["stop_operator"], "Alice")
        self.assertEqual(report.status, "complete")

    def test_unknown_labels_are_kept_and_reported(self):
        report = self.parse(FILLED.replace("Discrepancies: none",
                                           "Discrepancies: none\nWeather: sunny"))
        self.assertEqual(report.unknown, ["Weather"])
        self.assertEqual(report.status, "complete")

    def test_notepad_bom_and_crlf_are_handled(self):
        raw = b"\xef\xbb\xbf" + FILLED.replace("\n", "\r\n").encode("utf-8")
        self.assertEqual(self.parse(raw=raw).status, "complete")

    def test_missing_file_and_legacy_sheet(self):
        self.assertEqual(self.parse(name="absent.md").status, "missing")
        legacy = self.parse("# Session x observation sheet\n\n- Date / time:\n")
        self.assertEqual(legacy.status, "legacy")

    def test_reviewed_is_tracked_separately(self):
        report = self.parse(FILLED.replace("Reviewed by:", "Reviewed by: Carol 2026-10-02"))
        self.assertTrue(report.reviewed)
        self.assertEqual(report.status, "complete")


class NotesCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_replay_warns_about_an_unfilled_sheet_only_for_real_runs(self):
        real = new_session(self.root / "real", data_source="real")
        sim = new_session(self.root / "sim", data_source="simulated")
        def sheet_line(path):
            [line] = [line for line in cli(path).stdout.splitlines()
                      if "Observation sheet:" in line]
            return line

        self.assertIn("untouched", sheet_line(real))
        self.assertIn("WARNING", sheet_line(real))
        self.assertIn("untouched", sheet_line(sim))
        self.assertNotIn("WARNING", sheet_line(sim))

    def test_list_shows_the_sheet_status(self):
        path = new_session(self.root)
        (path / "notes.md").write_text(FILLED, encoding="utf-8")
        result = cli("list", self.root)
        self.assertIn("NOTES", result.stdout)
        self.assertIn("complete", result.stdout)

    def test_export_writes_labelled_notes_csv(self):
        path = new_session(self.root)
        (path / "notes.md").write_text(FILLED.replace("Alice", "-Alice"), encoding="utf-8")
        self.assertEqual(cli("export", path).returncode, 0)
        with open(path / "csv" / "notes.csv", encoding="utf-8-sig", newline="") as stream:
            rows = {row["field"]: row for row in csv.DictReader(stream)}
        self.assertEqual(rows["stop_operator"]["value"], "'-Alice")
        self.assertEqual(rows["run_ended"]["data_source"], "simulated")
        self.assertEqual(rows["notes_status"]["value"], "complete")

    def test_compare_rows_carry_notes_status_and_run_end(self):
        from test_analysis import record_sim_session
        path = record_sim_session(self.root)
        (path / "notes.md").write_text(FILLED, encoding="utf-8")
        table = self.root / "table.csv"
        self.assertIn(cli("compare", path, "--csv", table).returncode, (0,))
        with open(table, encoding="utf-8-sig", newline="") as stream:
            rows = [r for r in csv.DictReader(stream) if r["session_id"] != "POOLED"]
        self.assertTrue(rows)
        self.assertEqual({r["notes"] for r in rows}, {"complete"})
        self.assertEqual({r["run_ended"] for r in rows}, {"normal return"})
        self.assertTrue(json.dumps(rows))


if __name__ == "__main__":
    unittest.main()
