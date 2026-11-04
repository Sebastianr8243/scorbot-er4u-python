"""The idle hold: after a motion ends, every idle 0D message carries one fixed setpoint, not the encoders.

USBC.dll's idle message carries its setpoint array, which nothing changes while no move runs: the arm holds
the last commanded target. A Stop copies the measured counts over it once, and Control('&', 1) does the
same before the motors go on (docs/protocol/LEDGER.md PL98). The legacy idle loop instead sent the smoothed
encoders every cycle, so the setpoint drifted with the arm. Fake endpoints only; no USB, nothing moves.
"""

from importlib.util import find_spec
import queue
import threading
import unittest
from unittest import mock

from scorbot import Scorbot, ScorbotError
from scorbot.calibration import Calibration, JointCalibration
from scorbot.lab.dry_link import DryLink, DryScorbot
from scorbot.state import JOINTS, decode_state

try:
    from tests.test_software_stop import START, FollowingController
except ImportError:      # discovered with -s tests: the folder itself is on sys.path
    from test_software_stop import START, FollowingController

STEP, MOTOR = 0x0D, 0x42
SHOULDER_JOG = [6, 10, 1.0]       # positive shoulder jog, as Scorbot.jog_joint queues it
SHOULDER_REGION = 16               # OUT bytes of the shoulder setpoint field


class _OrderDone(Exception):
    """Raised from the hold callback: ends execute after one order, before the EXIT tail."""


class DriftingController(FollowingController):
    """FollowingController whose arm, once ``drift`` is set, creeps that many counts per message on every joint.

    ``exit_after`` = (message count, queue): EXIT is queued on the sync queue once that many messages were written,
    so ``syncro`` returns after a known number of idle cycles.
    """

    drift = 0
    exit_after = None

    def write(self, data, timeout):
        written = super().write(data, timeout)
        if self.drift:
            self.position = [value + self.drift for value in self.position]
            self._report()
            self.reported[-1] = list(self.position)
        if self.exit_after is not None and len(self.packets) == self.exit_after[0]:
            self.exit_after[1].put(self.exit_after[2])
        return written


