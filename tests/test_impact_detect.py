"""The impact check: abs(signed error word) above the vendor's per-axis ImpactDetect is error 201.

USBC.dll reads each axis's error word (reply bytes 22-23 + 5N) as a signed 16-bit value, takes its absolute
value and faults when it is strictly above ImpactDetect (70 for the arm motors, 300 for the gripper, from
ER4AxN.ini), on every reply while control is on, idle included (docs/protocol/LEDGER.md PL98). Stack 2 used
the legacy ``>= 40`` with a decode that read small negatives as huge. Fake endpoints only; nothing moves.
"""

from importlib.util import find_spec
import queue
import threading
import unittest
from unittest import mock

from scorbot import Scorbot
from scorbot.state import ERROR_OFFSETS, IMPACT_DETECT_COUNTS, JOINTS, impact_motors, signed_error_word

try:
    from tests.test_software_stop import START, FollowingController
except ImportError:      # discovered with -s tests: the folder itself is on sys.path
    from test_software_stop import START, FollowingController

MOVERS = (("move_hips", 5, 0), ("move_shoulder", 6, 1), ("move_elbow", 9, 2), ("move_wrist", 12, 3),
          ("move_wrist", 12, 4))
STOPPED = 14


def reply_with_error(joint: str, raw: int) -> bytes:
    reply = bytearray(64)
    reply[1] = 13
    for offset in (19, 24, 29, 34, 39, 44):
        reply[offset + 2] = 128
    offset = ERROR_OFFSETS[JOINTS.index(joint)]
    reply[offset:offset + 2] = raw.to_bytes(2, "little")
    return bytes(reply)


class DecodeTests(unittest.TestCase):
    def test_the_error_word_is_signed_16_bit(self):
        self.assertEqual(signed_error_word(0xFFBA), -70)
        self.assertEqual(signed_error_word(0xFFB9), -71)
        self.assertEqual(signed_error_word(0x8000), -32768)
        self.assertEqual(signed_error_word(0x7FFF), 32767)
        self.assertEqual(signed_error_word(70), 70)

    def test_strictly_above_impact_detect_both_signs(self):
        for raw, hit in ((0xFFBA, False), (0xFFB9, True), (70, False), (71, True)):
            with self.subTest(raw=hex(raw)):
                self.assertEqual(impact_motors(reply_with_error("shoulder", raw)), ["shoulder"] if hit else [])
        self.assertEqual(impact_motors(reply_with_error("gripper", 300)), [])
        self.assertEqual(impact_motors(reply_with_error("gripper", 301)), ["gripper"])

    def test_the_vendor_values(self):
        self.assertEqual(IMPACT_DETECT_COUNTS, {"base": 70, "shoulder": 70, "elbow": 70, "wrist_motor_1": 70,
                                                "wrist_motor_2": 70, "gripper": 300})


@unittest.skipUnless(find_spec("usb"), "PyUSB is needed to import the legacy modules")
class LegacyImpactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        robot = Scorbot()
        robot._legacy("conf").setup()
        cls.comm = robot._legacy("libcomm")
        cls.libdef = robot._legacy("libdef")

    def setUp(self):
        sleeping = mock.patch("time.sleep")
        sleeping.start()
        self.addCleanup(sleeping.stop)

    def test_the_legacy_table_matches_the_sdk_constant(self):
        self.assertEqual(self.libdef.IMPACT_DETECT, [IMPACT_DETECT_COUNTS[j] for j in JOINTS])

    def test_legacy_decode_and_compare(self):
        for raw, value, hit in ((0xFFBA, -70, False), (0xFFB9, -71, True), (70, 70, False), (71, 71, True)):
            with self.subTest(raw=hex(raw)):
                buffer = bytearray(64)
                buffer[27:29] = raw.to_bytes(2, "little")
                word = self.libdef.getErrorWord(buffer, 27)
                self.assertEqual(word, value)
                self.assertEqual(self.libdef.impacto(word, 1), hit)
        self.assertFalse(self.libdef.impacto(300, 5))
        self.assertTrue(self.libdef.impacto(-301, 5))

    def jog(self, mover, order, error_joint, word):
        controller = FollowingController(threading.Event(), rate=3, error_at=(6, error_joint))
        controller.ERROR_WORD = word
        reads, results = queue.Queue(), queue.Queue()
        reads.put([START] * len(JOINTS))
        getattr(self.comm, mover)(1, controller, controller, bytearray(controller.reply), order, reads, results,
                                  10, 1.0, controller.stop_event)
        return list(results.queue)

    def test_jogs_fault_only_above_impact_detect_on_either_sign(self):
        for mover, order, joint in MOVERS:
            for word, faults in ((0xFFBA, False), (0xFFB9, True), (70, False), (71, True)):
                with self.subTest(mover=mover, joint=joint, word=hex(word)):
                    self.assertEqual(self.jog(mover, order, joint, word), [1] if faults else [])


