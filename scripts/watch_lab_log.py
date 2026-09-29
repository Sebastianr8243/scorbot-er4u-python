"""Read-only live view of a lab run, built from the JSONL files it writes.

Run it in a second terminal with the same --output path given to
record_raw_state.py or bench_joint.py. It follows that file and its
.controller.jsonl companion. It never opens USB, imports no controller code,
and cannot send a command: the lab script stays the only controller owner.

Rows are written on events and samples, not on every USB packet, so a quiet
screen does not prove the controller link is alive; a stopped sync worker is
reported by the SDK as a sync_worker_crashed event.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from scorbot.calibration import signed_count_delta
from scorbot.state import JOINTS

# Legacy switch codes (libdef.get_switch); physical polarity unverified.
SWITCH_BITS = (("base", 1), ("shoulder", 2), ("elbow", 4), ("pitch", 8), ("roll", 16))
ALARM_EVENTS = {"command_timeout", "command_error", "command_interrupted", "feedback_fault",
                "sync_worker_crashed", "home_failed", "following_error", "calibration_fault",
                "connect_failed", "session_failed", "disable_skipped_worker_crashed"}
# Every SDK command logs these around it; they bury the events worth reading.
QUIET_EVENTS = {"command_start", "command_complete"}


class Follower:
    """Yield complete JSON lines appended to a file that may not exist yet."""

    def __init__(self, path: Path):
        self.path = path
        self._offset = 0
        self._partial = b""
        self.bad_lines = 0

    def poll(self) -> list[dict]:
        try:
            with self.path.open("rb") as stream:
                stream.seek(self._offset)
                chunk = stream.read()
        except FileNotFoundError:
            return []
        self._offset += len(chunk)
        lines = (self._partial + chunk).split(b"\n")
        self._partial = lines.pop()
        rows = []
        for line in lines:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                self.bad_lines += 1
                continue
            if isinstance(row, dict):
                rows.append(row)
        return rows


class RunView:
    """Fold rows from both files into what the operator needs on one screen."""

    def __init__(self):
        self.session = None
        self.first_state = None
        self.state = None
        self.state_source = None
        self.state_ns = -1
        self.plan = None
        self.motion_before = None
        self.motion_after = None
        self.events = []
        self.alarms = []
        self.samples = 0
        self.last_row_utc = None

    def add(self, row: dict, source: str):
        kind = row.get("type") or row.get("event")
        if not kind:
            return
        self.last_row_utc = row.get("timestamp_utc") or self.last_row_utc
        if kind == "session":
            self.session = row
        if kind == "sample":
            self.samples += 1
        elif kind not in QUIET_EVENTS:
            detail = row.get("error") or row.get("text") or ""
            self.events.append((source, kind, str(detail)))
            del self.events[:-8]
        if kind in ALARM_EVENTS:
            self.alarms.append(f"{kind}: {row.get('error', '')}".rstrip(": "))
        plan = row.get("plan")
        if kind == "motion_preview" and isinstance(plan, dict):
            self.plan = plan
        state = row.get("state")
        if isinstance(state, dict) and isinstance(state.get("encoder_counts"), dict):
            if self.first_state is None:
                self.first_state = state
            # Rows from the two files arrive per poll, not in time order; never let
            # an older state replace a newer one (e.g. "enabled" after "disabled").
            stamp = row.get("host_monotonic_ns")
            if not (type(stamp) is int and stamp < self.state_ns):
                self.state, self.state_source = state, f"{source}:{kind}"
                if type(stamp) is int:
                    self.state_ns = stamp
            if kind in ("before_jog", "motion_start"):
                self.motion_before, self.motion_after = state, None
            elif kind in ("after_jog", "motion_complete"):
                self.motion_after = state

    def render(self, width: int = 78) -> str:
        lines = []
        rule = "-" * width
        simulated = (self.session or {}).get("data_source") == "simulated" or (
            (self.state or {}).get("simulated") is True)
        title = "SCORBOT LAB VIEW (read-only, sends nothing)"
        if simulated:
            title += "   *** SIMULATED ***"
        lines += [title, rule]
        if self.session:
            s = self.session
            lines.append(f"robot {s.get('robot_id')}  operator {s.get('operator')}  "
                         f"joint {s.get('joint', '-')}  delta {s.get('requested_delta_deg', '-')}")
        if self.alarms:
            lines.append("")
            for alarm in self.alarms[-3:]:
                lines.append(f"!!! {alarm}")
            lines.append("!!! If motion or motor state is uncertain, use the physical stop.")
        state = self.state
        if state is None:
            lines += ["", "Waiting for the first controller state..."]
        else:
            fault = state.get("fault")
            lines += ["",
                      f"enabled {state.get('enabled')}  homed {state.get('homed')}  "
                      f"packet {state.get('packet_index')}  from {self.state_source}",
                      f"fault   {fault if fault else 'none'}",
                      ""]
            lines.append(f"{'joint':<15}{'raw':>8}{'signed':>9}{'sign':>6}{'err':>6}"
                         f"{'d_start':>9}{'plan':>8}{'moved':>8}")
            counts = state.get("encoder_counts", {})
            signed = state.get("signed_encoder_counts") or {}
            signs = state.get("encoder_sign_bytes") or {}
            errors = state.get("controller_error_counts") or {}
            first = (self.first_state or {}).get("encoder_counts", {})
            planned = (self.plan or {}).get("motor_count_deltas", {})
            before = (self.motion_before or {}).get("encoder_counts", {})
            after = (self.motion_after or {}).get("encoder_counts", {})
            for joint in JOINTS:
                lines.append(
                    f"{joint:<15}{_cell(counts.get(joint)):>8}{_cell(signed.get(joint)):>9}"
                    f"{_cell(signs.get(joint)):>6}{_cell(errors.get(joint)):>6}"
                    f"{_delta(counts.get(joint), first.get(joint)):>9}"
                    f"{_cell(planned.get(joint, '') if self.plan else ''):>8}"
                    f"{_delta(after.get(joint), before.get(joint)):>8}")
            bits = state.get("home_switch_bits")
            if isinstance(bits, int):
                active = [name for name, bit in SWITCH_BITS if bits & bit]
                lines += ["", f"home switch bits {bits:#07b}  set: {', '.join(active) or 'none'}"
                              "  (legacy decode; polarity unverified)"]
        if self.samples:
            lines.append(f"idle samples {self.samples}")
        lines += ["", "recent events:"]
        for source, kind, detail in self.events:
            lines.append(f"  {source:<10} {kind:<22} {detail[:width - 36]}")
        age = _age(self.last_row_utc)
        lines += [rule, f"last row {age}. A quiet screen does not prove the USB link is alive."]
        return "\n".join(lines)


def _cell(value):
    return "" if value is None else str(value)


def _delta(later, earlier):
    if type(later) is not int or type(earlier) is not int:
        return ""
    try:
        return f"{signed_count_delta(later, earlier):+d}"
    except ValueError:
        return "?"


def _age(timestamp_utc):
    if not timestamp_utc:
        return "never"
    try:
        then = datetime.fromisoformat(timestamp_utc)
    except ValueError:
        return "unknown"
    return f"{(datetime.now(timezone.utc) - then).total_seconds():.1f} s ago"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("output", type=Path,
                        help="The --output JSONL of record_raw_state.py or bench_joint.py")
    parser.add_argument("--once", action="store_true",
                        help="Print one snapshot of the files as they are now, then exit")
    parser.add_argument("--interval", type=float, default=0.25)
    args = parser.parse_args(argv)
    run = args.output.resolve()
    followers = [("run", Follower(run)),
                 ("controller", Follower(run.with_name(run.stem + ".controller.jsonl")))]
    view = RunView()

    def refresh():
        rows = [(row.get("host_monotonic_ns"), source, row)
                for source, follower in followers for row in follower.poll()]
        # Rows without a monotonic stamp (the idle session header) keep file order first.
        rows.sort(key=lambda item: item[0] if type(item[0]) is int else -1)
        for _stamp, source, row in rows:
            view.add(row, source)

    if args.once:
        refresh()
        print(view.render())
        return 0
    if sys.platform == "win32":
        os.system("")  # Enables ANSI escape handling in the classic Windows console.
    try:
        while True:
            refresh()
            sys.stdout.write("\x1b[H\x1b[2J" + view.render() + "\n\n(Ctrl-C closes this view only)\n")
            sys.stdout.flush()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
