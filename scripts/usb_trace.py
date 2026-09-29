"""Read USBPcap captures offline and compare controller traffic between programs.

Parses Wireshark/USBPcap .pcapng and legacy .pcap files (link type 249,
LINKTYPE_USBPCAP) with the standard library only. It never opens USB and never
commands the arm; it only reads files that were captured earlier.

  summary CAPTURE [--device N]        list bus/device/endpoints to find the ER-4U
  export  CAPTURE --device N --out F  one JSONL row per bulk/interrupt transfer
  compare A.jsonl B.jsonl             side-by-side protocol statistics
"""

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import struct

from scorbot.state import decode_state


LINKTYPE_USBPCAP = 249
TRANSFER_NAMES = {0: "isochronous", 1: "interrupt", 2: "control", 3: "bulk"}
PAYLOAD_TRANSFERS = (1, 3)  # interrupt, bulk
# Packed little-endian USBPCAP_BUFFER_PACKET_HEADER (27 bytes):
# headerLen, irpId, status, function, info, bus, device, endpoint, transfer, dataLength
USBPCAP_HEADER = struct.Struct("<HQIHBHHBBI")
PCAPNG_SHB = 0x0A0D0D0A
PCAPNG_BYTE_ORDER_MAGIC = 0x1A2B3C4D
PCAP_MAGICS = {
    b"\xd4\xc3\xb2\xa1": ("<", 1e-6), b"\xa1\xb2\xc3\xd4": (">", 1e-6),
    b"\x4d\x3c\xb2\xa1": ("<", 1e-9), b"\xa1\xb2\x3c\x4d": (">", 1e-9),
}
STATE_MIN_LENGTH = 49  # scorbot.state.decode_state needs this many bytes
OUT_BYTE_FIELDS = 8    # byte0 is the sequence byte, byte4 the command (openScorbot/libhex.py)


def parse_usbpcap_header(frame):
    """Split one USBPcap frame into header fields and payload bytes."""
    if len(frame) < USBPCAP_HEADER.size:
        raise ValueError(f"USBPcap frame too short: {len(frame)} bytes")
    (header_len, irp_id, status, function, info, bus, device, endpoint,
     transfer, data_length) = USBPCAP_HEADER.unpack_from(frame)
    if header_len < USBPCAP_HEADER.size or header_len > len(frame):
        raise ValueError(f"Invalid USBPcap header length: {header_len}")
    payload = bytes(frame[header_len:header_len + data_length])
    return {
        "header_len": header_len, "irp_id": irp_id, "status": status,
        "function": function, "info": info,
        # info bit 0: PDO -> FDO, i.e. the completion travelling back up.
        "completion": bool(info & 1),
        "bus": bus, "device": device, "endpoint": endpoint,
        "direction": "in" if endpoint & 0x80 else "out",
        "transfer": transfer, "data_length": data_length, "payload": payload,
    }


def _pcap_frames(data):
    """Yield (timestamp_s, linktype, frame) from a legacy pcap byte stream."""
    endian, resolution = PCAP_MAGICS[bytes(data[:4])]
    if len(data) < 24:
        raise ValueError("pcap global header truncated")
    linktype = struct.unpack_from(endian + "I", data, 20)[0] & 0x0FFFFFFF
    offset = 24
    while offset < len(data):
        if offset + 16 > len(data):
            raise ValueError(f"pcap record header truncated at byte {offset}")
        seconds, fraction, captured, _ = struct.unpack_from(endian + "IIII", data, offset)
        offset += 16
        if offset + captured > len(data):
            raise ValueError(f"pcap record truncated at byte {offset}")
        yield seconds + fraction * resolution, linktype, data[offset:offset + captured]
        offset += captured


def _tsresol(options, endian):
    """Return seconds per timestamp unit from pcapng interface options."""
    offset = 0
    while offset + 4 <= len(options):
        code, length = struct.unpack_from(endian + "HH", options, offset)
        if code == 0:
            break
        if code == 9 and length >= 1:
            value = options[offset + 4]
            return 2.0 ** -(value & 0x7F) if value & 0x80 else 10.0 ** -value
        offset += 4 + length + (-length % 4)
    return 1e-6


