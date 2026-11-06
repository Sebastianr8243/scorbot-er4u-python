"""One count frame on the legacy path (docs/requirements/count-frame.md).

The controller reports each axis as raw24 - 0x7FFFFF and reads each OUT setpoint slot as a signed
32-bit count (docs/protocol/VENDOR_DLL_PROTOCOL.md sections 3 and 6). The fake here does the same:
it reports its counts that way and moves each axis straight to the slot's signed 32-bit value. So a
message that echoes a joint one count low moves it one count. Fake endpoints only; no USB, nothing
moves. Unverified on the arm.
"""

from importlib.util import find_spec
import queue
import threading
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

    # CT10
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
    """``RobotState.encoder_counts``: the count's low 16 bits (CT13)."""
    return count & 0xFFFF


def raw_low(count):
    """The reply's raw low word for a count, which the legacy pair and the codec carry."""
    return (count + ZERO24) & 0xFFFF


class CountRangeTests(unittest.TestCase):
    # CT06
    def test_decode_state_reads_every_24_bit_count(self):
        from scorbot.state import decode_state
        for count in EDGES:
            with self.subTest(count=count):
                state = decode_state(bytes(reply_for([count] * 6)), connected=True, enabled=True,
                                     homed=False, fault=None)
                self.assertEqual(set(state.signed_encoder_counts.values()), {count})
                self.assertEqual(set(state.encoder_counts.values()), {low_word(count)})
                self.assertEqual(set(state.encoder_sign_bytes.values()), {(count + ZERO24) >> 16})

    # CT09
    def test_count_deltas_are_exact_across_every_top_byte(self):
        from scorbot.calibration import signed_count_delta
        for origin in EDGES:
            for delta in (-32766, -142, -1, 0, 1, 142, 32766):
                if -ZERO24 <= origin + delta <= 0x800000:
                    with self.subTest(origin=origin, delta=delta):
                        self.assertEqual(signed_count_delta(low_word(origin + delta), low_word(origin)), delta)

    # CT12
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

    # CT11
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

    # CT07
    def test_holds_and_echoes_round_trip_every_24_bit_count(self):
        for counts in self.batches():
            with self.subTest(counts=counts):
                buffer = bytearray(reply_for(counts))
                lows = [raw_low(c) for c in counts]
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

    # CT07, the codec side
    def test_the_codec_writes_every_24_bit_count(self):
        from scorbot.transport import codec
        for count in EDGES:
            with self.subTest(count=count):
                top = (count + ZERO24) >> 16
                self.assertEqual(int.from_bytes(codec.encode_count(raw_low(count), top), "little", signed=True),
                                 count)

    # CT08
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


class FlakyArm(Arm):
    """An Arm whose reply number ``bad`` reports ``joint``'s top byte as ``top``: one corrupt reply."""

    def __init__(self, counts, *, bad, joint, top):
        super().__init__(counts)
        self.reads, self.bad, self.joint, self.top = 0, bad, joint, top

    def read(self, buffer, timeout):
        from scorbot.simulated import encode_packet
        self.reads += 1
        if self.reads != self.bad:
            return super().read(buffer, timeout)
        reply = encode_packet(dict(zip(JOINTS, self.counts)), sign_override={self.joint: self.top})
        buffer[:64] = reply_for(self.counts)
        offset = ENCODER_OFFSETS[JOINTS.index(self.joint)]
        buffer[offset:offset + 3] = reply[offset:offset + 3]
        return len(buffer)


def answer(arm, buffer, libdef):
    """One message and its reply through ``libdef.check``, holding the fake arm where it is."""
    region = b"".join(count.to_bytes(4, "little", signed=True) for count in arm.counts)
    libdef.check(bytes(12) + region + bytes(28), arm, arm, buffer, 0, 0)


class Script:
    """A sync queue that hands out these sequence bytes in turn and ignores what is put back."""

    def __init__(self, items):
        self.items = list(items)

    def get(self):
        return self.items.pop(0)

    def put(self, item):
        pass


