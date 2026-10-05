"""One supervised ER-4U home and bounded jog with a durable bench record.

With --simulate the same procedure runs against the simulated controller, for
rehearsal away from the lab; every record is then labelled simulated.
"""

import argparse
import contextlib
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import signal
import subprocess
import threading
import time

from scorbot import MotionStopped, Scorbot, SimulatedScorbot
from scorbot.calibration import signed_count_delta
from scorbot.preflight import run_checks
from scorbot.provenance import motion_source_sha256
from scorbot.session import BestEffortRecorder, SessionWriter

# Exit code for a run the operator ended at a prompt (0 = completed, 1 = failed).
EXIT_DECLINED = 3


class OperatorDeclined(Exception):
    """The operator chose not to continue at a confirmation prompt."""


class LedCheckFailed(RuntimeError):
    """A required controller LED state was not confirmed before motion."""


# Operator LED observations. The SDK's ``enabled`` is only command history; the
# controller's green MOTORS LED is the only independent evidence of motor power,
# and POWER is green while the controller is communicating with the PC (orange:
# not communicating, flashing: USB timeout). See docs/manual/HARDWARE_REFERENCE.md.
MOTORS_KEYS = {"y": "lit", "n": "off", "u": "unsure"}
POWER_KEYS = {"g": "green", "o": "orange", "f": "flashing", "u": "unsure"}
_WORDS = {"yes": "lit", "no": "off", "lit": "lit", "off": "off", "unsure": "unsure",
          "green": "green", "orange": "orange", "flashing": "flashing"}
LED_ATTEMPTS = 3
_EXAMPLE_VALUES = frozenset({
    "arm nameplate", "controller nameplate", "current windows driver",
    "your initials", "photo/sketch of known start pose",
    "same known pose as idle capture",
})
_MISMATCH_TEXT = {
    ("motors", "off", "lit"): "Software says motors are DISABLED but the MOTORS LED was "
                              "reported LIT. Stop and check; the physical stop is "
                              "authoritative.",
    ("motors", "lit", "off"): "Software says motors are ENABLED but the MOTORS LED was "
                              "reported OFF. The controller may have cut motor power (COFF, "
                              "emergency stop, timeout or over-current). Stop and check.",
    ("power", "green", "orange"): "Software is talking to the controller but the POWER LED "
                                  "was reported ORANGE (not communicating). Stop and check.",
    ("power", "green", "flashing"): "Software is talking to the controller but the POWER LED "
                                    "was reported FLASHING (USB timeout). Stop and check.",
}


def reject_example_values(parser, **values):
    """Reject the literal sample labels before a controller connection."""
    examples = [f"--{name.replace('_', '-')}" for name, value in values.items()
                if value.strip().lower() in _EXAMPLE_VALUES]
    if examples:
        parser.error("Replace example values with actual lab metadata before connecting: "
                     + ", ".join(examples))


def ask_key(question, keys, ask=None):
    """Return (value, answered) for a single-key answer; never loops forever.

    Keys are case-insensitive and the full word (``yes``, ``green``...) works
    too. Invalid input is asked again, at most LED_ATTEMPTS times in all; after
    that, or at end of input, the answer is recorded as ``unsure``.
    """
    ask = ask or input  # Looked up per call, so a patched input() is honoured.
    for _ in range(LED_ATTEMPTS):
        try:
            answer = ask(question).strip().lower()
        except EOFError:
            print("\n  No answer (end of input); recording 'unsure'.")
            return "unsure", False
        value = keys.get(answer) or _WORDS.get(answer)
        if value in keys.values():
            return value, True
        print(f"  Type one key: {'/'.join(keys)}.")
    print("  No valid answer; recording 'unsure'.")
    return "unsure", False