class IdleImpactTests(unittest.TestCase):
    def robot(self, *, enabled=True):
        robot = Scorbot()
        robot._enabled = enabled
        patcher = mock.patch.object(robot, "_link_alive", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        return robot

    def queued(self, robot):
        return list(robot._commands.queue)

    def test_an_idle_shoulder_error_of_71_latches_201_and_queues_motors_off(self):
        for raw in (71, 0xFFB9):
            with self.subTest(raw=hex(raw)):
                robot = self.robot()
                robot._idle_hold = b"\x00" * 24
                robot._idle_reply(reply_with_error("shoulder", raw))
                self.assertIn("201", robot._fault)
                self.assertIn("shoulder", robot._fault)
                self.assertEqual(self.queued(robot), [[16, 1, 1]])
                self.assertIsNone(robot._enabled, "motor state is unverified after the latch")
                self.assertIsNone(robot._idle_setpoint())
                robot._idle_reply(reply_with_error("shoulder", raw))
                self.assertEqual(self.queued(robot), [[16, 1, 1]], "latched once")

    def test_an_idle_error_of_70_does_nothing(self):
        for raw in (70, 0xFFBA):
            with self.subTest(raw=hex(raw)):
                robot = self.robot()
                robot._idle_reply(reply_with_error("shoulder", raw))
                self.assertIsNone(robot._fault)
                self.assertEqual(self.queued(robot), [])

    def test_motors_off_or_a_large_gripper_error_is_not_checked(self):
        robot = self.robot(enabled=False)
        robot._idle_reply(reply_with_error("shoulder", 500))
        self.assertIsNone(robot._fault)
        robot = self.robot()
        robot._idle_reply(reply_with_error("gripper", 1000))   # a grasp; Stack 2 switches gripper control off
        self.assertIsNone(robot._fault)
        self.assertEqual(self.queued(robot), [])

    def test_a_short_reply_is_ignored(self):
        robot = self.robot()
        robot._idle_reply(b"\x00" * 10)
        self.assertIsNone(robot._fault)


class SyncroReplyHookTests(unittest.TestCase):
    def test_syncro_passes_each_idle_reply_to_the_hook(self):
        sync = Scorbot()._legacy("libsync")
        exit_code = sync.conf.readData("general", "EXIT")
        cola_sync, cola_read = queue.Queue(), queue.Queue()
        cola_sync.put(5)
        cola_read.put([0])
        buffer = bytearray(64)
        seen = []

        def send(*_args):
            buffer[1] = 99
            cola_sync.put(exit_code)

        with mock.patch.object(sync, "libdef") as libdef, mock.patch.object(sync, "libhex"):
            libdef.set_msg.side_effect = send
            sync.syncro(cola_sync, cola_read, None, None, buffer, None, None, seen.append)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][1], 99)
        self.assertIsInstance(seen[0], bytes)


if __name__ == "__main__":
    unittest.main()
