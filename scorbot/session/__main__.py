"""Replay, list, export and compare recorded sessions. No robot needed.

  python -m scorbot.session <session>                 replay one session
  python -m scorbot.session list <folder>             catalogue every session found
  python -m scorbot.session export <session>          write events/states/commands CSV
  python -m scorbot.session compare <paths>...        compare repeated runs
"""

from __future__ import annotations

import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import sys

from . import analysis
from .notes import parse_notes
from .replay import load_session

SUBCOMMANDS = ("replay", "list", "export", "compare")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Redirected output on Windows defaults to cp1252; never crash on a note's text.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    if not argv or argv[0] not in SUBCOMMANDS + ("-h", "--help"):
        argv = ["replay", *argv]  # backward compatible: a bare path means replay

    parser = argparse.ArgumentParser(prog="python -m scorbot.session", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    replay = sub.add_parser("replay", help="Print one session's timeline and integrity")
    replay.add_argument("path", help="Session folder or its .mcap file")
    replay.add_argument("--limit", type=int, default=200,
                        help="Maximum timeline rows to print (default: 200)")
    listing = sub.add_parser("list", help="Catalogue every session under folders")
    listing.add_argument("paths", nargs="+")
    export = sub.add_parser("export", help="Write events.csv, states.csv, commands.csv")
    export.add_argument("path", help="Session folder or its .mcap file")
    export.add_argument("--out", type=Path, help="Output folder (default: <session>/csv)")
    export.add_argument("--force", action="store_true", help="Overwrite existing CSV files")
    comparison = sub.add_parser("compare", help="Compare repeated runs")
    comparison.add_argument("paths", nargs="+", help="Sessions, or folders containing them")
    comparison.add_argument("--csv", type=Path, help="Also write the table to this CSV file")
    comparison.add_argument("--include-damaged", action="store_true",
                            help="Pool sessions that have integrity errors")
    args = parser.parse_args(argv)
    return {"replay": _replay, "list": _list, "export": _export,
            "compare": _compare}[args.command](args)


def _open(path):
    try:
        return load_session(path)
    except (FileNotFoundError, NotADirectoryError) as error:
        print(f"Cannot open session: {error}", file=sys.stderr)
        return None


def _findings(session):
    for finding in session.findings:
        print(f"  {finding.level.upper():<8} {finding.message}")


def _sheet_line(session) -> str:
    """Observation-sheet status: a warning only on real runs, where it is evidence."""
    sheet = parse_notes(Path(session.path) / "notes.md")
    real = session.metadata.get("data_source") == "real"
    level = "WARNING " if real and sheet.status not in ("complete", "legacy") else "        "
    detail = f" ({'; '.join(sheet.problems[:3])})" if sheet.problems else ""
    reviewed = "" if sheet.status in ("missing", "untouched") else (
        ", reviewed" if sheet.reviewed else ", not reviewed yet")
    return f"  {level} Observation sheet: {sheet.status}{reviewed}{detail}"


# -- replay ------------------------------------------------------------------

def _replay(args) -> int:
    session = _open(args.path)
    if session is None:
        return 2
    meta = session.metadata
    source = str(meta.get("data_source", "unknown")).upper()
    bar = "=" * 64
    print(bar)
    print(f"  {source} DATA   session {meta.get('session_id', '?')}")
    print(bar)
    code = meta.get("code") or {}
    for label, value in (
        ("robot", f"{meta.get('robot_id')} / controller {meta.get('controller_id')}"),
        ("operator", meta.get("operator") or "-"),
        ("task", meta.get("task") or "-"),
        ("start pose", meta.get("start_pose_note")),
        ("started", meta.get("started_utc")),
        ("ended", meta.get("ended_utc", "not recorded (session not closed)")),
        ("code", f"{code.get('git_commit') or 'no git'}"
                 f"{' (dirty)' if code.get('git_dirty') else ''}"),
        ("calibration", (meta.get("calibration") or {}).get("sha256", "none")),
    ):
        print(f"  {label:<12} {value}")
    print()

    start = session.events[0]["payload"]["_rec"]["logged_monotonic_ns"] if session.events else 0
    for event in session.events[:max(0, args.limit)]:
        rec = event["payload"]["_rec"]
        offset_ms = (rec["logged_monotonic_ns"] - start) / 1e6
        print(f"  +{offset_ms:10.1f} ms  #{event['seq']:<5} {event['topic']:<26} "
              f"{analysis.event_summary(event)}")
    hidden = len(session.events) - max(0, args.limit)
    if hidden > 0:
        print(f"  ... {hidden} more events not shown (use --limit)")
    print()

    counts = Counter(event["topic"] for event in session.events)
    print(f"  {len(session.events)} events: "
          + ", ".join(f"{topic} {count}" for topic, count in sorted(counts.items())))
    _findings(session)
    if not session.findings:
        print("  Integrity: no problems found")
    print(_sheet_line(session))
    print()
    print("  Open session.mcap in Foxglove or Lichtblick for video and plots.")
    return 1 if session.errors else 0


# -- list --------------------------------------------------------------------

def _report_search(search) -> int:
    """Print what the search could not use; return 1 if an argument matched nothing."""
    for argument in search.unmatched:
        print(f"No session found at {argument}", file=sys.stderr)
    for path in search.duplicates:
        print(f"Skipped duplicate session {path}")
    return 1 if search.unmatched else 0


def _list(args) -> int:
    search = analysis.find_sessions(args.paths)
    missing = _report_search(search)
    rows, broken = [], 0
    for mcap in search.not_sessions:
        rows.append(("NOT A SESSION", "-", str(mcap), "-", "-", 0, "-",
                     "not a session.mcap; not loaded"))
    for mcap in search.sessions:
        try:
            session = load_session(mcap)
        except Exception as error:  # an unreadable file is reported, never fatal
            rows.append(("UNREADABLE", "-", str(mcap), "-", "-", 0, "-", str(error)))
            broken += 1
            continue
        meta = session.metadata
        status = (f"ERROR {len(session.errors)}" if session.errors else
                  f"WARN {len(session.warnings)}" if session.warnings else "OK")
        broken += bool(session.errors)
        name = meta.get("session_id") or str(mcap)
        notes_status = parse_notes(Path(session.path) / "notes.md").status
        rows.append((status, str(meta.get("data_source", "?")).upper(), name,
                     meta.get("started_utc") or "-", meta.get("robot_id") or "-",
                     len(session.events), notes_status, meta.get("task") or ""))
        del session  # keep only one session in memory at a time
    if not rows:
        print("No sessions found.")
        return max(missing, 0)
    rows.sort(key=lambda row: row[3], reverse=True)
    print(f"{'STATUS':<13} {'SOURCE':<10} {'SESSION':<26} {'STARTED':<26} "
          f"{'ROBOT':<14} {'EVENTS':>6}  {'NOTES':<10}  TASK")
    for status, source, name, started, robot, events, notes_status, task in rows:
        print(f"{status:<13} {source:<10} {name:<26} {started[:25]:<26} "
              f"{robot[:14]:<14} {events:>6}  {notes_status:<10}  {task}")
    print(f"\n{len(search.sessions)} session(s); {broken} with integrity errors.")
    return 1 if broken or missing else 0


# -- export ------------------------------------------------------------------

def _write_csv(path: Path, columns, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: analysis.csv_safe(value) for key, value in row.items()})


