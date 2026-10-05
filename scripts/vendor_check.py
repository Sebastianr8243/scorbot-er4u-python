"""Check an exported USB trace against the layout read from the vendor DLL.

Reads a JSONL trace written by ``usb_trace.py export`` (a Wireshark capture)
or ``usb_trace.py from-log`` (the packets our own SDK copies during a jog), or
an idle recording from ``examples/record_raw_state.py`` (replies only, in its
``raw_hex`` field). It never opens USB and never commands the arm.

    python scripts/vendor_check.py TRACE.jsonl

The layout comes from disassembly (docs/protocol/VENDOR_DLL_PROTOCOL.md). A "matches"
line means this trace agrees with that layout. It is evidence about the
packets in this file, not a statement that anything is safe or calibrated.
"""

import argparse
from collections import Counter
import json
from pathlib import Path
import statistics

MESSAGE_LENGTH = 64
# OUT byte 4, with the DLL's own names (docs/protocol/VENDOR_DLL_PROTOCOL.md section 4).
COMMANDS = {
    0x0D: "No operation", 0x41: "Set analog output", 0x42: "Turn motors",
    0x44: "Set digital output", 0x47: "Clear communication buffer",
    0x48: "Set position", 0x4C: "Slave Cmd", 0x4D: "Move", 0x4F: "Mode",
    0x53: "Set parameter", 0x54: "Stop", 0x57: "Get PWM", 0x5A: "Reset",
    0x61: "Get analog output", 0x64: "Get digital output", 0x72: "Get Version",
    0x73: "Control Off", 0x75: "Stop USB test", 0x4B: "(unnamed 4B)", 0x46: "(unnamed 46)",
}
POSITION_OFFSET = 19   # IN: 24-bit little-endian position per axis, 5 bytes apart
AXIS_STRIDE = 5
AXES = 8
ZERO = 0x7FFFFF
ECHO_WINDOW = 256      # how many earlier OUT messages an IN may refer to
MATCHES, CONTRADICTED, NOT_SEEN = "matches", "CONTRADICTED", "not seen"


def read_rows(path):
    rows = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{number}: invalid JSON") from exc
    return rows


def _payloads(rows):
    packets = []
    for row in rows:
        if row.get("direction") in ("out", "in") and row.get("hex"):
            packets.append((row["direction"], bytes.fromhex(row["hex"])))
        elif row.get("type") == "sample" and row.get("raw_hex"):
            packets.append(("in", bytes.fromhex(row["raw_hex"])))  # idle recording
    return packets


def _check(name, status, detail):
    return {"claim": name, "status": status, "detail": detail}


def check_sizes(packets):
    sizes = Counter((direction, len(payload)) for direction, payload in packets)
    wrong = {key: count for key, count in sizes.items() if key[1] != MESSAGE_LENGTH}
    detail = ", ".join(f"{d} {n} bytes x{c}" for (d, n), c in sorted(sizes.items()))
    return _check("V1 messages are 64 bytes", CONTRADICTED if wrong else MATCHES, detail)


def check_commands(packets):
    seen = Counter(p[4] for d, p in packets if d == "out" and len(p) > 4)
    if not seen:
        return _check("V2 OUT byte 4 is a known command", NOT_SEEN, "no OUT messages")
    unknown = sorted(byte for byte in seen if byte not in COMMANDS)
    detail = ", ".join(f"{byte:02X} {COMMANDS.get(byte, '?')} x{count}"
                       for byte, count in sorted(seen.items()))
    if unknown:
        detail += "; not in the vendor table: " + " ".join(f"{b:02X}" for b in unknown)
    return _check("V2 OUT byte 4 is a known command", CONTRADICTED if unknown else MATCHES, detail)