def _pcapng_frames(data):
    """Yield (timestamp_s, linktype, frame) from a pcapng byte stream."""
    offset, endian, interfaces = 0, "<", []
    while offset < len(data):
        if offset + 12 > len(data):
            raise ValueError(f"pcapng block header truncated at byte {offset}")
        block_type = struct.unpack_from("<I", data, offset)[0]
        if block_type == PCAPNG_SHB:
            magic = struct.unpack_from("<I", data, offset + 8)[0]
            endian = "<" if magic == PCAPNG_BYTE_ORDER_MAGIC else ">"
            interfaces = []  # interface IDs restart in every section
        else:
            block_type = struct.unpack_from(endian + "I", data, offset)[0]
        length = struct.unpack_from(endian + "I", data, offset + 4)[0]
        if length < 12 or offset + length > len(data):
            raise ValueError(f"pcapng block truncated at byte {offset}")
        body = data[offset + 8:offset + length - 4]
        if block_type == 1:  # Interface Description Block
            linktype = struct.unpack_from(endian + "H", body, 0)[0]
            interfaces.append((linktype, _tsresol(body[8:], endian)))
        elif block_type == 6:  # Enhanced Packet Block
            iface, high, low, captured, _ = struct.unpack_from(endian + "IIIII", body, 0)
            if iface >= len(interfaces):
                raise ValueError(f"packet references unknown interface {iface}")
            linktype, resolution = interfaces[iface]
            yield ((high << 32) | low) * resolution, linktype, body[20:20 + captured]
        elif block_type == 3 and interfaces:  # Simple Packet Block: no timestamp
            original = struct.unpack_from(endian + "I", body, 0)[0]
            yield None, interfaces[0][0], body[4:4 + original]
        offset += length


def parse_capture(data):
    """Return every USBPcap packet in a pcap/pcapng byte stream, in file order."""
    data = memoryview(bytes(data))
    if len(data) < 4:
        raise ValueError("capture file is empty or truncated")
    if bytes(data[:4]) in PCAP_MAGICS:
        frames = _pcap_frames(data)
    elif struct.unpack_from("<I", data, 0)[0] == PCAPNG_SHB:
        frames = _pcapng_frames(data)
    else:
        raise ValueError("not a pcap or pcapng file")
    packets = []
    for timestamp, linktype, frame in frames:
        if linktype != LINKTYPE_USBPCAP:
            continue
        packet = parse_usbpcap_header(bytes(frame))
        packet["t"] = timestamp
        packets.append(packet)
    return packets


def select_transfers(packets, device, bus=None):
    """Keep bulk/interrupt packets with payload for one device (optionally one bus)."""
    return [p for p in packets
            if p["device"] == device and (bus is None or p["bus"] == bus)
            and p["transfer"] in PAYLOAD_TRANSFERS and p["payload"]]


def summarize(packets, device=None):
    """Group packets by bus/device/endpoint with counts and payload sizes."""
    groups = {}
    for packet in packets:
        if device is not None and packet["device"] != device:
            continue
        key = (packet["bus"], packet["device"], packet["endpoint"], packet["transfer"])
        entry = groups.setdefault(key, {
            "bus": key[0], "device": key[1], "endpoint": f"0x{key[2]:02x}",
            "direction": packet["direction"],
            "transfer": TRANSFER_NAMES.get(key[3], str(key[3])),
            "packets": 0, "with_payload": 0, "payload_sizes": Counter(),
        })
        entry["packets"] += 1
        if packet["payload"]:
            entry["with_payload"] += 1
            entry["payload_sizes"][len(packet["payload"])] += 1
    return [groups[key] for key in sorted(groups)]


def _decoded_state(payload):
    try:
        state = decode_state(payload, connected=True, enabled=None, homed=False, fault=None)
    except ValueError as exc:
        return {"decode_error": str(exc)}
    return {
        "encoder_counts": state.encoder_counts,
        "encoder_sign_bytes": state.encoder_sign_bytes,
        "signed_encoder_counts": state.signed_encoder_counts,
        "controller_error_counts": state.controller_error_counts,
        "home_switch_bits": state.home_switch_bits,
    }


