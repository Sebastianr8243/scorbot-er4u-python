"""One supervised ER-4U streaming trial: one motor out by at most a degree and back.

The first run of ``Scorbot.start_stream`` on the arm. It homes, streams a
target for one motor (base, shoulder or elbow) at most one degree from home,
holds, streams it back to home, and records every step. Streaming has never
run on the arm: the period, the limits and the three-setpoint message are
priors. Jog that motor with bench_joint.py first, so its direction is known.

With --simulate the same procedure runs against the simulated controller, for
rehearsal away from the lab; every record is then labelled simulated.
"""

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics
import subprocess
import time

from scorbot import Scorbot, ScorbotError, SimulatedScorbot, StreamRefused
from scorbot.preflight import run_checks
from scorbot.provenance import motion_source_sha256
from scorbot.session import BestEffortRecorder, SessionWriter

try:
    from examples.bench_joint import (EXIT_DECLINED, OperatorDeclined, observe_leds,
                                      reject_example_values, termination_as_interrupt)
except ImportError:  # Run as a script: examples/ itself is on sys.path.
    from bench_joint import (EXIT_DECLINED, OperatorDeclined, observe_leds,
                             reject_example_values, termination_as_interrupt)

MOTORS = ("base", "shoulder", "elbow")
# Tighter than the SDK allows (10 and 5 degrees), as bench_joint's 1 degree is.
TRAVEL_CAP_DEG = 2.0
LEAD_LIMIT_DEG = 2.0
PHASE_TIMEOUT_S = 4.0       # longest wait for the arm to arrive, each way
ARRIVED_COUNTS = 5          # ours: close enough to call it arrived
TARGET_REFRESH_S = 0.1      # well inside the stream's 0.5 s hold timeout


def summarize(steps, motor, target):
    """What the step records say. Counts are from home; gaps are between steps."""
    measured = [step["measured"] for step in steps]
    if not measured:
        return {"steps": 0, "reached_target": False, "returned_home": False}
    furthest = max((m[motor] for m in measured), key=abs)
    closest = min(abs(m[motor] - target) for m in measured)
    stamps = [step["host_monotonic_ns"] for step in steps]
    gaps = [(b - a) / 1e6 for a, b in zip(stamps, stamps[1:])]
    return {
        "steps": len(steps),
        "sends": sum(step["action"] == "send" for step in steps),
        "last_action": steps[-1]["action"],
        "furthest_counts_from_home": furthest,
        "closest_to_target_counts": closest,
        "reached_target": closest <= ARRIVED_COUNTS,
        "final_counts_from_home": measured[-1][motor],
        "returned_home": closest <= ARRIVED_COUNTS and abs(measured[-1][motor]) <= ARRIVED_COUNTS,
        "max_lead_counts": max(abs(step["lead"][motor]) for step in steps),
        "other_motors_max_counts": max(abs(m[other]) for m in measured
                                       for other in MOTORS if other != motor),
        "step_gap_ms": ({"min": round(min(gaps), 2), "median": round(statistics.median(gaps), 2),
                         "max": round(max(gaps), 2)} if gaps else None),
    }


def follow(stream, motor, target, hold_s):
    """Keep sending ``target`` until the arm is there, then for ``hold_s`` more.

    Returns False if the stream stopped taking targets (it faulted or was
    stopped); closing it then reports why.
    """
    deadline = time.monotonic() + PHASE_TIMEOUT_S
    arrived_at = None
    while True:
        try:
            stream.set_target({motor: target})
        except StreamRefused:
            return False
        now = time.monotonic()
        steps = stream.steps
        if arrived_at is None and steps and \
                abs(steps[-1]["measured"][motor] - target) <= ARRIVED_COUNTS:
            arrived_at = now
        if arrived_at is not None and now - arrived_at >= hold_s:
            return True
        if arrived_at is None and now >= deadline:
            return True          # not there in time: carry on, the record says so
        time.sleep(TARGET_REFRESH_S)


def main() -> int:
    with termination_as_interrupt():
        return _run()