@unittest.skipUnless(find_spec("usb"), "PyUSB is needed to import the legacy modules")
class LegacyIdleHoldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        robot = Scorbot()
        robot._legacy("conf").setup()
        cls.comm = robot._legacy("libcomm")
        cls.sync = robot._legacy("libsync")
        cls.exit = cls.sync.conf.readData("general", "EXIT")

    def setUp(self):
        sleeping = mock.patch("time.sleep")    # which messages are sent is under test, not when
        sleeping.start()
        self.addCleanup(sleeping.stop)

    def run_order(self, controller, order, *, media=None, stop_event=None):
        """Run one order through execute; returns (hold the order left, media it put back)."""
        cola_sync, cola_orden, cola_read, results = queue.Queue(), queue.Queue(), queue.Queue(), queue.Queue()
        self.results = results
        cola_sync.put(1)
        cola_read.put(list(media or [START] * len(JOINTS)))
        cola_orden.put(order)
        holds = []

        def on_hold(hold):
            holds.append(hold)
            raise _OrderDone

        self.buffer = bytearray(controller.reply)
        with self.assertRaises(_OrderDone):
            self.comm.execute(cola_sync, cola_orden, cola_read, controller, controller, self.buffer, results,
                              None, stop_event, None, on_hold)
        self.assertEqual(len(holds), 1)
        return holds[0], cola_read.get_nowait()

    def idle(self, controller, media, hold, cycles=12):
        """Run syncro for ``cycles`` idle messages with the arm drifting 5 counts per message."""
        controller.rate, controller.drift = 0, 5
        first = len(controller.packets)
        cola_sync, cola_read = queue.Queue(), queue.Queue()
        cola_sync.put(9)
        cola_read.put(list(media))
        controller.exit_after = (first + cycles, cola_sync, self.exit)
        self.sync.syncro(cola_sync, cola_read, controller, controller, self.buffer, None, lambda: hold)
        packets = controller.packets[first:]
        self.assertEqual(len(packets), cycles)
        self.assertEqual({packet[4] for packet in packets}, {STEP})
        return packets

    def test_after_a_finished_jog_idle_holds_the_jog_target_while_the_arm_drifts(self):
        controller = DriftingController()
        hold, media = self.run_order(controller, SHOULDER_JOG)
        last = controller.packets[-1]
        self.assertEqual(last[4], MOTOR, "the jog ended with closeMov")
        self.assertEqual(hold, last[12:36], "the hold is the setpoint region closeMov sent last")
        target = int.from_bytes(hold[SHOULDER_REGION - 12:SHOULDER_REGION - 10], "little")
        self.assertEqual(target, max(controller.targets(SHOULDER_REGION)), "the shoulder field is the jog target")
        self.assertGreater(target, START)
        before = list(controller.position)
        for packet in self.idle(controller, media, hold):
            self.assertEqual(packet[12:36], hold)
        self.assertEqual([p - b for p, b in zip(controller.position, before)], [60] * len(JOINTS))

    def test_without_the_hold_the_idle_setpoint_follows_the_drift(self):
        # The control case: shows the drift test above can fail.
        controller = DriftingController()
        hold, media = self.run_order(controller, SHOULDER_JOG)
        fields = {bytes(packet[12:36]) for packet in self.idle(controller, media, None)}
        self.assertGreater(len(fields), 1)
        self.assertNotIn(hold, fields)

    def test_after_a_stop_idle_holds_the_counts_at_the_stop(self):
        stop_event = threading.Event()
        controller = DriftingController(stop_event, stop_after=6, rate=3)
        hold, media = self.run_order(controller, SHOULDER_JOG, stop_event=stop_event)
        held = controller.reported[5]       # the reply to the step that saw the stop
        self.assertNotEqual(held[1], START)
        # The fake reports L with sign byte 128: controller count L + 1, held as a signed 32-bit slot.
        self.assertEqual(hold, b"".join((value + 1).to_bytes(4, "little") for value in held))
        self.assertEqual(hold, controller.packets[-1][12:36])
        for packet in self.idle(controller, media, hold):
            self.assertEqual(packet[12:36], hold)

    def test_settle_exhaustion_stops_at_measured_counts_and_keeps_result_2(self):
        for order in (5, 6, 9, 12):
            with self.subTest(order=order):
                controller = DriftingController(rate=0)  # reports no impact but never reaches the target
                hold, media = self.run_order(controller, [order, 10, 1.0])
                self.assertEqual(list(self.results.queue), [2], "settle failure must not become STOPPED")
                measured = b"".join((START + 1).to_bytes(4, "little") for _ in JOINTS)   # sign 128: L + 1
                self.assertEqual(hold, measured, "never publish the unreachable jog target")
                self.assertEqual(media, [START] * len(JOINTS))
                self.assertEqual(controller.commands()[-4:], [0x47, 0x4F, 0x73, 0x42])
                for packet in controller.packets[-4:]:
                    self.assertEqual(packet[12:36], measured)
                for packet in self.idle(controller, media, hold):
                    self.assertEqual(packet[12:36], measured)

    def test_enable_snapshots_the_measured_counts_not_the_smoothed_ones(self):
        controller = DriftingController()
        stale = [START - 100] * len(JOINTS)      # the smoothed vector lags; the snapshot must not
        hold, _ = self.run_order(controller, [17, 1, 1], media=stale)
        self.assertEqual(hold, b"".join((START + 1).to_bytes(4, "little") for _ in JOINTS))   # sign 128: L + 1

    def test_motors_off_and_vendor_home_leave_no_hold(self):
        # Motors off: the idle setpoint follows the encoders as before. Vendor home: its 48 re-zeroes the
        # counters, so a hold carried across it would command the old absolute counts.
        for order in ([16, 1, 1], [22, lambda reply, following: ("end",), 0]):
            with self.subTest(order=order[0]):
                hold, _ = self.run_order(DriftingController(), order)
                self.assertIsNone(hold)

    def test_exit_clears_the_worker_hold_callback(self):
        controller = DriftingController()
        cola_sync, cola_orden, cola_read, results = queue.Queue(), queue.Queue(), queue.Queue(), queue.Queue()
        cola_sync.put(1)
        cola_read.put([START] * len(JOINTS))
        cola_orden.put([self.exit, 1, 1])
        self.comm.libdef.ultimo_hold = b"\x01" * 24
        holds = []
        self.comm.execute(cola_sync, cola_orden, cola_read, controller, controller, bytearray(controller.reply),
                          results, None, None, None, holds.append)
        self.assertEqual(holds, [None])
        self.assertIsNone(self.comm.libdef.ultimo_hold)

    def test_a_jog_hold_does_not_survive_into_the_next_order(self):
        controller = DriftingController()
        self.run_order(controller, SHOULDER_JOG)
        hold, _ = self.run_order(controller, [16, 1, 1])
        self.assertIsNone(hold)


