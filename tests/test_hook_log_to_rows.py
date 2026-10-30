"""scripts/hook_log_to_rows.py: turn the capture hook's log into the rows replay_vendor_home.py reads.

Synthetic lines only: no DLL, no USB. The replay refuses anything that is not OUT, IN, OUT, IN, so the
converter must drop the open line and any read that returned nothing, and keep the order.
"""

import json
import unittest

from scripts import hook_log_to_rows as conv

OUT_A, IN_A, OUT_B, IN_B = "aa" * 64, "bb" * 64, "cc" * 64, "dd" * 64


def line(**fields):
    return json.dumps(fields)


class ConvertTests(unittest.TestCase):
    def test_writes_become_out_and_reads_become_in_in_order(self):
        lines = [line(ev="open", path="\\\\.\\ERUSBDevice0", handle="0x1c"),
                 line(ev="W", len=64, ok=1, hex=OUT_A, t=0.1), line(ev="R", req=64, ok=1, got=64, hex=IN_A, t=0.2),
                 line(ev="W", len=64, ok=1, hex=OUT_B, t=0.3), line(ev="R", req=64, ok=1, got=64, hex=IN_B, t=0.4)]
        rows = conv.convert(lines)
        self.assertEqual([r["direction"] for r in rows], ["out", "in", "out", "in"])
        self.assertEqual([r["hex"] for r in rows], [OUT_A, IN_A, OUT_B, IN_B])
        self.assertEqual(rows[1]["t_s"], 0.2)

    def test_a_read_that_returned_nothing_is_dropped(self):
        lines = [line(ev="W", len=64, ok=1, hex=OUT_A, t=0.1), line(ev="R", req=64, ok=0, got=0, hex="", t=0.2),
                 line(ev="R", req=64, ok=1, got=64, hex=IN_A, t=0.3)]
        self.assertEqual([r["hex"] for r in conv.convert(lines)], [OUT_A, IN_A])

    def test_a_failed_write_is_dropped(self):
        lines = [line(ev="W", len=64, ok=0, hex=OUT_A, t=0.1), line(ev="W", len=64, ok=1, hex=OUT_B, t=0.2),
                 line(ev="R", req=64, ok=1, got=64, hex=IN_B, t=0.3)]
        self.assertEqual([r["hex"] for r in conv.convert(lines)], [OUT_B, IN_B])

    def test_a_packet_that_is_not_64_bytes_is_refused_not_guessed(self):
        with self.assertRaisesRegex(ValueError, "64"):
            conv.convert([line(ev="W", len=63, ok=1, hex="aa" * 63, t=0.1)])

    def test_blank_lines_and_other_events_are_ignored(self):
        self.assertEqual(conv.convert(["", line(ev="open", path="x", handle="0x1")]), [])


if __name__ == "__main__":
    unittest.main()
