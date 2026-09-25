"""Replay a recorded session in the terminal: python -m scorbot.session <session-dir>"""

from __future__ import annotations

import argparse
from collections import Counter
import sys

from .replay import load_session


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scorbot.session", description=__doc__)
    parser.add_argument("path", help="Session folder or its session.mcap file")
    parser.add_argument("--limit", type=int, default=200,
                        help="Maximum timeline rows to print (default: 200)")
    args = parser.parse_args(argv)
    # Redirected output on Windows defaults to cp1252; never crash on a note's text.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")

    try:
        session = load_session(args.path)
    except (FileNotFoundError, NotADirectoryError) as error:
        print(f"Cannot open session: {error}", file=sys.stderr)
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
              f"{_summary(event)}")
    hidden = len(session.events) - max(0, args.limit)
    if hidden > 0:
        print(f"  ... {hidden} more events not shown (use --limit)")
    print()

    counts = Counter(event["topic"] for event in session.events)
    print(f"  {len(session.events)} events: "
          + ", ".join(f"{topic} {count}" for topic, count in sorted(counts.items())))
    for finding in session.findings:
        print(f"  {finding.level.upper():<8} {finding.message}")
    if not session.findings:
        print("  Integrity: no problems found")
    print()
    print("  Open session.mcap in Foxglove or Lichtblick for video and plots.")
    return 1 if session.errors else 0


def _summary(event: dict) -> str:
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
        return f"frame {rec.get('frame_number')} {payload['format']} " \
               f"{rec.get('width')}x{rec.get('height')}"
    if topic.endswith("/detections"):
        return f"frame {payload['frame_number']} {payload['label']} " \
               f"conf={payload['confidence']:.2f}"
    if topic == "/operator/decision":
        return f"choice={payload['choice']}"
    if topic == "/session/fault":
        return f"FAULT {payload['message']}"
    if topic == "/session/note":
        return payload["text"][:60]
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
