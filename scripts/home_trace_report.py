"""Read a legacy home search back from a controller event log.

``Scorbot.home`` copies every USB packet of the search into one ``motion_trace`` row
(``joint: "home"``). This prints one line per controller reply (counts, position-error
words, home-switch bits, collision flag) and a summary that answers the questions the
10-06 failure left open: how far each joint moved, how its error grew, and whether the
controller flagged a collision (IN byte 59, ULPGC thesis p. 57).

Reads a file only; never opens USB or commands the arm. Stdlib plus ``scorbot.state``.

    python scripts/home_trace_report.py logs/base-first-02.controller.jsonl
"""

import argparse
import json
from pathlib import Path
import sys

from scorbot.calibration import signed_count_delta
from scorbot.state import JOINTS, PACKET_MIN_LENGTH, collision_motors, decode_state

# The legacy search aborts at this position error (setHome.py: getError >= MAX_ERROR).
LEGACY_MAX_ERROR = 40
THRESHOLDS = (20, LEGACY_MAX_ERROR)


def error_size(raw: int) -> int:
    """The legacy ``libdef.getError`` rule: words at 65500 or above are small negatives."""
    return abs(65535 - raw) if raw >= 65500 else raw


def home_traces(path):
    """Every ``motion_trace`` row with ``joint == "home"``, in log order."""
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if row.get("event") == "motion_trace" and row.get("joint") == "home":
                rows.append(row)
    return rows


def replies(trace):
    """(t_ms from the first reply, packet bytes, decoded state) for each IN packet."""
    out, first = [], None
    for packet in trace.get("packets", []):
        if packet.get("direction") != "in":
            continue
        raw = bytes.fromhex(packet["hex"])
        if len(raw) < PACKET_MIN_LENGTH:
            continue
        stamp = packet["host_monotonic_ns"]
        first = stamp if first is None else first
        state = decode_state(raw, connected=True, enabled=None, homed=False, fault=None)
        out.append((round((stamp - first) / 1e6), raw, state))
    return out


def summary_lines(replies_, dropped=0):
    lines = [f"messages: {len(replies_)}"]
    if len(replies_) > 1:
        span = replies_[-1][0] - replies_[0][0]
        lines[0] += f", {span} ms from first to last, mean {span / (len(replies_) - 1):.1f} ms apart"
    lines.append("")
    lines.append(f"Position error, in counts (the legacy search aborts at {LEGACY_MAX_ERROR}):")
    for name in JOINTS:
        sizes = [(t, error_size(state.controller_error_counts[name])) for t, _, state in replies_]
        peak_time, peak = max(sizes, key=lambda item: item[1])
        parts = [f"max error {peak} at {peak_time} ms"]
        for limit in THRESHOLDS:
            reached = next((t for t, size in sizes if size >= limit), None)
            parts.append(f"first reached {limit} at {reached} ms" if reached is not None
                         else f"never reached {limit}")
        lines.append(f"  {name:<14} " + "; ".join(parts))
    lines.append("")
    lines.append("Travel: net is first reply to last; path adds every step, so out and back shows:")
    for name in JOINTS:
        series = [state.encoder_counts[name] for _, _, state in replies_]
        net = signed_count_delta(series[-1], series[0])
        path = sum(abs(signed_count_delta(b, a)) for a, b in zip(series, series[1:]))
        lines.append(f"  {name:<14} net {net} counts, path {path} counts")
    lines.append("")
    seen = {}
    for t, raw, _ in replies_:
        for motor in collision_motors(raw):
            seen.setdefault(motor, t)
    if seen:
        lines.append("collision flag set: " + ", ".join(
            f"{motor} at {t} ms" for motor, t in sorted(seen.items(), key=lambda i: i[1])))
    elif dropped:
        lines.append(f"collision flag: not set in the packets kept ({dropped} were dropped)")
    else:
        lines.append("collision flag: never set")
    changes, last_bits = [], replies_[0][2].home_switch_bits
    for t, _, state in replies_:
        if state.home_switch_bits != last_bits:
            changes.append(f"{last_bits} -> {state.home_switch_bits} at {t} ms")
            last_bits = state.home_switch_bits
    lines.append("switch bits changed: " + ", ".join(changes) if changes
                 else f"switch bits: never changed (stayed {last_bits})")
    return lines


SHORT = {"base": "base", "shoulder": "shldr", "elbow": "elbow",
         "wrist_motor_1": "wr1", "wrist_motor_2": "wr2", "gripper": "grip"}


def table_lines(replies_, every=1):
    names = " ".join(f"{SHORT[name]:>7}" for name in JOINTS)
    lines = [f"{'':<7}{'counts (signed)':<{len(names)}} | {'position error':<{len(names)}} |",
             f"{'t_ms':<7}{names} | {names} | sw  | collision"]
    for index, (t, raw, state) in enumerate(replies_):
        if index % every:
            continue
        counts = " ".join(f"{state.signed_encoder_counts[name]:>7}" for name in JOINTS)
        errors = " ".join(f"{error_size(state.controller_error_counts[name]):>7}"
                          for name in JOINTS)
        flagged = ",".join(collision_motors(raw)) or "-"
        lines.append(f"{t:<7d}{counts} | {errors} | {state.home_switch_bits:>3} | {flagged}")
    return lines


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Read a legacy home search back from a controller event log.")
    parser.add_argument("log", help="a <run>.controller.jsonl event log")
    parser.add_argument("--every", type=int, default=1, help="print every Nth reply in the table")
    parser.add_argument("--no-table", action="store_true", help="print only the summary")
    args = parser.parse_args(argv)
    traces = home_traces(args.log)
    if not traces:
        print(f"no home trace in {args.log}: the run never reached a legacy home, "
              "or it was recorded before home traces existed.")
        return 1
    trace = traces[-1]
    replies_ = replies(trace)
    print(f"Home trace from {args.log}"
          + (f" (the last of {len(traces)})" if len(traces) > 1 else ""))
    dropped = trace.get("dropped_packets") or 0
    if dropped:
        lost = "start" if trace.get("kept") == "last" else "end"
        print(f"note: {dropped} packets were dropped from the {lost} of the search; "
              "everything below is about the packets that were kept.")
    if not replies_:
        print("the trace holds no controller replies.")
        return 1
    print()
    if not args.no_table:
        print("\n".join(table_lines(replies_, max(1, args.every))))
        print()
    print("\n".join(summary_lines(replies_, dropped)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
