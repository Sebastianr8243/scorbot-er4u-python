"""Read one decoded state through the full legacy connection handshake."""

import argparse
from collections import Counter
import contextlib
from dataclasses import replace
import json
import statistics
import time

from ..pacing import echo_lag
from ..robot import Scorbot, ScorbotError
from ..state import decode_state
from .device_file import DeviceLinkError

CONNECT_WARNING = ("Connecting energises then releases the motors briefly; "
                   "the operator must be present and clear of the arm.")


def read_status(robot):
    """Connect if needed and decode one snapshot; the caller owns disconnect."""
    if robot._device is None:
        robot.connect()
    observed, packet = robot.get_state_and_packet()
    state = decode_state(packet, connected=True, enabled=observed.enabled,
                         homed=observed.homed, fault=observed.fault)
    return replace(state, simulated=observed.simulated), packet


def summarise(rows):
    """Reply spacing and echo lag from ``PacketTrace`` rows (``out``/``in``, host ns, hex); pure."""
    sent_id, lags, times = None, Counter(), []
    for row in rows:
        data = bytes.fromhex(row["hex"])
        if row["direction"] == "out":
            sent_id = data[0] if data else None
        else:
            times.append(row["host_monotonic_ns"])
            lags[echo_lag(sent_id, data) if sent_id else None] += 1
    gaps = sorted((b - a) / 1e6 for a, b in zip(times, times[1:]))
    if not gaps:
        return {"replies": len(times), "lag": dict(lags)}
    return {"replies": len(times), "lag": dict(sorted(lags.items(), key=lambda kv: (kv[0] is None, kv[0]))),
            "gap_ms": {"min": gaps[0], "median": statistics.median(gaps),
                       "p95": gaps[int(0.95 * (len(gaps) - 1))], "max": gaps[-1]}}


def measure(robot, reads, log=None, timeout_s=None):
    """Record the idle packets the sync thread already exchanges until ``reads`` replies arrive; sends nothing."""
    deadline = time.monotonic() + (timeout_s if timeout_s is not None else 2 + reads * 0.2)
    robot._trace.limit = max(robot._trace.limit, 2 * reads + 8)
    robot._trace.start()
    try:
        while time.monotonic() < deadline:
            time.sleep(0.01)
            with robot._trace._lock:
                if sum(p["direction"] == "in" for p in robot._trace._packets) >= reads:
                    break
    finally:
        rows, dropped = robot._trace.stop()
    if log is not None:
        for row in rows:
            log.write(json.dumps(row, allow_nan=False) + "\n")
    return summarise(rows), dropped


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", choices=("auto", "device_file", "pyusb"), default="auto")
    parser.add_argument("--simulate", action="store_true")
    parser.add_argument("--latency-ms", type=float, default=0, help="SIMULATED reply latency only")
    parser.add_argument("--online", action="store_true", help="real controller (default is refusing)")
    parser.add_argument("--reads", type=int, default=0, metavar="N",
                        help="after the snapshot, time N idle replies (spacing and echo lag); sends nothing extra")
    parser.add_argument("--log", metavar="PATH", help="with --reads: write every packet row here (new file only)")
    args = parser.parse_args(argv)
    if args.reads < 0 or (args.log and not args.reads):
        parser.error("--reads must be >= 0 and --log needs --reads")
    label = "SIMULATED" if args.simulate else "REAL"
    print(f"{label} {CONNECT_WARNING}")
    robot = None
    try:
        if not args.simulate:
            if not args.online:
                raise ScorbotError("Real mode needs --online (or use --simulate)")
        if args.simulate:
            from ..lab.dry_link import DryScorbot
            robot = DryScorbot(latency_ms=args.latency_ms)
        else:
            robot = Scorbot(transport=args.transport)
        try:
            state, packet = read_status(robot)
            print(f"{label} raw {packet.hex()}")
            print(f"{label} counts {state.encoder_counts}; switches {state.home_switch_bits:#04x}")
            if args.reads:
                with (open(args.log, "x", encoding="utf-8") if args.log else contextlib.nullcontext()) as log:
                    result, dropped = measure(robot, args.reads, log)
                print(f"{label} timing {json.dumps(result)}" + (f"; dropped {dropped}" if dropped else ""))
        finally:
            robot.disconnect()
    except (ScorbotError, DeviceLinkError, OSError, ValueError, EOFError) as exc:
        print(f"{label} FAIL {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
