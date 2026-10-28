"""Read a motors-off recording of the arm moved by hand: direction, travel and where each switch trips.

Offline only: it reads a JSONL file written earlier by ``examples/record_raw_state.py`` and never opens
USB. The recording is made with the arm unpowered (Control Off, MOTORS LED off) and one joint pushed by
hand through its range while the recorder runs, as the ER-4u manual's own troubleshooting does ("the
encoder reading should rise for rotation in one direction and fall for rotation in the opposite
direction", docs/manual/SCORBOT_Manual_Verification.md).

  python scripts/hand_sweep_report.py sweep.jsonl [--angles shoulder=10:20]

Per joint it prints the start-relative minimum, maximum and end counts and the largest step between
samples. For every home-switch bit it prints each stretch where the bit was set, as **two intervals**:
the switch turned on somewhere between the last "off" sample and the first "on" sample, and off
somewhere between the last "on" and the first "off". The recorder samples at 10 Hz at most, so the
intervals are as wide as the hand moved in 100 ms; move slowly. ``--angles joint=start:end`` takes the
two angles read from a phone level at the first and last sample and prints counts per degree (its sign
is the direction of the counts).

What it cannot tell you: a hand sweep says where the switches are in counts and which way the counts
go, not what the vendor's home offsets do. Switch polarity (a set bit is read as pressed) is the
vendor DLL's, unverified on the arm: press a switch by hand with a screwdriver and see the bit change.
"""

import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path
import sys

try:
    from scorbot.calibration import signed_count_delta
    from scorbot.state import HOME_SWITCH_BITS, JOINTS
except ImportError:  # Run as a script from scripts/.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scorbot.calibration import signed_count_delta
    from scorbot.state import HOME_SWITCH_BITS, JOINTS

MIN_SAMPLES = 2
JUMP_COUNTS = 5000        # no hand moves a joint this far in one sample; the step is skipped and reported
COARSE_COUNTS = 50        # a larger step between samples makes each switch edge this uncertain
WRIST = ("wrist_motor_1", "wrist_motor_2")
ROLL_AXIS = "wrist_roll_axis"
"""The name of the synthetic roll coordinate, (wrist motor 1 + wrist motor 2) / 2, in the band list."""
ROLL_MIN_ON_COUNTS = 800
"""The vendor home zeroes the wrist roll 690 counts past its switch edge and must find the switch again from
there, so the roll switch has to stay on at least this far (the offset plus the search's margin). Found by
running the port on the arm model with narrower switches: ``tests/test_vendor_home_geometry.py``."""
SWITCH_JOINTS = {"base": ("base",), "shoulder": ("shoulder",), "elbow": ("elbow",),
                 "wrist_pitch": WRIST, "wrist_roll": WRIST}


@dataclass(frozen=True)
class JointSweep:
    min: int
    max: int
    end: int
    max_step: int
    counts_per_degree: "float | None" = None


@dataclass(frozen=True)
class Band:
    switch: str
    joint: str
    on_edge: "tuple | None"    # (count before, count after) the bit turned on; None if on from the start
    off_edge: "tuple | None"   # (count before, count after) it turned off; None if still on at the end
    transitions: int
    samples: int


@dataclass
class Report:
    joints: dict
    bands: list
    samples: int
    warnings: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def read_rows(path):
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _runs(flags):
    """``(first, last)`` index of each stretch of true values."""
    runs, start = [], None
    for i, on in enumerate(flags):
        if on and start is None:
            start = i
        if not on and start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, len(flags) - 1))
    return runs


