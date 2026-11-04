"""The hold round trip: the measured counts the idle setpoint holds are the counts the arm reported.

The controller reports each axis as a 24-bit value, count = raw24 - 0x7FFFFF, and reads each OUT setpoint
slot as a signed 32-bit count (docs/protocol/VENDOR_DLL_PROTOCOL.md sections 3 and 6). The stop and the
enable snapshot copy the last reply's counts into the hold, as USBC.dll's Stop and Control('&', 1) do
(LEDGER.md PL98). Echoing the reply's low word and sign word held every count one low (-1 as -2, +1 as 0).
Fake endpoints only; no USB, nothing moves. Unverified on the arm.
"""

from importlib.util import find_spec
import queue
import unittest
from unittest import mock

from scorbot import Scorbot
from scorbot.state import ENCODER_OFFSETS

ZERO24 = 0x7FFFFF
COUNTS = [*range(-2000, 2001), -65535, 65535]


def reply_for(counts):
    """A 64-byte IN reply whose six axes report these controller counts."""
    reply = bytearray([128] * 64)
    for count, offset in zip(counts, ENCODER_OFFSETS):
        reply[offset:offset + 3] = (count + ZERO24).to_bytes(3, "little")
    return reply


def held(region):
    """The six OUT setpoint slots as the controller reads them: signed 32-bit."""
    return [int.from_bytes(region[i:i + 4], "little", signed=True) for i in range(0, 24, 4)]


def batches():
    """Every count in COUNTS on every joint, six joints per reply."""
    for shift in range(6):
        rotated = COUNTS[shift:] + COUNTS[:shift]
        for start in range(0, len(rotated), 6):
            chunk = rotated[start:start + 6]
            yield chunk + [0] * (6 - len(chunk))


def media():
    smoothed = queue.Queue()
    smoothed.put([0] * 6)
    return smoothed


@unittest.skipUnless(find_spec("usb"), "PyUSB is needed to import the legacy modules")
class HoldRoundTripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        robot = Scorbot()
        robot._legacy("conf").setup()
        cls.comm = robot._legacy("libcomm")
        cls.libdef = robot._legacy("libdef")

    def setUp(self):
        self.sent = []
        patcher = mock.patch.object(self.libdef, "set_msg",
                                    side_effect=lambda cadena, *args: self.sent.append(bytes.fromhex(cadena)))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(setattr, self.libdef, "ultimo_hold", None)

    def test_minus_one_zero_and_plus_one_are_held_exactly(self):
        self.comm.motors_on(1, None, None, reply_for([-1, 0, 1, -2, 2, 0]), media())
        self.assertEqual(self.libdef.ultimo_hold[:12].hex(), "ffffffff" "00000000" "01000000")

    def test_the_enable_snapshot_holds_the_reported_counts(self):
        for counts in batches():
            self.comm.motors_on(1, None, None, reply_for(counts), media())
            self.assertEqual(held(self.libdef.ultimo_hold), counts)

    def test_the_stop_holds_and_sends_the_reported_counts(self):
        for counts in batches():
            self.sent.clear()
            self.libdef.stopMov(1, [0] * 6, None, None, reply_for(counts), 0, 0)
            self.assertEqual(held(self.libdef.ultimo_hold), counts)
            self.assertEqual(len(self.sent), 4)
            for packet in self.sent:
                self.assertEqual(held(packet[12:36]), counts)


if __name__ == "__main__":
    unittest.main()