def _export(args) -> int:
    session = _open(args.path)
    if session is None:
        return 2
    out = args.out or session.path / "csv"
    files = {"events.csv": (analysis.EVENT_COLUMNS, analysis.event_rows(session)),
             "states.csv": (analysis.STATE_COLUMNS, analysis.state_rows(session)),
             "commands.csv": (analysis.COMMAND_COLUMNS, analysis.command_rows(session)),
             "notes.csv": (analysis.NOTES_COLUMNS, analysis.notes_rows(session))}
    existing = [name for name in files if (out / name).exists()]
    if existing and not args.force:
        print(f"Refusing to overwrite {', '.join(existing)} in {out} (use --force).",
              file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    for name, (columns, rows) in files.items():
        _write_csv(out / name, columns, rows)
        print(f"Wrote {len(rows):>5} rows to {out / name}")
    print(f"Data source: {str(session.metadata.get('data_source', '?')).upper()} "
          "(also a column in every row)")
    _findings(session)
    return 1 if session.errors else 0


# -- compare -----------------------------------------------------------------

def _compare(args) -> int:
    search = analysis.find_sessions(args.paths)
    problems = _report_search(search)
    for mcap in search.not_sessions:
        print(f"Ignored {mcap}: not a session.mcap")
    summaries = []
    for mcap in search.sessions:
        try:
            session = load_session(mcap)
        except Exception as error:
            print(f"Skipped unreadable {mcap}: {error}", file=sys.stderr)
            problems = 1
            continue
        summaries.append(analysis.summarize(session))
        del session  # keep only the summary in memory
    if not summaries:
        print("No sessions found.", file=sys.stderr)
        return 2
    result = analysis.compare(summaries, include_damaged=args.include_damaged)
    table = list(result.rows)
    if result.pooled is not None:
        # Labelled with the source of the sessions actually pooled, never an excluded one.
        table += [{"session_id": "POOLED", "data_source": result.pooled_source,
                   "integrity": "n/a", "notes": "n/a", "run_ended": "", "kind": kind,
                   **stats} for kind, stats in result.pooled.items()]

    print(f"{'SESSION':<26} {'SOURCE':<10} {'INTEGRITY':<9} {'NOTES':<10} {'KIND':<12} "
          f"{'N':>3} {'MEDIAN_MS':>10} {'MAX_MS':>9}  MAX|COUNT ERROR|")
    for row in table:
        print(f"{row['session_id'][:26]:<26} {str(row['data_source']).upper():<10} "
              f"{row['integrity']:<9} {row['notes']:<10} {row['kind'][:12]:<12} {row['n']:>3} "
              f"{_num(row['median_ms']):>10} {_num(row['max_ms']):>9}  "
              f"{json.dumps(row['max_abs_error']) if row['max_abs_error'] else '-'}")
    for message in result.messages:
        print(message)
    print("\nMEDIAN_MS/MAX_MS are recorder-observed durations (command to result, including "
          "script overhead), not arm motion time.")
    if args.csv:
        columns = ["session_id", "data_source", "integrity", "notes", "run_ended", "kind",
                   "n", "median_ms", "max_ms", "mean_abs_error", "max_abs_error"]
        _write_csv(args.csv, columns,
                   [{**{key: row[key] for key in columns[:9]},
                     "mean_abs_error": json.dumps(row["mean_abs_error"]),
                     "max_abs_error": json.dumps(row["max_abs_error"])} for row in table])
        print(f"Wrote {len(table)} rows to {args.csv}")
    # A mistyped or unreadable path must not pass silently as a smaller N.
    return max(result.exit_code, problems)


def _num(value):
    return "-" if value is None else f"{value:.1f}"


if __name__ == "__main__":
    raise SystemExit(main())
