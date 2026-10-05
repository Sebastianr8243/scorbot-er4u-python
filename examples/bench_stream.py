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
from importlib.util import find_spec
import json
import math
from pathlib import Path
import statistics
import time

from scorbot import Scorbot, ScorbotError, SimulatedScorbot, StreamRefused
from scorbot.calibration import signed_count_delta
from scorbot.preflight import run_checks
from scorbot.provenance import motion_source_sha256
from scorbot.session import BestEffortRecorder
from scorbot.streaming import DEFAULT_PERIOD_S
from scorbot.streaming import MOTORS

try:
    from examples import bench_joint as bench
except ImportError:  # Run as a script: examples/ itself is on sys.path.
    import bench_joint as bench

EXIT_DECLINED = bench.EXIT_DECLINED
OperatorDeclined = bench.OperatorDeclined
observe_leds = bench.observe_leds

# Tighter than the SDK allows (10 and 5 degrees), as bench_joint's 1 degree is.
TRAVEL_CAP_DEG = 2.0
LEAD_LIMIT_DEG = 2.0
PHASE_TIMEOUT_S = 4.0       # longest wait for the arm to arrive, each way
ARRIVED_COUNTS = 5          # ours: close enough to call it arrived
OTHER_MOTOR_LIMIT_COUNTS = 10   # ours: the two motors that were not asked to move
OVERSHOOT_LIMIT_COUNTS = 20     # ours: travel past the target
# Exit code for a run that completed its procedure but whose arm did not do
# what was asked (0 = passed, 1 also covers a failed session, 3 = declined).
EXIT_TRIAL_FAILED = 1
TARGET_REFRESH_S = 0.1      # well inside the stream's 0.5 s hold timeout


