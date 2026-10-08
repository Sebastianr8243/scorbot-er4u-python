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
from pathlib import Path

from scorbot.calibration import signed_count_delta
from scorbot.state import HOME_SWITCH_BITS, JOINTS

ALARM_EVENTS = {"command_timeout", "command_error", "command_interrupted", "feedback_fault",
                "sync_worker_crashed", "command_worker_crashed", "home_refused",
                "home_failed", "following_error", "calibration_fault", "connect_failed",
                "session_failed", "disable_skipped_worker_crashed",
                "led_gate_failed", "jog_refused", "jog_failed", "disable_failed",
                "counts_drift", "stop_settle_failed", "stream_fault", "stream_failed", "gripper_fault",
                "pre_home_failed", "park_failed"}
# Every SDK command logs these around it; they bury the events worth reading.
QUIET_EVENTS = {"command_start", "command_complete"}
KNOWN_SWITCH_MASK = sum(HOME_SWITCH_BITS.values())


class Follower:
    """Yield complete JSON lines appended to a file that may not exist yet."""

    def __init__(self, path: Path):
        self.path = path
        self._offset = 0
        self._partial = b""
        self.modified_epoch = None

    def poll(self) -> list[dict]:
        try:
            self.modified_epoch = self.path.stat().st_mtime
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
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue  # Blank or corrupt; review_lab_logs.py reports corruption.
            if isinstance(row, dict):
                rows.append(row)
        return rows


