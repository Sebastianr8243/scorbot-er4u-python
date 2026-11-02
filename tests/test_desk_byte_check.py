"""Stack 2's OUT messages against the lab USBC.dll's, byte for byte, with no arm and no USB.

The golden fixture (``fixtures/dll_lab_open_loop.json``, made by ``scripts/make_open_loop_fixture.py``) holds what
the lab DLL wrote to a fake controller on a named pipe: the handshake, control on, control off, every encoder
region and the sequence wrap. The Stack 2 side is the real ``Scorbot`` (legacy handshake and workers) against a
fake ``usb`` package, and the pure codec. This compares messages; the fake arm is our model, so it says nothing
about what the controller accepts or whether anything moves. Timing and the ``0D`` keep-alive count are pacing
and are not compared. Byte 0 is compared only for its wrap. Home is covered by ``test_vendor_home.py``.
"""

import json
import unittest
from pathlib import Path

from scorbot.state import JOINTS
from scorbot.transport import codec
from scripts import make_open_loop_fixture as reduce

try:
    from tests.desk_wire import record_session
except ImportError:                                     # plain `unittest discover -s tests`
    from desk_wire import record_session

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "dll_lab_open_loop.json").read_text(encoding="utf-8"))
DLL_STOP = FIXTURE["control_off"][:-2]  # the closing 42 FF 00 00, 4F FF 54 are only sent by disconnect


class DeskByteCheckTests(unittest.TestCase):
    def test_stack2_sends_the_dlls_messages_at_every_step(self):
        # One recording, one test: a session is about 5 s of busy threads, and pytest-xdist would otherwise
        # start one per worker and starve them all.
        calls = record_session()
        packets = [packet for call in calls.values() for packet in call]
        steps = {
            "packets are 64 bytes": (all(len(p) == 64 for p in packets), True),
            "handshake": (reduce.phases(calls["connect"])["initialization"], FIXTURE["initialization"]),
            "handshake odd header": (reduce.odd_headers(packets), FIXTURE["odd_headers"]),
            "handshake ends with control on": (reduce.phases(calls["connect"])["control_on"], FIXTURE["control_on"]),
            "enable is control on": (reduce.commands(calls["enable"]), FIXTURE["control_on"]),
            "disable is the stop group": (reduce.commands(calls["disable"]), DLL_STOP),
            # The DLL's close is its stop group, then 42 FF 00 00 and 4F FF 54 (LEDGER.md PL96).
            "disconnect is the DLL's close": (reduce.commands(calls["disconnect"]), FIXTURE["control_off"]),
        }
        for name, (got, want) in steps.items():
            with self.subTest(name):
                self.assertEqual(got, want)

    def test_every_region_the_dll_wrote_is_one_the_codec_builds(self):
        self.assertGreater(len(FIXTURE["regions"]), 100)
        for region_hex in FIXTURE["regions"]:
            region = bytes.fromhex(region_hex)
            joints = {}
            for index, joint in enumerate(JOINTS):
                field = region[4 * index:4 * index + 4]
                sign = {bytes(2): codec.SIGN_POSITIVE, bytes([255, 255]): codec.SIGN_NEGATIVE}.get(field[2:])
                self.assertIsNotNone(sign, f"{joint} sign word {field[2:].hex()} in {region_hex}")
                joints[joint] = (int.from_bytes(field[:2], "little"), sign)
            self.assertEqual(codec.encode_region(joints), region)

    def test_the_dll_wraps_the_sequence_to_zero_and_the_codec_to_one(self):
        self.assertEqual(FIXTURE["sequence_wrap_to"], 0)
        self.assertEqual(codec.next_sequence(255), 1)


if __name__ == "__main__":
    unittest.main()