def _roll_notes(bands):
    """What the wrist roll switch's width means for the vendor home (a prediction from the arm model).

    The width is taken on the roll coordinate, the mean of the two wrist motors (``ROLL_AXIS``): a pure roll
    moves both together, and pitch moves them apart."""
    notes = []
    for band in bands:
        if band.switch != "wrist_roll" or band.joint != ROLL_AXIS:
            continue
        if band.on_edge is None or band.off_edge is None:
            notes.append("wrist roll switch: the sweep did not cross both edges of it, so its width is unknown; "
                         "sweep the roll further in one direction")
            continue
        lower = abs(band.off_edge[0] - band.on_edge[1])      # first sample on to last sample on: at least this wide
        upper = abs(band.off_edge[1] - band.on_edge[0])      # last sample off before to first off after: at most this
        need = f"the vendor home needs it to stay on at least about {ROLL_MIN_ON_COUNTS} counts"
        if upper < ROLL_MIN_ON_COUNTS:
            notes.append(f"wrist roll switch stayed on for at most {upper} counts, narrower than that: {need}. "
                         "The home pose is zeroed 690 counts past the switch edge; a narrower switch leaves it off "
                         "the switch, the main pass cannot find it again and the home faults on the travel bound "
                         "(a safe stop). Tell the supervisor before running the vendor home")
        elif lower >= ROLL_MIN_ON_COUNTS:
            notes.append(f"wrist roll switch stayed on for at least {lower} counts: wide enough ({need})")
        else:
            notes.append(f"wrist roll switch stayed on for between {lower} and {upper} counts; {need}: "
                         "sweep it again more slowly to narrow the range")
    return notes


def _recorded_states(rows, angles):
    """The sample states of a recording, refusing one that cannot be a real hand sweep."""
    states = [r["state"] for r in rows if r.get("type") == "sample" and isinstance(r.get("state"), dict)]
    if any(r.get("data_source") == "simulated" for r in rows) or any(s.get("simulated") for s in states):
        raise ValueError("this recording is simulated; a hand sweep needs the real arm")
    if len(states) < MIN_SAMPLES:
        raise ValueError(f"need at least {MIN_SAMPLES} samples, found {len(states)}")
    for joint in (angles or {}):
        if joint not in JOINTS:
            raise ValueError(f"unknown joint {joint!r}; use one of {', '.join(JOINTS)}")
    return states


def _unwrap_counts(states):
    """Each joint's counts relative to the first sample, the largest step per joint and the jumps skipped."""
    relative = {j: [0] for j in JOINTS}
    steps = {j: 0 for j in JOINTS}
    jumps = 0
    for previous, current in zip(states, states[1:]):
        for joint in JOINTS:
            delta = signed_count_delta(current["encoder_counts"][joint], previous["encoder_counts"][joint])
            if abs(delta) > JUMP_COUNTS:
                jumps += 1
                delta = 0
            steps[joint] = max(steps[joint], abs(delta))
            relative[joint].append(relative[joint][-1] + delta)
    return relative, steps, jumps


def _recording_warnings(states, steps, jumps):
    warnings = []
    if jumps:
        warnings.append(f"{jumps} jump(s) over {JUMP_COUNTS} counts between samples were skipped: "
                        "a sample was lost or a count glitched; read the neighbours")
    if any(s.get("enabled") for s in states):
        warnings.append("the motors were enabled during part of this recording: it is not a hand sweep "
                        "and moving the arm by hand with motors on fights the servo")
    coarse = max(steps.values())
    if coarse > COARSE_COUNTS:
        warnings.append(f"coarse resolution: the largest step between samples was {coarse} counts, so each "
                        "switch edge is only known to within that; move more slowly")

    known = sum(HOME_SWITCH_BITS.values())
    unknown = 0
    for s in states:
        unknown |= int(s.get("home_switch_bits", 0)) & ~known
    if unknown:
        warnings.append(f"unexpected switch bits {unknown:#x} were set; do not home until they are explained")
    return warnings


def _joint_sweeps(relative, steps, angles):
    joints = {}
    for joint in JOINTS:
        end = relative[joint][-1]
        cpd = None
        if angles and joint in angles:
            first, last = angles[joint]
            if last == first:
                raise ValueError(f"{joint}: the two angles must differ")
            cpd = end / (last - first)
        joints[joint] = JointSweep(min(relative[joint]), max(relative[joint]), end, steps[joint], cpd)
    return joints


