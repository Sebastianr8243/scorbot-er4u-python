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

    # CF01
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

    # CF03
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

    # CF02
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

    # CF10
    def test_a_jog_past_the_old_range_keeps_counting(self):
        # From inside -65535..65536 out past it: the target goes on by the jog, no 65536 cliff.
        for mover, order, start in (("move_hips", 5, 65500), ("move_hips", 4, -65500),
                                    ("move_elbow", 9, 65500), ("move_elbow", 8, -65500)):
            with self.subTest(mover=mover, order=order, start=start):
                packets, increments = self.stop_then_jog(mover, order, start)
                total = sum(abs(i) for i in increments)
                self.assertGreater(abs(start) + total, 65536, "the jog crosses out of the old range")
                moving = 0 if mover == "move_hips" else 2
                hold = held(self.libdef.ultimo_hold)
                self.assertEqual(hold[moving], start + (total if start > 0 else -total))
                for packet in packets:
                    self.assertLessEqual(abs(slots(packet)[moving] - start), total)


# The ends and seams of the 24-bit range: raw 0x000000, the 7E/7F, 7F/80 and 80/81 seams, raw 0xFFFFFF.
EDGES = (-ZERO24, -65537, -65536, -65535, -1, 0, 1, 65535, 65536, 65537, 0x800000)


def low_word(count):
    return (count + ZERO24) & 0xFFFF


class CountRangeTests(unittest.TestCase):
    # CF06
    def test_decode_state_reads_every_24_bit_count(self):
        from scorbot.state import decode_state
        for count in EDGES:
            with self.subTest(count=count):
                state = decode_state(bytes(reply_for([count] * 6)), connected=True, enabled=True,
                                     homed=False, fault=None)
                self.assertEqual(set(state.signed_encoder_counts.values()), {count})
                self.assertEqual(set(state.encoder_counts.values()), {low_word(count)})
                self.assertEqual(set(state.encoder_sign_bytes.values()), {(count + ZERO24) >> 16})

    # CF09
    def test_count_deltas_are_exact_across_every_top_byte(self):
        from scorbot.calibration import signed_count_delta
        for origin in EDGES:
            for delta in (-32766, -142, -1, 0, 1, 142, 32766):
                if -ZERO24 <= origin + delta <= 0x800000:
                    with self.subTest(origin=origin, delta=delta):
                        self.assertEqual(signed_count_delta(low_word(origin + delta), low_word(origin)), delta)

    # CF12
    def test_the_simulator_reports_every_24_bit_count(self):
        from scorbot.simulated import encode_packet
        from scorbot.state import decode_state
        for count in EDGES:
            with self.subTest(count=count):
                packet = encode_packet(dict.fromkeys(JOINTS, count))
                self.assertEqual(packet[19:22], (count + ZERO24).to_bytes(3, "little"))
                state = decode_state(packet, connected=True, enabled=True, homed=False, fault=None)
                self.assertEqual(state.signed_encoder_counts["base"], count)
        for count in (-ZERO24 - 1, 0x800001):
            with self.subTest(count=count), self.assertRaises(ValueError):
                encode_packet({"base": count})

    # CF11
    def test_a_legacy_jog_must_start_inside_the_sign_byte_range(self):
        # The legacy jog keeps the moving joint as a low word and the sign word of 128 or 127
        # (libdef.get_signo), so it can only start at -65535..65536; the SDK refuses it before sending.
        robot = Scorbot()
        for start in (-65535, 0, 65536):
            with self.subTest(start=start):
                plan = robot.preview_jog("base", 1.0, starting_signed_counts=dict.fromkeys(JOINTS, start))
                self.assertEqual(plan["starting_signed_counts"]["base"], start)
        for start in (-65536, 65537, 0x800000):
            with self.subTest(start=start), self.assertRaises(ValueError):
                robot.preview_jog("base", 1.0, starting_signed_counts=dict.fromkeys(JOINTS, start))


@unittest.skipUnless(find_spec("usb"), "PyUSB is needed to import the legacy modules")
class LegacyCountRangeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        robot = Scorbot()
        robot._legacy("conf").setup()
        cls.comm = robot._legacy("libcomm")
        cls.libdef = robot._legacy("libdef")

    def setUp(self):
        self.addCleanup(setattr, self.libdef, "ultimo_hold", None)

    def batches(self):
        for shift in range(len(EDGES)):
            rotated = EDGES[shift:] + EDGES[:shift]
            yield list(rotated[:6])

    # CF07
    def test_holds_and_echoes_round_trip_every_24_bit_count(self):
        for counts in self.batches():
            with self.subTest(counts=counts):
                buffer = bytearray(reply_for(counts))
                lows = [low_word(c) for c in counts]
                self.assertEqual(held(bytes.fromhex(self.libdef.get_encoder(buffer, lows))), counts)
                self.assertEqual(held(bytes.fromhex(self.libdef.get_hold(buffer))), counts)
                arm = Arm(counts)
                self.libdef.stopMov(1, [0] * 6, arm, arm, buffer, 0, 0)
                self.assertEqual(held(self.libdef.ultimo_hold), counts)
                self.assertEqual({tuple(slots(p)) for p in arm.packets}, {tuple(counts)})
                arm, media = Arm(counts), queue.Queue()
                media.put(lows)
                with mock.patch("time.sleep"):
                    self.comm.motors_on(1, arm, arm, bytearray(reply_for(counts)), media)
                self.assertEqual({tuple(slots(p)) for p in arm.packets}, {tuple(counts)})
                self.assertEqual(held(self.libdef.ultimo_hold), counts)

    # CF07, the codec side
    def test_the_codec_writes_every_24_bit_count(self):
        from scorbot.transport import codec
        for count in EDGES:
            with self.subTest(count=count):
                top = (count + ZERO24) >> 16
                self.assertEqual(int.from_bytes(codec.encode_count(low_word(count), top), "little", signed=True),
                                 count)

    # CF08
    def test_stream_setpoints_send_the_count_they_read(self):
        from scorbot.state import decode_state
        for count in EDGES:
            with self.subTest(count=count):
                state = decode_state(bytes(reply_for([count] * 6)), connected=True, enabled=True,
                                     homed=False, fault=None)
                field = self.libdef.setpointField(state.signed_encoder_counts["base"])
                self.assertEqual(int.from_bytes(bytes.fromhex(field), "little", signed=True), count)
        for count in (-ZERO24 - 1, 0x800001):
            with self.subTest(count=count), self.assertRaises(ValueError):
                self.libdef.setpointField(count)


if __name__ == "__main__":
    unittest.main()
