"""The hand-filled observation sheet (notes.md): parsing and validation."""

from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
