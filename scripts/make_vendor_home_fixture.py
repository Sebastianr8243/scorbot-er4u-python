"""Reduce a fake-controller recording of the vendor's Home to the golden fixture the tests compare against.

    python scripts/make_vendor_home_fixture.py rehearsal/vendor-trace-04.jsonl tests/fixtures/vendor_home_trace04.json

The recording is the JSONL a named-pipe fake controller wrote while Intelitek's USBC.dll ran its
``Home('A')`` (see docs/archive/specs/2026-10-08-vendor-dll-home-plan.md). Each row has ``n`` (message
number) and ``out`` (the 64-byte OUT packet as hex). The fixture keeps only the commands, with the
``0D`` setpoint stream dropped and consecutive repeats collapsed:

* ``steps``  every non-``0D`` command from the first mode message of the home to the sweep's last
  ``73 20 / 42 20`` pair: ``[cmd, mask, arg16, arg32]``.
* ``recipe`` the same list restricted to non-zero ``4D`` drives, ``48``, ``4F`` and ``4C``: the part
  of the sequence that does not depend on how many cycles a wait took.

Standard library only. The vendor binaries and the raw recording are never committed; the fixture
holds command bytes and numbers, not Intelitek code.
"""

import json
import sys

CMD_NOP, CMD_MODE, CMD_MOVE, CMD_SETPOS, CMD_SLAVE = 0x0D, 0x4F, 0x4D, 0x48, 0x4C
MODE_T = 0x54


def load_commands(path):
    """Non-0D commands in message order as ``(n, cmd, mask, arg16, arg32)``."""
    commands = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line) if line.strip() else {}
            if "out" not in row:
                continue
            data = bytes.fromhex(row["out"])
            if data[4] == CMD_NOP:
                continue
            commands.append((row["n"], data[4], data[5], int.from_bytes(data[6:8], "little"),
                             int.from_bytes(data[8:12], "little", signed=True)))
    return commands


def collapse(items):
    out, last = [], None
    for item in items:
        if item != last:
            out.append(item)
        last = item
    return out


def reduce_recording(commands):
    """Cut the home out of the whole session and build the two lists."""
    first = next(i for i, c in enumerate(commands)
                 if c[1] == CMD_MODE and c[3] == MODE_T and c[2] == 0x02 and c[0] > 100)
    # The home ends with the sweep's gripper-off pair; Control off (47, 73 3F ...) follows it.
    stop = next(i for i, c in enumerate(commands) if i > first and c[1] == 0x47
                and commands[i + 1][1:4] == (0x73, 0x3F, 0))
    steps = collapse([c[1:] for c in commands[first:stop]])
    while steps and steps[-1][0] not in (0x42,):        # keep through the last 42 20 of the sweep
        steps.pop()
    recipe = [s for s in steps if (s[0] == CMD_MOVE and s[3] != 0) or s[0] in (CMD_SETPOS, CMD_MODE, CMD_SLAVE)]
    return steps, collapse(recipe)


def main(argv):
    source, target = argv[1], argv[2]
    steps, recipe = reduce_recording(load_commands(source))
    with open(target, "w", encoding="utf-8", newline="\n") as handle:
        json.dump({"source": "vendor USBC.dll Home('A') against a fake controller on a named pipe, 2026-10-08",
                   "fields": ["cmd", "mask", "arg16", "arg32"],
                   "steps": steps, "recipe": recipe}, handle, indent=0, separators=(",", ":"))
        handle.write("\n")
    print(f"{len(steps)} steps, {len(recipe)} recipe steps -> {target}")


if __name__ == "__main__":
    main(sys.argv)
