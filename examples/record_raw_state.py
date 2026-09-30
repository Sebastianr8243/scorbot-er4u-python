"""Record raw ER-4U controller state without requesting motion.

Connecting is not passive: the inherited USB handshake briefly sends motor-on
packets before the adapter requests motor-disable. Run only at a supervised
bench with the physical emergency stop available.

With --simulate the same procedure runs against the simulated controller, for
rehearsal away from the lab; every record is then labelled simulated.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time

from scorbot import Scorbot, SimulatedScorbot
from scorbot.preflight import run_checks
from scorbot.provenance import motion_source_sha256
from scorbot.session import BestEffortRecorder, SessionWriter

try:
    from examples.bench_joint import observe_leds, reject_example_values, termination_as_interrupt
except ImportError:  # Run as a script: examples/ itself is on sys.path.
    from bench_joint import observe_leds, reject_example_values, termination_as_interrupt


def main() -> int:
    with termination_as_interrupt():
        return _run()


def _run() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True,
                        help="New JSONL file for raw samples (never overwritten)")
    parser.add_argument("--robot-id", required=True,
                        help="Your label for this physical arm, such as lab-er4u-1")
    parser.add_argument("--arm-label", required=True)
    parser.add_argument("--controller-label", required=True)
    parser.add_argument("--driver", required=True)
    parser.add_argument("--operator", required=True, help="Name or lab initials")
    parser.add_argument("--seconds", type=float, default=10.0,
                        help="Approximate sampling period, 1 to 30 seconds (default: 10)")
    parser.add_argument("--hz", type=float, default=2.0,
                        help="Samples per second, 0.2 to 10 (default: 2)")
    parser.add_argument("--pose-note", required=True,
                        help="Operator note describing the starting pose")
    parser.add_argument("--acknowledge-connect-handshake", action="store_true",
                        help="Confirm an operator is present and the physical stop is accessible")
    parser.add_argument("--session-root", type=Path, default=None,
                        help="Folder for the MCAP session (default: <output folder>/sessions)")
    parser.add_argument("--simulate", action="store_true",
                        help="Rehearse with the simulated controller: no USB, no robot")
    args = parser.parse_args()

    if not args.acknowledge_connect_handshake:
        parser.error("Connecting sends motor-on handshake packets. Pass "
                     "--acknowledge-connect-handshake only at the supervised bench.")
    if not math.isfinite(args.seconds) or not 1 <= args.seconds <= 30:
        parser.error("--seconds must be between 1 and 30")
    if not math.isfinite(args.hz) or not 0.2 <= args.hz <= 10:
        parser.error("--hz must be between 0.2 and 10")
    if any(not value.strip() for value in (
            args.robot_id, args.arm_label, args.controller_label,
            args.driver, args.operator, args.pose_note)):
        parser.error("All session labels and the pose note must be nonempty")
    reject_example_values(parser, arm_label=args.arm_label,
                          controller_label=args.controller_label, driver=args.driver,
                          operator=args.operator, pose_note=args.pose_note)

    output = args.output.resolve()
    event_log = output.with_name(output.stem + ".controller.jsonl")
    if output.exists() or event_log.exists():
        parser.error("The output or controller event log already exists; choose a new --output")

    # The only difference between a lab run and a rehearsal.
    if args.simulate:
        robot_class, data_source = SimulatedScorbot, "simulated"
        print("SIMULATED rehearsal: no USB and no robot are used.")
    else:
        robot_class, data_source = Scorbot, "real"
        checks = run_checks()
        for check in checks:
            print(f"{'PASS' if check.passed else 'FAIL'} {check.name}: {check.detail}")
        if not all(check.passed for check in checks):
            print("Preflight failed; no controller connection attempted.")
            return 1

    output.parent.mkdir(parents=True, exist_ok=True)
    sample_count = math.ceil(args.seconds * args.hz)
    # The recorder opens before the controller, so a failure to start it happens
    # before the motor-on handshake; once running it is best-effort and never
    # cuts the JSONL capture short. The controller event log records connect,
    # shutdown and any command errors.
    recorder = SessionWriter.create(
        args.session_root or output.parent / "sessions", data_source=data_source,
        robot_id=args.robot_id.strip(), controller_id=args.controller_label.strip(),
        operator=args.operator.strip(), task=f"idle capture ({output.name})",
        start_pose_note=args.pose_note.strip(), usb_driver=args.driver.strip())
    with recorder as writer, output.open("x", encoding="utf-8") as stream:
        rec = BestEffortRecorder(writer)

        def write(kind, **fields):
            stream.write(json.dumps({"type": kind, "host_monotonic_ns": time.monotonic_ns(),
                                     **fields}, allow_nan=False) + "\n")
            stream.flush()

        try:
            with robot_class(log_path=event_log, robot_id=args.robot_id.strip()) as robot:
                session = {
                    "type": "session",
                    "schema_version": 1,
                    "robot_id": args.robot_id.strip(),
                    "arm_label": args.arm_label.strip(),
                    "controller_label": args.controller_label.strip(),
                    "driver": args.driver.strip(),
                    "operator": args.operator.strip(),
                    "pose_note": args.pose_note.strip(),
                    "motion_source_sha256": motion_source_sha256(),
                    "started_utc": datetime.now(timezone.utc).isoformat(),
                    "requested_seconds": args.seconds,
                    "requested_hz": args.hz,
                    "controller_event_log": event_log.name,
                    "data_source": data_source,
                    "mcap_session": rec.path.name,
                    "led_prompts": True,
                }
                stream.write(json.dumps(session, allow_nan=False) + "\n")
                # Connect ends with a motor-disable request; the LED must confirm it.
                # A contradictory or unsure answer ends the run before sampling.
                observe_leds("after_connect", write, rec,
                             expect_motors="off", expect_power="green",
                             require_expected=True)
                next_sample = time.monotonic()
                for index in range(sample_count):
                    time.sleep(max(0.0, next_sample - time.monotonic()))
                    state = robot.get_state()
                    write("sample", index=index, state=asdict(state))
                    rec.log_state(state)
                    next_sample += 1 / args.hz
            # Verify motor power after release. POWER has no fixed expectation here:
            # the manual does not say which colour follows a close.
            observe_leds("after_exit", write, rec, expect_motors="off")
        except (Exception, KeyboardInterrupt) as exc:
            # If the robot context was entered, its exit path attempted disconnect.
            # Never infer an after-exit LED answer from software cleanup.
            write("session_failed", error_type=type(exc).__name__, error=str(exc))
            print("Idle capture failed. If motor state is uncertain, use the physical stop.")
            raise
        if rec.failure is not None:
            write("recorder_failed", error_type=type(rec.failure).__name__,
                  error=str(rec.failure))
            print("MCAP recording is incomplete; review the JSONL and recorder failure.")

    print(f"Saved {sample_count} raw samples to {output}")
    print(f"Saved controller events to {event_log}")
    if rec.failure is None:
        print(f"Saved MCAP session to {rec.path}")
    return 1 if rec.failure is not None else 0


if __name__ == "__main__":
    raise SystemExit(main())
