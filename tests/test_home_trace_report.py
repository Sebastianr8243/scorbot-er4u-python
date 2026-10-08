"""scripts/home_trace_report.py: read a home search back from a controller event log.

Synthetic logs only: a shoulder whose position error grows until the controller flags it.
"""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from scorbot.simulated import encode_packet
from scorbot.state import COLLISION_OFFSET, ERROR_OFFSETS, JOINTS
from scripts import home_trace_report

MS = 1_000_000


def in_packet(counts, errors=None, switches=0, collision=0) -> str:
    packet = bytearray(encode_packet(counts, switch_bits=switches))
    for name, value in (errors or {}).items():
        offset = ERROR_OFFSETS[JOINTS.index(name)]
        packet[offset:offset + 2] = int(value).to_bytes(2, "little")
    packet[COLLISION_OFFSET] = collision
    return bytes(packet).hex()


def stalling_home(messages=10):
    """OUT/IN pairs 25 ms apart; the shoulder error grows 8 counts per message."""
    packets = []
    for k in range(messages):
        t = 1_000 * MS + k * 25 * MS
        last = k == messages - 1
        packets.append({"direction": "out", "host_monotonic_ns": t, "hex": "00" * 64})
        packets.append({"direction": "in", "host_monotonic_ns": t + 5 * MS, "hex": in_packet(
            {"shoulder": 10 * k}, errors={"shoulder": 8 * (k + 1)},
            switches=0, collision=0x02 if last else 0)})
    return {"event": "motion_trace", "joint": "home", "packets": packets, "dropped_packets": 0}


class HomeTraceReportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.log = Path(self._tmp.name) / "run.controller.jsonl"

    def write(self, *events):
        self.log.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")

    def run_report(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = home_trace_report.main([str(self.log)])
        return code, out.getvalue()

    def test_summary_names_the_stalled_joint_and_the_error_crossings(self):
        self.write({"event": "home_start"}, stalling_home(), {"event": "command_error"})
        code, text = self.run_report()
        self.assertEqual(code, 0)
        self.assertIn("messages: 10", text)
        self.assertRegex(text, r"shoulder\s+max error 80")
        self.assertRegex(text, r"first reached 40 at 100 ms")   # the legacy check is >= 40: 8 * 5 at k = 4
        self.assertRegex(text, r"collision flag set: shoulder at 225 ms")

    def test_travel_is_reported_as_net_and_as_total_path(self):
        self.write(stalling_home())
        _, text = self.run_report()
        self.assertRegex(text, r"shoulder\s+net 90 counts, path 90 counts")

    def test_a_joint_that_goes_out_and_back_shows_its_path_not_just_zero(self):
        row = stalling_home(3)
        out_and_back = [0, 40, 0]
        ins = [p for p in row["packets"] if p["direction"] == "in"]
        for packet, count in zip(ins, out_and_back):
            packet["hex"] = in_packet({"shoulder": count}, errors={"shoulder": 1})
        self.write(row)
        _, text = self.run_report()
        self.assertRegex(text, r"shoulder\s+net 0 counts, path 80 counts")

    def test_a_quiet_home_reports_no_collision(self):
        quiet = stalling_home()
        for packet in quiet["packets"]:
            if packet["direction"] == "in":
                raw = bytearray.fromhex(packet["hex"])
                raw[COLLISION_OFFSET] = 0
                packet["hex"] = bytes(raw).hex()
        self.write(quiet)
        _, text = self.run_report()
        self.assertIn("collision flag: never set", text)

    def test_a_log_with_no_home_trace_says_so_and_fails(self):
        self.write({"event": "motion_trace", "joint": "base", "packets": []})
        code, text = self.run_report()
        self.assertEqual(code, 1)
        self.assertIn("no home trace", text)

    def test_dropped_packets_are_reported_and_say_which_end(self):
        row = stalling_home()
        row["dropped_packets"] = 120
        row["kept"] = "last"
        self.write(row)
        _, text = self.run_report()
        self.assertIn("120 packets were dropped from the start of the search", text)

    def test_a_quiet_trace_with_dropped_packets_does_not_claim_the_flag_never_set(self):
        quiet = stalling_home()
        for packet in quiet["packets"]:
            if packet["direction"] == "in":
                raw = bytearray.fromhex(packet["hex"])
                raw[COLLISION_OFFSET] = 0
                packet["hex"] = bytes(raw).hex()
        quiet["dropped_packets"] = 50
        quiet["kept"] = "last"
        self.write(quiet)
        _, text = self.run_report()
        self.assertNotIn("never set", text)
        self.assertIn("not set in the packets kept (50 were dropped)", text)

    def test_the_table_has_one_line_per_in_packet(self):
        self.write(stalling_home(4))
        _, text = self.run_report()
        lines = [line for line in text.splitlines() if line.strip().startswith("t_ms") is False
                 and line[:1].isspace() is False and line[:1].isdigit()]
        self.assertEqual(len(lines), 4)


if __name__ == "__main__":
    unittest.main()
