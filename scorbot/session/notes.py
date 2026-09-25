"""The operator's hand-filled observation sheet, ``notes.md``, in each session folder.

The sheet stays plain Markdown for editing in Notepad or VS Code: one
``Label: answer`` line per question, then free text under ``## Free notes``.
It is parsed with the standard library only. YAML is avoided on purpose:
YAML 1.1 reads ``14:30`` as 870 and ``no`` as False.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re

NOTES_SCHEMA = 1
FREE_NOTES_HEADING = "## Free notes"
YES_NO = ("yes", "no", "n/a", "not sure")
RUN_ENDINGS = ("normal return", "declined prompt", "emergency stop", "error")

# key, printed label, required, allowed answers (None = any text)
FIELDS = (
    ("date_time", "Date / time", True, None),
    ("stop_operator", "Stop operator", True, None),
    ("recorder", "Recorder", True, None),
    ("estop_tested", "E-stop tested before start", True, YES_NO),
    ("start_pose_matches", "Start pose matches photo", True, YES_NO),
    ("observed_direction", "Observed direction", False, None),
    ("other_joints_moved", "Other joints moved", False, YES_NO),
    ("python_returned", "Python returned normally", False, None),
    ("run_ended", "How run ended", True, RUN_ENDINGS),
    ("discrepancies", "Discrepancies", True, None),
    ("reviewed_by", "Reviewed by", False, None),
)
_BY_LABEL = {" ".join(label.lower().split()): key for key, label, _, _ in FIELDS}
_LABELS = {key: label for key, label, _, _ in FIELDS}
_FIELD_LINE = re.compile(r"^([^:#]+?)\s*:\s*(.*)$")


def template(session_id: str) -> str:
    lines = [f"# Session {session_id} observation sheet",
             "",
             "> Fill in during or right after the run: one answer after each colon.",
             "> Yes/no questions: yes, no, n/a or not sure.",
             "> How run ended: normal return, declined prompt, emergency stop, error,",
             "> or other: <what happened>.",
             "",
             f"notes_schema: {NOTES_SCHEMA}",
             ""]
    lines += [f"{label}:" for _, label, _, _ in FIELDS]
    lines += ["", FREE_NOTES_HEADING, ""]
    return "\n".join(lines)


@dataclass
class NotesReport:
    status: str                      # missing, legacy, untouched, incomplete, complete
    schema: int | None = None
    fields: dict = field(default_factory=dict)
    problems: list = field(default_factory=list)
    unknown: list = field(default_factory=list)
    free_notes: str = ""

    @property
    def reviewed(self) -> bool:
        return bool(self.fields.get("reviewed_by"))


def parse_notes(path) -> NotesReport:
    path = Path(path)
    if not path.is_file():
        return NotesReport("missing", problems=["no notes.md in the session folder"])
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")  # strips the BOM Notepad adds
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")

    head, _, free = text.partition(FREE_NOTES_HEADING)
    report = NotesReport("complete", free_notes=free.strip())
    for line in head.splitlines():  # handles CRLF
        if line.lstrip().startswith(">"):
            continue  # instructions, not answers
        match = _FIELD_LINE.match(line.strip())
        if not match:
            continue
        label, value = match.group(1).strip(), match.group(2).strip()
        normalised = " ".join(label.lower().split())
        if normalised == "notes_schema":
            report.schema = int(value) if value.isdigit() else None
        elif normalised in _BY_LABEL:
            report.fields[_BY_LABEL[normalised]] = value
        else:
            report.unknown.append(label)

    if report.schema is None:
        report.status = "legacy"
        report.problems.append("sheet has no notes_schema line (older format); not checked")
        return report
    if not any(report.fields.values()) and not report.free_notes:
        report.status = "untouched"
        report.problems.append("observation sheet was never filled in")
        return report

    for key, label, required, allowed in FIELDS:
        value = report.fields.get(key, "")
        if not value:
            if required:
                report.problems.append(f"'{label}' is not answered")
            continue
        if allowed is RUN_ENDINGS:
            lowered = value.lower()
            if lowered.startswith("other:"):
                if not value.split(":", 1)[1].strip():
                    report.problems.append(f"'{label}: other:' needs a description")
            elif lowered not in RUN_ENDINGS:
                report.problems.append(f"'{label}' must be one of {', '.join(RUN_ENDINGS)}, "
                                       f"or other: <text>; found {value!r}")
        elif allowed is not None and value.lower() not in allowed:
            report.problems.append(f"'{label}' must be {', '.join(allowed)}; found {value!r}")
    if report.problems:
        report.status = "incomplete"
    return report


def label_for(key: str) -> str:
    return _LABELS.get(key, key)
