"""One supervised ER-4U home and bounded jog with a durable bench record."""

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import subprocess
import time

from scorbot import Scorbot
from scorbot.preflight import run_checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("--arm-label", required=True)
    parser.add_argument("--controller-label", required=True)
    parser.add_argument("--driver", required=True)
    parser.add_argument("--start-pose-note", required=True)
    parser.add_argument("--joint", choices=("base", "shoulder", "elbow"), required=True)
    parser.add_argument("--delta", type=float, required=True,
                        help="Signed requested legacy jog in degrees, at most 1")
    parser.add_argument("--speed", type=int, default=10)
    parser.add_argument("--acknowledge-supervised-motion", action="store_true")
    args = parser.parse_args()
    if not args.acknowledge_supervised_motion:
        parser.error("An operator and the physical emergency stop are required")
    if not math.isfinite(args.delta) or not 0 < abs(args.delta) <= 1:
        parser.error("--delta must be nonzero and at most one degree")
    if not 1 <= args.speed <= 20:
        parser.error("--speed must be 1 through 20")
    if any(not value.strip() for value in (args.robot_id, args.arm_label,
                                           args.controller_label, args.driver,
                                           args.start_pose_note)):
        parser.error("All labels and the pose note must be nonempty")
    output = args.output.resolve()
    events = output.with_name(output.stem + ".controller.jsonl")
    if output.exists() or events.exists():
        parser.error("Output already exists; choose a new session filename")
    checks = run_checks()
    for check in checks:
        print(f"{'PASS' if check.passed else 'FAIL'} {check.name}: {check.detail}")
    if not all(check.passed for check in checks):
        return 1
    try:
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, timeout=3).strip()
    except (OSError, subprocess.SubprocessError):
        revision = "unknown"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        def write(kind, **fields):
            stream.write(json.dumps({
                "type": kind,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "host_monotonic_ns": time.monotonic_ns(),
                **fields,
            }, allow_nan=False) + "\n")
            stream.flush()

        write("session", schema_version=1, robot_id=args.robot_id,
              arm_label=args.arm_label, controller_label=args.controller_label,
              driver=args.driver, start_pose_note=args.start_pose_note,
              software_commit=revision, controller_event_log=events.name,
              joint=args.joint, requested_delta_deg=args.delta, speed=args.speed)
        try:
            with Scorbot(log_path=events, robot_id=args.robot_id) as robot:
                write("connected", state=asdict(robot.get_state()))
                print("Confirm the arm is in the documented legacy homing start pose.")
                if input("Type HOME to search home: ").strip() != "HOME":
                    raise RuntimeError("Operator canceled before homing")
                robot.enable()
                robot.home(start_position_confirmed=True)
                write("home_complete", state=asdict(robot.get_state()))
                preview_state = robot.get_state()
                preview = robot.preview_jog(
                    args.joint, args.delta, speed=args.speed,
                    starting_signed_counts=preview_state.signed_encoder_counts)
                write("motion_preview", plan=preview, state=asdict(preview_state))
                print(json.dumps(preview, indent=2))
                print("Clear the travel path and keep the emergency stop within reach.")
                if input("Type MOVE for one bounded jog: ").strip() != "MOVE":
                    raise RuntimeError("Operator canceled before jog")
                before = robot.get_state()
                write("before_jog", state=asdict(before))
                after = robot.jog_joint(args.joint, args.delta, speed=args.speed)
                write("after_jog", state=asdict(after))
                observation = input("Observed direction/displacement and any issue: ").strip()
                write("operator_observation", text=observation or "not recorded")
                robot.disable()
                write("disabled", state=asdict(robot.get_state()))
        except (Exception, KeyboardInterrupt) as exc:
            write("session_failed", error=str(exc))
            print("Session failed. If motion or motor state is uncertain, use the physical stop.")
            raise
    print(f"Saved bench record to {output} and controller events to {events}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
