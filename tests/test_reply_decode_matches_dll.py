"""Our IN-reply count decode against the lab USBC.dll's own (FUN_1000b130: (u32 at 19+5N & 0xFFFFFF) - 0x7FFFFF).

fixtures/dll_lab_xyz.json holds the DLL's GetCurrentPosition encoder array for replies that
tools/usbc_fake/pipe_server.py built as raw24 = count + 0x7FFFFF. Every row read back unchanged,
so these bytes and these counts are the DLL's transform.
"""
import json
import unittest
from pathlib import Path

from scorbot import state, vendor_home as vh
from scorbot.calibration import signed_count_delta

ROWS = json.loads((Path(__file__).parent / "fixtures" / "dll_lab_xyz.json").read_text(encoding="utf-8"))["rows"]


def reply(counts):
    packet = bytearray(64)
    for n, count in enumerate(counts):
        packet[19 + 5 * n:22 + 5 * n] = ((count + 0x7FFFFF) & 0xFFFFFF).to_bytes(3, "little")
    return bytes(packet)


class ReplyDecodeMatchesDll(unittest.TestCase):
    def test_vendor_home_decode_is_the_dlls(self):
        for row in ROWS:
            with self.subTest(counts=row["counts"]):
                self.assertEqual(list(vh.decode_reply(reply(row["counts"] + [0]), 0).counts[:5]), row["counts"])

    def test_zero_is_raw_7fffff(self):
        self.assertEqual(vh.decode_reply(reply([0] * 6), 0).counts, (0,) * 6)
        self.assertEqual(reply([0])[19:22], b"\xff\xff\x7f")

    def test_legacy_path_reads_one_low_on_the_positive_side_only(self):
        # PolicyArm feeds vendor_limit_problem signed_count_delta(state.encoder_counts, 0).
        for row in ROWS:
            decoded = state.decode_state(reply(row["counts"] + [0]), connected=True, enabled=None,
                                         homed=False, fault=None).encoder_counts
            for name, count in zip(state.JOINTS, row["counts"]):
                with self.subTest(motor=name, count=count):
                    self.assertEqual(signed_count_delta(decoded[name], 0), count - (count > 0))


if __name__ == "__main__":
    unittest.main()
