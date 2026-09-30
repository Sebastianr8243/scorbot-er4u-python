"""Synthetic USBPcap captures; the trace reader never opens USB."""

import json
from pathlib import Path
import struct
import tempfile
import unittest

from scripts.usb_trace import (
    compare_stats, decode_setpoint_region, export_rows, format_compare, format_setpoints,
    parse_capture, parse_usbpcap_header, read_rows, select_transfers, setpoint_stats,
    summarize, write_timeline,
)
from scorbot.state import ENCODER_OFFSETS, JOINTS


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


def region_payload(targets, sign_word=b"\x00\x00"):
    """OUT message whose bytes 12-35 carry one little-endian value + sign word per joint."""
    payload = bytearray(out_payload(1))
    for index, name in enumerate(JOINTS):
        offset = 12 + 4 * index
        payload[offset:offset + 2] = targets.get(name, 100).to_bytes(2, "little")
        payload[offset + 2:offset + 4] = sign_word
    return bytes(payload)


def out_row(t, sign_word=b"\x00\x00", **targets):
    return {"direction": "out", "t_s": t, "hex": region_payload(targets, sign_word).hex()}


def in_row(t, **counts):
    return {"direction": "in", "t_s": t,
            "encoder_counts": {name: counts.get(name, 100) for name in JOINTS}}


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


class SetpointTests(unittest.TestCase):
    def test_region_decodes_values_and_sign_words(self):
        region = decode_setpoint_region(region_payload({"base": 300, "gripper": 65535},
                                                       b"\xff\xff"))
        self.assertEqual(region["base"], (300, "ffff"))
        self.assertEqual(region["gripper"], (65535, "ffff"))
        self.assertIsNone(decode_setpoint_region(out_payload(1)))   # all-zero region
        self.assertIsNone(decode_setpoint_region(b"\x01" * 20))     # too short

    def test_idle_echo_has_no_leads(self):
        rows = [in_row(0.00), out_row(0.01, base=105), in_row(0.02), out_row(0.03)]
        stats = setpoint_stats(rows)
        base = stats["joints"]["base"]
        self.assertEqual((base["messages"], base["echo"], base["lead"]), (2, 2, 0))
        self.assertEqual(base["max_abs_gap"], 5)
        self.assertEqual(stats["simultaneous_leads"], {})
        self.assertIn("only echoed", format_setpoints(stats))

    def test_single_joint_lead_that_the_arm_follows(self):
        rows = [in_row(0.00),
                out_row(0.10, base=200), in_row(0.11, base=130),
                out_row(0.20, base=300), in_row(0.21, base=220),
                out_row(0.30, base=300), in_row(0.31, base=295),   # re-sent target, still a lead
                out_row(0.40, base=296), in_row(0.41, base=296)]   # echo ends the episode
        stats = setpoint_stats(rows)
        base = stats["joints"]["base"]
        self.assertEqual(base["lead"], 3)
        self.assertEqual(base["episodes"], 1)
        self.assertEqual(base["converged"], 1)
        # Timed from the last target change (0.20), not the unchanged re-send at 0.30.
        self.assertAlmostEqual(base["follow_ms"]["median"], 110.0, places=3)
        self.assertEqual(stats["joints"]["shoulder"]["lead"], 0)
        self.assertEqual(stats["simultaneous_leads"], {1: 3})

    def test_simultaneous_leads_and_unfollowed_target(self):
        rows = [in_row(0.0),
                out_row(0.1, base=400, shoulder=400, elbow=400), in_row(0.2),
                out_row(0.3), in_row(0.4)]                  # back to echo; arm never moved
        stats = setpoint_stats(rows, follow_window_s=0.5)
        self.assertEqual(stats["simultaneous_leads"], {3: 1})
        self.assertEqual(stats["joints"]["elbow"]["episodes"], 1)
        self.assertEqual(stats["joints"]["elbow"]["converged"], 0)
        self.assertIn("2+ joints", format_setpoints(stats))

    def test_counts_wrap_and_bad_rows_are_counted_not_fatal(self):
        rows = [out_row(0.0),                                # no IN yet
                in_row(0.1, base=65530),
                out_row(0.2, base=3),                        # 8 counts across the wrap: echo
                out_row(0.3, sign_word=b"\x12\x34", base=65530),
                {"direction": "out", "t_s": 0.4, "hex": out_payload(2).hex()},  # no region
                {"direction": "in", "t_s": 0.5, "decode_error": "bad sign"}]
        stats = setpoint_stats(rows)
        self.assertEqual(stats["skipped"], {"no_reference": 1, "no_region": 1,
                                            "decode_error": 1})
        self.assertEqual(stats["joints"]["base"]["echo"], 2)
        self.assertEqual(stats["joints"]["base"]["max_abs_gap"], 8)
        self.assertEqual(stats["unexpected_sign_words"], {"1234": 6})

    def test_timeline_csv_and_export_pipeline(self):
        frames = [usbpcap_frame(state_payload(), endpoint=0x81, info=1),
                  usbpcap_frame(region_payload({"base": 250}), endpoint=0x01, info=0)]
        rows = export_rows(select_transfers(parse_capture(pcapng(frames)), device=5))
        timeline = []
        stats = setpoint_stats(rows, timeline=timeline)
        self.assertEqual(stats["joints"]["base"]["lead"], 1)
        self.assertEqual(timeline[0]["base_label"], "lead")
        self.assertEqual(timeline[0]["base_gap"], 150)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "timeline.csv"
            write_timeline(timeline, path)
            lines = path.read_text(encoding="utf-8").splitlines()
            with self.assertRaises(FileExistsError):
                write_timeline(timeline, path)
        self.assertTrue(lines[0].startswith("index,t_s,leading,base_target"))
        self.assertEqual(len(lines), 2)


if __name__ == "__main__":
    unittest.main()
