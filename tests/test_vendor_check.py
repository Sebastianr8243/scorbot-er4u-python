"""scripts/vendor_check.py against synthetic traces. No capture, no hardware."""

import contextlib
import io
import json
import os
import tempfile
import unittest

from scripts import vendor_check
from scripts.vendor_check import CONTRADICTED, MATCHES, NOT_SEEN, ZERO


def out(message_id, command=0x0D, mask=0, length=64):
    payload = bytearray(length)
    payload[0], payload[4], payload[5] = message_id, command, mask
    return {"direction": "out", "hex": bytes(payload).hex()}


def reply(message_id, command=0x0D, byte2=0, positions=None, length=64):
    payload = bytearray(length)
    payload[0], payload[1], payload[2] = message_id, command, byte2
    for axis in range(8):
        raw = (positions or {}).get(axis, ZERO)
        payload[19 + 5 * axis:22 + 5 * axis] = raw.to_bytes(3, "little")
    return {"direction": "in", "hex": bytes(payload).hex()}


def status(rows, check):
    return check(vendor_check._payloads(rows))["status"]


class VendorCheckTest(unittest.TestCase):
    def test_sizes(self):
        self.assertEqual(status([out(1), reply(1)], vendor_check.check_sizes), MATCHES)
        self.assertEqual(status([out(1, length=32), reply(1)], vendor_check.check_sizes),
                         CONTRADICTED)

    def test_commands(self):
        good = [out(1, 0x47), out(2, 0x4F, 0x3F), out(3, 0x0D)]
        self.assertEqual(status(good, vendor_check.check_commands), MATCHES)
        result = vendor_check.check_commands(vendor_check._payloads([*good, out(4, 0x99)]))
        self.assertEqual(result["status"], CONTRADICTED)
        self.assertIn("99", result["detail"])
        self.assertEqual(status([reply(1)], vendor_check.check_commands), NOT_SEEN)

    def test_echo_matches_with_a_lag(self):
        rows = []
        for message_id in range(1, 60):
            rows.append(out(message_id, 0x47 if message_id % 7 == 0 else 0x0D))
            if message_id > 3:   # the controller answers three messages behind
                behind = message_id - 3
                rows.append(reply(behind, 0x47 if behind % 7 == 0 else 0x0D))
        result = vendor_check.check_echo(vendor_check._payloads(rows))
        self.assertEqual(result["status"], MATCHES)
        self.assertIn("min 3, median 3, max 3", result["detail"])

    def test_echo_contradicted_when_the_reply_names_no_sent_message(self):
        rows = []
        for message_id in range(1, 60):
            rows += [out(message_id), reply(200)]
        self.assertEqual(status(rows, vendor_check.check_echo), CONTRADICTED)

    def test_echo_contradicted_when_the_command_differs(self):
        rows = []
        for message_id in range(1, 60):
            rows += [out(message_id, 0x0D), reply(message_id, 0x47)]
        self.assertEqual(status(rows, vendor_check.check_echo), CONTRADICTED)

    def test_emergency_needs_the_bit_to_change(self):
        self.assertEqual(status([reply(1), reply(2)], vendor_check.check_emergency), NOT_SEEN)
        pressed = [reply(1), reply(2, byte2=1), reply(3)]
        self.assertEqual(status(pressed, vendor_check.check_emergency), MATCHES)

    def test_id_wrap_reports_what_follows_255(self):
        result = vendor_check.check_id_wrap(vendor_check._payloads([out(254), out(255), out(1)]))
        self.assertEqual(result["status"], MATCHES)
        self.assertIn("255 -> 1", result["detail"])
        self.assertEqual(status([out(1), out(2)], vendor_check.check_id_wrap), NOT_SEEN)

    def test_position_crossing_zero_smoothly_matches(self):
        rows = [reply(1, positions={0: ZERO - 1}), reply(2, positions={0: ZERO}),
                reply(3, positions={0: ZERO + 1}), reply(4, positions={0: ZERO + 2})]
        self.assertEqual(status(rows, vendor_check.check_position), MATCHES)

    def test_position_jump_at_the_zero_is_contradicted(self):
        rows = [reply(1, positions={0: ZERO - 1}), reply(2, positions={0: ZERO + 60000})]
        self.assertEqual(status(rows, vendor_check.check_position), CONTRADICTED)

    def test_position_without_a_crossing_is_not_seen(self):
        rows = [reply(1, positions={0: ZERO + 5}), reply(2, positions={0: ZERO + 9})]
        self.assertEqual(status(rows, vendor_check.check_position), NOT_SEEN)

    def test_main_prints_a_report_and_exits_1_on_a_contradiction(self):
        def run(rows):
            handle, path = tempfile.mkstemp(suffix=".jsonl")
            os.close(handle)
            self.addCleanup(os.remove, path)
            with open(path, "w", encoding="utf-8") as stream:
                for row in rows:
                    stream.write(json.dumps(row) + "\n")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = vendor_check.main([path])
            return code, output.getvalue()

        code, text = run([out(1), reply(1)])
        self.assertEqual(code, 0)
        self.assertIn("not a safety verdict", text)
        code, _ = run([out(1, 0x99), reply(1, 0x99)])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