def observe_leds(step, write, rec, *, expect_motors=None, expect_power=None,
                 require_expected=False, ask=None):
    """Ask for the MOTORS and POWER LEDs, record the answers, and flag mismatches.

    The expectation is never shown before the answer, so it cannot lead the
    observer. A contradiction prints a prominent warning and writes a
    ``led_mismatch`` row. Required checks end the software run if either LED
    differs or is unsure. The physical stop remains authoritative.
    """
    ask = ask or input
    print(f"LED check {step.replace('_', ' ')}: look at the controller front panel.")
    motors, motors_ok = ask_key("  MOTORS LED lit? [y/n/u=unsure] ", MOTORS_KEYS, ask)
    power, power_ok = ask_key("  POWER LED colour? [g=green/o=orange/f=flashing/u=unsure] ",
                              POWER_KEYS, ask)
    row = dict(step=step, motors_led=motors, power_led=power,
               expected_motors_led=expect_motors, expected_power_led=expect_power)
    defaulted = [name for name, ok in (("motors_led", motors_ok), ("power_led", power_ok))
                 if not ok]
    if defaulted:
        row["defaulted_to_unsure"] = defaulted
    write("led_observation", **row)
    rec.log_decision(f"motors_led={motors} power_led={power}",
                     reason=f"LED observation {step} (expected motors_led={expect_motors}, "
                            f"power_led={expect_power})")
    mismatches = [(led, expected, observed) for led, expected, observed in
                  (("motors", expect_motors, motors), ("power", expect_power, power))
                  if expected is not None and observed not in ("unsure", expected)]
    for led, expected, observed in mismatches:
        message = _MISMATCH_TEXT.get(
            (led, expected, observed),
            f"{led.upper()} LED reported {observed}; software expects {expected}. "
            "Stop and check.")
        write("led_mismatch", step=step, led=led, observed=observed, expected=expected,
              message=message)
        rec.log_note(f"LED MISMATCH {step}: {message}")
        print("!" * 72)
        print(f"!!! WARNING ({step.replace('_', ' ')}): {message}")
        print("!!! This warning does not stop motor power. If in doubt, use the physical stop.")
        print("!" * 72)
    if require_expected:
        unconfirmed = [(led, expected, observed) for led, expected, observed in
                       (("motors", expect_motors, motors), ("power", expect_power, power))
                       if expected is not None and observed != expected]
        if unconfirmed:
            reason = (f"LED check {step} not confirmed: " + ", ".join(
                f"{led}={observed} (expected {expected})"
                for led, expected, observed in unconfirmed))
            write("led_gate_failed", step=step, reason=reason)
            rec.log_note(reason)
            print(f"!!! {reason}. End this run; use the physical stop if motor state is uncertain.")
            raise LedCheckFailed(reason)
    return row


@contextlib.contextmanager
def termination_as_interrupt():
    """Treat SIGTERM and Windows SIGBREAK like Ctrl-C while a lab script runs.

    The scripts already record a failure row, disconnect and close the recorder on
    KeyboardInterrupt. Closing the console window on Windows is expected to arrive
    as SIGBREAK, with a few seconds before the process is killed (unverified here).
    """
    def interrupt(signum, _frame):
        raise KeyboardInterrupt(f"stopped by signal {signum}")

    previous = {}
    for name in ("SIGTERM", "SIGBREAK"):
        number = getattr(signal, name, None)
        if number is not None:
            try:
                previous[number] = signal.signal(number, interrupt)
            except ValueError:   # not the main thread: leave handlers alone
                break
    try:
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


# -- shared by the lab scripts ----------------------------------------------
#
# bench_stream.py imports these, so a home-then-move procedure is written once.
# Each script passes in its own robot classes and ``run_checks``: tests replace
# those names on the script module, and a helper that imported them itself
# would not see the replacement.

def add_session_arguments(parser) -> None:
    """The labels every bench record carries, plus the output path."""
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("--arm-label", required=True)
    parser.add_argument("--controller-label", required=True)
    parser.add_argument("--driver", required=True)
    parser.add_argument("--operator", required=True, help="Name or lab initials")
    parser.add_argument("--start-pose-note", required=True)


def check_session_labels(parser, args) -> None:
    """Refuse empty labels and the documentation's sample values. No file access."""
    if any(not value.strip() for value in (args.robot_id, args.arm_label,
                                           args.controller_label, args.driver,
                                           args.operator, args.start_pose_note)):
        parser.error("All labels and the pose note must be nonempty")
    reject_example_values(parser, arm_label=args.arm_label,
                          controller_label=args.controller_label, driver=args.driver,
                          operator=args.operator, start_pose_note=args.start_pose_note)