def summarize(steps, motor, target, final=None):
    """What the step records say. Counts are from home; gaps are between steps.

    ``final`` is the state read after the stream closed, as counts from home
    for the three motors. The steps end when the stream does, so without it a
    move after the last step would not count.
    """
    measured = [step["measured"] for step in steps]
    if measured and final is not None:
        measured.append({m: int(final[m]) for m in MOTORS})
    if not measured:
        return {"steps": 0, "reached_target": False, "returned_home": False,
                "passed": False, "problems": ["the stream took no steps"]}
    furthest = max((m[motor] for m in measured), key=abs)
    closest = min(abs(m[motor] - target) for m in measured)
    stamps = [step["host_monotonic_ns"] for step in steps]
    gaps = [(b - a) / 1e6 for a, b in zip(stamps, stamps[1:])]
    reached = closest <= ARRIVED_COUNTS
    returned = reached and abs(measured[-1][motor]) <= ARRIVED_COUNTS
    # Past the target, or away from home on the wrong side of it.
    side = 1 if target > 0 else -1
    overshoot = max(0, max(max(m[motor] * side - abs(target), -m[motor] * side)
                           for m in measured))
    others = max(abs(m[other]) for m in measured for other in MOTORS if other != motor)
    # The core's "lead" is taken before it makes the new setpoint, so the
    # setpoint a step sends is compared with that step's own measurement too.
    # Sampled once per step: the true peak between steps is not seen.
    lead = max(max(abs(step["lead"][motor]) for step in steps),
               max((abs(step["commanded"][motor] - step["measured"][motor])
                    for step in steps if step["action"] == "send"), default=0))
    problems = []
    if not reached:
        problems.append("did not reach the target")
    elif not returned:
        problems.append("did not return to home")
    if overshoot > OVERSHOOT_LIMIT_COUNTS:
        problems.append(f"went {overshoot} counts past the target")
    if others > OTHER_MOTOR_LIMIT_COUNTS:
        problems.append(f"another motor moved {others} counts")
    return {
        "steps": len(steps),
        "sends": sum(step["action"] == "send" for step in steps),
        "last_action": steps[-1]["action"],
        "furthest_counts_from_home": furthest,
        "closest_to_target_counts": closest,
        "reached_target": reached,
        "final_counts_from_home": measured[-1][motor],
        "returned_home": returned,
        "overshoot_counts": overshoot,
        "max_lead_counts": lead,
        "other_motors_max_counts": others,
        "step_gap_ms": ({"min": round(min(gaps), 2), "median": round(statistics.median(gaps), 2),
                         "max": round(max(gaps), 2)} if gaps else None),
        "problems": problems,
        "passed": not problems,
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


def report(result) -> None:
    """Print the verdict the operator copies onto the lab day card."""
    gap = result["step_gap_ms"] or {}
    print(f"Reached the target: {'yes' if result['reached_target'] else 'NO'} "
          f"(closest {result['closest_to_target_counts']} counts away).")
    print(f"Returned to home: {'yes' if result['returned_home'] else 'NO'} "
          f"(ended {result['final_counts_from_home']:+d} counts from home).")
    print(f"Largest lead of the command over the arm: "
          f"{result['max_lead_counts']} counts (sampled once per step).")
    print(f"Other two motors moved at most {result['other_motors_max_counts']} "
          "counts.")
    print(f"Time between steps: median {gap.get('median')} ms "
          f"(min {gap.get('min')}, max {gap.get('max')}); planned "
          f"{DEFAULT_PERIOD_S * 1000:g} ms.")
    if result["passed"]:
        print("Trial result: PASSED.")
    else:
        # Not an SDK fault: a degree is inside the lead limit, so a
        # motor that stalls or lags is caught here, not there.
        print("!!! Trial result: FAILED (" + "; ".join(result["problems"]) + ").")
        print("!!! The arm did not follow the stream out and back. Do not "
              "repeat with a larger move; review the record first.")


def main() -> int:
    with bench.termination_as_interrupt():
        return _run()


def _run() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    bench.add_session_arguments(parser)
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
    bench.check_session_labels(parser, args)
    # Checked now: found after homing, it would leave the arm enabled for nothing.
    if find_spec("ruckig") is None:
        parser.error("Streaming needs the planning extra, which is not installed. Run: "
                     "uv sync --locked --extra windows --extra test --extra planning "
                     "(or pip install -e .[windows,test,planning])")
    output, events = bench.new_output_paths(parser, args)
    # The target is the count change a jog of this joint by --delta would plan,
    # so the direction is the one a bench jog has already shown. Planned
    # offline now, before preflight (preview never opens USB).
    try:
        target = Scorbot().preview_jog(args.motor, args.delta)["motor_count_deltas"][args.motor]
    except ValueError as exc:
        parser.error(f"--delta cannot be planned: {exc}")
    if abs(target) <= 2 * ARRIVED_COUNTS:
        parser.error(f"--delta plans {target:+d} counts, too small to tell from standing still "
                     f"(arrival is judged within {ARRIVED_COUNTS} counts); use a larger move")

    backend = bench.choose_backend(args.simulate, Scorbot, SimulatedScorbot, run_checks)
    if backend is None:
        return 1
    robot_class, data_source = backend
    revision = bench.software_revision()
    recorder = bench.open_recorder(
        args, output, data_source,
        f"stream trial {args.motor} {args.delta:+g} deg ({output.name})")
    with recorder as writer, output.open("x", encoding="utf-8") as record:
        rec = BestEffortRecorder(writer)
        write, prompt = bench.row_writer(record), bench.confirmation_prompt(rec)
        command = bench.OpenCommand(rec)
        write("session", schema_version=1, procedure="stream_trial", robot_id=args.robot_id,
              arm_label=args.arm_label, controller_label=args.controller_label,
              driver=args.driver, operator=args.operator,
              start_pose_note=args.start_pose_note,
              software_commit=revision, motion_source_sha256=motion_source_sha256(),
              controller_event_log=events.name,
              motor=args.motor, requested_delta_deg=args.delta, hold_s=args.hold_s,
              data_source=data_source, mcap_session=rec.path.name, led_prompts=True)
        try:
            with robot_class(log_path=events, robot_id=args.robot_id) as robot:
                home_state = bench.connect_and_home(
                    robot, write, rec, prompt, command,
                    "stopped after homing; no stream requested")
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
                # One motor, one degree, from home: so every motor must be at home
                # now. The stream holds the other two wherever they start.
                try:
                    from_home = {m: signed_count_delta(before.encoder_counts[m],
                                                       home_state.encoder_counts[m])
                                 for m in MOTORS}
                except ValueError as exc:
                    raise ScorbotError(f"Cannot place the arm relative to home: {exc}") from exc
                if any(abs(counts) > ARRIVED_COUNTS for counts in from_home.values()):
                    raise ScorbotError(f"The arm is not at home (counts from home {from_home}); "
                                       "no stream was started")
                command.start("start_stream", plan)
                stream = None
                try:
                    with robot.start_stream(travel_cap_deg=TRAVEL_CAP_DEG,
                                            lead_limit_deg=LEAD_LIMIT_DEG) as stream:
                        if follow(stream, args.motor, target, args.hold_s):
                            follow(stream, args.motor, 0, args.hold_s)
                except (Exception, KeyboardInterrupt):
                    # The steps taken so far are the evidence; write them first.
                    result = summarize(stream.steps if stream is not None else [],
                                       args.motor, target)
                    write("stream_failed", result=result)
                    raise
                after = stream.final_state
                try:
                    final = {m: signed_count_delta(after.encoder_counts[m],
                                                   home_state.encoder_counts[m])
                             for m in MOTORS}
                except ValueError as exc:
                    raise ScorbotError(f"Cannot place the arm relative to home: {exc}") from exc
                result = summarize(stream.steps, args.motor, target, final)
                command_id = command.take()
                write("after_stream", state=asdict(after), result=result)
                if result["passed"]:
                    rec.log_command_result(command_id, "completed",
                                           completion_source="stream closed")
                else:
                    rec.log_command_result(command_id, "faulted",
                                           detail="; ".join(result["problems"]))
                rec.log_state(after)
                rec.log_note("stream result: " + json.dumps(result))
                report(result)
                observe_leds("after_stream", write, rec,
                             expect_motors="lit", expect_power="green")
                bench.observe_and_disable(
                    robot, write, rec,
                    indicators_question="Other controller indicators or sounds during the "
                                        "stream: ",
                    issue_question="Fault, noise, jerky or unexpected motion, or other issue "
                                   "(write 'none' if none): ")
        except OperatorDeclined as exc:
            return bench.report_declined(exc, write, rec)
        except (Exception, KeyboardInterrupt) as exc:
            bench.report_failure(exc, write, rec, command)
            raise
        bench.report_recorder_failure(write, rec)
    print(f"Saved stream record to {output} and controller events to {events}")
    if rec.failure is None:
        print(f"Saved MCAP session to {rec.path}")
    if rec.failure is not None:
        return 1
    return 0 if result["passed"] else EXIT_TRIAL_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
