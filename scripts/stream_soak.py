"""Desk-only Stack 2 streaming soak. No USB, DLL or native Win32 API is opened.

Uses device_file.DeviceLink(api=...) (as test_transport_device_file does) and
the PL12 pipe server's Plant, with the real SDK and legacy workers. A failing
verdict is evidence, never repaired by the harness's cleanup stop.
"""

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import itertools
import math
from pathlib import Path
import random
import sys
import threading
import time
from types import SimpleNamespace
from unittest import mock

from scorbot import Scorbot, ScorbotError
from scorbot.streaming import StreamRefused
from scorbot.transport.device_file import DeviceLink
from scorbot.vendor_home_source import VendorHomeSource
from tools.usbc_fake.pipe_server import Plant

FAKE_PATH = r"\\.\pipe\scorbot_stream_soak"
FAULTS = ("late", "stale", "impact", "drop-write", "drop-read")
HOLD_S = 0.5
DELAY_S = 0.6
OBSERVE_S = 0.9


def record(log, event, **fields):
    log.write(json.dumps({"event": event, "host_monotonic_ns": time.monotonic_ns(),
                          "data_source": "simulated", "simulated": True, **fields}) + "\n")
    # Buffered packet logs avoid a disk flush per fake exchange under parallel pytest.
    if event not in ("write", "read"):
        log.flush()


class FaultWire:
    """Only an in-process fake API. Every attempted OUT is recorded, even failed writes."""

    def __init__(self, log):
        self.log, self.plant = log, Plant()
        self.closed = False
        self.fault = None
        self.injected = threading.Event()
        self.armed = threading.Event()
        self.watch = False
        self.attempts = []
        self.boundary = None
        self.last = bytes(64)
        self.previous = bytes(64)
        self.reply = bytes(64)
        self.stale_until = 0.0
        self.repeats = 0
        self.stream = None
        self.stale_deadline = None

    def create_file(self, path):
        if path != FAKE_PATH:
            raise ValueError("only the desk fake path is accepted")
        return 1

    def close_handle(self, handle):
        self.closed = True

    def cancel_io(self, thread_id):
        pass  # no blocking native I/O exists in this fake

    def arm(self, fault):
        self.fault = fault
        self.armed.set()

    def _inject(self, *, exclude_attempt=False):
        self.boundary = (None if self.fault == "stale" else
                         len(self.attempts) + int(exclude_attempt))
        record(self.log, "injected", fault=self.fault, boundary=self.boundary,
               delay_s=DELAY_S if self.fault in ("late", "stale") else None)
        self.injected.set()

    def write_file(self, handle, data):
        if self.closed:
            raise OSError("desk fake closed")
        data = bytes(data)
        if self.armed.is_set() and not self.injected.is_set() and self.fault == "drop-write" and data[4] == 0x0D:
            self._inject(exclude_attempt=True)
        failed = self.injected.is_set() and self.fault == "drop-write"
        # Compare every packet's arm setpoints with the fake's actual counts,
        # including stop/disable packets, not just entries in Stream.steps.
        targets = [int.from_bytes(data[12 + 4*n:16 + 4*n], "little", signed=True) for n in range(3)]
        measured = [self.plant.reported(n) for n in range(3)]
        moving = any(self.plant.mode[n] == "S" and targets[n] != measured[n] for n in range(3))
        row = {"packet": data.hex(), "delivered": not failed, "moving": moving,
               "targets": targets, "measured": measured}
        if self.watch:
            self.attempts.append(row)
        record(self.log, "write", **row)
        self.last = data
        if failed:
            raise OSError("injected dropped link: writes fail")
        self.plant.on_out(data)
        self.reply = self.plant.reply(data)
        return len(data)

    def read_file(self, handle, size):
        if self.armed.is_set() and not self.injected.is_set() and self.last[4] == 0x0D:
            if self.fault == "late":
                time.sleep(DELAY_S)
            self._inject()
            if self.fault == "stale":
                self.stale_until = time.monotonic() + DELAY_S
                self.stale_deadline = self.stream._last_reply_time + self.stream.core.reply_timeout_s
        if self.injected.is_set() and self.fault in ("drop-read", "drop-write"):
            record(self.log, "read", failed=True)
            raise OSError("injected dropped link: reads fail")
        reply = self.reply
        if time.monotonic() < self.stale_until:
            reply = self.previous
            self.repeats += 1
        elif self.injected.is_set() and self.fault == "impact" and not getattr(self, "impact_sent", False):
            packet = bytearray(reply)
            packet[22:24] = (71).to_bytes(2, "little")
            reply = bytes(packet)
            self.impact_sent = True
        self.previous = reply
        record(self.log, "read", packet=reply.hex(), failed=False)
        if self.stale_deadline is not None and self.boundary is None and time.monotonic() > self.stale_deadline:
            # The write preceding this expired reply is already transmitted.
            # Inspect every attempt after this read, independently of the SDK's verdict.
            self.boundary = len(self.attempts)
            record(self.log, "watchdog_boundary", boundary=self.boundary, deadline_s=self.stale_deadline)
        return reply


