"""One supervised ER-4U gripper trial: open and close the jaws, at most four moves.

The first run of ``Scorbot.move_gripper`` on the arm. It connects, switches
the motors on (the arm is not homed and no arm joint is commanded), and
opens or closes the gripper once per move, asking before each one.

The gripper sequence is the legacy one and has never been run by this
project. It moves a fixed distance with no force limit: closing on an object
drives the setpoint past it. First trials are with EMPTY JAWS, then something
soft. Keep fingers out of the jaws.

With --simulate the same procedure runs against the simulated controller, for
rehearsal away from the lab; every record is then labelled simulated.
"""

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from scorbot import Scorbot, SimulatedScorbot
from scorbot.preflight import run_checks
from scorbot.provenance import motion_source_sha256
from scorbot.session import BestEffortRecorder

try:
    from examples import bench_joint as bench
except ImportError:  # Run as a script: examples/ itself is on sys.path.
    import bench_joint as bench

EXIT_DECLINED = bench.EXIT_DECLINED
OperatorDeclined = bench.OperatorDeclined
observe_leds = bench.observe_leds
MAX_MOVES = 4
DIRECTIONS = ("open", "close")


def describe(result) -> str:
    """One printed line per move, for the lab day card."""
    how = ("full travel" if result["full_travel"]
           else "stopped short: an object in the jaws, or the end of the gripper's travel")
    return (f"{result['direction']}: moved {result['moved_counts']:+d} of "
            f"{result['planned_counts']} counts ({how})")


def main() -> int:
    with bench.termination_as_interrupt():
        return _run()


def _run() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    bench.add_session_arguments(parser)
    parser.add_argument("--moves", nargs="+", choices=DIRECTIONS, required=True,
                        metavar="MOVE",
                        help=f"open or close, in order, at most {MAX_MOVES} (e.g. open close)")
    parser.add_argument("--acknowledge-supervised-motion", action="store_true")
    parser.add_argument("--session-root", type=Path, default=None,
                        help="Folder for the MCAP session (default: <output folder>/sessions)")
    parser.add_argument("--simulate", action="store_true",
                        help="Rehearse with the simulated controller: no USB, no robot")
    args = parser.parse_args()
    if not args.acknowledge_supervised_motion:
        parser.error("An operator and the physical emergency stop are required")
    if len(args.moves) > MAX_MOVES:
        parser.error(f"--moves takes at most {MAX_MOVES} moves in one run")
    bench.check_session_labels(parser, args)
    output, events = bench.new_output_paths(parser, args)

    backend = bench.choose_backend(args.simulate, Scorbot, SimulatedScorbot, run_checks)
    if backend is None:
        return 1
    robot_class, data_source = backend
    revision = bench.software_revision()
    recorder = bench.open_recorder(args, output, data_source,
                                   f"gripper trial {' '.join(args.moves)} ({output.name})")
    with recorder as writer, output.open("x", encoding="utf-8") as record:
        rec = BestEffortRecorder(writer)
        write, prompt = bench.row_writer(record), bench.confirmation_prompt(rec)
        command = bench.OpenCommand(rec)
        write("session", schema_version=1, procedure="gripper_trial", robot_id=args.robot_id,
              arm_label=args.arm_label, controller_label=args.controller_label,
              driver=args.driver, operator=args.operator,
              start_pose_note=args.start_pose_note,
              software_commit=revision, motion_source_sha256=motion_source_sha256(),
              controller_event_log=events.name, moves=list(args.moves),
              data_source=data_source, mcap_session=rec.path.name, led_prompts=True)
        try:
            with robot_class(log_path=events, robot_id=args.robot_id) as robot:
                state = robot.get_state()
                write("connected", state=asdict(state))
                rec.log_state(state)
                # Connect requests motor-disable; require LED confirmation.
                observe_leds("after_connect", write, rec,
                             expect_motors="off", expect_power="green",
                             require_expected=True)
                print("The motors will be switched on. The arm is NOT homed and no arm "
                      "joint is commanded; it should hold where it is.")
                if prompt("Type ENABLE to switch the motors on: ", "ENABLE") != "ENABLE":
                    raise OperatorDeclined("declined before enabling the motors")
                robot.enable()
                observe_leds("after_enable", write, rec, expect_motors="lit",
                             expect_power="green", require_expected=True)
                plan = dict(moves=list(args.moves), sequence="legacy clamp (orders 14 and 15)")
                write("gripper_plan", **plan)
                rec.log_note("gripper plan: " + json.dumps(plan))
                print("The gripper moves a fixed distance with no force limit. Empty jaws for "
                      "a first trial; keep fingers clear.")
                print("This sequence has never been run on this arm. Ctrl-C asks for a "
                      "software stop; it is NOT an emergency stop.")
                for number, direction in enumerate(args.moves, 1):
                    print(f"Move {number} of {len(args.moves)}: the gripper will "
                          f"{direction.upper()}.")
                    if prompt(f"Type GRIP to {direction} the gripper: ", "GRIP") != "GRIP":
                        raise OperatorDeclined(f"declined before move {number} ({direction})")
                    before = robot.get_state()
                    write("before_gripper", move=number, direction=direction,
                          state=asdict(before))
                    rec.log_state(before)
                    command.start("move_gripper", {"direction": direction})
                    moved = robot.move_gripper(direction)
                    result = dict(direction=moved.direction,
                                  planned_counts=moved.planned_counts,
                                  moved_counts=moved.moved_counts,
                                  full_travel=moved.full_travel)
                    command_id = command.take()
                    # The primary JSONL evidence is written before any recorder call.
                    write("after_gripper", move=number, state=asdict(moved.state), result=result)
                    rec.log_command_result(command_id, "completed",
                                           completion_source="move_gripper() returned")
                    rec.log_state(moved.state)
                    print(describe(result))
                observe_leds("after_gripper", write, rec,
                             expect_motors="lit", expect_power="green")
                bench.observe_and_disable(
                    robot, write, rec,
                    indicators_question="Other controller indicators or sounds during the "
                                        "gripper moves: ",
                    issue_question="Fault, noise, stalling, arm motion, or other issue "
                                   "(write 'none' if none): ")
        except OperatorDeclined as exc:
            return bench.report_declined(exc, write, rec)
        except (Exception, KeyboardInterrupt) as exc:
            bench.report_failure(exc, write, rec, command)
            raise
        bench.report_recorder_failure(write, rec)
    print(f"Saved gripper record to {output} and controller events to {events}")
    if rec.failure is None:
        print(f"Saved MCAP session to {rec.path}")
    return 1 if rec.failure is not None else 0


if __name__ == "__main__":
    raise SystemExit(main())
