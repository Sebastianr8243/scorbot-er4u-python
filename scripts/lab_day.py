"""Stack 2 lab day: steps T, S, P (and X) and G of docs/lab/LAB_CHECKLIST.md section 3b, in order, with their pass lines.

    python scripts/lab_day.py --simulate                      # rehearsal: no arm, no USB
    python scripts/lab_day.py --online --transport pyusb      # on WinUSB (Zadig)
    python scripts/lab_day.py --online --transport auto       # on Intelitek's driver
    python scripts/lab_day.py --online --from G               # resume at a step after a stop you resolved
    python scripts/lab_day.py --online --x                    # P also judges X (Stack 1 left the arm off home)

Step H (the vendor Home, Stack 1) comes first and is run by hand: this script never loads the vendor DLL.
Every step that connects to the arm waits for a typed GO; G's own prompts (EMPTY, CLEAR, HOME) still follow.
It stops at the first FAIL. Every log goes in one new folder, ``logs/lab-<UTC time>/``.
Ctrl+C is not an emergency stop; the red physical stop is.

The steps reuse the existing tools, each in its own process (``read_timeout_check.py``,
``scorbot.transport.probe``, ``bring_up.py``); S is read here, connect plus state reads only.
"""

import argparse
import ast
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
STEPS = ("T", "S", "P", "G")      # T first: the checklist wants it before any Stack 2 connect
SWITCH_BITS = {1: "base", 2: "shoulder", 4: "elbow", 8: "pitch", 16: "roll"}
SWITCH_SECONDS = 180
X_COUNTS = 100          # "some joint still reads hundreds of counts from 0"
GRIPPER = 5


