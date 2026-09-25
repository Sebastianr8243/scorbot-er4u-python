"""One supervised ER-4U home and bounded jog with a durable bench record.

With --simulate the same procedure runs against the simulated controller, for
rehearsal away from the lab; every record is then labelled simulated.
"""

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import subprocess
import time

from scorbot import Scorbot, SimulatedScorbot
from scorbot.preflight import run_checks
from scorbot.provenance import motion_source_sha256
from scorbot.session import BestEffortRecorder, SessionWriter


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("--arm-label", required=True)
    parser.add_argument("--controller-label", required=True)
    parser.add_argument("--driver", required=True)
    parser.add_argument("--operator", required=True, help="Name or lab initials")
    parser.add_argument("--start-pose-note", required=True)
    parser.add_argument("--joint", choices=("base", "shoulder", "elbow"), required=True)
    parser.add_argument("--delta", type=float, required=True,
                        help="Signed requested legacy jog in degrees, at most 1")
    parser.add_argument("--speed", type=int, default=10)
    parser.add_argument("--acknowledge-supervised-motion", action="store_true")
    parser.add_argument("--session-root", type=Path, default=None,
                        help="Folder for the MCAP session (default: <output folder>/sessions)")
    parser.add_argument("--simulate", action="store_true",
                        help="Rehearse with the simulated controller: no USB, no robot")
    args = parser.parse_args()
    if not args.acknowledge_supervised_motion:
        parser.error("An operator and the physical emergency stop are required")
    if not math.isfinite(args.delta) or not 0 < abs(args.delta) <= 1:
        parser.error("--delta must be nonzero and at most one degree")
    if not 1 <= args.speed <= 20:
        parser.error("--speed must be 1 through 20")
    if any(not value.strip() for value in (args.robot_id, args.arm_label,
                                           args.controller_label, args.driver,
                                           args.operator, args.start_pose_note)):
        parser.error("All labels and the pose note must be nonempty")
    output = args.output.resolve()
    events = output.with_name(output.stem + ".controller.jsonl")
    if output.exists() or events.exists():
        parser.error("Output already exists; choose a new session filename")

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
            return 1
    try:
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, timeout=3).strip()
    except (OSError, subprocess.SubprocessError):
        revision = "unknown"
    output.parent.mkdir(parents=True, exist_ok=True)
    # The recorder opens before the controller, so a failure to start it happens
    # before the motor-on handshake. Once running it is best-effort: a later
    # recording error warns but never skips the JSONL record or the procedure.
    recorder = SessionWriter.create(
        args.session_root or output.parent / "sessions", data_source=data_source,
        robot_id=args.robot_id, controller_id=args.controller_label,
        operator=args.operator, start_pose_note=args.start_pose_note,
        task=f"bench jog {args.joint} {args.delta:+g} deg ({output.name})",
        usb_driver=args.driver)
    with recorder as writer, output.open("x", encoding="utf-8") as stream:
        rec = BestEffortRecorder(writer)

        def write(kind, **fields):
            stream.write(json.dumps({
                "type": kind,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "host_monotonic_ns": time.monotonic_ns(),
                **fields,
            }, allow_nan=False) + "\n")
            stream.flush()

        def prompt(question, choice):
            answer = input(question).strip()
            rec.log_decision(choice if answer == choice else "declined",
                             reason=f"typed {answer!r} at: {question.strip()}")
            return answer

        write("session", schema_version=1, robot_id=args.robot_id,
              arm_label=args.arm_label, controller_label=args.controller_label,
              driver=args.driver, operator=args.operator,
              start_pose_note=args.start_pose_note,
              software_commit=revision, motion_source_sha256=motion_source_sha256(),
              controller_event_log=events.name,
              joint=args.joint, requested_delta_deg=args.delta, speed=args.speed,
              data_source=data_source, mcap_session=rec.path.name)
        open_command = None
        try:
            with robot_class(log_path=events, robot_id=args.robot_id) as robot:
                state = robot.get_state()
                write("connected", state=asdict(state))
                rec.log_state(state)
                print("Confirm the arm is in the documented legacy homing start pose.")
                if prompt("Type HOME to search home: ", "HOME") != "HOME":
                    raise RuntimeError("Operator canceled before homing")
                robot.enable()
                open_command = rec.log_command("home", {"start_position_confirmed": True})
                robot.home(start_position_confirmed=True)
                command_id, open_command = open_command, None
                home_state = robot.get_state()
                write("home_complete", state=asdict(home_state))
                rec.log_command_result(command_id, "completed",
                                       completion_source="home() returned")
                rec.log_state(home_state)
                home_observation = input("Describe the physical home pose, motion, and controller indicators: ").strip()
                write("home_observation", text=home_observation or "not recorded")
                rec.log_note(f"home observation: {home_observation or 'not recorded'}")
                if prompt("If home looked correct and travel is clear, type HOME_OK: ",
                          "HOME_OK") != "HOME_OK":
                    raise RuntimeError("Operator stopped after homing; no jog requested")
                preview_state = robot.get_state()
                preview = robot.preview_jog(
                    args.joint, args.delta, speed=args.speed,
                    starting_signed_counts=preview_state.signed_encoder_counts)
                write("motion_preview", plan=preview, state=asdict(preview_state))
                rec.log_note(f"motion preview: {args.joint} {preview['motor_count_deltas']} "
                             f"in {len(preview['increments'])} increments")
                print(json.dumps(preview, indent=2))
                print("Clear the travel path and keep the emergency stop within reach.")
                if prompt("Type MOVE for one bounded jog: ", "MOVE") != "MOVE":
                    raise RuntimeError("Operator canceled before jog")
                before = robot.get_state()
                write("before_jog", state=asdict(before))
                rec.log_state(before)
                open_command = rec.log_command("jog_joint", {
                    "joint": args.joint, "delta_degrees": args.delta, "speed": args.speed,
                    "motor_count_deltas": preview["motor_count_deltas"]})
                after = robot.jog_joint(args.joint, args.delta, speed=args.speed)
                command_id, open_command = open_command, None
                # The primary JSONL evidence is written before any recorder call.
                write("after_jog", state=asdict(after))
                rec.log_command_result(command_id, "completed",
                                       completion_source="jog_joint() returned")
                rec.log_state(after)
                direction = input("Observed joint direction and approximate displacement: ").strip()
                other_motion = input("Did any other joint move? Describe what you saw: ").strip()
                indicators = input("Controller indicators after jog: ").strip()
                issue = input("Fault, noise, unexpected motion, or other issue (write 'none' if none): ").strip()
                observation = dict(
                    direction_and_displacement=direction or "not recorded",
                    other_motion=other_motion or "not recorded",
                    controller_indicators=indicators or "not recorded",
                    issue=issue or "not recorded")
                write("operator_observation", **observation)
                rec.log_note("operator observation: " + json.dumps(observation))
                robot.disable()
                disabled = robot.get_state()
                write("disabled", state=asdict(disabled))
                rec.log_state(disabled)
        except (Exception, KeyboardInterrupt) as exc:
            write("session_failed", error=str(exc))
            if open_command is not None:
                rec.log_command_result(open_command, "faulted", detail=str(exc))
            rec.log_fault(f"{type(exc).__name__}: {exc}")
            print("Session failed. If motion or motor state is uncertain, use the physical stop.")
            raise
    print(f"Saved bench record to {output} and controller events to {events}")
    print(f"Saved MCAP session to {rec.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
