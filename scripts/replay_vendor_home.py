"""Replay a capture of the vendor's home through our port and report where the two differ.

Offline only: it reads rows already exported from a USBPcap capture (``scripts/usb_trace.py export``),
never opens USB and never commands the arm.

  python scripts/replay_vendor_home.py capture.jsonl [--first N]

The rows alternate OUT, IN: the OUT is what the vendor's ``USBC.dll`` sent, the IN is what the real
controller answered. Each real IN is handed to ``scorbot.vendor_home.vendor_home`` as the reply to the
message our port sent in the same position, and the two OUT messages are compared field by field
(command, mask, 16-bit and 32-bit argument). The sequence byte and the setpoint table are not compared:
the first is the sender's counter and the second depends on where each side's wait loops stood.

What a match means: for this capture, our port sends the vendor's commands in the vendor's order when
fed the real replies. What a difference means: our port is wrong, or its waits (counted in cycles, not
in the vendor's timing) ended on a different message. Look at the neighbours before deciding which.
"""

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sys

try:
    from scorbot import vendor_home as vh
except ImportError:  # Run as a script from scripts/.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scorbot import vendor_home as vh

FIELDS = ("cmd", "mask", "arg16", "arg32")
CYCLE_S = 0.016  # host period, used for the clock when a row has no time


@dataclass(frozen=True)
class Difference:
    message: int      # index of the OUT/IN pair, from 0
    field: str        # one of FIELDS, "end" (port finished first) or "error" (port raised HomeError)
    vendor: object
    port: object


@dataclass(frozen=True)
class Report:
    compared: int
    total: int
    first_difference: "Difference | None"
    port_finished: bool


def read_rows(path):
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _pairs(rows):
    """``(out_packet, in_packet, in_time)`` per exchange; refuses anything that is not OUT, IN, OUT, IN."""
    data = [r for r in rows if r.get("direction") in ("out", "in")]
    if len(data) % 2 or any(r["direction"] != ("out", "in")[i % 2] for i, r in enumerate(data)):
        raise ValueError("rows must alternate OUT, IN, starting with an OUT (export one device and one endpoint pair)")
    out = []
    for i in range(0, len(data), 2):
        t = data[i + 1].get("t_s")
        out.append((bytes.fromhex(data[i]["hex"]), bytes.fromhex(data[i + 1]["hex"]),
                    (i // 2 + 1) * CYCLE_S if t is None else t))
    return out


def _fields(packet):
    return {"cmd": packet[4], "mask": packet[5],
            "arg16": int.from_bytes(packet[6:8], "little"),
            "arg32": int.from_bytes(packet[8:12], "little", signed=True)}


def replay(rows, *, first=0, version=0):
    """Run the port against the capture's replies. ``first`` is the pair whose reply starts the port."""
    pairs = _pairs(rows)
    if not pairs:
        raise ValueError("no OUT/IN pairs in the capture")
    home = vh.vendor_home(vh.decode_reply(pairs[first][1], pairs[first][2]), version=version)
    compared = 0
    try:
        out = next(home)
    except StopIteration:
        return Report(0, len(pairs), None, True)
    for index, (vendor_packet, reply_packet, now) in enumerate(pairs):
        if index < first:
            continue
        mine, theirs = _fields(vh.encode_out(out, 1)), _fields(vendor_packet)
        for name in FIELDS:
            if mine[name] != theirs[name]:
                return Report(compared, len(pairs), Difference(index, name, theirs[name], mine[name]), False)
        compared += 1
        try:
            out = home.send(vh.decode_reply(reply_packet, now))
        except StopIteration:
            if index + 1 < len(pairs):
                return Report(compared, len(pairs), Difference(index + 1, "end", "more messages", "port finished"), True)
            return Report(compared, len(pairs), None, True)
        except vh.HomeError as error:
            return Report(compared, len(pairs), Difference(index, "error", "no error", f"{error.code}: {error}"), True)
    return Report(compared, len(pairs), None, False)


def format_report(report):
    lines = [f"Compared {report.compared} of {report.total} messages."]
    diff = report.first_difference
    if diff is None:
        lines.append("No difference: our port sent the vendor's commands in the vendor's order for this capture."
                     + ("" if report.port_finished else " (The capture ends before the port finished.)"))
    else:
        lines.append(f"First difference at message {diff.message}: {diff.field}: vendor {diff.vendor!r}, port {diff.port!r}.")
        lines.append("Neighbours matter: a wait that ends on a different cycle shows up here as a shifted command, "
                     "not as a wrong one. Compare the commands either side before changing the port.")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("capture", help="JSONL from usb_trace.py export, OUT and IN rows of one device")
    parser.add_argument("--first", type=int, default=0, help="index of the pair where the home starts")
    args = parser.parse_args(argv)
    report = replay(read_rows(args.capture), first=args.first)
    print(format_report(report))
    return 0 if report.first_difference is None else 1


if __name__ == "__main__":
    sys.exit(main())