def run_tool(argv, *, capture):
    """Run one tool from the repo root; returns ``(exit code, its output or '')``. Captured output is echoed live."""
    if not capture:
        return subprocess.run(argv, cwd=ROOT).returncode, ""
    lines = []
    with subprocess.Popen(argv, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as proc:
        for line in proc.stdout:
            print(line, end="")
            lines.append(line)
    return proc.returncode, "".join(lines)


# ---- pass lines (pure) ----

def judge_probe(output, reads):
    """Step P's pass line on the probe's printed ``timing`` line; returns a list of problems (empty is a pass)."""
    line = next((ln for ln in output.splitlines() if " timing {" in ln), None)
    if line is None:
        return ["the probe printed no timing line"]
    timing = json.loads(line.split(" timing ", 1)[1].split("; dropped", 1)[0])
    problems = []
    if timing["replies"] < reads:
        problems.append(f"{timing['replies']} of {reads} replies")
    median = timing.get("gap_ms", {}).get("median")
    if median is None or not 14 <= median <= 25:
        problems.append(f"gap median {median} ms, wanted 14 to 25")
    lags = {k: v for k, v in timing["lag"].items()}
    if lags.get("null"):
        problems.append(f"{lags['null']} replies echoed no usable ID")
    worst = max((int(k) for k in lags if k != "null"), default=None)
    if worst is None or worst > 60:
        problems.append(f"worst echo lag {worst}, wanted at most 60")
    longest = timing["repeats"]["longest"]
    if longest > 4:
        problems.append(f"{longest} repeated replies in a row, wanted at most 4")
    return problems


def probe_counts(output):
    """The probe's ``counts {...}`` line as signed counts per joint, or ``None``."""
    line = next((ln for ln in output.splitlines() if " counts {" in ln), None)
    if line is None:
        return None
    from scorbot.calibration import signed_count_delta
    counts = ast.literal_eval(line.split(" counts ", 1)[1].split("; switches", 1)[0])
    return {joint: signed_count_delta(value, 0) for joint, value in counts.items()}


def judge_x(counts):
    """Step X: after Stack 2's handshake some joint still reads hundreds of counts from 0."""
    if counts is None:
        return ["the probe printed no counts line"]
    if any(abs(v) >= X_COUNTS for v in counts.values()):
        return []
    return [f"every count is within {X_COUNTS} of 0 ({counts}): the handshake re-zeroed the vendor's Home"]


class SwitchWatch:
    """Step S: each home switch must be seen on alone. ``see(bits)`` returns a line to print when something new shows."""

    def __init__(self):
        self.seen = set()

    def see(self, bits):
        if bits in SWITCH_BITS and bits not in self.seen:
            self.seen.add(bits)
            return f"  {SWITCH_BITS[bits]} switch seen (bit {bits}); still to press: {', '.join(self.missing()) or 'none'}"
        if bits and bits not in SWITCH_BITS:
            return f"  bits {bits:#04x}: more than one switch, or an unknown bit; press one at a time"
        return None

    def missing(self):
        return [name for bit, name in SWITCH_BITS.items() if bit not in self.seen]

    def done(self):
        return not self.missing()


def gripper_span(log_path):
    """Smallest to largest gripper count in the Home's packet trace (lab step G records it for ledger PL29)."""
    from scorbot.calibration import signed_count_delta
    from scorbot.state import JOINTS, PACKET_MIN_LENGTH, decode_state
    start, low, high = None, 0, 0
    for row in Path(log_path).read_text(encoding="utf-8").splitlines():
        row = json.loads(row)
        if row.get("event") != "motion_trace" or row.get("joint") != "vendor_home":
            continue
        for packet in row.get("packets") or []:
            data = bytes.fromhex(packet["hex"])
            if packet["direction"] != "in" or len(data) < PACKET_MIN_LENGTH:
                continue
            try:
                count = decode_state(data, connected=True, enabled=None, homed=False, fault=None).encoder_counts[JOINTS[GRIPPER]]
            except ValueError:          # a reply with a bad sign byte: skip it, the span is a note, not a check
                continue
            start = count if start is None else start
            moved = signed_count_delta(count, start)
            low, high = min(low, moved), max(high, moved)
    return None if start is None else high - low


# ---- steps ----

def step_s(args, robot_factory=None, clock=time.monotonic, pause=time.sleep):
    """Connect, then watch IN byte 5 while the operator presses each home switch by hand. Commands nothing after connect."""
    if args.simulate:
        print("  skipped when simulated: the simulator has no switches to press")
        return []
    from scorbot import Scorbot
    from scorbot.transport.probe import read_status
    print("  Motors stay off. Press each home switch with a screwdriver, one at a time, and release it.")
    watch, robot = SwitchWatch(), (robot_factory or Scorbot)(transport=args.transport)
    try:
        read_status(robot)
        deadline = clock() + SWITCH_SECONDS
        while not watch.done() and clock() < deadline:
            note = watch.see(robot.get_state().home_switch_bits)
            if note:
                print(note)
            pause(0.05)
    finally:
        robot.disconnect()
    return [] if watch.done() else [f"not seen within {SWITCH_SECONDS} s: {', '.join(watch.missing())}"]


def step_t(args, run=run_tool):
    if args.simulate or args.transport == "pyusb":
        print("  skipped: T times Intelitek's driver, so run it on that driver (before Zadig), not on WinUSB")
        return []
    code, _ = run([sys.executable, "scripts/read_timeout_check.py", "--online"], capture=True)
    return [] if code == 0 else ["read_timeout_check did not PASS (see the line above)"]


def step_p(args, folder, run=run_tool):
    reads = 200
    argv = [sys.executable, "-m", "scorbot.transport.probe", "--reads", str(reads), "--log", str(folder / "probe-reads.jsonl")]
    argv += ["--simulate", "--latency-ms", "10"] if args.simulate else ["--online", "--transport", args.transport]
    code, output = run(argv, capture=True)
    problems = [] if code == 0 else [f"the probe exited {code}"]
    problems += judge_probe(output, reads)
    if args.x:
        problems += judge_x(probe_counts(output))
    return problems


def step_g(args, folder, run=run_tool, ask=input):
    argv = [sys.executable, "scripts/bring_up.py", "--logs", str(folder / "bring-up")]
    argv += ["--simulate"] if args.simulate else ["--online", "--transport", args.transport]
    code, _ = run(argv, capture=False)
    logs = sorted((folder / "bring-up").glob("*.jsonl"))
    if logs:
        span = gripper_span(logs[-1])
        print(f"  gripper span in the Home trace: {span} counts (write it down: ledger PL29)" if span is not None
              else "  no vendor Home packet trace in the log")
    if code != 0:
        return [f"bring_up exited {code}: note the error number it printed (574 is the gripper budget; do not raise it here)"]
    if args.simulate:
        return []
    answer = ask("Check every joint against its tape mark from step H. All within about 2 degrees? Type ALIGNED: ")
    if answer.strip() != "ALIGNED":
        return ["a joint is off its tape mark (or not checked): Stack 2's zero is not the vendor's; no streaming"]
    return []


TITLES = {"S": "home switches, pressed by hand (connects; no commanded motion)",
          "T": "driver timeout: one read, no write (no motion)",
          "P": "Stack 2 probe, 200 replies (connect pulses the motors)",
          "G": "Stack 2 vendor Home and a 1 degree jog (MOVES THE ARM; jaws empty)"}
CONNECTS = {"S", "P", "G"}


def main(argv=None, *, ask=input, steps=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--simulate", action="store_true", help="rehearsal: no arm, no USB")
    mode.add_argument("--online", action="store_true", help="the real controller")
    parser.add_argument("--transport", choices=("auto", "device_file", "pyusb"), default="auto")
    parser.add_argument("--from", dest="start", choices=STEPS, default=STEPS[0], help="first step to run")
    parser.add_argument("--x", action="store_true", help="P also judges X: Stack 1 left the arm off home first")
    parser.add_argument("--logs", help="parent folder (default: logs, or rehearsal when simulated)")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)     # keep our lines in order with the tools' own output
    folder = ROOT / (args.logs or ("rehearsal" if args.simulate else "logs")) / f"lab-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}"
    folder.mkdir(parents=True, exist_ok=False)
    runners = steps or {"S": lambda: step_s(args), "T": lambda: step_t(args),
                        "P": lambda: step_p(args, folder), "G": lambda: step_g(args, folder, ask=ask)}
    label = "SIMULATED" if args.simulate else "REAL"
    print(f"{label} lab day, transport {args.transport}; logs in {folder}")
    print("Step H (the vendor Home, Stack 1) is done by hand first. The red physical stop is the only emergency stop.")
    results = []
    for name in STEPS[STEPS.index(args.start):]:
        print(f"\n=== {name}: {TITLES[name]} ===")
        if name in CONNECTS and not args.simulate:
            print("Hand on the red stop. Nobody in the arm's path. SCORBASE and the vendor tools closed.")
            if ask(f"Type GO to start step {name} (anything else stops here): ").strip() != "GO":
                print(f"Stopped by you before {name}. Nothing was started.")
                return 3
        try:
            problems = runners[name]()
        except Exception as exc:        # a tool's own refusal or a lost link: still a plain FAIL, never a traceback
            problems = [f"{type(exc).__name__}: {exc}"]
        results.append((name, problems))
        if problems:
            for problem in problems:
                print(f"FAIL {name}: {problem}")
            print(f"Stop here and write it down. Logs: {folder}. Resume after you resolve it: --from {name}")
            return 1
        print(f"PASS {name}")
    print(f"\n{label} all steps passed: {' '.join(n for n, _ in results)}. Logs: {folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
