"""One supervised ER-4U policy run: a policy sends small joint deltas, a clamp bounds them.

Built on bench_stream.py and its safety flow (preflight, LED checks, typed words, recorder,
fault handling). It homes, then for --steps control ticks asks a policy for a joint delta
(degrees, base/shoulder/elbow), clamps it, streams the result, records every tick, and
returns to home. The policy is any function ``policy(obs) -> {motor: delta_deg} | None``;
``None`` or an exception means hold. The clamp is the only way a delta reaches the stream.

    --policy reach:base=1,shoulder=-0.5     built in: walk each joint to that many degrees from home
    --policy-file my_policy.py              your own ``policy(obs)``; obs is documented in OBS below
    --grip-at 10                            before tick 10 stop the stream, open the jaw, resume (#209)

``--grip-at`` runs the LeRobot grasp path (``PolicyArm``'s stop and gripper call, then a stream
resumed where the arm is) mid-reach and logs a ``gripper_resume`` row: the last measured counts
before the stop, the counts it stopped at and the resumed stream's start, all from home. Vendor
Home leaves the jaw closed, so the trial opens it.

Streaming has never run on the arm: the period, the limits and the stop sequence are priors, so
the defaults are 0.5 degree per tick, 5 Hz, 20 ticks, with vendor travel windows only. Run
bench_joint.py and bench_stream.py first. With --simulate the same procedure runs against the
simulated controller; every record is then labelled simulated. Not an emergency stop: the
physical stop is authoritative.
"""

import argparse
from contextlib import ExitStack
from dataclasses import asdict
import importlib.util
from importlib.util import find_spec
import json
import math
from pathlib import Path
import time

from scorbot import Scorbot, ScorbotError, SimulatedScorbot, StreamRefused, limits
from scorbot.calibration import signed_count_delta
from scorbot.policy_arm import (LEAD_LIMIT_DEG, LEAD_MARGIN_DEG, STEP_MAX_DEG,
                                clamp, stop_stream_and_move_gripper)
from scorbot.preflight import run_checks
from scorbot.provenance import motion_source_sha256
from scorbot.session import BestEffortRecorder
from scorbot.streaming import MOTORS

try:
    from examples import bench_joint as bench
except ImportError:  # Run as a script: examples/ itself is on sys.path.
    import bench_joint as bench

EXIT_TRIAL_FAILED = 1
OperatorDeclined = bench.OperatorDeclined
observe_leds = bench.observe_leds

# Nothing here is measured on the arm. Extra travel caps are caller options.
RATE_RANGE_HZ = (1.0, 10.0)
MAX_STEPS = 200
TARGET_REFRESH_S = 0.1          # re-send the target between ticks: the stream holds after 0.5 s of silence
HOME_TIMEOUT_S = 6.0
ARRIVED_COUNTS = 5
MAX_POLICY_ERRORS = 3           # in a row; then the run ends and returns home

OBS = ("obs passed to a policy: step (int), t (seconds since the first tick), "
       "deg ({motor: degrees from home, measured}), target_deg ({motor: last commanded}), "
       "counts ({motor: counts from home, measured})")


def reach_policy(goals):
    """Built-in policy: step each joint toward ``goals`` (degrees from home) and hold there."""
    def policy(obs):
        return {m: goal - obs["target_deg"][m] for m, goal in goals.items()}
    return policy


def parse_reach(text):
    goals = {}
    for part in text.split(","):
        motor, _, value = part.partition("=")
        if motor.strip() not in MOTORS:
            raise ValueError(f"unknown motor {motor!r}; use {', '.join(MOTORS)}")
        goals[motor.strip()] = float(value)
        if not math.isfinite(goals[motor.strip()]):
            raise ValueError("goals must be finite")
    return goals


def load_policy(args):
    if args.policy_file:
        spec = importlib.util.spec_from_file_location("user_policy", args.policy_file)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.policy, f"file:{args.policy_file}"
    kind, _, rest = args.policy.partition(":")
    if kind != "reach":
        raise ValueError("--policy must be reach:motor=deg[,motor=deg] (or use --policy-file)")
    return reach_policy(parse_reach(rest)), args.policy


def counts_to_deg(counts, per_degree):
    return {m: counts[m] / per_degree[m] for m in MOTORS}