class RobotIdleHoldTests(unittest.TestCase):
    @unittest.skipUnless(find_spec("usb"), "PyUSB is needed to import the legacy modules")
    def test_disconnect_clears_all_hold_caches_even_without_a_device(self):
        robot = Scorbot()
        robot._legacy("conf").setup()
        libdef = robot._legacy("libdef")
        robot._idle_hold, robot._vendor_joint_hold = b"\x01" * 24, b"\x02" * 32
        libdef.ultimo_hold = b"\x03" * 24
        robot.disconnect()
        self.assertIsNone(robot._idle_hold)
        self.assertIsNone(robot._vendor_joint_hold)
        self.assertIsNone(libdef.ultimo_hold)

    @unittest.skipUnless(find_spec("usb"), "PyUSB is needed to import the legacy modules")
    def test_reconnect_clears_holds_before_the_handshake_and_workers(self):
        robot = DryScorbot()
        robot._legacy("conf").setup()
        sync, libdef = robot._legacy("libsync"), robot._legacy("libdef")
        handshake, idle_setpoint = sync.msg_start, robot._idle_setpoint
        at_handshake, idle_holds = [], []

        def start(*args):
            at_handshake.append((robot._idle_hold, robot._vendor_joint_hold, libdef.ultimo_hold))
            return handshake(*args)

        def hold():
            value = idle_setpoint()
            idle_holds.append(value)
            return value

        with mock.patch("time.sleep"), mock.patch.object(sync, "msg_start", side_effect=start), \
                mock.patch.object(robot, "_idle_setpoint", side_effect=hold):
            for _ in range(2):
                # Seed each cache independently of EXIT's cleanup to exercise startup too.
                robot._idle_hold, robot._vendor_joint_hold = b"\x01" * 24, b"\x02" * 32
                libdef.ultimo_hold = b"\x03" * 24
                robot.dry_link = DryLink()
                try:
                    robot.connect()
                    robot._set_idle_hold(b"\x04" * 24)
                    robot._vendor_joint_hold = b"\x05" * 32
                    libdef.ultimo_hold = b"\x06" * 24
                finally:
                    robot.disconnect()
                self.assertIsNone(robot._idle_hold)
                self.assertIsNone(robot._vendor_joint_hold)
                self.assertIsNone(libdef.ultimo_hold)
        self.assertEqual(at_handshake, [(None, None, None)] * 2)
        self.assertTrue(idle_holds)
        self.assertNotIn(b"\x01" * 24, idle_holds)
        self.assertNotIn(b"\x02" * 32, idle_holds)

    def calibration_fault(self, *, jog):
        robot = Scorbot()
        robot._enabled = robot._homed = True
        robot._home_counts = dict.fromkeys(JOINTS, START)
        robot._vendor_joint_target = (0.0,) * 5
        robot._idle_hold, robot._vendor_joint_hold = b"\x01" * 24, b"\x02" * 32
        robot._prehome_travel["base"] = 1.0
        robot._calibration = Calibration("fake", "0" * 64, {"shoulder": JointCalibration(
            "shoulder", START, 0.0, 100.0, -10.0, 10.0, 5, 1.0, 1.0)})
        controller = FollowingController()
        controller.position[1] = START + 32767  # ambiguous encoder distance from the session home
        controller._report()
        state = decode_state(bytes(controller.reply), connected=True, enabled=True, homed=True, fault=None)
        with mock.patch.object(robot, "_motion_state", return_value=state):
            with self.assertRaisesRegex(ScorbotError, "Calibrated state invalid"):
                if jog:
                    robot.jog_joint("shoulder", 1.0)
                else:
                    robot.get_joint_angles()
        self.assertTrue(robot._commands.empty(), "calibration failed before any motion was queued")
        self.assertIsNone(robot._idle_setpoint())
        self.assertIsNone(robot._idle_hold)
        self.assertIsNone(robot._vendor_joint_hold)
        self.assertIsNone(robot._vendor_joint_target)
        self.assertIsNone(robot._enabled)
        self.assertFalse(robot._homed)
        self.assertIsNone(robot._home_counts)
        self.assertEqual(robot._prehome_travel, dict.fromkeys(robot._prehome_travel, 0.0))

    def test_jog_calibration_error_latches_the_fault_and_drops_holds(self):
        self.calibration_fault(jog=True)

    def test_angle_calibration_error_latches_the_fault_and_drops_holds(self):
        self.calibration_fault(jog=False)

    def test_faulted_idle_setpoint_ignores_a_late_hold(self):
        robot = Scorbot()
        robot._latch_fault("test")
        robot._idle_hold, robot._vendor_joint_hold = b"\x01" * 24, b"\x02" * 32
        self.assertIsNone(robot._idle_setpoint())

    def test_the_worker_hold_reaches_the_idle_hook_and_the_vendor_hold_wins(self):
        robot = Scorbot()
        seen = {}

        def execute(*args):
            seen["on_hold"] = args[-1]

        robot._run_command_worker(None, mock.Mock(execute=execute))
        self.assertIsNone(robot._idle_setpoint())
        seen["on_hold"](b"\x01" * 24)
        self.assertEqual(robot._idle_setpoint(), b"\x01" * 24)
        robot._vendor_joint_hold = b"\x02" * 32
        self.assertEqual(robot._idle_setpoint(), b"\x02" * 32)
        robot._latch_fault("test")
        self.assertIsNone(robot._idle_setpoint(), "a fault drops both holds: idle follows the encoders")
        seen["on_hold"](b"" * 24)       # a stream that faulted still ends in stopMov after the latch
        self.assertIsNone(robot._idle_setpoint(), "a latched session takes no new hold")

    def test_the_sync_worker_is_given_the_hold_and_reply_hooks(self):
        robot = Scorbot()
        seen = []
        robot._run_sync_worker(mock.Mock(syncro=lambda *args: seen.extend(args)), "a", "b")
        self.assertEqual(seen[:2], ["a", "b"])
        self.assertEqual(seen[2], robot._idle_setpoint)
        self.assertEqual(seen[3], robot._idle_reply)


if __name__ == "__main__":
    unittest.main()
