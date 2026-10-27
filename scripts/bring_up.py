"""Supervised Stack 2 ladder. Use --simulate for a desk rehearsal without USB."""

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time
import threading
from uuid import uuid4

from scorbot import Scorbot, ScorbotError, SimulatedScorbot
from scorbot import limits
from scorbot.calibration import signed_count_delta
from scorbot.lab.dry_link import DryScorbot
from scorbot.provenance import motion_source_sha256
from scorbot.state import JOINTS
from scorbot.transport.probe import CONNECT_WARNING, read_status


def _check_state(state):
    if state.fault or state.collision_bits or state.home_switch_bits >= 32:
        raise ScorbotError("Unexpected fault, collision or unknown switch bits; abort")
    if any(state.controller_error_counts.values()):
        raise ScorbotError("Controller reports position error counts; abort and inspect")


def _check_move(before, after, expected):
    for motor in JOINTS:
        moved = signed_count_delta(after.encoder_counts[motor], before.encoder_counts[motor])
        if abs(moved - expected.get(motor, 0)) > limits.DRIFT_COUNTS:
            raise ScorbotError(f"Unexpected {motor} count change: {moved}; abort")


def _sine(robot, joint, seconds, adaptive_pacing):
    before = robot._motion_state()
    home = robot._home_counts
    start = {m: signed_count_delta(before.encoder_counts[m], home[m])
             for m in ("base", "shoulder", "elbow")}
    scale = robot.preview_jog(joint, 1)["motor_count_deltas"][joint]
    with robot.start_stream(adaptive_pacing=adaptive_pacing) as stream:
        started = time.monotonic()
        while True:
            elapsed = time.monotonic() - started
            if elapsed >= seconds:
                break
            stream.set_target({**start, joint: start[joint] + scale * math.sin(2 * math.pi * elapsed / seconds)})
            time.sleep(0.02)
        stream.set_target(start)
    after = robot._motion_state()
    _check_move(before, after, {})


