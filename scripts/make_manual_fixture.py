"""Reduce two fake-controller recordings of the lab DLL's joint jog to a small golden fixture.

    python scripts/make_manual_fixture.py REC/run2/pipe-out.jsonl REC/run3/pipe-out.jsonl \
        tests/fixtures/dll_lab_manual.json

The recordings are the JSONL ``tools/usbc_fake/pipe_server.py`` wrote while the lab ``USBC.dll`` ran ``Home('A')``
and then ``EnterManual(0)``, ``MoveManual(axis, speed)`` and ``CloseManual`` (``docs/protocol/LEDGER.md``, the
velocity-jog row), with a 10 ms reply delay after the Home so the DLL's 0.3 s call spacing met about 27 messages:

* run 2: base speed -20 re-sent every 0.3 s, then -40 twice and +20 twice, then ``CloseManual``; base -20 once
  and no call for 3 s, then ``CloseManual``; base +20 twice, then ``Stop('&')`` and ``CloseManual``.
* run 3: one ``MoveManual`` per jog for 0.8 s, then ``CloseManual``: base -5, -10, -20, -50, -100, +50,
  shoulder -20, elbow +20 (and wrist jogs, not kept: Stack 2 does not jog the wrist).

Each jog runs from its ``47`` to its last ``42`` (or ``42 FF`` for the ``Stop``). The fixture keeps, per jog:

* ``commands``  the non-``0D`` messages as ``[index, bytes 1-11 as hex]``, index counted from the ``47``.
* ``steps``     the change of the jogged slot's setpoint (signed, controller counts) at every message.
* ``other_slots_move``  whether any other arm slot changed (it never does).

Timestamps are not kept (the DLL's planner advances once per reply). Standard library only. The vendor binaries
and the raw recordings are never committed; the fixture holds message fields the DLL wrote to a fake pipe.
"""

import json
import struct
import sys

CLEAR, NOP, MOTORS, SET_POSITION = 0x47, 0x0D, 0x42, 0x48
SOURCE = ("lab USBC.dll (SHA-256 starts 6118465d) on a fake controller (pipe_server.py, 10 ms reply delay after "
          "Home), Home('A') then EnterManual(0), MoveManual, CloseManual, 2026-11-02")
PLAN = {    # recording -> what each jog was, in order (the DLL's own call log, kept outside the repo)
    "run2": [("renewed, then -40 and +20 re-sent, closed", 0, -20), ("one call, 3 s, closed", 0, -20),
             ("Stop('&') after 0.6 s", 0, 20)],
    "run3": [("closed after 0.8 s", 0, -5), ("closed after 0.8 s", 0, -10), ("closed after 0.8 s", 0, -20),
             ("closed after 0.8 s", 0, -50), ("closed after 0.8 s", 0, -100), ("closed after 0.8 s", 0, 50),
             ("closed after 0.8 s", 1, -20), ("closed after 0.8 s", 2, 20)],
}


def jogs(path):
    """Every jog in a recording: a ``47`` followed by a ``0D``, up to the next ``42``, that moves a slot and sends no ``48``."""
    with open(path, encoding="utf-8") as handle:
        out = [bytes.fromhex(row["out"]) for row in map(json.loads, handle) if "out" in row]
    found, k = [], 0
    while k < len(out) - 1:
        if out[k][4] == CLEAR and out[k + 1][4] == NOP:
            end = next(j for j in range(k, len(out)) if out[j][4] == MOTORS)
            moved = len({m[12:32] for m in out[k:end + 1]}) > 1
            if moved and all(m[4] != SET_POSITION for m in out[k:end + 1]):     # the Home's own moves zero counts
                found.append(out[k:end + 1])
            k = end
        k += 1
    return found


def describe(jog, label, axis, speed):
    slots = [struct.unpack_from("<5i", m, 12) for m in jog]
    return {"label": label, "axis": axis, "speed": speed,
            "commands": [[i, m[1:12].hex()] for i, m in enumerate(jog) if m[4] != NOP],
            "steps": [b[axis] - a[axis] for a, b in zip(slots, slots[1:])],
            "other_slots_move": any(b[n] != a[n] for a, b in zip(slots, slots[1:]) for n in range(5) if n != axis)}


def main(argv):
    run2, run3, target = argv[1], argv[2], argv[3]
    found = {"run2": jogs(run2), "run3": jogs(run3)[:len(PLAN["run3"])]}
    fixture = {"source": SOURCE, "fields": "per jog: label, DLL axis, MoveManual speed, non-0D commands with their "
                                           "index from the 47, jogged slot setpoint change per message",
               "jogs": [describe(jog, *plan) for name in ("run2", "run3")
                        for jog, plan in zip(found[name], PLAN[name], strict=True)]}
    with open(target, "x", encoding="utf-8", newline="\n") as handle:
        json.dump(fixture, handle, separators=(",", ":"))
        handle.write("\n")
    print([(j["axis"], j["speed"], len(j["steps"]), j["commands"][-1]) for j in fixture["jogs"]], "->", target)


if __name__ == "__main__":
    main(sys.argv)
