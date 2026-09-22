"""Record raw ER-4U controller state without requesting motion.

Connecting is not passive: the inherited USB handshake briefly sends motor-on
packets before the adapter requests motor-disable. Run only at a supervised
bench with the physical emergency stop available.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time

from scorbot import Scorbot
from scorbot.preflight import run_checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True,
                        help="New JSONL file for raw samples (never overwritten)")
    parser.add_argument("--robot-id", required=True,
                        help="Your label for this physical arm, such as lab-er4u-1")
    parser.add_argument("--seconds", type=float, default=10.0,
                        help="Approximate sampling period, 1 to 30 seconds (default: 10)")
    parser.add_argument("--hz", type=float, default=2.0,
                        help="Samples per second, 0.2 to 10 (default: 2)")
    parser.add_argument("--pose-note", default="unmeasured",
                        help="Operator note describing the starting pose")
    parser.add_argument("--acknowledge-connect-handshake", action="store_true",
                        help="Confirm an operator is present and the physical stop is accessible")
    args = parser.parse_args()

    if not args.acknowledge_connect_handshake:
        parser.error("Connecting sends motor-on handshake packets. Pass "
                     "--acknowledge-connect-handshake only at the supervised bench.")
    if not math.isfinite(args.seconds) or not 1 <= args.seconds <= 30:
        parser.error("--seconds must be between 1 and 30")
    if not math.isfinite(args.hz) or not 0.2 <= args.hz <= 10:
        parser.error("--hz must be between 0.2 and 10")
    if not args.robot_id.strip():
        parser.error("--robot-id must contain a label")

    output = args.output.resolve()
    event_log = output.with_name(output.stem + ".controller.jsonl")
    if output.exists() or event_log.exists():
        parser.error("The output or controller event log already exists; choose a new --output")

    checks = run_checks()
    for check in checks:
        print(f"{'PASS' if check.passed else 'FAIL'} {check.name}: {check.detail}")
    if not all(check.passed for check in checks):
        print("Preflight failed; no controller connection attempted.")
        return 1

    output.parent.mkdir(parents=True, exist_ok=True)
    sample_count = math.ceil(args.seconds * args.hz)
    # The controller event log records connect, shutdown and any command errors.
    with Scorbot(log_path=event_log) as robot:
        with output.open("x", encoding="utf-8") as stream:
            session = {
                "type": "session",
                "schema_version": 1,
                "robot_id": args.robot_id.strip(),
                "pose_note": args.pose_note,
                "started_utc": datetime.now(timezone.utc).isoformat(),
                "requested_seconds": args.seconds,
                "requested_hz": args.hz,
                "controller_event_log": event_log.name,
            }
            stream.write(json.dumps(session, allow_nan=False) + "\n")
            next_sample = time.monotonic()
            for index in range(sample_count):
                time.sleep(max(0.0, next_sample - time.monotonic()))
                state = robot.get_state()
                sample = {
                    "type": "sample",
                    "index": index,
                    "host_monotonic_ns": time.monotonic_ns(),
                    "state": asdict(state),
                }
                stream.write(json.dumps(sample, allow_nan=False) + "\n")
                stream.flush()
                next_sample += 1 / args.hz

    print(f"Saved {sample_count} raw samples to {output}")
    print(f"Saved controller events to {event_log}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