def run(args, handle, *, robot_factory=None, ask=None, emit=print):
    """Run once; injectable factory/input are for guarded tests, never gate bypasses."""
    label = "SIMULATED" if args.simulate else "REAL"
    source = "simulated" if args.simulate else "real"
    fingerprint = motion_source_sha256()
    ask = input if ask is None else ask
    log_lock = threading.Lock()
    trace_exists = False
    log_error = None

    def record(event, **fields):
        nonlocal trace_exists, log_error
        if event == "motion_trace" and fields.get("joint") == "vendor_home" and fields.get("packets"):
            trace_exists = True
        row = json.dumps({"event": event, "label": label, "data_source": source,
                          "simulated": args.simulate, "host_monotonic_ns": time.monotonic_ns(),
                          "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                          "motion_source_sha256": fingerprint, **fields}, allow_nan=False) + "\n"
        with log_lock:
            if log_error is not None:
                return
            try:
                handle.write(row)
                handle.flush()
            except (OSError, ValueError) as exc:
                log_error = exc
                raise

    def say(message):
        emit(f"{label} {message}")

    def confirm(word, reason):
        if not args.simulate and ask(f"REAL {reason}; type {word}: ") != word:
            raise ScorbotError(f"Expected exact words {word}; abort")

    robot = None
    step = "authorization"
    before = after = None
    failed = False
    record("ladder_start", options=vars(args))
    say("The red physical stop is the only emergency stop.")
    say(CONNECT_WARNING)
    try:
        if not args.simulate and not args.online:
            raise ScorbotError("Real mode needs --online (or use --simulate)")
        factory = robot_factory or (SimulatedScorbot if args.simulate else Scorbot)
        robot = factory(transport=args.transport)
        robot._record = record

        def perform(name, action):
            nonlocal step, before, after
            step, before, after = name, None, None
            record("step_start", step=step)
            say(f"--- {step} ---")
            if robot._device is not None:
                before = robot._motion_state()
                _check_state(before)
            action()
            if log_error is not None:
                raise ScorbotError(f"JSONL recording failed: {log_error}")
            after = robot._motion_state()
            _check_state(after)
            if step == "Home" and not after.homed:
                raise ScorbotError("Home did not establish a session home")
            if step == "enable motors" and after.enabled is not True:
                raise ScorbotError("Enable did not complete")
            record("step_pass", step=step, before=asdict(before) if before else None,
                   after=asdict(after))
            say(f"PASS {step} counts {list(before.encoder_counts.values()) if before else 'unavailable'}"
                f" -> {list(after.encoder_counts.values())}")

        def probe():
            if args.simulate:
                with DryScorbot(latency_ms=args.latency_ms) as dry:
                    state, packet = read_status(dry)
                    _check_state(state)
                    record("dry_probe", state=asdict(state), raw_hex=packet.hex())
            state, packet = read_status(robot)
            _check_state(state)
            record("probe", state=asdict(state), raw_hex=packet.hex())

        def home():
            if args.legacy_home:
                confirm("HOME", "Legacy Home failed on the real arm on 2026-10-06")
                robot.home(start_position_confirmed=True)
            else:
                confirm("EMPTY", "Gripper jaws empty")
                confirm("CLEAR", "Wrist and gripper path clear")
                confirm("HOME", "Vendor Home moves every axis; operator at physical stop")
                robot.vendor_home(operator_at_stop=True, jaws_empty=True, wrist_clear=True)

        def jog(delta):
            start = robot._motion_state()
            expected = robot.preview_jog(args.joint, delta)["motor_count_deltas"]
            robot.jog_joint(args.joint, delta, speed=1)
            _check_move(start, robot._motion_state(), expected)

        perform("probe", probe)
        perform("enable motors", robot.enable)
        perform("Home", home)
        origin = robot._motion_state()
        perform(f"{args.joint} +1 degree", lambda: jog(1))

        def back():
            jog(-1)
            _check_move(origin, robot._motion_state(), {})

        perform(f"{args.joint} back 1 degree", back)
        if args.stream:
            perform("1 degree sine stream", lambda: _sine(robot, args.joint, args.stream_seconds,
                                                          args.adaptive_pacing))
        perform("disable motors", robot.disable)
    except (Exception, KeyboardInterrupt) as exc:
        failed = True
        if robot is not None:
            robot.request_stop()
            robot._latch_fault(f"Bring-up aborted at {step}: {exc}", keep_first=True)
            if after is None and robot._device is not None:
                try:
                    after = robot.get_state()
                except Exception:
                    pass
        record("step_fail", step=step, error=str(exc), before=asdict(before) if before else None,
               after=asdict(after) if after else None)
        say(f"FAIL {step}: {exc}; counts "
            f"{list(before.encoder_counts.values()) if before else 'unavailable'} -> "
            f"{list(after.encoder_counts.values()) if after else 'unavailable'}; ABORT")
    finally:
        if robot is not None:
            try:
                robot.disconnect()
            except (Exception, KeyboardInterrupt) as exc:
                failed = True
                record("cleanup_failed", error=str(exc))
                say(f"FAIL disconnect: {exc}; use the physical stop")
        record("ladder_end", passed=not failed)
        say(f"Log: {handle.name}")
        if trace_exists:
            say(f"Vendor Home trace: python scripts/usb_trace.py from-log {handle.name} --out home-trace.jsonl")
            say("Compare only same-source exports: python scripts/usb_trace.py compare vendor-trace.jsonl home-trace.jsonl")
    return 1 if failed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simulate", action="store_true")
    parser.add_argument("--transport", choices=("auto", "device_file", "pyusb"), default="auto")
    parser.add_argument("--logs", required=True)
    parser.add_argument("--online", action="store_true", help="real controller (default is refusing)")
    parser.add_argument("--joint", choices=("base", "shoulder", "elbow"), default="base")
    parser.add_argument("--legacy-home", action="store_true")
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--stream-seconds", type=float, default=3)
    parser.add_argument("--adaptive-pacing", action="store_true")
    parser.add_argument("--latency-ms", type=float, default=0, help="SIMULATED dry-link reply latency only")
    args = parser.parse_args(argv)
    if not math.isfinite(args.stream_seconds) or not 1 <= args.stream_seconds <= 10:
        parser.error("--stream-seconds must be 1 through 10 seconds")
    if not math.isfinite(args.latency_ms) or args.latency_ms < 0:
        parser.error("--latency-ms must be finite and non-negative")
    label = "SIMULATED" if args.simulate else "REAL"
    try:
        folder = Path(args.logs)
        folder.mkdir(parents=True, exist_ok=True)
        name = f"{label.lower()}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid4().hex[:8]}.jsonl"
        with (folder / name).open("x", encoding="utf-8") as handle:
            return run(args, handle)
    except OSError as exc:
        print(f"{label} FAIL log I/O: {exc}; use the physical stop if connected")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