def new_output_paths(parser, args):
    """(record path, controller event log path); refuses to overwrite either."""
    output = args.output.resolve()
    events = output.with_name(output.stem + ".controller.jsonl")
    if output.exists() or events.exists():
        parser.error("Output already exists; choose a new session filename")
    return output, events


def choose_backend(simulate: bool, real, simulated, checks):
    """(robot class, data source), or None when the read-only preflight fails.

    The only difference between a lab run and a rehearsal.
    """
    if simulate:
        print("SIMULATED rehearsal: no USB and no robot are used.")
        return simulated, "simulated"
    results = checks()
    for check in results:
        print(f"{'PASS' if check.passed else 'FAIL'} {check.name}: {check.detail}")
    if not all(check.passed for check in results):
        return None
    return real, "real"


def software_revision() -> str:
    try:
        # Ask the checkout this script lives in, not the operator's current folder.
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[1],
            text=True, timeout=3).strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def open_recorder(args, output: Path, data_source: str, task: str):
    """The MCAP session, opened before the controller.

    A failure to start it then happens before the motor-on handshake. Once
    running it is best-effort: a later recording error warns but never skips
    the JSONL record or the procedure.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    return SessionWriter.create(
        args.session_root or output.parent / "sessions", data_source=data_source,
        robot_id=args.robot_id, controller_id=args.controller_label,
        operator=args.operator, start_pose_note=args.start_pose_note,
        task=task, usb_driver=args.driver)


def row_writer(stream):
    """``write(kind, **fields)``: one flushed JSONL row, the primary evidence."""
    def write(kind, **fields):
        stream.write(json.dumps({
            "type": kind,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "host_monotonic_ns": time.monotonic_ns(),
            **fields,
        }, allow_nan=False) + "\n")
        stream.flush()
    return write


def confirmation_prompt(rec):
    """``prompt(question, choice)``: ask, record the decision, return the answer."""
    def prompt(question, choice):
        answer = input(question).strip()
        rec.log_decision(choice if answer == choice else "declined",
                         reason=f"typed {answer!r} at: {question.strip()}")
        return answer
    return prompt


class OpenCommand:
    """The recorder command in progress, so a failure can mark it faulted."""

    def __init__(self, rec):
        self._rec, self._id = rec, None

    def start(self, name: str, parameters: dict) -> None:
        self._id = self._rec.log_command(name, parameters)

    def take(self):
        """Close the bookkeeping and return the id for ``log_command_result``."""
        command_id, self._id = self._id, None
        return command_id

    def fault(self, exc: BaseException) -> None:
        if self._id is not None:
            self._rec.log_command_result(self._id, "faulted", detail=str(exc))


def connect_and_home(robot, write, rec, prompt, command: OpenCommand, declined_after_home: str):
    """Connected robot to a home the operator accepted. Returns the home state.

    Every prompt, row and LED step here is shared by the jog and the stream
    procedures; docs/design/OPERATOR_UX.md and the review script depend on them.
    """
    state = robot.get_state()
    write("connected", state=asdict(state))
    rec.log_state(state)
    # Connect requests motor-disable; require LED confirmation.
    observe_leds("after_connect", write, rec,
                 expect_motors="off", expect_power="green",
                 require_expected=True)
    print("Confirm the arm is in the documented legacy homing start pose.")
    if prompt("Type HOME to search home: ", "HOME") != "HOME":
        raise OperatorDeclined("declined before homing")
    robot.enable()
    # A contradictory or unsure LED cannot lead to homing.
    observe_leds("after_enable", write, rec, expect_motors="lit",
                 expect_power="green", require_expected=True)
    command.start("home", {"start_position_confirmed": True})
    robot.home(start_position_confirmed=True)
    command_id = command.take()
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
        raise OperatorDeclined(declined_after_home)
    return home_state


def observe_and_disable(robot, write, rec, *, indicators_question: str, issue_question: str):
    """What the operator saw, then motors off and the LED check that follows."""
    direction = input("Observed joint direction and approximate displacement: ").strip()
    other_motion = input("Did any other joint move? Describe what you saw: ").strip()
    indicators = input(indicators_question).strip()
    issue = input(issue_question).strip()
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


def report_declined(exc, write, rec) -> int:
    # A decline is the procedure working, not a fault: no traceback, no
    # alarm, and a distinct exit code. Disconnect has already run.
    write("operator_declined", text=str(exc))
    rec.log_note(f"operator declined: {exc}")
    print(f"Run ended by the operator ({exc}). Confirm the MOTORS LED is off.")
    return EXIT_DECLINED


def report_failure(exc, write, rec, command: OpenCommand) -> None:
    write("session_failed", error_type=type(exc).__name__, error=str(exc))
    command.fault(exc)
    rec.log_fault(f"{type(exc).__name__}: {exc}")
    print("Session failed. If motion or motor state is uncertain, use the physical stop.")


def report_recorder_failure(write, rec) -> None:
    if rec.failure is not None:
        write("recorder_failed", error_type=type(rec.failure).__name__,
              error=str(rec.failure))
        print("MCAP recording is incomplete; review the JSONL and recorder failure.")


def main() -> int:
    with termination_as_interrupt():
        return _run()


def _run() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_session_arguments(parser)
    parser.add_argument("--joint", choices=("base", "shoulder", "elbow"), required=True)
    parser.add_argument("--delta", type=float, required=True,
                        help="Signed requested legacy jog in degrees, at most 1")
    parser.add_argument("--speed", type=int, default=10)
    parser.add_argument("--acknowledge-supervised-motion", action="store_true")
    parser.add_argument("--session-root", type=Path, default=None,
                        help="Folder for the MCAP session (default: <output folder>/sessions)")
    parser.add_argument("--simulate", action="store_true",
                        help="Rehearse with the simulated controller: no USB, no robot")
    parser.add_argument("--stop-after-ms", type=int, default=None,
                        help="Software-stop trial: request a stop this many milliseconds "
                             "after the jog starts. Not an emergency stop")
    args = parser.parse_args()
    if not args.acknowledge_supervised_motion:
        parser.error("An operator and the physical emergency stop are required")
    if not math.isfinite(args.delta) or not 0 < abs(args.delta) <= 1:
        parser.error("--delta must be nonzero and at most one degree")
    if not 1 <= args.speed <= 20:
        parser.error("--speed must be 1 through 20")
    if args.stop_after_ms is not None and not 1 <= args.stop_after_ms <= 5000:
        parser.error("--stop-after-ms must be 1 through 5000")
    check_session_labels(parser, args)
    output, events = new_output_paths(parser, args)
    # Plan the jog offline now, so a delta below one motor count is refused
    # before preflight and homing rather than after (preview never opens USB).
    try:
        Scorbot().preview_jog(args.joint, args.delta, speed=args.speed)
    except ValueError as exc:
        parser.error(f"--delta cannot be planned: {exc}")

    backend = choose_backend(args.simulate, Scorbot, SimulatedScorbot, run_checks)
    if backend is None:
        return 1
    robot_class, data_source = backend
    revision = software_revision()
    recorder = open_recorder(args, output, data_source,
                             f"bench jog {args.joint} {args.delta:+g} deg ({output.name})")
    with recorder as writer, output.open("x", encoding="utf-8") as stream:
        rec = BestEffortRecorder(writer)
        write, prompt, command = row_writer(stream), confirmation_prompt(rec), OpenCommand(rec)
        write("session", schema_version=1, robot_id=args.robot_id,
              arm_label=args.arm_label, controller_label=args.controller_label,
              driver=args.driver, operator=args.operator,
              start_pose_note=args.start_pose_note,
              software_commit=revision, motion_source_sha256=motion_source_sha256(),
              controller_event_log=events.name,
              joint=args.joint, requested_delta_deg=args.delta, speed=args.speed,
              stop_after_ms=args.stop_after_ms,
              data_source=data_source, mcap_session=rec.path.name, led_prompts=True)
        try:
            with robot_class(log_path=events, robot_id=args.robot_id) as robot:
                connect_and_home(robot, write, rec, prompt, command,
                                 "stopped after homing; no jog requested")
                preview_state = robot.get_state()
                preview = robot.preview_jog(
                    args.joint, args.delta, speed=args.speed,
                    starting_signed_counts=preview_state.signed_encoder_counts)
                write("motion_preview", plan=preview, state=asdict(preview_state))
                rec.log_note(f"motion preview: {args.joint} {preview['motor_count_deltas']} "
                             f"in {len(preview['increments'])} increments")
                print(json.dumps(preview, indent=2))
                print("Clear the travel path and keep the emergency stop within reach.")
                if args.stop_after_ms is not None:
                    print(f"Stop trial: a software stop is requested {args.stop_after_ms} ms "
                          "after the jog starts. It is NOT an emergency stop and is "
                          "untested on the arm; the jog may still complete.")
                if prompt("Type MOVE for one bounded jog: ", "MOVE") != "MOVE":
                    raise OperatorDeclined("declined before the jog")
                before = robot.get_state()
                write("before_jog", state=asdict(before))
                rec.log_state(before)
                command.start("jog_joint", {
                    "joint": args.joint, "delta_degrees": args.delta, "speed": args.speed,
                    "motor_count_deltas": preview["motor_count_deltas"]})
                stop_timer, outcome = None, "completed"
                if args.stop_after_ms is not None:
                    stop_timer = threading.Timer(args.stop_after_ms / 1000, robot.request_stop)
                    stop_timer.daemon = True
                    stop_timer.start()
                try:
                    after = robot.jog_joint(args.joint, args.delta, speed=args.speed)
                except MotionStopped as exc:
                    after = exc.state
                    outcome = "stopped" if exc.started else "not_started"
                finally:
                    if stop_timer is not None:
                        stop_timer.cancel()
                if outcome == "stopped":
                    # A stop is only an early stop if the counts say so: every planned
                    # motor must have travelled less than its plan.
                    try:
                        short = all(
                            abs(signed_count_delta(after.encoder_counts[motor],
                                                   before.encoder_counts[motor])) < abs(planned)
                            for motor, planned in preview["motor_count_deltas"].items())
                    except ValueError:
                        short = False
                    outcome = "stopped_early" if short else "stopped_at_full_travel"
                command_id = command.take()
                # The primary JSONL evidence is written before any recorder call.
                write("after_jog", state=asdict(after), stop_trial_outcome=outcome,
                      stopped_on_request=outcome == "stopped_early")
                status, source = {
                    "completed": ("completed", "jog_joint() returned"),
                    "not_started": ("rejected", "stop requested before the jog started"),
                    "stopped_early": ("stopped", "jog_joint() stopped on request"),
                    "stopped_at_full_travel": ("stopped", "jog_joint() stopped on request, "
                                                          "after the full planned travel"),
                }[outcome]
                rec.log_command_result(command_id, status, completion_source=source)
                rec.log_state(after)
                if args.stop_after_ms is not None:
                    print({
                        "completed": "The jog completed; the stop request came too late "
                                     "or had no effect.",
                        "not_started": "The stop request arrived before the jog started; "
                                       "nothing was sent. The trial is inconclusive: use a "
                                       "longer --stop-after-ms.",
                        "stopped_early": "The jog ended early on the stop request.",
                        "stopped_at_full_travel": "A stop was reported, but the counts show "
                                                  "the full planned travel. The trial does "
                                                  "not show an early stop.",
                    }[outcome])
                observe_leds("after_jog", write, rec,
                             expect_motors="lit", expect_power="green")
                observe_and_disable(
                    robot, write, rec,
                    indicators_question="Other controller indicators or sounds after jog: ",
                    issue_question="Fault, noise, unexpected motion, or other issue "
                                   "(write 'none' if none): ")
        except OperatorDeclined as exc:
            return report_declined(exc, write, rec)
        except (Exception, KeyboardInterrupt) as exc:
            report_failure(exc, write, rec, command)
            raise
        report_recorder_failure(write, rec)
    print(f"Saved bench record to {output} and controller events to {events}")
    if rec.failure is None:
        print(f"Saved MCAP session to {rec.path}")
    return 1 if rec.failure is not None else 0


if __name__ == "__main__":
    raise SystemExit(main())
