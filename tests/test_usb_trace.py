"""Synthetic USBPcap captures; the trace reader never opens USB."""

import json
from pathlib import Path
import struct
import tempfile
import unittest

from scripts.usb_trace import (
    compare_stats, export_rows, format_compare, parse_capture, parse_usbpcap_header,
    read_rows, select_transfers, summarize,
)
from scorbot.state import ENCODER_OFFSETS


def usbpcap_frame(payload=b"", *, endpoint=0x01, transfer=3, info=0, device=5, bus=1,
                  irp=1):
    header = struct.pack("<HQIHBHHBBI", 27, irp, 0, 9, info, bus, device, endpoint,
                         transfer, len(payload))
    return header + payload


def state_payload(base=100, sign=128, home_bits=0):
    packet = bytearray(64)
    packet[5] = home_bits
    for offset in ENCODER_OFFSETS:
        packet[offset:offset + 2] = (100).to_bytes(2, "little")
        packet[offset + 2] = 128
    packet[ENCODER_OFFSETS[0]:ENCODER_OFFSETS[0] + 2] = base.to_bytes(2, "little")
    packet[ENCODER_OFFSETS[0] + 2] = sign
    return bytes(packet)


def out_payload(sequence, command=0x0D):
    return bytes([sequence, 0, 0, 0, command]) + bytes(123)


def exchange(sequence, home_bits=0, base=100, sign=128):
    """One legacy write/read pair as USBPcap records it: submit OUT, IN completion."""
    return [
        usbpcap_frame(out_payload(sequence), endpoint=0x01, info=0),
        usbpcap_frame(b"", endpoint=0x01, info=1),                  # OUT completion
        usbpcap_frame(b"", endpoint=0x81, info=0),                  # IN submission
        usbpcap_frame(state_payload(base, sign, home_bits), endpoint=0x81, info=1),
    ]


def pcapng(frames, step_us=1000, linktype=249):
    def block(block_type, body):
        body += b"\0" * (-len(body) % 4)
        length = len(body) + 12
        return struct.pack("<II", block_type, length) + body + struct.pack("<I", length)

    shb = block(0x0A0D0D0A, struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1))
    idb = block(1, struct.pack("<HHI", linktype, 0, 65535))
    packets = b""
    for index, frame in enumerate(frames):
        timestamp = 1_700_000_000_000_000 + index * step_us
        packets += block(6, struct.pack("<IIIII", 0, timestamp >> 32, timestamp & 0xFFFFFFFF,
                                        len(frame), len(frame)) + frame)
    return shb + idb + packets


def pcap(frames, step_us=1000):
    data = struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 249)
    for index, frame in enumerate(frames):
        data += struct.pack("<IIII", 1000, index * step_us, len(frame), len(frame)) + frame
    return data


class UsbTraceTests(unittest.TestCase):
    def test_header_fields_and_direction(self):
        packet = parse_usbpcap_header(usbpcap_frame(b"\x01\x02", endpoint=0x81, info=1))
        self.assertEqual(packet["direction"], "in")
        self.assertTrue(packet["completion"])
        self.assertEqual(packet["payload"], b"\x01\x02")
        self.assertEqual((packet["bus"], packet["device"], packet["transfer"]), (1, 5, 3))
        with self.assertRaises(ValueError):
            parse_usbpcap_header(b"\x00" * 10)

    def test_pcapng_and_pcap_give_same_packets(self):
        frames = exchange(1) + exchange(2)
        from_ng = parse_capture(pcapng(frames))
        from_legacy = parse_capture(pcap(frames))
        self.assertEqual(len(from_ng), 8)
        self.assertEqual([p["payload"] for p in from_ng], [p["payload"] for p in from_legacy])
        self.assertAlmostEqual(from_ng[1]["t"] - from_ng[0]["t"], 0.001, places=6)
        self.assertAlmostEqual(from_legacy[1]["t"] - from_legacy[0]["t"], 0.001, places=6)

    def test_other_link_types_and_bad_files(self):
        self.assertEqual(parse_capture(pcapng(exchange(1), linktype=1)), [])
        with self.assertRaises(ValueError):
            parse_capture(b"not a capture")
        with self.assertRaises(ValueError):
            parse_capture(pcap(exchange(1))[:-5])

    def test_summary_finds_device_endpoints(self):
        frames = exchange(1) + [usbpcap_frame(b"\x00" * 8, endpoint=0x80, transfer=2,
                                              device=2)]
        entries = summarize(parse_capture(pcapng(frames)))
        by_key = {(e["device"], e["endpoint"]): e for e in entries}
        self.assertEqual(by_key[(5, "0x01")]["packets"], 2)
        self.assertEqual(by_key[(5, "0x01")]["payload_sizes"], {128: 1})
        self.assertEqual(by_key[(5, "0x81")]["payload_sizes"], {64: 1})
        self.assertEqual(by_key[(2, "0x80")]["transfer"], "control")
        self.assertEqual({e["device"] for e in summarize(parse_capture(pcapng(frames)), 2)}, {2})

    def test_export_decodes_state_and_out_bytes(self):
        frames = exchange(7, home_bits=0x04) + exchange(8, base=3, sign=77)
        frames.append(usbpcap_frame(b"\x00" * 8, endpoint=0x80, transfer=2))  # control: dropped
        rows = export_rows(select_transfers(parse_capture(pcapng(frames)), device=5))
        self.assertEqual([r["direction"] for r in rows], ["out", "in", "out", "in"])
        self.assertEqual(rows[0]["t_s"], 0)
        self.assertEqual((rows[0]["byte0"], rows[0]["byte4"]), (7, 0x0D))
        self.assertEqual(rows[1]["home_switch_bits"], 4)
        self.assertEqual(rows[1]["encoder_counts"]["base"], 100)
        self.assertIn("decode_error", rows[3])
        self.assertEqual(select_transfers(parse_capture(pcapng(frames)), device=9), [])

    def test_compare_reports_sequence_timing_and_headers(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for name, sequences, step in (("a", [254, 255, 1, 2], 1000),
                                          ("b", [255, 0, 1, 1], 2000)):
                frames = [frame for sequence in sequences for frame in exchange(sequence)]
                rows = export_rows(select_transfers(parse_capture(pcapng(frames, step)), 5))
                path = Path(directory) / f"{name}.jsonl"
                path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
                paths.append(path)
            a, b = (compare_stats(read_rows(path)) for path in paths)
        self.assertEqual(a["sequence_byte0"]["zero_seen"], 0)
        self.assertEqual(a["sequence_byte0"]["wraps"], {"255->1": 1})
        self.assertEqual(b["sequence_byte0"]["zero_seen"], 1)
        self.assertEqual(b["sequence_byte0"]["wraps"], {"255->0": 1})
        self.assertEqual(b["sequence_byte0"]["other_steps"], {0: 1})
        self.assertAlmostEqual(a["out_interval_ms"]["median"], 4.0, places=3)
        self.assertAlmostEqual(b["out_interval_ms"]["p95"], 8.0, places=3)
        self.assertEqual(a["headers"], {"0000000d": 4})
        self.assertEqual(a["home_switch_bits"], {0: 4})
        text = format_compare(a, b, "intelitek", "python")
        self.assertIn("seq byte0 zero_seen", text)
        self.assertIn("0000000d", text)


if __name__ == "__main__":
    unittest.main()
