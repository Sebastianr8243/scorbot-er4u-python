"""Defined, reproducible measures over recorded sessions. Pure functions, no printing.

A *command record* joins one command with its result and with the robot
states recorded just before and just after it. Observed motor-count changes
use ``scorbot.calibration.signed_count_delta`` on raw encoder counts, the
same arithmetic as ``scripts/review_lab_logs.py``, so both tools report the
same numbers for a run. ``recorded_duration_ms`` is the time between two
recorder entries (it includes script overhead), not how long the arm moved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import statistics

from ..calibration import signed_count_delta
from ..state import JOINTS

AMBIGUOUS = "ambiguous"
_FORMULA_START = ("=", "+", "-", "@")


@dataclass
class CommandRecord:
    command_id: str
    kind: str
    params: dict
    command_seq: int
    status: str
    result_seq: int | None
    recorded_duration_ms: float | None
    state_before_seq: int | None
    state_after_seq: int | None
    state_gap_ms: float | None
    planned_counts: dict = field(default_factory=dict)
    observed_counts: dict = field(default_factory=dict)
    count_error: dict = field(default_factory=dict)
    note: str = ""


@dataclass
class RunSummary:
    session_id: str
    path: str
    data_source: str
    started_utc: str | None
    robot_id: str | None
    task: str | None
    n_events: int
    errors: list[str]
    warnings: list[str]
    status_counts: dict
    # Raw per-kind values kept so pooled statistics are exact, not averages of averages.
    kinds: dict


@dataclass
class Comparison:
    rows: list[dict]
    pooled: dict | None
    excluded: list[str]
    messages: list[str]
    exit_code: int


def csv_safe(value):
    """Stop spreadsheet programs from reading free text such as '-1 deg' as a formula."""
    if isinstance(value, str) and value.startswith(_FORMULA_START):
        return "'" + value
    return value


def event_summary(event: dict) -> str:
    payload, topic = event["payload"], event["topic"]
    if topic == "/robot/state":
        counts = payload.get("encoder_counts", {})
        joints = " ".join(f"{name}={value}" for name, value in counts.items())
        return f"{joints} switches={payload.get('home_switch_bits')}"
    if topic == "/robot/command":
        return f"{payload['command_id']} {payload['kind']} {payload['params']}"
    if topic == "/robot/command_result":
        return f"{payload['command_id']} {payload['status']}"
    if topic.endswith("/image"):
        rec = payload["_rec"]
        return (f"frame {rec.get('frame_number')} {payload['format']} "
                f"{rec.get('width')}x{rec.get('height')}")
    if topic.endswith("/detections"):
        return (f"frame {payload['frame_number']} {payload['label']} "
                f"conf={payload['confidence']:.2f}")
    if topic == "/operator/decision":
        return f"choice={payload['choice']}"
    if topic == "/session/fault":
        return f"FAULT {payload['message']}"
    if topic == "/session/note":
        return payload["text"][:60]
    return ""


def _logged(event):
    return event["payload"]["_rec"]["logged_monotonic_ns"]


def _observed(event):
    return event["payload"]["_rec"].get("observed_monotonic_ns")


def _planned(params) -> dict:
    deltas = params.get("motor_count_deltas") if isinstance(params, dict) else None
    if not isinstance(deltas, dict):
        return {}
    return {motor: value for motor, value in deltas.items() if type(value) is int}


def _observed_counts(before: dict, after: dict) -> dict:
    first = before.get("encoder_counts") or {}
    second = after.get("encoder_counts") or {}
    result = {}
    for motor in first.keys() & second.keys():
        a, b = second[motor], first[motor]
        if not all(type(v) is int and 0 <= v <= 65535 for v in (a, b)):
            continue
        try:
            result[motor] = signed_count_delta(a, b)
        except ValueError:
            result[motor] = AMBIGUOUS
    return dict(sorted(result.items()))


def command_records(session) -> list[CommandRecord]:
    events = session.events
    commands = [i for i, e in enumerate(events) if e["topic"] == "/robot/command"]
    states = [i for i, e in enumerate(events) if e["topic"] == "/robot/state"]
    results = {}
    for i, event in enumerate(events):
        if event["topic"] == "/robot/command_result":
            results.setdefault(event["payload"].get("command_id"), i)

    records = []
    previous_end = -1
    for n, position in enumerate(commands):
        command = events[position]["payload"]
        command_id = command.get("command_id")
        result_position = results.get(command_id)
        if result_position is not None and result_position < position:
            result_position = None
        next_start = commands[n + 1] if n + 1 < len(commands) else len(events)
        before = next((i for i in reversed(states) if previous_end < i < position), None)
        after = (next((i for i in states if result_position < i < next_start), None)
                 if result_position is not None else None)
        notes = []
        if result_position is None:
            notes.append("no result")
        if before is None:
            notes.append("no state before")
        if result_position is not None and after is None:
            notes.append("no state after")

        planned = _planned(command.get("params"))
        observed = (_observed_counts(events[before]["payload"], events[after]["payload"])
                    if before is not None and after is not None else {})
        error = {motor: observed[motor] - value for motor, value in planned.items()
                 if type(observed.get(motor)) is int}
        records.append(CommandRecord(
            command_id=command_id, kind=command.get("kind"), params=command.get("params"),
            command_seq=events[position]["seq"],
            status=(events[result_position]["payload"].get("status")
                    if result_position is not None else "no_result"),
            result_seq=events[result_position]["seq"] if result_position is not None else None,
            recorded_duration_ms=((_logged(events[result_position]) - _logged(events[position]))
                                  / 1e6 if result_position is not None else None),
            state_before_seq=events[before]["seq"] if before is not None else None,
            state_after_seq=events[after]["seq"] if after is not None else None,
            state_gap_ms=((_logged(events[after]) - _logged(events[result_position])) / 1e6
                          if after is not None else None),
            planned_counts=planned, observed_counts=observed, count_error=error,
            note="; ".join(notes)))
        previous_end = result_position if result_position is not None else position
    return records


def _base(session):
    return _logged(session.events[0]) if session.events else 0


def _ms(value, base):
    return None if value is None else round((value - base) / 1e6, 3)


def _identity(session) -> dict:
    return {"session_id": session.metadata.get("session_id"),
            "data_source": session.metadata.get("data_source")}


def event_rows(session) -> list[dict]:
    base, ident = _base(session), _identity(session)
    return [{**ident, "seq": e["seq"], "topic": e["topic"],
             "logged_ms": _ms(_logged(e), base), "observed_ms": _ms(_observed(e), base),
             "summary": event_summary(e)} for e in session.events]


STATE_COLUMNS = (["session_id", "data_source", "seq", "logged_ms", "observed_ms"]
                 + [f"count_{j}" for j in JOINTS] + [f"signed_{j}" for j in JOINTS]
                 + ["home_switch_bits", "enabled", "homed", "fault", "simulated"])
EVENT_COLUMNS = ["session_id", "data_source", "seq", "topic", "logged_ms", "observed_ms",
                 "summary"]
COMMAND_COLUMNS = ["session_id", "data_source", "command_id", "kind", "status",
                   "command_seq", "result_seq", "recorded_duration_ms", "state_before_seq",
                   "state_after_seq", "state_gap_ms", "planned_counts", "observed_counts",
                   "count_error", "params", "note"]


def state_rows(session) -> list[dict]:
    base, ident, rows = _base(session), _identity(session), []
    for e in session.events:
        if e["topic"] != "/robot/state":
            continue
        payload = e["payload"]
        counts = payload.get("encoder_counts") or {}
        signed = payload.get("signed_encoder_counts") or {}
        row = {**ident, "seq": e["seq"], "logged_ms": _ms(_logged(e), base),
               "observed_ms": _ms(_observed(e), base)}
        row.update({f"count_{j}": counts.get(j) for j in JOINTS})
        row.update({f"signed_{j}": signed.get(j) for j in JOINTS})
        row.update({key: payload.get(key) for key in
                    ("home_switch_bits", "enabled", "homed", "fault", "simulated")})
        rows.append(row)
    return rows


def command_rows(session) -> list[dict]:
    ident = _identity(session)
    return [{**ident, "command_id": r.command_id, "kind": r.kind, "status": r.status,
             "command_seq": r.command_seq, "result_seq": r.result_seq,
             "recorded_duration_ms": r.recorded_duration_ms,
             "state_before_seq": r.state_before_seq, "state_after_seq": r.state_after_seq,
             "state_gap_ms": r.state_gap_ms,
             "planned_counts": json.dumps(r.planned_counts, sort_keys=True),
             "observed_counts": json.dumps(r.observed_counts, sort_keys=True),
             "count_error": json.dumps(r.count_error, sort_keys=True),
             "params": json.dumps(r.params, sort_keys=True), "note": r.note}
            for r in command_records(session)]


def summarize(session) -> RunSummary:
    meta = session.metadata
    kinds: dict = {}
    status_counts: dict = {}
    for record in command_records(session):
        status_counts[record.status] = status_counts.get(record.status, 0) + 1
        raw = kinds.setdefault(record.kind, {"n": 0, "durations": [], "errors": {}})
        raw["n"] += 1
        if record.recorded_duration_ms is not None:
            raw["durations"].append(record.recorded_duration_ms)
        for motor, value in record.count_error.items():
            raw["errors"].setdefault(motor, []).append(value)
    return RunSummary(
        session_id=meta.get("session_id") or str(session.path), path=str(session.path),
        data_source=meta.get("data_source", "unknown"), started_utc=meta.get("started_utc"),
        robot_id=meta.get("robot_id"), task=meta.get("task"), n_events=len(session.events),
        errors=[f.message for f in session.errors], warnings=[f.message for f in session.warnings],
        status_counts=status_counts, kinds=kinds)


def kind_stats(raw: dict) -> dict:
    durations, errors = raw["durations"], raw["errors"]
    return {
        "n": raw["n"],
        "median_ms": round(statistics.median(durations), 3) if durations else None,
        "max_ms": round(max(durations), 3) if durations else None,
        "mean_abs_error": {m: round(statistics.fmean(abs(v) for v in vals), 3)
                           for m, vals in sorted(errors.items())},
        "max_abs_error": {m: max(abs(v) for v in vals) for m, vals in sorted(errors.items())},
    }


def compare(summaries: list[RunSummary], include_damaged: bool = False) -> Comparison:
    rows, messages, excluded, seen = [], [], [], set()
    usable = []
    for summary in summaries:
        if summary.session_id in seen:
            messages.append(f"Skipped duplicate session {summary.session_id}")
            continue
        seen.add(summary.session_id)
        status = ("ERROR" if summary.errors else "WARN" if summary.warnings else "OK")
        for kind, raw in sorted(summary.kinds.items()):
            rows.append({"session_id": summary.session_id, "data_source": summary.data_source,
                         "integrity": status, "kind": kind, **kind_stats(raw)})
        if summary.errors and not include_damaged:
            excluded.append(summary.session_id)
            messages.append(f"Excluded {summary.session_id} from the pooled row: "
                            f"{len(summary.errors)} integrity error(s). "
                            "Use --include-damaged to include it.")
        else:
            usable.append(summary)

    exit_code = 1 if excluded else 0
    sources = {s.data_source for s in usable}
    if len(sources) > 1:
        messages.insert(0, "Mixed data sources (" + ", ".join(sorted(sources)) + "): "
                        "no pooled row. Compare real and simulated runs separately.")
        return Comparison(rows, None, excluded, messages, 1)
    pooled_raw: dict = {}
    for summary in usable:
        for kind, raw in summary.kinds.items():
            target = pooled_raw.setdefault(kind, {"n": 0, "durations": [], "errors": {}})
            target["n"] += raw["n"]
            target["durations"] += raw["durations"]
            for motor, values in raw["errors"].items():
                target["errors"].setdefault(motor, []).extend(values)
    pooled = {kind: kind_stats(raw) for kind, raw in sorted(pooled_raw.items())}
    return Comparison(rows, pooled, excluded, messages, exit_code)


def find_sessions(paths) -> list[Path]:
    """Every .mcap file under the given paths (files or folders), each once."""
    found, seen = [], set()
    for path in map(Path, paths):
        candidates = ([path] if path.is_file() else
                      [path / "session.mcap"] if (path / "session.mcap").is_file() else
                      sorted(path.rglob("*.mcap")) if path.is_dir() else [])
        for candidate in candidates:
            key = candidate.resolve()
            if key not in seen:
                seen.add(key)
                found.append(candidate)
    return found
