"""Reduce a fake-controller recording of the vendor DLL's post-Home gripper calls to a small golden fixture.

    python scripts/make_gripper_fixture.py C:/Users/sebas/tools/usbc_desk/rec/2026-11-01-pl51/pipe-out.jsonl 203680 \
        tests/fixtures/dll_lab_gripper.json

The recording is the JSONL ``tools/usbc_fake/pipe_server.py`` wrote while the lab ``USBC.dll`` ran ``Home('A')``
and then ``OpenGripper``, ``CloseGripper``, ``OpenGripper``, ``CloseGripper`` (``docs/protocol/LEDGER.md`` PL51).
The second argument is the recording line where the Home ended. For each gripper call, from its ``4F`` to its
last ``42``, the fixture keeps:

* ``commands``   the non-``0D`` messages, bytes 1-11 (header and command) as hex.
* ``nop_count``  how many ``0D`` messages lay between them.
* ``gripper``    the gripper setpoint (OUT bytes 32-35, signed) of every message, ``4F`` to ``42``.
* ``arm``        every distinct setpoint tuple of slots 0-4 in those messages.

The timestamps are not kept: the fake answers in about 1 ms, and the vendor's planner advances a fixed tick per
reply (``docs/protocol/VENDOR_DLL_PROTOCOL.md`` section 11), so wall time on the fake says nothing about the
profile. Standard library only. The vendor binaries and the raw recording are never committed; the fixture holds
message bytes the DLL wrote to a fake pipe, not Intelitek code.
"""

import json
import struct
import sys

CMD_NOP, CMD_MODE, CMD_MOTORS = 0x0D, 0x4F, 0x42
GRIPPER_MASK = 0x20
SOURCE = ("lab USBC.dll (SHA-256 starts 6118465d) via usbc_server --online on a fake controller (pipe_server.py), "
          "Home('A') then OpenGripper, CloseGripper, OpenGripper, CloseGripper, 2026-11-01")


def load_messages(path, start_line):
    """The 64-byte OUT packets of a pipe_server recording from ``start_line`` (0-based) on."""
    messages = []
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle):
            if number < start_line or not line.strip():
                continue
            row = json.loads(line)
            if "out" in row:
                messages.append(bytes.fromhex(row["out"]))
    return messages


def calls(messages):
    """Split the messages into gripper calls: each runs from a ``4F 20`` to the second ``42 20`` after it."""
    found, index = [], 0
    while index < len(messages):
        m = messages[index]
        if m[4] == CMD_MODE and m[5] == GRIPPER_MASK:
            motors, end = 0, index
            for end in range(index, len(messages)):
                if messages[end][4] == CMD_MOTORS and messages[end][5] == GRIPPER_MASK:
                    motors += 1
                    if motors == 2:
                        break
            found.append(messages[index:end + 1])
            index = end + 1
        else:
            index += 1
    return found


def describe(call):
    gripper = [struct.unpack_from("<i", m, 32)[0] for m in call]
    return {"direction": "open" if gripper[-1] > gripper[0] else "close",
            "commands": [m[1:12].hex() for m in call if m[4] != CMD_NOP],
            "nop_count": sum(1 for m in call if m[4] == CMD_NOP),
            "gripper": gripper,
            "arm": sorted({struct.unpack_from("<5i", m, 12) for m in call})}


def main(argv):
    source, start_line, target = argv[1], int(argv[2]), argv[3]
    fixture = {"source": SOURCE, "fields": "per call: header and command (bytes 1-11) as hex, 0D count, "
                                           "gripper setpoint per message, distinct arm setpoints",
               "calls": [describe(call) for call in calls(load_messages(source, start_line))]}
    with open(target, "x", encoding="utf-8", newline="\n") as handle:
        json.dump(fixture, handle, indent=0, separators=(",", ":"))
        handle.write("\n")
    print([(c["direction"], c["nop_count"], c["gripper"][0], c["gripper"][-1]) for c in fixture["calls"]], "->", target)


if __name__ == "__main__":
    main(sys.argv)