class RunView:
    """Fold rows from both files into what the operator needs on one screen."""

    def __init__(self):
        self.session = None
        self.first_counts = {}
        self.state = None
        self.state_source = None
        self.state_index = -1
        self.plan_deltas = None
        self.before_counts = {}
        self.after_counts = {}
        self.events = []
        self.alarms = []
        self.samples = 0
        self.led = None

    def add(self, row: dict, source: str):
        kind = row.get("type") or row.get("event")
        if not kind:
            return
        if kind == "session":
            self.session = row
        if kind == "led_observation":
            self.led = row
        elif kind == "led_mismatch":
            self.alarms.append(f"LED mismatch {row.get('step')}: "
                               f"{row.get('message') or row.get('led')}")
        if kind == "sample":
            self.samples += 1
        elif kind not in QUIET_EVENTS:
            detail = (row.get("error") or row.get("text") or row.get("message")
                      or row.get("step") or "")
            if kind == "after_stream":
                detail = "PASSED" if (row.get("result") or {}).get("passed") else "FAILED"
            self.events.append((source, kind, str(detail)))
            del self.events[:-8]
        if kind in ALARM_EVENTS:
            error = row.get("error")
            self.alarms.append(f"{kind}: {error}" if error else kind)
        if kind == "motion_preview" and isinstance(row.get("plan"), dict):
            self.plan_deltas = row["plan"].get("motor_count_deltas", {})
        if kind == "stream_plan":        # a stream trial: one motor out to this and back
            self.plan_deltas = {row.get("motor"): row.get("target_counts_from_home")}
        if kind == "after_stream" and not (row.get("result") or {}).get("passed"):
            problems = (row.get("result") or {}).get("problems") or ["no verdict recorded"]
            self.alarms.append("stream trial FAILED: " + "; ".join(problems))
        state = row.get("state")
        if not (isinstance(state, dict) and isinstance(state.get("encoder_counts"), dict)):
            return
        counts = state["encoder_counts"]
        if not self.first_counts:
            self.first_counts = counts
        # Both files are polled in turn, so a newer state can arrive before an older
        # one. The SDK reads a strictly newer packet on every call, so the packet
        # index orders states (e.g. keeps "disabled" over an earlier "enabled").
        index = state.get("packet_index")
        if type(index) is not int or index >= self.state_index:
            self.state, self.state_source = state, f"{source}:{kind}"
            if type(index) is int:
                self.state_index = index
        if kind in ("before_jog", "motion_start", "before_stream", "before_gripper",
                    "gripper_start"):
            self.before_counts, self.after_counts = counts, {}
        elif kind in ("after_jog", "motion_complete", "after_stream", "after_gripper",
                      "gripper_complete"):
            self.after_counts = counts

    def render(self, width: int = 78, last_write_epoch: float | None = None) -> str:
        rule = "-" * width
        simulated = (self.session or {}).get("data_source") == "simulated" or (
            (self.state or {}).get("simulated") is True)
        lines = ["SCORBOT LAB VIEW (read-only, sends nothing)"
                 + ("   *** SIMULATED ***" if simulated else ""), rule]
        if self.session:
            s = self.session
            lines.append(f"robot {s.get('robot_id')}  operator {s.get('operator')}  "
                         f"joint {s.get('joint') or s.get('motor') or '-'}  "
                         f"delta {s.get('requested_delta_deg', '-')}")
        if self.alarms:
            lines += ["", *(f"!!! {alarm}" for alarm in self.alarms[-3:]),
                      "!!! If motion or motor state is uncertain, use the physical stop."]
        if self.led:
            led = self.led
            lines += ["", f"LEDs (operator, {str(led.get('step')).replace('_', ' ')}): "
                          f"MOTORS {led.get('motors_led')}  POWER {led.get('power_led')}"]
        if self.state is None:
            lines += ["", "Waiting for the first controller state..."]
        else:
            lines += self._state_lines(self.state)
        if self.samples:
            lines.append(f"idle samples {self.samples}")
        lines += ["", "recent events:"]
        lines += [f"  {source:<10} {kind:<22} {detail[:width - 36]}"
                  for source, kind, detail in self.events]
        age = ("never" if last_write_epoch is None
               else f"{max(0.0, time.time() - last_write_epoch):.1f} s ago")
        lines += [rule, f"last log write {age}. A quiet screen does not prove the USB link "
                        "is alive."]
        return "\n".join(lines)

    def _state_lines(self, state: dict) -> list[str]:
        fault = state.get("fault")
        lines = ["",
                 f"enabled {state.get('enabled')}  homed {state.get('homed')}  "
                 f"packet {state.get('packet_index')}  from {self.state_source}",
                 f"fault   {fault if fault else 'none'}",
                 "",
                 f"{'joint':<15}{'raw':>8}{'signed':>9}{'sign':>6}{'err':>6}"
                 f"{'d_start':>9}{'plan':>8}{'moved':>8}"]
        counts = state["encoder_counts"]
        signed = state.get("signed_encoder_counts") or {}
        signs = state.get("encoder_sign_bytes") or {}
        errors = state.get("controller_error_counts") or {}
        planned = self.plan_deltas or {}
        for joint in JOINTS:
            lines.append(
                f"{joint:<15}{_cell(counts.get(joint)):>8}{_cell(signed.get(joint)):>9}"
                f"{_cell(signs.get(joint)):>6}{_cell(errors.get(joint)):>6}"
                f"{_delta(counts.get(joint), self.first_counts.get(joint)):>9}"
                f"{_cell(planned.get(joint)):>8}"
                f"{_delta(self.after_counts.get(joint), self.before_counts.get(joint)):>8}")
        bits = state.get("home_switch_bits")
        if isinstance(bits, int):
            active = [name for name, bit in HOME_SWITCH_BITS.items() if bits & bit]
            lines += ["", f"home switch bits {bits:#07b}  set: {', '.join(active) or 'none'}"
                          "  (legacy decode; polarity unverified)"]
            if bits & ~KNOWN_SWITCH_MASK:
                lines.append(f"!!! byte 5 = {bits} has a bit >= 32; legacy homing misreads the "
                             "switches. Do not home.")
        return lines


def _cell(value):
    return "" if value is None else str(value)


def _delta(later, earlier):
    if type(later) is not int or type(earlier) is not int:
        return ""
    try:
        return f"{signed_count_delta(later, earlier):+d}"
    except ValueError:
        return "?"


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
        # Display events in time order; unstamped rows (the idle header) go first.
        rows.sort(key=lambda item: item[0] if type(item[0]) is int else -1)
        for _stamp, source, row in rows:
            view.add(row, source)

    def screen():
        stamps = [f.modified_epoch for _, f in followers if f.modified_epoch is not None]
        return view.render(last_write_epoch=max(stamps, default=None))

    if args.once:
        refresh()
        print(screen())
        return 0
    if sys.platform == "win32":
        os.system("")  # Enables ANSI escape handling in the classic Windows console.
    try:
        while True:
            refresh()
            sys.stdout.write("\x1b[H\x1b[2J" + screen() + "\n\n(Ctrl-C closes this view only)\n")
            sys.stdout.flush()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
