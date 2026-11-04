"""The impact check: abs(signed error word) above the vendor's per-axis ImpactDetect is error 201.

USBC.dll reads each axis's error word (reply bytes 22-23 + 5N) as a signed 16-bit value, takes its absolute
value and faults when it is strictly above ImpactDetect (70 for the arm motors, 300 for the gripper, from
ER4AxN.ini), on every reply while control is on, idle included (docs/protocol/LEDGER.md PL98). Stack 2 used
the legacy ``>= 40`` with a decode that read small negatives as huge. Fake endpoints only; nothing moves.
"""

from importlib.util import find_spec
import queue
import threading
import time
import unittest
from unittest import mock

from scorbot import Scorbot, ScorbotError
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

    def idle_impact_race(self, queued_before_impact):
        robot = Scorbot(command_timeout=3)
        robot._legacy("conf").setup()
        comm, sync = robot._legacy("libcomm"), robot._legacy("libsync")
        controller = FollowingController()
        robot._device = object()       # fake endpoints below; never connect
        robot._enabled = True
        robot._sync.put(1)
        robot._reads.put([START] * len(JOINTS))
        buffer = bytearray(controller.reply)
        in_reply, attempted, impacted = threading.Event(), threading.Event(), threading.Event()
        orders, errors = [], []
        put = robot._commands.put
        sleep = time.sleep

        def enqueue(payload):
            orders.append(payload[0])
            if payload[0] == 6:
                if queued_before_impact:
                    put(payload)
                attempted.set()         # _command has already checked _fault
                if not queued_before_impact:
                    impacted.wait(3)
                    put(payload)
            else:
                put(payload)

        def idle_reply(reply):
            if not in_reply.is_set():
                in_reply.set()
                attempted.wait(3)       # sync still owns the sequence byte
                robot._idle_reply(reply_with_error("shoulder", 71))
                impacted.set()

        def jog():
            try:
                with robot._motion_lock:  # sync must not try to acquire this
                    robot._command([6, 10, 1.0])
            except ScorbotError as exc:
                errors.append(exc)

        robot._sync_thread = threading.Thread(target=sync.syncro,
            args=(robot._sync, robot._reads, controller, controller, buffer,
                  robot._command_waiting, robot._idle_setpoint, idle_reply), daemon=True)
        robot._command_thread = threading.Thread(target=robot._run_command_worker,
            args=(robot._sync, robot._commands, robot._reads, controller, controller,
                  buffer, robot._results, comm), daemon=True)
        caller = threading.Thread(target=jog, daemon=True)
        with mock.patch.object(robot._commands, "put", side_effect=enqueue), \
                mock.patch.object(comm, "move_shoulder", wraps=comm.move_shoulder) as move, \
                mock.patch("time.sleep", side_effect=lambda seconds: sleep(min(seconds, 0.001))):
            robot._sync_thread.start()
            robot._command_thread.start()
            try:
                self.assertTrue(in_reply.wait(3))
                caller.start()
                caller.join(6)
            finally:
                attempted.set()
                impacted.set()
                put([528, 1, 1])
                robot._command_thread.join(6)
                robot._sync_thread.join(6)
            self.assertFalse(caller.is_alive())
            self.assertFalse(robot._command_thread.is_alive())
            self.assertFalse(robot._sync_thread.is_alive())
            self.assertEqual(len(errors), 1)
            self.assertIn("201", robot._fault)
            self.assertTrue(robot._cancel_event.is_set())
            self.assertTrue(robot._stop_event.is_set())
            move.assert_not_called()
            self.assertEqual(orders, [6, 16], "idle impact queues motors-off only once")
            self.assertLessEqual(max(controller.targets(16)), START)

    @unittest.skipUnless(find_spec("usb"), "PyUSB is needed to import the legacy modules")
    def test_idle_impact_rejects_a_jog_that_passed_the_fault_check(self):
        for queued_before_impact in (True, False):
            with self.subTest(queued_before_impact=queued_before_impact):
                self.idle_impact_race(queued_before_impact)

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