def run_policy(stream, policy, per_degree, *, steps, rate_hz, cap_deg, write,
               grip_at=None, grip=None):
    """The control loop. Returns (per-tick rows, the stream now live). Never raises for a policy fault.

    Before tick ``grip_at``, ``grip(stream)`` replaces the stream and returns (new stream, its start
    counts from home); a gripper or resume failure raises.
    """
    target = dict.fromkeys(MOTORS, 0.0)
    rows, errors_in_row, started = [], 0, time.monotonic()
    last = dict.fromkeys(MOTORS, 0)
    for i in range(steps):
        if grip is not None and i == grip_at:
            stream, last = grip(stream)      # a new stream has no steps yet: read from its start
        tick_end = time.monotonic() + 1.0 / rate_hz
        seen = stream.steps
        counts = ({m: int(seen[-1]["measured"][m]) for m in MOTORS} if seen else dict(last))
        last = counts
        measured = counts_to_deg(counts, per_degree)
        obs = {"step": i, "t": round(time.monotonic() - started, 3), "deg": measured,
               "target_deg": dict(target), "counts": counts}
        error = None
        try:
            delta = policy(obs)
        except Exception as exc:         # a policy bug holds the arm, it never stops the loop
            delta, error = None, repr(exc)
        errors_in_row = errors_in_row + 1 if error else 0
        target, clamped = clamp(delta, target, measured, cap_deg=cap_deg)
        target_counts = {m: int(round(target[m] * per_degree[m])) for m in MOTORS}
        row = dict(step=i, t=obs["t"], measured_deg=measured, measured_counts=counts,
                   delta_asked=delta, target_deg=target, target_counts=target_counts,
                   clamped=clamped, policy_error=error)
        write("policy_step", **row)
        rows.append(row)
        try:
            while True:                  # hold the cadence and keep the stream fed
                stream.set_target(target_counts)
                if time.monotonic() >= tick_end:
                    break
                time.sleep(min(TARGET_REFRESH_S, max(0.0, tick_end - time.monotonic())))
        except StreamRefused:
            break                        # the stream faulted or stopped; closing it reports why
        if errors_in_row >= MAX_POLICY_ERRORS:
            break
    return rows, stream


def grip_and_resume(robot, stream, start_stream, *, step, direction, write, rec):
    """Stop the stream, move the jaw, start the next stream with resume=True; log where each stood.

    The same stop and gripper call as ``PolicyArm`` (``stop_stream_and_move_gripper``). Returns
    (new stream, its start counts from home). Answers issue #209: does the resumed stream start
    from the arm's real position. Unverified on the arm.
    """
    def from_home(state, home):
        return {m: signed_count_delta(state.encoder_counts[m], home[m]) for m in MOTORS}

    seen = stream.steps
    before = {m: int(seen[-1]["measured"][m]) for m in MOTORS} if seen else None
    stopped, moved = stop_stream_and_move_gripper(robot, stream, direction)
    new = start_stream(resume=True)
    start = from_home(new._before, new._home)
    offset = None if before is None else {m: start[m] - before[m] for m in MOTORS}
    row = dict(step=step, direction=direction, measured_counts_before_stop=before,
               stopped_counts=from_home(stopped, stream._home), resume_start_counts=start,
               resume_minus_before=offset, drift_counts=limits.DRIFT_COUNTS,
               within_drift=None if offset is None else all(
                   abs(v) <= limits.DRIFT_COUNTS for v in offset.values()),
               gripper=dict(planned_counts=moved.planned_counts, moved_counts=moved.moved_counts,
                            full_travel=moved.full_travel),
               state_after_resume=asdict(new._before))
    write("gripper_resume", **row)
    rec.log_note("gripper resume (#209): " + json.dumps(
        {k: row[k] for k in ("step", "measured_counts_before_stop", "resume_start_counts",
                             "within_drift")}))
    print(f"Gripper resume (#209) before tick {step}: measured before stop {before}, "
          f"resumed stream start {start}, offset {offset} (drift limit {limits.DRIFT_COUNTS}).")
    return new, start


def go_home(stream):
    """Send zero counts until every motor is back, or HOME_TIMEOUT_S. True if it got there."""
    deadline = time.monotonic() + HOME_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            stream.set_target(dict.fromkeys(MOTORS, 0))
        except StreamRefused:
            return False
        seen = stream.steps
        if seen and all(abs(seen[-1]["measured"][m]) <= ARRIVED_COUNTS for m in MOTORS):
            return True
        time.sleep(TARGET_REFRESH_S)
    return False


def summarize(rows, final):
    """What the run did. ``final`` is counts from home after the stream closed."""
    problems = []
    if not rows:
        problems.append("the policy loop took no steps")
    if any(abs(final[m]) > ARRIVED_COUNTS for m in MOTORS):
        problems.append(f"did not return to home (counts from home {final})")
    broken = [r["step"] for r in rows if r["policy_error"]]
    if len(broken) >= MAX_POLICY_ERRORS and broken[-MAX_POLICY_ERRORS:] == \
            list(range(rows[-1]["step"] - MAX_POLICY_ERRORS + 1, rows[-1]["step"] + 1)):
        problems.append("the policy failed repeatedly; the run ended early")
    peak = {m: max((abs(r["measured_deg"][m]) for r in rows), default=0.0) for m in MOTORS}
    return {"ticks": len(rows), "clamped_ticks": sum(bool(r["clamped"]) for r in rows),
            "policy_errors": len(broken), "peak_deg_from_home": {m: round(v, 3) for m, v in peak.items()},
            "final_counts_from_home": final, "problems": problems, "passed": not problems}


