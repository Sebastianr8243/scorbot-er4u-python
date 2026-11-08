"""Teach pendant events in the lab summary.

Every time reply byte 3 changes between consecutive records carrying raw_packet_hex,
one line is printed with the record's time, hex code, and decoded text.
Physical meaning of keys is DLL naming, unverified.
"""

import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout

from scripts import lab_summary


def _make_packet_hex(byte3: int, length: int = 64) -> str:
    packet = bytearray(length)
    packet[3] = byte3
    return packet.hex()


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class LabSummaryPendantTests(unittest.TestCase):
    def test_pendant_abort_pressed_then_released(self):
        # Three records whose byte 3 goes 0x40 -> 0x21 -> 0x61
        rows = [
            {
                "timestamp_utc": "2026-11-08T00:00:01Z",
                "raw_packet_hex": _make_packet_hex(0x40),
            },
            {
                "timestamp_utc": "2026-11-08T00:00:02Z",
                "raw_packet_hex": _make_packet_hex(0x21),
            },
            {
                "timestamp_utc": "2026-11-08T00:00:03Z",
                "raw_packet_hex": _make_packet_hex(0x61),
            },
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "test.jsonl"
            _write_jsonl(path, rows)
            lines, _ = lab_summary.summarize(path, phone={})

        pendant_lines = [line for line in lines if "pendant:" in line]
        self.assertEqual(len(pendant_lines), 2)

        first_event = pendant_lines[0]
        self.assertIn("2026-11-08T00:00:02Z", first_event)
        self.assertIn("0x21", first_event)
        self.assertIn("Abort", first_event)
        self.assertIn("pressed", first_event)

        second_event = pendant_lines[1]
        self.assertIn("2026-11-08T00:00:03Z", second_event)
        self.assertIn("0x61", second_event)
        self.assertIn("Abort", second_event)
        self.assertIn("released", second_event)

    def test_file_with_no_raw_packet_hex_prints_no_pendant_section(self):
        rows = [
            {"type": "note", "text": "hello"},
            {"type": "note", "text": "world"},
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "no_raw.jsonl"
            _write_jsonl(path, rows)
            lines, _ = lab_summary.summarize(path, phone={})

        pendant_lines = [line for line in lines if "pendant" in line.lower()]
        self.assertEqual(pendant_lines, [])

    def test_mode_event_decoded(self):
        # 0x21 (Abort pressed) -> 0x40 (In Teach mode not in Home)
        rows = [
            {"timestamp_utc": "2026-11-08T00:00:01Z", "raw_packet_hex": _make_packet_hex(0x21)},
            {"timestamp_utc": "2026-11-08T00:00:02Z", "raw_packet_hex": _make_packet_hex(0x40)},
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "mode.jsonl"
            _write_jsonl(path, rows)
            lines, _ = lab_summary.summarize(path, phone={})

        pendant_lines = [line for line in lines if "pendant:" in line]
        self.assertEqual(len(pendant_lines), 1)
        self.assertIn("0x40", pendant_lines[0])
        self.assertIn("In Teach mode not in Home", pendant_lines[0])

    def test_main_cli_prints_pendant_lines(self):
        rows = [
            {"timestamp_utc": "2026-11-08T00:00:01Z", "raw_packet_hex": _make_packet_hex(0x40)},
            {"timestamp_utc": "2026-11-08T00:00:02Z", "raw_packet_hex": _make_packet_hex(0x21)},
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "cli_test.jsonl"
            _write_jsonl(path, rows)
            with redirect_stdout(io.StringIO()) as out:
                lab_summary.main([str(path)])
        output = out.getvalue()
        self.assertIn("0x21", output)
        self.assertIn("Abort", output)
        self.assertIn("pressed", output)

    def test_byte_3_never_changing_prints_no_pendant_section(self):
        rows = [
            {"timestamp_utc": "2026-11-08T00:00:01Z", "raw_packet_hex": _make_packet_hex(0x40)},
            {"timestamp_utc": "2026-11-08T00:00:02Z", "raw_packet_hex": _make_packet_hex(0x40)},
            {"timestamp_utc": "2026-11-08T00:00:03Z", "raw_packet_hex": _make_packet_hex(0x40)},
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "unchanged.jsonl"
            _write_jsonl(path, rows)
            lines, _ = lab_summary.summarize(path, phone={})

        pendant_lines = [line for line in lines if "pendant" in line.lower()]
        self.assertEqual(pendant_lines, [])

    def test_short_packets_and_bad_hex_skipped_without_crashing(self):
        rows = [
            {"timestamp_utc": "2026-11-08T00:00:01Z", "raw_packet_hex": _make_packet_hex(0x40)},
            {"timestamp_utc": "2026-11-08T00:00:02Z", "raw_packet_hex": "000102"},  # 3 bytes, too short
            {"timestamp_utc": "2026-11-08T00:00:03Z", "raw_packet_hex": "not_hex_data"},  # bad hex
            {"timestamp_utc": "2026-11-08T00:00:04Z", "raw_packet_hex": _make_packet_hex(0x21)},
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "robustness.jsonl"
            _write_jsonl(path, rows)
            lines, _ = lab_summary.summarize(path, phone={})

        pendant_lines = [line for line in lines if "pendant:" in line]
        self.assertEqual(len(pendant_lines), 1)
        self.assertIn("0x21", pendant_lines[0])
        self.assertIn("Abort", pendant_lines[0])
        self.assertIn("pressed", pendant_lines[0])

    def test_in_home_key_and_nested_state(self):
        # 0xA1: Abort pressed with in_Home (bit 7 set)
        rows = [
            {
                "type": "sample",
                "timestamp_utc": "2026-11-08T00:00:01Z",
                "state": {"raw_packet_hex": _make_packet_hex(0x40)},
            },
            {
                "type": "sample",
                "timestamp_utc": "2026-11-08T00:00:02Z",
                "state": {"raw_packet_hex": _make_packet_hex(0xA1)},
            },
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "nested.jsonl"
            _write_jsonl(path, rows)
            lines, _ = lab_summary.summarize(path, phone={})

        pendant_lines = [line for line in lines if "pendant:" in line]
        self.assertEqual(len(pendant_lines), 1)
        self.assertIn("0xa1", pendant_lines[0])
        self.assertIn("Abort", pendant_lines[0])
        self.assertIn("pressed", pendant_lines[0])
        self.assertIn("in-Home", pendant_lines[0])


if __name__ == "__main__":
    unittest.main()