def export_rows(transfers):
    """Build JSONL-ready rows; t_s is relative to the first transfer."""
    times = [p["t"] for p in transfers if p["t"] is not None]
    start = min(times) if times else None
    rows = []
    for index, packet in enumerate(transfers):
        payload = packet["payload"]
        row = {
            "index": index,
            "t_s": None if packet["t"] is None else round(packet["t"] - start, 9),
            "direction": packet["direction"], "endpoint": packet["endpoint"],
            "transfer": TRANSFER_NAMES.get(packet["transfer"], str(packet["transfer"])),
            "length": len(payload), "hex": payload.hex(),
        }
        if packet["direction"] == "out":
            for position in range(OUT_BYTE_FIELDS):
                row[f"byte{position}"] = payload[position] if position < len(payload) else None
        elif len(payload) >= STATE_MIN_LENGTH:
            row.update(_decoded_state(payload))
        rows.append(row)
    return rows


def read_rows(path):
    rows = []
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON") from exc
    return rows


def percentile(values, fraction):
    """Nearest-rank percentile; None for an empty list."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def _sequence_stats(values):
    stats = {"count": len(values), "min": min(values, default=None),
             "max": max(values, default=None), "zero_seen": values.count(0),
             "wraps": [], "other_steps": Counter()}
    for earlier, later in zip(values, values[1:]):
        if later == earlier + 1:
            continue
        if later < earlier and earlier >= 128 and later < 16:
            stats["wraps"].append(f"{earlier}->{later}")
        else:
            stats["other_steps"][later - earlier] += 1
    stats["wraps"] = dict(Counter(stats["wraps"]))
    stats["other_steps"] = dict(stats["other_steps"])
    return stats


def compare_stats(rows, header_bytes=4, header_skip=1):
    """Protocol statistics for one exported trace.

    OUT headers are bytes [header_skip, header_skip + header_bytes); skipping
    byte0 by default keeps the ever-changing sequence byte out of the key.
    """
    outs = [row for row in rows if row.get("direction") == "out"]
    ins = [row for row in rows if row.get("direction") == "in"]
    out_times = [row["t_s"] for row in outs if row.get("t_s") is not None]
    intervals = [(b - a) * 1000 for a, b in zip(out_times, out_times[1:])]
    latencies, pending = [], None
    for row in rows:
        if row.get("t_s") is None:
            continue
        if row.get("direction") == "out":
            pending = row["t_s"]
        elif row.get("direction") == "in" and pending is not None:
            latencies.append((row["t_s"] - pending) * 1000)
            pending = None
    headers = Counter()
    for row in outs:
        payload = bytes.fromhex(row.get("hex", ""))
        headers[payload[header_skip:header_skip + header_bytes].hex()] += 1
    sequence = [row["byte0"] for row in outs if type(row.get("byte0")) is int]
    sign_bytes = Counter()
    for row in ins:
        for value in (row.get("encoder_sign_bytes") or {}).values():
            sign_bytes[value] += 1
    return {
        "out_packets": len(outs), "in_packets": len(ins),
        "duration_s": (max(out_times) - min(out_times)) if len(out_times) > 1 else None,
        "out_lengths": dict(Counter(row.get("length") for row in outs)),
        "in_lengths": dict(Counter(row.get("length") for row in ins)),
        "out_interval_ms": {
            "min": min(intervals, default=None), "median": percentile(intervals, 0.5),
            "p95": percentile(intervals, 0.95), "max": max(intervals, default=None),
        },
        "out_to_in_ms": {"median": percentile(latencies, 0.5),
                         "p95": percentile(latencies, 0.95)},
        "sequence_byte0": _sequence_stats(sequence),
        "headers": dict(headers),
        "home_switch_bits": dict(Counter(row["home_switch_bits"] for row in ins
                                         if "home_switch_bits" in row)),
        "encoder_sign_bytes": dict(sign_bytes),
        "decode_errors": sum(1 for row in ins if "decode_error" in row),
    }


def _fmt(value):
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.3f}"
    if isinstance(value, dict):
        return ", ".join(f"{k}:{v}" for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))) or "-"
    return str(value)


def format_compare(a, b, name_a="A", name_b="B", header_bytes=4, header_skip=1):
    """Render two compare_stats results as plain text."""
    lines = []
    width = 28

    def row(label, left, right):
        lines.append(f"{label:<{width}} | {_fmt(left):<30} | {_fmt(right)}")

    lines.append(f"{'metric':<{width}} | {name_a:<30} | {name_b}")
    lines.append("-" * (width + 66))
    for key in ("out_packets", "in_packets", "duration_s", "out_lengths", "in_lengths",
                "decode_errors"):
        row(key, a[key], b[key])
    for key in ("min", "median", "p95", "max"):
        row(f"OUT->OUT interval {key} ms", a["out_interval_ms"][key], b["out_interval_ms"][key])
    for key in ("median", "p95"):
        row(f"OUT->IN latency {key} ms", a["out_to_in_ms"][key], b["out_to_in_ms"][key])
    for key in ("count", "min", "max", "zero_seen", "wraps", "other_steps"):
        row(f"seq byte0 {key}", a["sequence_byte0"][key], b["sequence_byte0"][key])
    row("home_switch_bits", a["home_switch_bits"], b["home_switch_bits"])
    row("encoder sign bytes", a["encoder_sign_bytes"], b["encoder_sign_bytes"])
    lines.append("")
    lines.append(f"OUT headers: bytes {header_skip}..{header_skip + header_bytes - 1} (hex)")
    totals = Counter(a["headers"]) + Counter(b["headers"])
    for header, _ in sorted(totals.items(), key=lambda kv: (-kv[1], kv[0])):
        left, right = a["headers"].get(header, 0), b["headers"].get(header, 0)
        flag = "" if left and right else f"  <- only in {name_a if left else name_b}"
        row(f"  {header or '(empty)'}", left, f"{right}{flag}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    summary = commands.add_parser("summary", help="list devices and endpoints")
    summary.add_argument("capture", type=Path)
    summary.add_argument("--device", type=int)
    export = commands.add_parser("export", help="write transfers as JSONL")
    export.add_argument("capture", type=Path)
    export.add_argument("--device", type=int, required=True)
    export.add_argument("--bus", type=int, help="only needed if two buses share the device number")
    export.add_argument("--out", type=Path, required=True)
    compare = commands.add_parser("compare", help="compare two exported JSONL traces")
    compare.add_argument("a", type=Path)
    compare.add_argument("b", type=Path)
    compare.add_argument("--header-bytes", type=int, default=4)
    compare.add_argument("--header-skip", type=int, default=1,
                         help="leading OUT bytes excluded from the header (default skips byte0)")
    args = parser.parse_args()

    if args.command == "summary":
        packets = parse_capture(args.capture.read_bytes())
        print(f"{len(packets)} USBPcap packets in {args.capture}")
        for entry in summarize(packets, args.device):
            sizes = ", ".join(f"{size}B x{count}"
                              for size, count in sorted(entry["payload_sizes"].items()))
            print(f"bus {entry['bus']} device {entry['device']:>3} ep {entry['endpoint']} "
                  f"{entry['direction']:<3} {entry['transfer']:<11} packets {entry['packets']:>6} "
                  f"with payload {entry['with_payload']:>6}  sizes: {sizes or '-'}")
        return 0
    if args.command == "export":
        packets = parse_capture(args.capture.read_bytes())
        rows = export_rows(select_transfers(packets, args.device, args.bus))
        if not rows:
            print(f"No bulk/interrupt payloads for device {args.device}; run summary first.")
            return 1
        with args.out.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")
        print(f"Wrote {len(rows)} transfers to {args.out}")
        return 0
    stats = [compare_stats(read_rows(path), args.header_bytes, args.header_skip)
             for path in (args.a, args.b)]
    print(format_compare(stats[0], stats[1], args.a.name, args.b.name,
                         args.header_bytes, args.header_skip))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