class DeskScorbot(Scorbot):
    """Override only the link-opening seam; there is no native API fallback."""

    def __init__(self, wire, **kwargs):
        super().__init__(transport="device_file", **kwargs)
        self.wire = wire

    def _open_link(self):
        self.transport_used = "device_file desk fake"
        link = DeviceLink(FAKE_PATH, api=self.wire)
        # No blocking I/O exists during fake setup: thousands of cancellation
        # timer threads would make Home depend on scheduler load. Keep them for streaming.
        timed = link._timed
        link._timed = lambda call, timeout_ms: timed(call, timeout_ms if self.wire.watch else 0)
        return link

    def _record(self, event, **fields):
        super()._record(event, data_source="simulated", simulated=True, **fields)


class Targets:
    """Seeded steps on a sine, kept well inside both travel and vendor bounds."""

    def __init__(self, bounds, seed):
        self.bounds, self.rng = bounds, random.Random(seed)
        self.steps = {m: 0.0 for m in bounds}
        self.next_step = 0.0

    def at(self, elapsed):
        if elapsed >= self.next_step:
            self.steps = {m: self.rng.uniform(-0.25, 0.25) for m in self.bounds}
            self.next_step = elapsed + 0.2
        result = {}
        for i, (motor, (low, high)) in enumerate(self.bounds.items()):
            amplitude = min(60.0, -low * 0.4, high * 0.4)
            result[motor] = round(amplitude * (math.sin(2 * math.pi * elapsed / 1.7 + i) + self.steps[motor]))
        return result


def refused(call):
    try:
        call()
    except (ScorbotError, StreamRefused) as exc:
        return str(exc)
    return None


