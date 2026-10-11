"""scripts/replay_vendor_home.py: feed a capture's real replies to the port and report where it differs.

A synthetic capture is made by running the port against the arm model and keeping each OUT and the
packet the model answered with. Replaying it must find no difference; a capture whose vendor-side
command is changed must be reported at exactly that message. Nothing here opens USB.
"""

import unittest

from scorbot import vendor_home as vh
from scripts import replay_vendor_home as rp
from tests.vendor_home_plant import Plant


def synthetic_rows(max_messages=400, cycle_s=0.016):
    """usb_trace-style rows (direction, hex, t_s) for the start of a home against the model."""
    plant = Plant()
    first_packet = plant.exchange(vh.encode_out(vh.Out(vh.NOP), 1))
    home = vh.vendor_home(vh.decode_reply(first_packet, 0.0))
    rows, now, sequence = [], 0.0, 1
    out = next(home)
    for _ in range(max_messages):
        sequence = sequence % 255 + 1
        now += cycle_s
        packet = vh.encode_out(out, sequence)
        reply = plant.exchange(packet)
        rows.append({"direction": "out", "hex": packet.hex(), "t_s": now})
        rows.append({"direction": "in", "hex": reply.hex(), "t_s": now + 0.0165})
        try:
            out = home.send(vh.decode_reply(reply, now))
        except StopIteration:
            break
    return [{"index": i, **row} for i, row in enumerate(rows)]


class ReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = synthetic_rows()

    def test_a_capture_made_by_the_port_replays_with_no_difference(self):
        report = rp.replay(self.rows)
        self.assertIsNone(report.first_difference)
        self.assertEqual(report.compared, len(self.rows) // 2)

    def test_a_changed_vendor_command_is_found_at_that_message(self):
        rows = [dict(r) for r in self.rows]
        target = next(i for i, r in enumerate(rows)
                      if r["direction"] == "out" and bytes.fromhex(r["hex"])[4] == vh.MOVE)
        packet = bytearray(bytes.fromhex(rows[target]["hex"]))
        packet[6] ^= 0x01
        rows[target]["hex"] = bytes(packet).hex()
        report = rp.replay(rows)
        self.assertEqual(report.first_difference.message, target // 2)
        self.assertEqual(report.first_difference.field, "arg16")

    def test_the_sequence_byte_and_setpoints_are_not_compared(self):
        rows = [dict(r) for r in self.rows]
        packet = bytearray(bytes.fromhex(rows[0]["hex"]))
        packet[0] = (packet[0] + 7) & 0xFF
        packet[12] ^= 0x55
        rows[0]["hex"] = bytes(packet).hex()
        self.assertIsNone(rp.replay(rows).first_difference)

    def test_rows_that_are_not_out_in_pairs_are_refused(self):
        rows = [r for r in self.rows if r["direction"] == "out"]
        with self.assertRaises(ValueError):
            rp.replay(rows)

    def test_the_report_names_the_command_on_each_side(self):
        rows = [dict(r) for r in self.rows]
        target = next(i for i, r in enumerate(rows)
                      if r["direction"] == "out" and bytes.fromhex(r["hex"])[4] == vh.MODE)
        packet = bytearray(bytes.fromhex(rows[target]["hex"]))
        packet[4] = vh.MOVE
        rows[target]["hex"] = bytes(packet).hex()
        diff = rp.replay(rows).first_difference
        self.assertEqual((diff.field, diff.vendor, diff.port), ("cmd", vh.MOVE, vh.MODE))
        self.assertIn("cmd", rp.format_report(rp.replay(rows)))


if __name__ == "__main__":
    unittest.main()