def check_echo(packets):
    """IN byte 0 names an earlier OUT message; IN byte 1 is that message's command."""
    recent = []            # (id, command) of OUT messages, oldest first
    lags, id_misses, command_misses, replies = [], 0, 0, 0
    for direction, payload in packets:
        if len(payload) < 5:
            continue
        if direction == "out":
            recent.append((payload[0], payload[4]))
            del recent[:-ECHO_WINDOW]
            continue
        if not recent:
            continue
        replies += 1
        for lag, (message_id, command) in enumerate(reversed(recent)):
            if message_id == payload[0]:
                lags.append(lag)
                command_misses += payload[1] != command
                break
        else:
            id_misses += 1
    name = "V4 reply echoes an OUT message ID and its command"
    if not replies:
        return _check(name, NOT_SEEN, "no reply after an OUT message")
    detail = (f"{replies} replies; ID not among recent OUT: {id_misses}; "
              f"command differs from that OUT: {command_misses}")
    if lags:
        detail += (f"; messages behind: min {min(lags)}, median "
                   f"{statistics.median(lags):g}, max {max(lags)}")
    bad = id_misses + command_misses
    # A few misses at the start are replies to messages sent before the trace began.
    return _check(name, MATCHES if bad <= max(2, replies // 50) else CONTRADICTED, detail)


def check_emergency(packets):
    values = Counter(p[2] for d, p in packets if d == "in" and len(p) > 2)
    name = "V6 reply byte 2 bit 0 is emergency"
    if not values:
        return _check(name, NOT_SEEN, "no replies")
    detail = ", ".join(f"{value:02X} x{count}" for value, count in sorted(values.items()))
    bits = {value & 1 for value in values}
    if bits == {0, 1}:
        return _check(name, MATCHES, f"bit 0 changes in this trace ({detail}); "
                      "compare the change with when the stop was pressed")
    return _check(name, NOT_SEEN, f"bit 0 never changes ({detail}); "
                  "needs a trace recorded across an e-stop press")


def check_id_wrap(packets):
    ids = [p[0] for d, p in packets if d == "out" and p]
    name = "V13 message ID after 255"
    after = Counter(later for earlier, later in zip(ids, ids[1:]) if earlier == 255)
    if not after:
        return _check(name, NOT_SEEN, f"no wrap in {len(ids)} OUT messages")
    detail = ", ".join(f"255 -> {value} x{count}" for value, count in sorted(after.items()))
    return _check(name, MATCHES, detail)


def check_position(packets):
    """A joint crossing 0x7FFFFF must not jump: the three bytes are one number."""
    needed = POSITION_OFFSET + AXIS_STRIDE * AXES
    previous, crossings, worst = None, 0, 0
    for direction, payload in packets:
        if direction != "in" or len(payload) < needed:
            continue
        raw = [int.from_bytes(payload[POSITION_OFFSET + AXIS_STRIDE * axis:][:3], "little")
               for axis in range(AXES)]
        if previous is not None:
            for before, now in zip(previous, raw):
                if (before > ZERO) != (now > ZERO):
                    crossings += 1
                    worst = max(worst, abs(now - before))
        previous = raw
    name = "V14 position is one 24-bit number, zero at 0x7FFFFF"
    if not crossings:
        return _check(name, NOT_SEEN, "no joint crossed 0x7FFFFF in this trace")
    detail = f"{crossings} crossings, largest step across the zero {worst} counts"
    return _check(name, MATCHES if worst < 256 else CONTRADICTED, detail)


CHECKS = (check_sizes, check_commands, check_echo, check_emergency, check_id_wrap, check_position)


def run_checks(rows):
    packets = _payloads(rows)
    return [check(packets) for check in CHECKS]


def format_report(results):
    lines = ["Trace compared with the layout read from the vendor DLL (from disassembly):"]
    for result in results:
        lines.append(f"  [{result['status']:^12}] {result['claim']}")
        lines.append(f"                 {result['detail']}")
    lines.append("A match is evidence about this trace only. It is not a safety verdict.")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("trace", type=Path,
                        help="JSONL from usb_trace.py export or from-log, or an idle recording")
    args = parser.parse_args(argv)
    results = run_checks(read_rows(args.trace))
    print(format_report(results))
    return 1 if any(r["status"] == CONTRADICTED for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