def _switch_bands(states, relative):
    """Where each home switch turned on and off, against each joint it can be read on (roll axis included)."""
    relative = {**relative, ROLL_AXIS: [(a + b) / 2 for a, b in zip(relative["wrist_motor_1"], relative["wrist_motor_2"])]}
    bands = []
    last_index = len(states) - 1
    for name, bit in HOME_SWITCH_BITS.items():
        flags = [bool(int(s.get("home_switch_bits", 0)) & bit) for s in states]
        for first, last in _runs(flags):
            for joint in SWITCH_JOINTS[name] + ((ROLL_AXIS,) if name == "wrist_roll" else ()):
                rel = relative[joint]
                on_edge = None if first == 0 else (rel[first - 1], rel[first])
                off_edge = None if last == last_index else (rel[last], rel[last + 1])
                bands.append(Band(name, joint, on_edge, off_edge,
                                  (first > 0) + (last < last_index), last - first + 1))
    return bands


def _roll_motors_note(bands, relative):
    """A note when the wrist motors ended far apart, so the recorded roll was not a pure roll."""
    if any(b.switch == "wrist_roll" for b in bands):
        end_1, end_2 = relative["wrist_motor_1"][-1], relative["wrist_motor_2"][-1]
        if abs(end_1 - end_2) > 0.2 * max(abs(end_1), abs(end_2), 1):
            return ("the two wrist motors did not move together (motor 1 ended at "
                    f"{end_1}, motor 2 at {end_2}): this was not a pure roll, so the roll width above may be wrong")
    return None


def analyse(rows, *, angles=None):
    states = _recorded_states(rows, angles)
    relative, steps, jumps = _unwrap_counts(states)
    warnings = _recording_warnings(states, steps, jumps)
    joints = _joint_sweeps(relative, steps, angles)
    bands = _switch_bands(states, relative)
    notes = _roll_notes(bands)
    motors_note = _roll_motors_note(bands, relative)
    if motors_note:
        notes.append(motors_note)
    return Report(joints, bands, len(states), warnings, notes)


def _edge(edge):
    return "from the start" if edge is None else f"between {edge[0]} and {edge[1]}"


def format_report(report):
    lines = [f"{report.samples} samples. Counts are relative to the first sample (unwrapped across 0/65535).", ""]
    lines.append(f"{'joint':<14}{'min':>8}{'max':>8}{'end':>8}{'max step':>10}  counts/degree")
    for joint, s in report.joints.items():
        cpd = "" if s.counts_per_degree is None else f"{s.counts_per_degree:.2f}"
        lines.append(f"{joint:<14}{s.min:>8}{s.max:>8}{s.end:>8}{s.max_step:>10}  {cpd}")
    lines.append("")
    if not report.bands:
        lines.append("No home switch changed during the recording.")
    for b in report.bands:
        lines.append(f"{b.switch} switch (against {b.joint}), {b.samples} samples on: turned on "
                     f"{_edge(b.on_edge)}; turned off "
                     f"{'still on at the end' if b.off_edge is None else _edge(b.off_edge)}.")
    if report.notes:
        lines.append("")
        lines.extend(f"NOTE: {n}" for n in report.notes)
    if report.warnings:
        lines.append("")
        lines.extend(f"WARNING: {w}" for w in report.warnings)
    return "\n".join(lines)


def _angles(items):
    out = {}
    for item in items or ():
        try:
            joint, pair = item.split("=")
            first, last = (float(x) for x in pair.split(":"))
        except ValueError as exc:
            raise ValueError(f"bad --angles {item!r}; use joint=start:end, e.g. shoulder=10:20") from exc
        out[joint] = (first, last)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("recording", help="JSONL from examples/record_raw_state.py (motors off, joint moved by hand)")
    parser.add_argument("--angles", action="append", metavar="JOINT=START:END",
                        help="phone-level angles at the first and last sample (repeatable)")
    args = parser.parse_args(argv)
    try:
        report = analyse(read_rows(args.recording), angles=_angles(args.angles))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(format_report(report))
    return 1 if report.warnings else 0


if __name__ == "__main__":
    sys.exit(main())