def _run() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("--arm-label", required=True)
    parser.add_argument("--controller-label", required=True)
    parser.add_argument("--driver", required=True)
    parser.add_argument("--operator", required=True, help="Name or lab initials")
    parser.add_argument("--start-pose-note", required=True)
    parser.add_argument("--motor", choices=MOTORS, required=True)
    parser.add_argument("--delta", type=float, required=True,
                        help="Signed target in degrees from home, at most 1; the sign "
                             "means what it means for a jog of that joint")
    parser.add_argument("--hold-s", type=float, default=1.0,
                        help="Seconds to hold at the target and again at home (0.2 to 5)")
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
    if not math.isfinite(args.hold_s) or not 0.2 <= args.hold_s <= 5:
        parser.error("--hold-s must be 0.2 through 5")
    if any(not value.strip() for value in (args.robot_id, args.arm_label,
                                           args.controller_label, args.driver,
                                           args.operator, args.start_pose_note)):
        parser.error("All labels and the pose note must be nonempty")
    reject_example_values(parser, arm_label=args.arm_label,
                          controller_label=args.controller_label, driver=args.driver,
                          operator=args.operator, start_pose_note=args.start_pose_note)
    output = args.output.resolve()
    events = output.with_name(output.stem + ".controller.jsonl")
    if output.exists() or events.exists():
        parser.error("Output already exists; choose a new session filename")
    # The target is the count change a jog of this joint by --delta would plan,
    # so the direction is the one a bench jog has already shown. Planned
    # offline now, before preflight (preview never opens USB).
    try:
        target = Scorbot().preview_jog(args.motor, args.delta)["motor_count_deltas"][args.motor]
    except ValueError as exc:
        parser.error(f"--delta cannot be planned: {exc}")

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
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[1],
            text=True, timeout=3).strip()
    except (OSError, subprocess.SubprocessError):
        revision = "unknown"
    output.parent.mkdir(parents=True, exist_ok=True)
    # The recorder opens before the controller, as in bench_joint.py.
    recorder = SessionWriter.create(
        args.session_root or output.parent / "sessions", data_source=data_source,
        robot_id=args.robot_id, controller_id=args.controller_label,
        operator=args.operator, start_pose_note=args.start_pose_note,
        task=f"stream trial {args.motor} {args.delta:+g} deg ({output.name})",
        usb_driver=args.driver)
    with recorder as writer, output.open("x", encoding="utf-8") as record:
        rec = BestEffortRecorder(writer)

        def write(kind, **fields):
            record.write(json.dumps({
                "type": kind,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "host_monotonic_ns": time.monotonic_ns(),
                **fields,
            }, allow_nan=False) + "\n")
            record.flush()

        def prompt(question, choice):
            answer = input(question).strip()
            rec.log_decision(choice if answer == choice else "declined",
                             reason=f"typed {answer!r} at: {question.strip()}")
            return answer

        write("session", schema_version=1, procedure="stream_trial", robot_id=args.robot_id,
              arm_label=args.arm_label, controller_label=args.controller_label,
              driver=args.driver, operator=args.operator,
              start_pose_note=args.start_pose_note,
              software_commit=revision, motion_source_sha256=motion_source_sha256(),
              controller_event_log=events.name,
              motor=args.motor, requested_delta_deg=args.delta, hold_s=args.hold_s,
              data_source=data_source, mcap_session=rec.path.name, led_prompts=True)
        open_command = None
        try:
            with robot_class(log_path=events, robot_id=args.robot_id) as robot:
                state = robot.get_state()
                write("connected", state=asdict(state))
                rec.log_state(state)
                observe_leds("after_connect", write, rec,
                             expect_motors="off", expect_power="green",
                             require_expected=True)
                print("Confirm the arm is in the documented legacy homing start pose.")
                if prompt("Type HOME to search home: ", "HOME") != "HOME":
                    raise OperatorDeclined("declined before homing")
                robot.enable()
                observe_leds("after_enable", write, rec, expect_motors="lit",
                             expect_power="green", require_expected=True)
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
                    raise OperatorDeclined("stopped after homing; no stream requested")
                plan = dict(motor=args.motor, requested_delta_deg=args.delta,
                            target_counts_from_home=target, travel_cap_deg=TRAVEL_CAP_DEG,
                            lead_limit_deg=LEAD_LIMIT_DEG, hold_s=args.hold_s,
                            phase_timeout_s=PHASE_TIMEOUT_S, arrived_counts=ARRIVED_COUNTS)
                write("stream_plan", **plan)
                rec.log_note("stream plan: " + json.dumps(plan))
                print(json.dumps(plan, indent=2))
                print(f"The {args.motor} motor will move {target:+d} counts from home "
                      f"(what a {args.delta:+g} degree jog of that joint plans; the two "
                      "signs can differ), hold, and return. Only that motor is commanded.")
                print("Streaming has never run on this arm. Ctrl-C asks for a software stop; "
                      "it is NOT an emergency stop.")
                print("Clear the travel path and keep the emergency stop within reach.")
                if prompt("Type STREAM for one bounded stream: ", "STREAM") != "STREAM":
                    raise OperatorDeclined("declined before the stream")
                before = robot.get_state()
                write("before_stream", state=asdict(before))
                rec.log_state(before)
                open_command = rec.log_command("start_stream", plan)
                stream = None
                try:
                    with robot.start_stream(travel_cap_deg=TRAVEL_CAP_DEG,
                                            lead_limit_deg=LEAD_LIMIT_DEG) as stream:
                        if follow(stream, args.motor, target, args.hold_s):
                            follow(stream, args.motor, 0, args.hold_s)
                except (ScorbotError, KeyboardInterrupt):
                    # The steps taken so far are the evidence; write them first.
                    result = summarize(stream.steps if stream is not None else [],
                                       args.motor, target)
                    write("stream_failed", result=result)
                    raise
                after = stream.final_state
                result = summarize(stream.steps, args.motor, target)
                command_id, open_command = open_command, None
                write("after_stream", state=asdict(after), result=result)
                rec.log_command_result(command_id, "completed",
                                       completion_source="stream closed")
                rec.log_state(after)
                rec.log_note("stream result: " + json.dumps(result))
                gap = result["step_gap_ms"] or {}
                print(f"Reached the target: {'yes' if result['reached_target'] else 'NO'} "
                      f"(closest {result['closest_to_target_counts']} counts away).")
                print(f"Returned to home: {'yes' if result['returned_home'] else 'NO'} "
                      f"(ended {result['final_counts_from_home']:+d} counts from home).")
                print(f"Largest lead of the command over the arm: "
                      f"{result['max_lead_counts']} counts.")
                print(f"Other two motors moved at most {result['other_motors_max_counts']} "
                      "counts.")
                print(f"Time between steps: median {gap.get('median')} ms "
                      f"(min {gap.get('min')}, max {gap.get('max')}); planned 24 ms.")
                if not result["returned_home"]:
                    # Not a fault: a degree is inside the lead limit, so a motor
                    # that stalls or lags ends the run here, not in the SDK.
                    print("!!! The arm did not follow the stream out and back. Do not "
                          "repeat with a larger move; review the record first.")
                observe_leds("after_stream", write, rec,
                             expect_motors="lit", expect_power="green")
                direction = input("Observed joint direction and approximate displacement: ").strip()
                other_motion = input("Did any other joint move? Describe what you saw: ").strip()
                indicators = input("Other controller indicators or sounds during the stream: ").strip()
                issue = input("Fault, noise, jerky or unexpected motion, or other issue (write 'none' if none): ").strip()
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
                observe_leds("after_disable", write, rec,
                             expect_motors="off", expect_power="green")
        except OperatorDeclined as exc:
            write("operator_declined", text=str(exc))
            rec.log_note(f"operator declined: {exc}")
            print(f"Run ended by the operator ({exc}). Confirm the MOTORS LED is off.")
            return EXIT_DECLINED
        except (Exception, KeyboardInterrupt) as exc:
            write("session_failed", error_type=type(exc).__name__, error=str(exc))
            if open_command is not None:
                rec.log_command_result(open_command, "faulted", detail=str(exc))
            rec.log_fault(f"{type(exc).__name__}: {exc}")
            print("Session failed. If motion or motor state is uncertain, use the physical stop.")
            raise
        if rec.failure is not None:
            write("recorder_failed", error_type=type(rec.failure).__name__,
                  error=str(rec.failure))
            print("MCAP recording is incomplete; review the JSONL and recorder failure.")
    print(f"Saved stream record to {output} and controller events to {events}")
    if rec.failure is None:
        print(f"Saved MCAP session to {rec.path}")
    return 1 if rec.failure is not None else 0


if __name__ == "__main__":
    raise SystemExit(main())
