"""Turn the capture hook's log into the rows ``replay_vendor_home.py`` reads. Offline; opens no USB.

  python scripts/hook_log_to_rows.py frida_io.jsonl rows.jsonl

The hook (``tools/usbc_capture/hook_io.js``) logs one line per ``WriteFile`` and ``ReadFile`` the vendor DLL
makes. A write becomes an OUT row and a read becomes an IN row. The open line, a failed write and a read that
returned nothing are dropped. A packet that is not 64 bytes is refused, not padded: the replay treats every
row as one 64-byte message.
"""

import json
import sys

PACKET = 64


def convert(lines):
    rows = []
    for text in lines:
        if not text.strip():
            continue
        record = json.loads(text)
        kind = record.get("ev")
        if kind == "W" and record.get("ok"):
            direction = "out"
        elif kind == "R" and record.get("ok") and record.get("got"):
            direction = "in"
        else:
            continue
        packet = bytes.fromhex(record["hex"])
        if len(packet) != PACKET:
            raise ValueError(f"expected a {PACKET}-byte packet, got {len(packet)} bytes at t={record.get('t')}")
        rows.append({"direction": direction, "hex": record["hex"], "length": PACKET, "t_s": record.get("t")})
    return rows


def main(argv):
    if len(argv) != 3:
        raise SystemExit("usage: hook_log_to_rows.py frida_io.jsonl rows.jsonl")
    with open(argv[1], encoding="utf-8") as source:
        rows = convert(source)
    with open(argv[2], "w", encoding="utf-8") as target:
        for row in rows:
            target.write(json.dumps(row) + "\n")
    print(f"{len(rows)} rows written to {argv[2]}")


if __name__ == "__main__":
    main(sys.argv)