def report(result):
    print(f"Ticks: {result['ticks']} (clamped {result['clamped_ticks']}, policy errors "
          f"{result['policy_errors']}). Peak degrees from home: {result['peak_deg_from_home']}.")
    print(f"Final counts from home: {result['final_counts_from_home']}.")
    if result["passed"]:
        print("Run result: PASSED.")
    else:
        print("!!! Run result: FAILED (" + "; ".join(result["problems"]) + ").")


def main():
    with bench.termination_as_interrupt():
        return _run()


def _run() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    bench.add_session_arguments(parser)
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--policy", help="reach:motor=deg[,motor=deg]")
    which.add_argument("--policy-file", type=Path, help="a .py file defining policy(obs)")
    parser.add_argument("--steps", type=int, default=20, help=f"control ticks, 1 to {MAX_STEPS}")
    parser.add_argument("--rate-hz", type=float, default=5.0,
                        help="control rate, %g to %g" % RATE_RANGE_HZ)
    parser.add_argument("--travel-cap-deg", type=float, default=None,
                        help="optional positive travel bound from vendor Home; default: vendor windows only")
    parser.add_argument("--acknowledge-supervised-motion", action="store_true")
    parser.add_argument("--session-root", type=Path, default=None)
    parser.add_argument("--simulate", action="store_true",
                        help="Rehearse with the simulated controller: no USB, no robot")
    parser.add_argument("--grip-at", type=int, default=None, metavar="N",
                        help="opt-in: before tick N (1 to steps-1) stop the stream, open the jaw, "
                             "resume and log where the new stream starts (#209)")
    args = parser.parse_args()
    if not args.acknowledge_supervised_motion:
        parser.error("An operator and the physical emergency stop are required")
    if not 1 <= args.steps <= MAX_STEPS:
        parser.error(f"--steps must be 1 through {MAX_STEPS}")
    if not math.isfinite(args.rate_hz) or not RATE_RANGE_HZ[0] <= args.rate_hz <= RATE_RANGE_HZ[1]:
        parser.error("--rate-hz must be %g through %g" % RATE_RANGE_HZ)
    if args.travel_cap_deg is not None and (not math.isfinite(args.travel_cap_deg)
                                            or args.travel_cap_deg <= 0):
        parser.error("--travel-cap-deg must be a positive finite number")
    if args.grip_at is not None and not 1 <= args.grip_at < args.steps:
        parser.error("--grip-at must be 1 through --steps minus 1")
    bench.check_session_labels(parser, args)
    if find_spec("ruckig") is None:
        parser.error("Streaming needs the planning extra: uv sync --locked --extra windows "
                     "--extra test --extra planning")
    try:
        policy, policy_name = load_policy(args)
    except (ValueError, OSError, AttributeError) as exc:
        parser.error(f"cannot load the policy: {exc}")
    if args.policy:      # a goal past the cap is clamped anyway; say so now, not mid-run
        for motor, goal in parse_reach(args.policy.partition(":")[2]).items():
            if args.travel_cap_deg is not None and abs(goal) > args.travel_cap_deg:
                parser.error(f"goal {motor}={goal:g} is past --travel-cap-deg {args.travel_cap_deg:g}")
    output, events = bench.new_output_paths(parser, args)
    try:    # degrees to counts, with the sign a bench jog of that joint has: planned offline, no USB
        probe = Scorbot()
        per_degree = {m: probe.preview_jog(m, 1.0)["motor_count_deltas"][m] for m in MOTORS}
    except ValueError as exc:
        parser.error(f"counts per degree cannot be planned: {exc}")

    backend = bench.choose_backend(args.simulate, Scorbot, SimulatedScorbot, run_checks,
                                   transport=args.transport)
    if backend is None:
        return 1
    robot_class, data_source = backend
    revision = bench.software_revision()
    recorder = bench.open_recorder(args, output, data_source,
                                   f"policy run {policy_name} ({output.name})")
    result = None
    with recorder as writer, output.open("x", encoding="utf-8") as record:
        rec = BestEffortRecorder(writer)
        write, prompt = bench.row_writer(record), bench.confirmation_prompt(rec)
        command = bench.OpenCommand(rec)
        write("session", schema_version=1, procedure="policy_run", robot_id=args.robot_id,
              arm_label=args.arm_label, controller_label=args.controller_label,
              driver=args.driver, operator=args.operator, start_pose_note=args.start_pose_note,
              software_commit=revision, motion_source_sha256=motion_source_sha256(),
              controller_event_log=events.name, policy=policy_name, steps=args.steps,
              rate_hz=args.rate_hz, travel_cap_deg=args.travel_cap_deg,
              data_source=data_source, mcap_session=rec.path.name, led_prompts=True)
        try:
            with robot_class(log_path=events, robot_id=args.robot_id) as robot:
                home_state = bench.connect_and_home(
                    robot, write, rec, prompt, command,
                    "stopped after homing; no policy run requested", vendor=True)
                plan = dict(policy=policy_name, steps=args.steps, rate_hz=args.rate_hz,
                            travel_cap_deg=args.travel_cap_deg, step_max_deg=STEP_MAX_DEG,
                            lead_margin_deg=LEAD_MARGIN_DEG, counts_per_degree=per_degree,
                            grip_at=args.grip_at)
                write("policy_plan", **plan)
                rec.log_note("policy plan: " + json.dumps(plan))
                print(json.dumps(plan, indent=2))
                print("Streaming has never run on this arm. Ctrl-C asks for a software stop; it is "
                      "NOT an emergency stop. Clear the travel path, keep the emergency stop in reach.")
                if prompt("Type POLICY for one bounded policy run: ", "POLICY") != "POLICY":
                    raise OperatorDeclined("declined before the policy run")
                before = robot.get_state()
                write("before_policy", state=asdict(before))
                rec.log_state(before)
                try:
                    from_home = {m: signed_count_delta(before.encoder_counts[m],
                                                       home_state.encoder_counts[m]) for m in MOTORS}
                except ValueError as exc:
                    raise ScorbotError(f"Cannot place the arm relative to home: {exc}") from exc
                if any(abs(c) > ARRIVED_COUNTS for c in from_home.values()):
                    raise ScorbotError(f"The arm is not at home (counts from home {from_home}); "
                                       "no stream was started")
                command.start("start_stream", plan)
                stream, rows = None, []
                try:
                    with ExitStack() as streams:     # --grip-at replaces the stream; exit each one
                        def start_stream(*, resume=False):
                            return streams.enter_context(robot.start_stream(
                                travel_cap_deg=args.travel_cap_deg, lead_limit_deg=LEAD_LIMIT_DEG,
                                resume=resume))

                        def grip(old):
                            return grip_and_resume(robot, old, start_stream, step=args.grip_at,
                                                   direction="open", write=write, rec=rec)

                        stream = start_stream()
                        rows, stream = run_policy(
                            stream, policy, per_degree, steps=args.steps, rate_hz=args.rate_hz,
                            cap_deg=args.travel_cap_deg, write=write, grip_at=args.grip_at,
                            grip=grip if args.grip_at is not None else None)
                        go_home(stream)
                except (Exception, KeyboardInterrupt):
                    write("policy_failed", result=summarize(rows, dict.fromkeys(MOTORS, 0)))
                    raise
                after = stream.final_state
                try:
                    final = {m: signed_count_delta(after.encoder_counts[m],
                                                   home_state.encoder_counts[m]) for m in MOTORS}
                except ValueError as exc:
                    raise ScorbotError(f"Cannot place the arm relative to home: {exc}") from exc
                result = summarize(rows, final)
                command_id = command.take()
                write("after_policy", state=asdict(after), result=result)
                if result["passed"]:
                    rec.log_command_result(command_id, "completed", completion_source="stream closed")
                else:
                    rec.log_command_result(command_id, "faulted", detail="; ".join(result["problems"]))
                rec.log_state(after)
                rec.log_note("policy result: " + json.dumps(result))
                report(result)
                observe_leds("after_policy", write, rec, expect_motors="lit", expect_power="green")
                bench.observe_and_disable(
                    robot, write, rec,
                    indicators_question="Other controller indicators or sounds during the run: ",
                    issue_question="Fault, noise, jerky or unexpected motion, or other issue "
                                   "(write 'none' if none): ")
        except OperatorDeclined as exc:
            return bench.report_declined(exc, write, rec)
        except (Exception, KeyboardInterrupt) as exc:
            bench.report_failure(exc, write, rec, command)
            raise
        bench.report_recorder_failure(write, rec)
    print(f"Saved policy record to {output} and controller events to {events}")
    if rec.failure is None:
        print(f"Saved MCAP session to {rec.path}")
    if rec.failure is not None:
        return 1
    return 0 if result and result["passed"] else EXIT_TRIAL_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