def scenario(fault, seconds, folder, log, seed):
    controller_name, wire_name = f"{fault}.controller.jsonl", f"{fault}.wire.jsonl"
    result = {"fault": fault, "error": None, "cleanup_error": None,
              "controller_log": controller_name, "wire_log": wire_name, "checks": {"scenario_completed": False}}
    stream, robot = None, None
    with (folder / wire_name).open("x", encoding="utf-8") as wire_log, ExitStack() as patches:
        wire = FaultWire(wire_log)
        robot = DeskScorbot(wire, log_path=folder / controller_name, command_timeout=5)
        # Plant motion is per exchange: Home deadlines must use that same clock,
        # regardless of host load. Only streaming and injected latency use real time.
        patches.enter_context(mock.patch("scorbot.homing.VendorHomeSource", side_effect=lambda **kwargs:
            VendorHomeSource(clock=itertools.count(step=0.001).__next__, **kwargs)))
        # Faster coarse travel on this desk plant; the full vendor Home still runs.
        patches.enter_context(mock.patch("tools.usbc_fake.pipe_server.DRIVE_GAIN", 1.0))
        for name in ("libdef", "libcomm", "libsync"):
            module = robot._legacy(name)
            patches.enter_context(mock.patch.object(module, "time", SimpleNamespace(
                monotonic=time.monotonic, sleep=lambda delay: None)))
        robot.STOP_SETTLE_INTERVAL_S = 0.001
        try:
            robot.connect()
            robot.enable()
            robot.vendor_home(operator_at_stop=True, jaws_empty=True, wrist_clear=True)
            stream = robot.start_stream(travel_cap_deg=1.0, hold_timeout_s=HOLD_S)
            wire.stream = stream
            targets = Targets(stream.core.bounds, seed)
            wire.watch = True
            started = time.monotonic()
            inject_at = started + max(0.35, seconds)
            finished = None
            while True:
                now = time.monotonic()
                target = targets.at(now - started)
                target_error = refused(lambda target=target: stream.set_target(target))
                record(log, "target", fault=fault, target=target, error=target_error)
                if not wire.armed.is_set() and now >= inject_at:
                    wire.arm(fault)
                    finished = now + DELAY_S + OBSERVE_S + 2
                if wire.injected.is_set() and finished is not None:
                    # Observe for a full interval after the actual injection,
                    # independent of OS scheduling and the requested run length.
                    if not hasattr(wire, "observed_until"):
                        wire.observed_until = now + OBSERVE_S
                    if now >= wire.observed_until:
                        break
                if finished is not None and now >= finished:
                    break
                time.sleep(0.02)

            boundary = wire.boundary
            before = wire.attempts[:boundary] if boundary is not None else wire.attempts
            after = wire.attempts[boundary:] if boundary is not None else []
            moving = [row for row in after if row["moving"]]
            result.update(injected=wire.injected.is_set(), healthy_moving_packets=sum(r["moving"] for r in before),
                          moving_after_fault=len(moving), first_moving_after_fault=next(iter(moving), None),
                          state=stream.core.state, fault_latched=bool(robot._fault),
                          error=robot._fault or stream.core.fault, repeated_replies=wire.repeats,
                          motors_off_queued=robot._fault_off_queued)
            result["target_refusal"] = refused(lambda: stream.set_target({"base": 0}))
            off = [r for r in after if bytes.fromhex(r["packet"])[4:6] == b"\x42\xff"]
            result["motors_off_attempted"] = bool(off)
            result["motors_off_sent"] = any(r["delivered"] for r in off)
            checks = {"injected": result["injected"], "healthy_motion": result["healthy_moving_packets"] > 0,
                      "boundary_observed": boundary is not None,
                      "no_motion_after_fault": not moving,
                      "stopped_or_holding": stream.core.state in ("faulted", "stopped", "holding"),
                      "targets_refused": result["target_refusal"] is not None}
            expected = ("ImpactDetect" if fault == "impact" else "USB error" if fault.startswith("drop-")
                        else "no fresh controller reply for")
            checks.update(error=expected in (result["error"] or ""),
                              fault_latched=result["fault_latched"], motors_off_queued=result["motors_off_queued"],
                              motors_off_attempted=result["motors_off_attempted"])
            # These observations end before the harness asks for cleanup.
            record(log, "observed", **result)
            if robot._fault:
                result["close_error"] = refused(stream.close)
                result["motion_refusal"] = refused(lambda: robot.jog_joint("base", 0.1))
                result["reconnect_refusal"] = None
                checks["motion_refused"] = result["motion_refusal"] is not None
            else:
                stream.stop()
            wire.watch = False
            result["checks"] = checks
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
            result["checks"] = {"scenario_completed": False}
        finally:
            # Restore a failed fake link only for teardown, after the verdict
            # evidence. No production recovery or success is inferred from it.
            wire.fault = None
            wire.armed.clear()
            wire.stale_until = 0
            wire.watch = False
            try:
                if robot._worker_failed:
                    # Like tests/desk_wire.py: EXIT to both queues. A dead
                    # worker may also have taken the media; release that wait.
                    for _ in range(3):
                        robot._reads.put(robot._legacy("libcomm").fresh_media(robot._buffer))
                        robot._commands.put([528, 1, 1])
                        robot._sync.put(528)
                    robot._command_waiting.clear()
                    for thread in (robot._command_thread, robot._sync_thread):
                        if thread is not None:
                            thread.join(timeout=2)
                robot.disconnect()
                if robot._fault:
                    result["reconnect_refusal"] = refused(robot.connect)
                    result["checks"]["fault_requires_new_instance"] = result["reconnect_refusal"] is not None
                if any(t is not None and t.is_alive() for t in (robot._command_thread, robot._sync_thread)):
                    raise RuntimeError("legacy worker still alive after disconnect")
            except Exception as exc:
                result["cleanup_error"] = str(exc)
            record(log, "cleanup", fault=fault, error=result["cleanup_error"])
    result["checks"]["cleanup"] = result["cleanup_error"] is None
    result["passed"] = all(result["checks"].values())
    record(log, "result", **result)
    failed_checks = [name for name, passed in result["checks"].items() if not passed]
    print(f"{fault}: state={result.get('state')}; error={result['error']!r}; latched={result.get('fault_latched')}; "
          f"motors-off queued/attempted/sent={result.get('motors_off_queued')}/"
          f"{result.get('motors_off_attempted')}/{result.get('motors_off_sent')}; "
          f"moving-after={result.get('moving_after_fault')}; {'PASS' if result['passed'] else 'FAIL'}"
          f"{': ' + ', '.join(failed_checks) if failed_checks else ''}", flush=True)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutes", type=float, default=10, help="total healthy streaming minutes, split across faults")
    parser.add_argument("--logs", type=Path, default=Path(".pytest_cache/stream-soak"))
    parser.add_argument("--fault", action="append", choices=FAULTS, help="select faults; default: each once in listed order")
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--device-path", default=FAKE_PATH, help="only the built-in desk fake is accepted")
    parser.add_argument("--online", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.online or args.device_path != FAKE_PATH:
        parser.error("desk only: --online and real device paths are refused; only the built-in fake is accepted")
    if not math.isfinite(args.minutes) or args.minutes <= 0:
        parser.error("--minutes must be positive and finite")
    faults = tuple(dict.fromkeys(args.fault or FAULTS))
    folder = args.logs / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    folder.mkdir(parents=True, exist_ok=False)
    print(f"DESK FAKE ONLY (E2, unverified on the arm); logs={folder}", flush=True)
    with (folder / "results.jsonl").open("x", encoding="utf-8") as log:
        record(log, "session", seed=args.seed, minutes=args.minutes, faults=faults,
               model="pipe fake", hold_s=HOLD_S, delay_s=DELAY_S)
        results = [scenario(fault, args.minutes * 60 / len(faults), folder, log, args.seed)
                   for fault in faults]
        passed = all(r["passed"] for r in results)
        record(log, "summary", passed=passed, failures=[r["fault"] for r in results if not r["passed"]])
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
