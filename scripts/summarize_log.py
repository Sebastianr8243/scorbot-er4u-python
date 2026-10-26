"""Read a server log (logs/server-<time>.jsonl) as a table: what was asked, what came back.

    .venv/Scripts/python.exe scripts/summarize_log.py logs/server-20261026-101500.jsonl

One row per command: time (s since the server started), op, outcome, seconds the server spent. Outcome is `ok`,
`refused: <reason>` (the server said no, nothing reached the DLL) or `dll code N` (the DLL call failed).
Read-only. It reports what was asked and answered, not whether the arm physically moved.
"""
import argparse
import json
import sys
from pathlib import Path


def read_events(path):
    """(events, ended_early): the parsed log lines; a last line that is cut off is dropped and flagged."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    events, ended_early = [], False
    for number, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except ValueError:
            ended_early = ended_early or number == len(lines) - 1      # a bad line mid-file is skipped, not a crash sign
    return events, ended_early


def shorten(text, limit=60):
    return text if len(text) <= limit else text[:limit - 3] + "..."      # the full reason stays in the log


def outcome(reply):
    if reply is None:
        return "no reply", "failed"
    if reply.get("ok"):
        return "ok", "ok"
    if "code" in reply:
        return f"dll code {reply['code']}", "failed"
    return f"refused: {shorten(str(reply.get('error', '?')))}", "refused"


def op_of(event):
    try:
        return json.loads(event.get("line", "")).get("op", "?")
    except (ValueError, AttributeError):
        return "?"


def reply_to(events, index):
    """The first reply after events[index], skipping the server's own step events; None if another command comes first."""
    for event in events[index + 1:]:
        if event.get("event") == "reply":
            return event
        if event.get("event") == "command":
            return None
    return None


def summarize(path):
    """(rows, totals, ended_early); each row is (t, op, outcome text, seconds or None)."""
    events, ended_early = read_events(path)
    rows, totals = [], {"ok": 0, "refused": 0, "failed": 0}
    for index, event in enumerate(events):
        if event.get("event") != "command":
            continue
        reply = reply_to(events, index)
        text, kind = outcome(reply)
        totals[kind] += 1
        timing = reply.get("timing_s") if reply else None
        seconds = round(sum(timing.values()), 2) if isinstance(timing, dict) and timing else None
        rows.append((event.get("t", 0.0), op_of(event), text, seconds))
    return rows, totals, ended_early


def main(argv=None, out=print):
    parser = argparse.ArgumentParser(description="Print a server log as a table of commands and outcomes")
    parser.add_argument("log", type=Path)
    args = parser.parse_args(argv)
    if not args.log.is_file():
        out(f"no such log: {args.log}")
        return 1
    rows, totals, ended_early = summarize(args.log)
    out(f"{'time':>8}  {'op':<9} {'outcome':<66} seconds")
    for t, op, text, seconds in rows:
        out(f"{t:>8.1f}  {op:<9} {text:<66} {'' if seconds is None else seconds}")
    out(f"{len(rows)} commands: {totals['ok']} ok, {totals['refused']} refused, {totals['failed']} failed")
    if ended_early:
        out("log ended early: the last line is cut off (the server stopped mid-write)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