@unittest.skipUnless(find_spec("usb"), "PyUSB is needed to import the legacy modules")
class CorruptTopByteTests(unittest.TestCase):
    """CT16: one reply with a corrupt top byte never becomes a setpoint 65536 counts away."""

    START = 1000          # raw 0x8003E7: top byte 0x80; 0x81 reads 66536 and 0x7F reads -64536

    @classmethod
    def setUpClass(cls):
        robot = Scorbot()
        robot._legacy("conf").setup()
        cls.comm = robot._legacy("libcomm")
        cls.sync = robot._legacy("libsync")
        cls.libdef = robot._legacy("libdef")

    def setUp(self):
        sleeping = mock.patch("time.sleep")
        sleeping.start()
        self.addCleanup(sleeping.stop)
        self.addCleanup(setattr, self.libdef, "ultimo_hold", None)
        self.libdef.reset_count_guard()
        self.addCleanup(self.libdef.reset_count_guard)

    def assert_only_true_counts_sent(self, arm):
        for packet in arm.packets:
            self.assertEqual(slots(packet), [self.START] * 6)

    def cases(self):
        for joint in ("base", "shoulder", "wrist_motor_2", "gripper"):
            for top in (0x7F, 0x81):
                yield joint, top

    def test_a_stop_after_a_corrupt_reply_refuses_and_names_the_motor(self):
        for joint, top in self.cases():
            with self.subTest(joint=joint, top=top):
                self.libdef.reset_count_guard()
                arm = FlakyArm([self.START] * 6, bad=2, joint=joint, top=top)
                buffer = bytearray(reply_for(arm.counts))
                answer(arm, buffer, self.libdef)
                answer(arm, buffer, self.libdef)                  # the corrupt reply
                arm.packets.clear()
                with self.assertRaisesRegex(self.libdef.CountJumpError, joint):
                    self.libdef.stopMov(1, [0] * 6, arm, arm, buffer, 0, 0)
                self.assertEqual(arm.packets, [], "nothing is sent after the corrupt reply")
                self.assertIsNone(self.libdef.ultimo_hold, "no hold is left for the idle messages")

    def test_the_idle_loop_refuses_to_echo_a_corrupt_reply(self):
        for joint, top in self.cases():
            with self.subTest(joint=joint, top=top):
                self.libdef.reset_count_guard()
                arm = FlakyArm([self.START] * 6, bad=2, joint=joint, top=top)
                buffer = bytearray(reply_for(arm.counts))
                media = queue.Queue()
                media.put([raw_low(self.START)] * 6)
                with self.assertRaisesRegex(self.libdef.CountJumpError, joint):
                    self.sync.syncro(Script([5, 6, 7, 8]), media, arm, arm, buffer)
                self.assertEqual(len(arm.packets), 2)
                self.assert_only_true_counts_sent(arm)

    def test_a_jog_after_a_corrupt_reply_sends_no_setpoint_from_it(self):
        # The moving joint's own top byte and a held joint's (the echo).
        for mover, order, joint in (("move_hips", 5, "base"), ("move_hips", 5, "elbow"),
                                    ("move_wrist", 10, "wrist_motor_2"), ("move_shoulder", 6, "gripper")):
            for bad in (4,):          # a later jog reply; the open reply is covered by CT20
                with self.subTest(mover=mover, joint=joint, bad=bad):
                    self.libdef.reset_count_guard()
                    arm = FlakyArm([self.START] * 6, bad=bad + 1, joint=joint, top=0x81)
                    buffer = bytearray(reply_for(arm.counts))
                    answer(arm, buffer, self.libdef)       # the reply before the jog
                    arm.packets.clear()
                    reads, results = queue.Queue(), queue.Queue()
                    reads.put([raw_low(self.START)] * 6)
                    # SD01: the corrupt reply no longer kills the worker; the jog ends in motors-off, result 1.
                    getattr(self.comm, mover)(1, arm, arm, buffer, order, reads, results, 10, 1.0)
                    self.assertIn(1, list(results.queue))
                    arm.packets[:] = arm.packets[:bad]
                    self.assertEqual(len(arm.packets), bad, "nothing is sent after the corrupt reply")
                    for packet in arm.packets:
                        self.assertLess(abs(slots(packet)[JOINTS.index(joint)] - self.START), 1000, joint)

    # CT20
    def test_a_jog_with_a_corrupt_open_reply_stops_at_trusted_counts(self):
        for mover, order, _ in MOVERS:
            for joint in JOINTS:             # moving and held joints, including the gripper
                for top in (0x7F, 0x81):     # a legal legacy sign, and a sign outside the pair
                    with self.subTest(mover=mover, order=order, joint=joint, top=top):
                        self.libdef.reset_count_guard()
                        counts = [self.START + 100 * i for i in range(6)]
                        arm = FlakyArm(counts, bad=2, joint=joint, top=top)
                        buffer = bytearray(reply_for(counts))
                        answer(arm, buffer, self.libdef)       # seed the last trusted counts
                        arm.packets.clear()
                        reads, results = queue.Queue(), queue.Queue()
                        reads.put([raw_low(c) for c in counts])
                        sequence = getattr(self.comm, mover)(1, arm, arm, buffer, order,
                                                             reads, results, 10, 1.0)
                        self.assertEqual([p[4] for p in arm.packets], [OPEN, OPEN, 0x4F, 0x73, 0x42])
                        self.assertEqual([p[0] for p in arm.packets], [2, 3, 4, 5, 6])
                        self.assertEqual(sequence, 6, "the sequence is handed back")
                        self.assertEqual([slots(p) for p in arm.packets], [counts] * 5)
                        self.assertEqual(held(self.libdef.ultimo_hold), counts)
                        self.assertEqual(list(results.queue), [1])
                        self.assertEqual(list(reads.queue), [[raw_low(c) for c in counts]])

    def test_the_worker_survives_a_corrupt_open_reply(self):
        arm = FlakyArm([self.START] * 6, bad=2, joint="elbow", top=0x81)
        buffer = bytearray(reply_for(arm.counts))
        answer(arm, buffer, self.libdef)
        arm.packets.clear()
        syncs, orders, reads, results = (queue.Queue() for _ in range(4))
        syncs.put(1)
        reads.put([raw_low(self.START)] * 6)
        errors = []

        def run():
            try:
                self.comm.execute(syncs, orders, reads, arm, arm, buffer, results)
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        try:
            orders.put([5, 10, 1.0])
            self.assertEqual(results.get(timeout=0.5), 1)
            self.assertEqual(results.get(timeout=0.5), self.comm.DONE)
            self.assertTrue(worker.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(syncs.get_nowait(), self.comm.MAX_COUNT)
            self.assertEqual(syncs.get_nowait(), 6)
            self.assertEqual(reads.qsize(), 1)
            self.assertEqual([p[4] for p in arm.packets], [OPEN, OPEN, 0x4F, 0x73, 0x42])
            self.assert_only_true_counts_sent(arm)
        finally:
            syncs.put(6)
            orders.put([self.comm.EXIT, 1, 1])
            worker.join(timeout=0.5)
        self.assertFalse(worker.is_alive(), "the worker exits and is joined")
        self.assertEqual(errors, [])
        self.assertEqual(results.get_nowait(), self.comm.DONE)

    def test_the_trusted_stop_freezes_counts_while_replies_keep_changing(self):
        class DriftingArm(FlakyArm):
            def read(self, buffer, timeout):
                if self.reads >= 2:          # the replies during the stop drift five counts
                    self.counts = [c + 5 for c in self.counts]
                return super().read(buffer, timeout)

        counts = [0, -1, 1, -65535, 65536, -0x7FFFFF]
        arm = DriftingArm(counts, bad=2, joint="gripper", top=1)
        buffer = bytearray(reply_for(counts))
        answer(arm, buffer, self.libdef)
        answer(arm, buffer, self.libdef)      # corrupt: the guard must keep the trusted counts
        arm.packets.clear()
        sequence, _, media = self.libdef.stop_at_trusted(1, [0] * 6, arm, arm, buffer, 0, 0)
        self.assertEqual(sequence, 5)
        self.assertEqual([p[4] for p in arm.packets], [OPEN, 0x4F, 0x73, 0x42])
        self.assertEqual([slots(p) for p in arm.packets], [counts] * 4)
        self.assertEqual(held(self.libdef.ultimo_hold), counts)
        self.assertEqual(media, [raw_low(c) for c in counts])
        self.assertEqual(held(bytes.fromhex(self.libdef.get_hold(buffer))), [c + 5 for c in counts])

    def test_a_trusted_stop_without_trusted_counts_sends_nothing(self):
        arm = Arm([self.START] * 6)
        buffer = bytearray(reply_for(arm.counts))
        for seed_other_buffer in (False, True):
            with self.subTest(seed_other_buffer=seed_other_buffer):
                if seed_other_buffer:
                    answer(arm, bytearray(buffer), self.libdef)
                    arm.packets.clear()
                with self.assertRaisesRegex(self.libdef.CountJumpError, "No trusted counts"):
                    self.libdef.stop_at_trusted(1, [0] * 6, arm, arm, buffer, 0, 0)
                self.assertEqual(arm.packets, [])

    # CT17
    def test_a_jog_whose_joint_is_past_the_legacy_pair_stops_cleanly(self):
        # A real count past 65536 (top byte 0x81) is no corrupt reply, but the legacy jog cannot keep it
        # as a 128/127 pair (get_signo). It found that out after the open message was sent: it now sends
        # the stop at the counts it read and reports a failure, and the worker lives on.
        for mover, order, moving in (("move_hips", 5, 0), ("move_shoulder", 6, 1), ("move_elbow", 9, 2),
                                     ("move_wrist", 10, 3), ("move_wrist", 11, 4)):
            with self.subTest(mover=mover, moving=JOINTS[moving]):
                self.libdef.reset_count_guard()
                counts = [self.START] * 6
                counts[moving] = 70000
                arm = Arm(counts)
                buffer = bytearray(reply_for(counts))
                reads, results = queue.Queue(), queue.Queue()
                reads.put([raw_low(c) for c in counts])
                getattr(self.comm, mover)(1, arm, arm, buffer, order, reads, results, 10, 1.0)
                self.assertEqual(list(results.queue), [1])
                self.assertEqual([p[4] for p in arm.packets], [OPEN, OPEN, 0x4F, 0x73, 0x42],
                                 "the open message, then the stop sequence")
                self.assertEqual({tuple(slots(p)) for p in arm.packets}, {tuple(counts)})
                self.assertEqual(held(self.libdef.ultimo_hold), counts)
                self.assertEqual(reads.qsize(), 1, "the media is handed back for the idle loop")

    def test_one_corrupt_reply_does_not_poison_the_next(self):
        arm = FlakyArm([self.START] * 6, bad=2, joint="elbow", top=0x81)
        buffer = bytearray(reply_for(arm.counts))
        answer(arm, buffer, self.libdef)
        answer(arm, buffer, self.libdef)          # the corrupt one
        with self.assertRaisesRegex(self.libdef.CountJumpError, "elbow"):
            self.libdef.get_hold(buffer)
        answer(arm, buffer, self.libdef)          # good again: judged against the last good count
        self.assertEqual(held(bytes.fromhex(self.libdef.get_hold(buffer))), [self.START] * 6)

    def test_counts_crossing_a_top_byte_by_moving_are_not_refused(self):
        # A joint that really moves across 65535/65536 changes its top byte with its low word.
        buffer = bytearray(reply_for([65530] * 6))
        for count in range(65530, 65545, 3):
            arm = Arm([count] * 6)
            answer(arm, buffer, self.libdef)
            self.assertEqual(held(bytes.fromhex(self.libdef.get_hold(buffer))), [count] * 6)

    def test_a_new_connection_or_a_vendor_home_starts_the_guard_afresh(self):
        # The vendor home re-zeroes the counters (48): any jump is legal there.
        before = Arm([70000] * 6)
        buffer = bytearray(reply_for(before.counts))
        answer(before, buffer, self.libdef)
        arm = Arm([0] * 6)
        answer(arm, buffer, self.libdef)            # 70000 to 0 in one reply: refused
        with self.assertRaisesRegex(self.libdef.CountJumpError, "base"):
            self.libdef.get_hold(buffer)
        media = queue.Queue()
        media.put([0] * 6)
        self.comm.vendor_home_run(1, arm, arm, buffer, media, queue.Queue(), lambda reply, sequence: ("end",))
        answer(arm, buffer, self.libdef)
        self.assertEqual(held(bytes.fromhex(self.libdef.get_hold(buffer))), [0] * 6)
        answer(before, buffer, self.libdef)       # and back: refused again
        with self.assertRaises(self.libdef.CountJumpError):
            self.libdef.get_hold(buffer)
        names = ("send_pkt1", "send_wait", "send_pkt2", "send_pkt3")
        with mock.patch.multiple(self.sync, **{name: mock.Mock(return_value=1) for name in names}):
            self.sync.msg_start(arm, arm, buffer)                 # a new connection
        answer(before, buffer, self.libdef)
        self.assertEqual(held(bytes.fromhex(self.libdef.get_hold(buffer))), [70000] * 6)

class HomeReachTests(unittest.TestCase):
    """CT14: past 32766 counts from home a low word aliases; read the signed count or refuse."""

    def robot(self):
        from scorbot import SimulatedScorbot
        robot = SimulatedScorbot().__enter__()
        self.addCleanup(robot.__exit__, None, None, None)
        robot.enable()
        robot.vendor_home(operator_at_stop=True, jaws_empty=True, wrist_clear=True)
        return robot

    def test_a_stream_refuses_a_motor_past_the_home_reach_and_names_it(self):
        from scorbot import ScorbotError
        robot = self.robot()
        robot.sim.counts["wrist_motor_2"] = 40000      # e.g. turned there on the pendant
        queued = list(robot.sim.commands)
        for wrist in (False, True):                     # the pitch reads wrist_motor_2 either way
            with self.subTest(wrist=wrist),                     self.assertRaisesRegex(ScorbotError, "wrist_motor_2 is 40000 counts from home"):
                robot.start_stream(wrist=wrist)
        self.assertEqual(robot.sim.commands, queued)
        self.assertIsNone(robot._stream)
        robot.sim.counts["wrist_motor_2"] = 32766       # within reach, but ST01 still refuses it
        with self.assertRaisesRegex(ScorbotError, "wrist_motor_2 is 32766 counts from home"):
            robot.start_stream()
        robot.sim.counts["wrist_motor_2"] = 0
        robot.start_stream().close()

    def test_the_jog_vendor_check_reads_the_exact_count(self):
        from scorbot import nominal
        robot = self.robot()
        robot.sim.counts["wrist_motor_2"] = 40000      # as a low-word delta from home: -25536
        with mock.patch.object(nominal, "vendor_limit_excess", wraps=nominal.vendor_limit_excess) as excess:
            with self.assertRaisesRegex(ValueError, "vendor limit"):
                robot._refuse_past_joint_limit(robot.get_state(), {"wrist_motor_2": 10})
        self.assertEqual(excess.call_args_list[0].args[0]["wrist_motor_2"], 40000)

    def test_the_vendor_limits_keep_every_motor_within_the_home_reach(self):
        # So the SDK never commands a motor where it cannot read it back from home (vendor Home's zero).
        from scorbot import nominal
        from scorbot.calibration import MAX_COUNTS_FROM_HOME
        for motor, limit in nominal.VENDOR_ENCODER_SOFT_LIMITS.items():
            if motor != "wrist_motor_2":
                self.assertLessEqual(max(-limit.minimum, limit.maximum), MAX_COUNTS_FROM_HOME, motor)
        counts = {m: 0 for m in nominal.VENDOR_ENCODER_SOFT_LIMITS}
        for m1 in range(-15000, 15001, 1000):           # wrist_motor_1's EncLimit
            for m2 in range(MAX_COUNTS_FROM_HOME + 1, 1000001, 997):
                for sign in (1, -1):
                    pose = {**counts, "wrist_motor_1": m1, "wrist_motor_2": sign * m2}
                    self.assertIsNotNone(nominal.vendor_limit_problem(pose), pose)


if __name__ == "__main__":
    unittest.main()
