"""Reduce a fake-controller recording of the vendor DLL to the open-loop golden fixture for the byte check.

    python scripts/make_open_loop_fixture.py rehearsal/lab-dll-out.jsonl tests/fixtures/dll_lab_open_loop.json

The recording is the JSONL ``tools/usbc_fake/pipe_server.py`` wrote while the lab ``USBC.dll`` ran a Home and
one small move (``tools/usbc_fake/fake_move.py``). Each row has ``out``, the 64-byte OUT packet as hex. The
fixture keeps only what does not depend on how many cycles a wait took:

* ``initialization``  the non-``0D`` messages before the first ``47`` (the DLL's ``Initialization``).
* ``control_on``      the first ``47`` through the next ``42 20`` (``Control`` on).
* ``control_off``     the last ``47`` through the last ``4F FF 54`` (the DLL's close).
* ``regions``         every distinct non-zero 24-byte encoder region (OUT bytes 12-35) the DLL wrote.
* ``sequence_wrap_to`` the value byte 0 takes after 255.
* ``odd_headers``     every message whose bytes 1-3 are not zero (the handshake's closing ``0D``).

Messages are bytes 1-11 (header and command) as hex, repeats collapsed, byte 0 and the ``0D`` keep-alives
dropped. The ``0D`` count is pacing, not content. ``tests/test_desk_byte_check.py`` builds the same lists from
what the Stack 2 code sends and compares them.

Standard library only. The vendor binaries and the raw recording are never committed; the fixture holds
message bytes the DLL wrote to a fake pipe, not Intelitek code. The fake arm is our model, so this proves what
the DLL sends, not what the controller accepts.
"""

import json
import sys

CMD_NOP, CMD_CONTROL, CMD_MODE = 0x0D, 0x47, 0x4F
SOURCE = "lab USBC.dll (build 2014-06-11, SHA-256 starts 6118465d2ab2aaa3) against a fake controller on a named pipe, 2026-10-26"


def collapse(items):
    out, last = [], None
    for item in items:
        if item != last:
            out.append(item)
        last = item
    return out


def load_messages(path):
    """The 64-byte OUT packets of a pipe_server recording, in order."""
    messages = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line) if line.strip() else {}
            if "out" in row:
                messages.append(bytes.fromhex(row["out"]))
    return messages


def _key(message):
    return message[1:12].hex()


def _command(key):
    """The command byte and mask of a key (``47``, ``4fff``...)."""
    return key[6:10]


def commands(messages):
    """Header-and-command hex of every non-``0D`` message, consecutive repeats collapsed."""
    return collapse([_key(m) for m in messages if m[4] != CMD_NOP])


def phases(messages):
    """The three open-loop phases of one session as lists of header-and-command hex strings."""
    keys = commands(messages)
    first_on = next(i for i, k in enumerate(keys) if _command(k).startswith("47"))
    on_end = next(i for i in range(first_on, len(keys)) if _command(keys[i]) == "4220")
    last_off = max(i for i, k in enumerate(keys) if _command(k).startswith("47"))
    off_end = max(i for i, k in enumerate(keys) if _command(k) == "4fff" and k[10:12] == "54")
    return {"initialization": keys[:first_on],
            "control_on": keys[first_on:on_end + 1],
            "control_off": keys[last_off:off_end + 1]}


def regions(messages):
    return sorted({m[12:36].hex() for m in messages if any(m[12:36])})


def odd_headers(messages):
    return sorted({_key(m) for m in messages if any(m[1:4])})


def sequence_wrap_to(messages):
    """What byte 0 is after 255 (0 or 1), or None when the recording never reaches it."""
    seen = {b[0] for a, b in zip(messages, messages[1:]) if a[0] == 255}
    return seen.pop() if len(seen) == 1 else None


def main(argv):
    source, target = argv[1], argv[2]
    messages = load_messages(source)
    fixture = {"source": SOURCE, "fields": "header (bytes 1-3) and command (bytes 4-11) as hex",
               "sequence_wrap_to": sequence_wrap_to(messages), "odd_headers": odd_headers(messages),
               **phases(messages), "regions": regions(messages)}
    with open(target, "x", encoding="utf-8", newline="\n") as handle:
        json.dump(fixture, handle, indent=0, separators=(",", ":"))
        handle.write("\n")
    print({name: len(fixture[name]) for name in ("initialization", "control_on", "control_off", "regions")}, "->", target)


if __name__ == "__main__":
    main(sys.argv)
