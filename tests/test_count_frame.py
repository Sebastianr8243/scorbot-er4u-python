"""One count frame on the legacy path (docs/requirements/count-frame.md).

The controller reports each axis as raw24 - 0x7FFFFF and reads each OUT setpoint slot as a signed
32-bit count (docs/protocol/VENDOR_DLL_PROTOCOL.md sections 3 and 6). The fake here does the same:
it reports its counts that way and moves each axis straight to the slot's signed 32-bit value. So a
message that echoes a joint one count low moves it one count. Fake endpoints only; no USB, nothing
moves. Unverified on the arm.
"""

from importlib.util import find_spec
import queue
import unittest
from unittest import mock

from scorbot import Scorbot
from scorbot.state import ENCODER_OFFSETS, JOINTS

ZERO24 = 0x7FFFFF
OPEN = 0x47
# (legacy mover, order, the joints it moves): both directions, so a jog from near zero crosses it
# both ways (libdef.suma and resta)
MOVERS = (("move_hips", 5, (0,)), ("move_hips", 4, (0,)), ("move_shoulder", 6, (1,)),
          ("move_shoulder", 7, (1,)), ("move_elbow", 9, (2,)), ("move_elbow", 8, (2,)),
          ("move_wrist", 10, (3, 4)), ("move_wrist", 11, (3, 4)), ("move_wrist", 12, (3, 4)),
          ("move_wrist", 13, (3, 4)))
# Counts on both sides of zero, on both sides of the sign-byte seam, away from the wrap a jog can reach.
STARTS = (-30000, -1001, -2, 0, 1, 2, 1000, 30000)


def reply_for(counts):
    """A 64-byte IN reply whose six axes report these controller counts."""
    reply = bytearray([128] * 64)
    reply[1] = 13
    reply[59] = 0
    for offset in ENCODER_OFFSETS:
        reply[offset + 3:offset + 5] = b"\x00\x00"      # error words: no impact
    for count, offset in zip(counts, ENCODER_OFFSETS):
        reply[offset:offset + 3] = (count + ZERO24).to_bytes(3, "little")
    return reply


def held(region):
    """Six setpoint slots as the controller reads them: signed 32-bit."""
    return [int.from_bytes(region[4 * i:4 * i + 4], "little", signed=True) for i in range(6)]


def slots(packet):
    """The six setpoint slots of an OUT packet (offsets 12-35)."""
    return held(packet[12:36])


class Arm:
    """Fake endpoints: each OUT slot is the axis's new count; the reply reports the counts."""

    def __init__(self, counts):
        self.counts = list(counts)
        self.packets = []

    def write(self, data, timeout):
        data = bytes(data)
        self.packets.append(data)
        self.counts = slots(data)
        return len(data)

    def read(self, buffer, timeout):
        buffer[:64] = reply_for(self.counts)
        return len(buffer)


@unittest.skipUnless(find_spec("usb"), "PyUSB is needed to import the legacy modules")
class CountFrameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        robot = Scorbot()
        robot._legacy("conf").setup()
        cls.comm = robot._legacy("libcomm")
        cls.libdef = robot._legacy("libdef")
        cls.profile = robot._legacy("motion_profile")

    def setUp(self):
        sleeping = mock.patch("time.sleep")
        sleeping.start()
        self.addCleanup(sleeping.stop)
        self.addCleanup(setattr, self.libdef, "ultimo_hold", None)

    def stop_then_jog(self, mover, order, start):
        """A stop at `start` on every joint, then a jog: the OUT packets of the jog and the jog's increments."""
        arm = Arm([start] * 6)
        buffer = bytearray(reply_for(arm.counts))
        [b_1, buffer, media] = self.libdef.stopMov(1, [0] * 6, arm, arm, buffer, 0, 0)
        arm.packets.clear()
        reads, results = queue.Queue(), queue.Queue()
        reads.put(media)
        getattr(self.comm, mover)(b_1, arm, arm, buffer, order, reads, results, 10, 1.0)
        self.assertEqual(list(results.queue), [])
        return arm.packets, self.profile.plan_jog(order, 1.0, 10)["increments"]

    # CT01
    def test_after_a_stop_the_jog_keeps_every_joint_where_it_was(self):
        for mover, order, moving in MOVERS:
            for start in STARTS:
                with self.subTest(mover=mover, order=order, start=start):
                    packets, increments = self.stop_then_jog(mover, order, start)
                    self.assertEqual(packets[0][4], OPEN)
                    self.assertEqual(slots(packets[0]), [start] * 6, "the open message")
                    for packet in packets:
                        for joint in range(6):
                            if joint not in moving:
                                self.assertEqual(slots(packet)[joint], start, JOINTS[joint])
                    for joint in moving:
                        self.assertEqual(abs(slots(packets[1])[joint] - start), abs(increments[0]),
                                         "the first step moves by the first increment, not one more")

    # CT03
    def test_the_jog_ends_holding_the_start_plus_the_jog(self):
        for mover, order, moving in MOVERS:
            for start in STARTS:
                with self.subTest(mover=mover, order=order, start=start):
                    packets, increments = self.stop_then_jog(mover, order, start)
                    hold = held(self.libdef.ultimo_hold)
                    self.assertEqual(slots(packets[-1]), hold)
                    for joint in range(6):
                        if joint in moving:
                            self.assertEqual(abs(hold[joint] - start), sum(abs(i) for i in increments))
                        else:
                            self.assertEqual(hold[joint], start, JOINTS[joint])

    # CT02
    def test_every_motor_on_message_carries_the_reported_counts(self):
        for counts in ([-1, 0, 1, -2, 2, 0], [-65535, 65535, 65536, -30000, 30000, 7],
                       [s for s in STARTS[:6]]):
            with self.subTest(counts=counts):
                arm = Arm(counts)
                buffer = bytearray(reply_for(counts))
                media = queue.Queue()
                media.put([int.from_bytes(buffer[o:o + 2], "little") for o in ENCODER_OFFSETS])
                self.comm.motors_on(1, arm, arm, buffer, media)
                self.assertEqual(len(arm.packets), 7)
                for packet in arm.packets:
                    self.assertEqual(slots(packet), counts)


if __name__ == "__main__":
    unittest.main()
