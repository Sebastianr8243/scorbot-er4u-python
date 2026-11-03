"""Reduce fake-controller recordings of the lab DLL's ``MoveJoint`` calls to ``tests/fixtures/dll_lab_move_joint.json``.

    python scripts/make_move_joint_fixture.py A.jsonl C.jsonl B.jsonl tests/fixtures/dll_lab_move_joint.json

The recordings are what ``tools/usbc_fake/pipe_server.py`` wrote while the lab ``USBC.dll`` (SHA-256 starts
6118465d) ran ``Home('A')``, ``DefineVector('A', "MJ", 4)`` and then, per move, ``Speed`` or ``Time`` if listed,
``SetJoints("MJ", 1, target)`` and ``MoveJoint("MJ", 1)`` (``PLANS`` below; desk, 2026-11-02). A MoveJoint the DLL
refused moved nothing and is not listed. For each move the fixture keeps the setpoint slots 0-4 of every message
from the first change through the last one, repeats included, and ``total_ms``: the one duration in a window around
the message count for which ``vendor_move.setpoints`` reproduces them (the builder fails if none or two fit). The
messages before the first change hold the start counts, so they cannot be told apart from the hold before the move.
Slots 5-7 were 0 in every message (checked). Standard library and ``scorbot`` only; the recordings stay outside the
repo.
"""

import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scorbot import vendor_move  # noqa: E402

HOME = [0, -120278, 95021, 88805, 0]       # GetCurrentPosition joints after Home('A') on the fake, millidegrees
HOLD = 50                                  # messages with no change that separate two moves
SOURCE = ("lab USBC.dll (SHA-256 starts 6118465d) on a fake controller (pipe_server.py), Home('A'), "
          "DefineVector, then SetJoints and MoveJoint per move, 2026-11-02")


def _with(index, delta):
    joints = list(HOME)
    for k, d in zip(index, delta):
        joints[k] += d
    return joints


# (label, target millidegrees, speed percent or None, time ms or None), in call order. Speed stays set once given.
PLANS = {
    "a": [("base +10", _with([0], [10000]), None, None), ("base +40", _with([0], [40000]), None, None),
          ("base back", HOME, None, None), ("base +30", _with([0], [30000]), 100, None),
          ("base +2", _with([0], [2000]), 100, None), ("base back", HOME, 10, None)],
    "c": [m for p in (1, 5, 20, 50, 80)
          for m in (("base +30", _with([0], [30000]), p, None), ("base back", HOME, p, None))]
         + [("shoulder +10", _with([1], [10000]), 50, None), ("elbow -10", _with([2], [-10000]), 50, None),
            ("five joints", _with(range(5), [20000, 5000, -5000, 10000, 20000]), 50, None),
            ("back", HOME, 50, None), ("roll +90", _with([4], [90000]), 50, None), ("back", HOME, 50, None)],
    "b": [("pitch +20", _with([3], [20000]), None, None), ("roll +30", _with([4], [30000]), None, None),
          ("back", HOME, None, None), ("base +20", _with([0], [20000]), None, 3000)],
}


def setpoint_messages(path):
    """Slots 0-7 of every ``0D`` message after the Home's last non-``0D`` command."""
    packets = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                if "out" in row:
                    packets.append(bytes.fromhex(row["out"]))
    first = _first_move(packets)
    last_command = max(i for i in range(first) if packets[i][4] != 0x0D)
    return [[struct.unpack_from("<i", p, 12 + 4 * k)[0] for k in range(8)] for p in packets[last_command + 1:]
            if p[4] == 0x0D]


def _first_move(packets):
    """Index of the first ``0D`` whose setpoints change after the Home's last ``42 20 00`` (gripper motor off)."""
    end = max(i for i, p in enumerate(packets) if p[4:6] == bytes([0x42, 0x20]) and i < len(packets) // 2)
    previous = None
    for i in range(end + 1, len(packets)):
        if packets[i][4] == 0x0D:
            if previous is not None and packets[i][12:44] != previous:
                return i
            previous = packets[i][12:44]
    raise ValueError("no move after the Home")


def split_moves(messages):
    """Runs of changing setpoints, each from its first change through its last, repeats inside kept."""
    moves, current, still = [], None, 0
    for before, now in zip(messages, messages[1:]):
        if now == before:
            still += 1
            continue
        if current is None or still > HOLD:
            current = []
            moves.append(current)
        else:
            current.extend([before] * still)
        current.append(now)
        still = 0
    return moves


def fit_ms(start, target, recorded):
    """The one duration whose setpoints, after the lead and before the hold, are ``recorded``."""
    hits = []
    for total_ms in range(max(1, 24 * (len(recorded) - 2)), 24 * (len(recorded) + 12)):
        sent = vendor_move.setpoints(start, target, total_ms)
        lead = next(k for k, s in enumerate(sent) if s != sent[0])
        body = sent[lead:]
        while len(body) > 1 and body[-1] == body[-2]:
            body.pop()
        if body == recorded:
            hits.append((total_ms, lead, len(sent)))
    if len(hits) != 1:
        raise ValueError(f"{len(hits)} durations fit a move of {len(recorded)} messages")
    return hits[0]


def main(paths, out):
    calls = []
    for plan, path in zip("acb", paths):
        moves = split_moves(setpoint_messages(path))
        if len(moves) != len(PLANS[plan]):
            raise ValueError(f"{path}: {len(moves)} moves, plan {plan} has {len(PLANS[plan])}")
        start, start_name = vendor_move.home_joints(), "home"
        for (label, target_mdeg, speed, time_ms), messages in zip(PLANS[plan], moves):
            if any(any(m[5:]) for m in messages):
                raise ValueError("a setpoint slot 5-7 was not 0")
            recorded = [tuple(m[:5]) for m in messages]
            target = vendor_move.joints_from_millidegrees(target_mdeg)
            total_ms, lead, count = fit_ms(start, target, recorded)
            calls.append({"recording": plan, "label": label, "start": start_name, "target_millideg": target_mdeg,
                          "speed_percent": speed, "time_ms": time_ms, "total_ms": total_ms,
                          "accel_bound_ms": vendor_move.accel_bound_ms(start, target),
                          "lead": lead, "messages": count, "setpoints": recorded})
            start, start_name = target, "previous"
    fixture = {"source": SOURCE,
               "fields": "per move: slots 0-4 of every message from the first change through the last (counts); "
                         "start home = the DLL's joints at zero counts, previous = the last target; lead and messages = "
                         "the model's messages before the first change and its whole length, inferred, not "
                         "recorded; total_ms = the one duration that reproduces the recorded setpoints",
               "moves": calls}
    Path(out).write_text(json.dumps(fixture, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{len(calls)} moves to {out}")


if __name__ == "__main__":
    main(sys.argv[1:4], sys.argv[4])
