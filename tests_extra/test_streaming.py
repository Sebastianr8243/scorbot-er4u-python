"""The streaming driver wired in: the legacy loop against fake endpoints, then
``Scorbot.start_stream`` through the simulator. No USB, no hardware.

Requirement numbers are from
specs/2026-10-04-streaming-driver-requirements.md (in git history).
"""

from importlib.util import find_spec
import queue
import threading
import time
import unittest
from unittest import mock

from scorbot import MotionStopped, Scorbot, ScorbotError, StreamRefused
from scorbot.state import ENCODER_OFFSETS, JOINTS

try:
    from tests.sim_support import SimulatedRobotCase
except ImportError:      # discovered with -s tests: the folder itself is on sys.path
    from sim_support import SimulatedRobotCase

HAS_RUCKIG = find_spec("ruckig") is not None
OPEN, STEP, MODE, CONTROL_OFF, MOTOR = 0x47, 0x0D, 0x4F, 0x73, 0x42
STOPPED = 14
START = 1000
ZERO24 = 0x7FFFFF          # the controller count is raw24 - 0x7FFFFF
START_LOW = START - 1      # its low word under sign byte 128, the form the smoothed media keeps


class Controller:
    """Fake endpoints whose reply reports the targets of the last message."""

    def __init__(self):
        self.packets = []
        self.reply = bytearray(64)
        self.reply[1] = 13
        for offset in ENCODER_OFFSETS:
            self.reply[offset:offset + 3] = (START + ZERO24).to_bytes(3, "little")

    def write(self, data, timeout):
        # Each slot is a controller count (signed 32-bit); the reply reports it as raw24 - 0x7FFFFF.
        data = bytes(data)
        self.packets.append(data)
        for index, offset in enumerate(ENCODER_OFFSETS):
            count = int.from_bytes(data[12 + 4 * index:16 + 4 * index], "little", signed=True)
            self.reply[offset:offset + 3] = (count + ZERO24).to_bytes(3, "little")
        return len(data)

    def read(self, buffer, timeout):
        buffer[:len(self.reply)] = self.reply
        return len(buffer)

    def commands(self):
        return [packet[4] for packet in self.packets]


def field(packet, index):
    """One joint's setpoint in an OUT packet: the controller count, signed 32-bit."""
    return int.from_bytes(packet[12 + 4 * index:16 + 4 * index], "little", signed=True)


@unittest.skipUnless(find_spec("usb"), "PyUSB is needed to import the legacy modules")
class LegacyStreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        robot = Scorbot()
        robot._legacy("conf").setup()
        cls.comm = robot._legacy("libcomm")
        cls.lib = robot._legacy("libdef")

    def setUp(self):
        # These tests check which messages are sent, not when. The legacy loop
        # sleeps about 20 ms per message; against a fake endpoint that is only
        # waiting. The product code is untouched.
        sleeping = mock.patch("time.sleep")
        sleeping.start()
        self.addCleanup(sleeping.stop)

    def stream(self, script):
        """Run the legacy loop with a source that plays back ``script``."""
        controller = Controller()
        buffer = bytearray(controller.reply)
        reads, results = queue.Queue(), queue.Queue()
        reads.put([START_LOW] * len(JOINTS))
        seen = []
        steps = iter(script)

        def source(reply):
            seen.append(reply)
            return next(steps)

        self.comm.stream_targets(1, controller, controller, buffer, reads, results, source)
        self.assertEqual(reads.qsize(), 1)
        return controller, list(results.queue), seen

    def test_steps_carry_three_setpoints_and_end_like_a_normal_move(self):
        script = [("send", (1010, 990, 1005)), ("send", (1020, 980, 1010)), ("end",)]
        controller, results, seen = self.stream(script)
        self.assertEqual(controller.commands(), [OPEN, STEP, STEP, MODE, CONTROL_OFF, MOTOR])
        self.assertEqual(results, [])
        self.assertEqual([field(controller.packets[1], i) for i in range(3)], [1010, 990, 1005])
        self.assertEqual([field(controller.packets[2], i) for i in range(3)], [1020, 980, 1010])
        # closing keeps the last targets, as closeMov does for a jog
        for packet in controller.packets[3:]:
            self.assertEqual([field(packet, i) for i in range(3)], [1020, 980, 1010])
        self.assertTrue(all(isinstance(reply, bytes) and len(reply) == 64 for reply in seen))

    def test_r9_wrist_and_gripper_fields_always_echo_the_measured_position(self):
        controller, _, _ = self.stream([("send", (1500, 500, 1200)), ("end",)])
        for packet in controller.packets:
            self.assertEqual([field(packet, i) for i in (3, 4, 5)], [START] * 3)

    def test_five_setpoints_fill_the_wrist_fields_and_leave_the_gripper_measured(self):
        # A wrist stream (start_stream(wrist=True)) sends both wrist motors in the same
        # message, where the legacy wrist jog puts them; the packet keeps its length.
        script = [("send", (1010, 990, 1005, 1100, 900)), ("end",)]
        controller, results, _ = self.stream(script)
        self.assertEqual(controller.commands(), [OPEN, STEP, MODE, CONTROL_OFF, MOTOR])
        self.assertEqual(results, [])
        self.assertEqual({len(packet) for packet in controller.packets}, {64})
        for packet in controller.packets[1:]:
            self.assertEqual([field(packet, i) for i in range(5)], [1010, 990, 1005, 1100, 900])
            self.assertEqual(field(packet, 5), START)

    def test_setpoints_are_the_controller_counts_as_signed_32_bit(self):
        # Until 2026-11-04 the old decode's form, -1 as FE FF FF FF (docs/requirements/count-frame.md).
        controller, _, _ = self.stream([("send", (-1, 0, -142)), ("end",)])
        step = controller.packets[1]
        self.assertEqual(step[12:16], bytes([0xFF, 0xFF, 0xFF, 0xFF]))
        self.assertEqual(step[16:20], bytes([0x00, 0x00, 0x00, 0x00]))
        self.assertEqual([field(step, i) for i in range(3)], [-1, 0, -142])
        self.assertEqual(self.lib.setpointField(65536), "00000100")
        with self.assertRaises(ValueError):
            self.lib.setpointField(0x800001)

    def test_r5_stop_ends_with_the_stop_sequence_on_the_measured_position(self):
        script = [("send", (1300, 1000, 1000)), ("stop",)]
        controller, results, _ = self.stream(script)
        self.assertEqual(controller.commands(), [OPEN, STEP, OPEN, MODE, CONTROL_OFF, MOTOR])
        self.assertEqual(results, [STOPPED])
        # the stop asks for where the arm is reported to be, not for the target
        for packet in controller.packets[2:]:
            self.assertLessEqual(field(packet, 0), 1300)
            self.assertEqual([field(packet, i) for i in (1, 2)], [1000, 1000])

    @unittest.skipUnless(HAS_RUCKIG, "needs the planning extra")
    def test_teach_mode_mid_stream_sends_exactly_the_normal_stop_bytes(self):
        from scorbot.streaming import Stream, StreamCore
        packets = []
        for teach in (False, True):
            controller = Controller()
            buffer = bytearray(controller.reply)
            reads, results = queue.Queue(), queue.Queue()
            reads.put([START_LOW] * len(JOINTS))
            robot = Scorbot()
            zero = dict.fromkeys(("base", "shoulder", "elbow"), 0)
            core = StreamCore(zero, travel_cap=dict.fromkeys(zero, 1400),
                              lead_limit=dict.fromkeys(zero, 300))
            stream = Stream(robot, core, dict.fromkeys(JOINTS, START), None,
                            home_signed=dict.fromkeys(JOINTS, START))
            core.set_target({"base": 500}, time.monotonic())
            calls = []

            def source(reply, teach=teach, calls=calls, stream=stream):
                data = bytearray(reply)
                data[3] = 0x40 if teach and len(calls) >= 3 else 0x47
                if (not teach and len(calls) >= 3) or len(calls) >= 5:
                    stream.request_stop()       # bound the failing test too
                answer = stream.source(bytes(data))
                calls.append(answer[0])
                return answer

            self.comm.stream_targets(1, controller, controller, buffer, reads, results, source)
            self.assertEqual(calls, ["send"] * 3 + ["stop"])
            self.assertEqual(list(results.queue), [STOPPED])
            self.assertIsNone(core.fault)
            packets.append(controller.packets)
        self.assertEqual(packets[1], packets[0])

    def test_ending_before_anything_was_sent_closes_on_the_measured_position(self):
        controller, results, _ = self.stream([("end",)])
        self.assertEqual(controller.commands(), [OPEN, OPEN, MODE, CONTROL_OFF, MOTOR])
        self.assertEqual(results, [])

    def test_an_answer_the_loop_does_not_know_stops_the_stream(self):
        # There is no wait action; nothing unexpected may be sent as a setpoint.
        script = [("send", (1010, 1000, 1000)), ("wait",), ("send", (1020, 1000, 1000))]
        controller, results, seen = self.stream(script)
        self.assertEqual(controller.commands().count(STEP), 1)
        self.assertEqual(len(seen), 2)
        self.assertEqual(results, [STOPPED])

    def test_sequence_bytes_stay_consecutive(self):
        controller, _, _ = self.stream([("send", (1010, 1000, 1000))] * 5 + [("stop",)])
        self.assertEqual([p[0] for p in controller.packets], list(range(2, 2 + 10)))

    def test_execute_runs_a_stream_for_order_21(self):
        controller = Controller()
        buffer = bytearray(controller.reply)
        sync, orders, reads, results = (queue.Queue() for _ in range(4))
        sync.put(1)
        reads.put([START_LOW] * len(JOINTS))
        worker = threading.Thread(
            target=self.comm.execute,
            args=(sync, orders, reads, controller, controller, buffer, results), daemon=True)
        worker.start()
        steps = iter([("send", (1010, 1000, 1000)), ("stop",)])
        orders.put([21, lambda reply: next(steps), 0])
        self.assertEqual(results.get(timeout=10), STOPPED)
        self.assertEqual(results.get(timeout=10), 0)
        orders.put([528, 1, 1])
        worker.join(timeout=30)
        self.assertFalse(worker.is_alive())


@unittest.skipUnless(HAS_RUCKIG, "ruckig not installed (pip install .[planning])")
class SdkStreamTests(SimulatedRobotCase):
    CONTROLLER_DEFAULTS = {"step_delay_s": 0.001}

    def follow(self, stream, robot, target, seconds=3.0, within=0):
        """Keep sending ``target`` until the arm is there (or time runs out)."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            stream.set_target(target)
            counts = robot.get_state().signed_encoder_counts
            if all(abs(counts[m] - value) <= within for m, value in target.items()):
                return True
            time.sleep(0.002)
        return False

    @staticmethod
    def running(stream, seconds=2.0):
        """Wait until the worker has picked the stream up and taken a step."""
        deadline = time.monotonic() + seconds
        while not stream.steps and time.monotonic() < deadline:
            time.sleep(0.002)
        return bool(stream.steps)

    def test_r1_follows_targets_for_three_motors_and_ends_cleanly(self):
        robot = self.robot()
        with robot.start_stream() as stream:
            self.assertTrue(self.follow(stream, robot, {"base": 142, "shoulder": -60, "elbow": 30}))
            self.assertTrue(self.follow(stream, robot, {"base": -50, "shoulder": 0, "elbow": 0}))
        final = stream.final_state.signed_encoder_counts
        self.assertEqual((final["base"], final["shoulder"], final["elbow"]), (-50, 0, 0))
        self.assertEqual((final["wrist_motor_1"], final["wrist_motor_2"], final["gripper"]),
                         (0, 0, 0))
        self.assertIsNone(robot._fault)
        events = self.events()
        for name in ("stream_start", "stream_trace", "stream_complete"):
            self.assertIn(name, events)
        self.assertTrue(all(len(step) == 3 for step in robot.sim.stream_sent))   # R9
        # the session is still usable for an ordinary jog
        self.assertEqual(robot.jog_joint("base", 1.0).signed_encoder_counts["base"], 92)

    def test_r4_target_outside_the_cap_is_refused_and_the_stream_carries_on(self):
        robot = self.robot()
        with robot.start_stream() as stream:
            self.assertTrue(self.follow(stream, robot, {"base": 100}))
            sent = len(robot.sim.stream_sent)
            with self.assertRaises(StreamRefused):
                stream.set_target({"base": 21000})         # past the base's joint limit
            with self.assertRaises(StreamRefused):
                stream.set_target({"wrist_motor_1": 5})
            self.assertTrue(self.follow(stream, robot, {"base": 120}))
            self.assertGreater(len(robot.sim.stream_sent), sent)
            self.assertLessEqual(max(abs(step[0]) for step in robot.sim.stream_sent), 120)

    def test_r4_the_shoulder_cannot_be_streamed_past_its_joint_limit(self):
        # Home has the upper arm 120 degrees up and the limit is 124 (source model).
        robot = self.robot()
        with robot.start_stream() as stream:
            with self.assertRaises(StreamRefused) as caught:
                stream.set_target({"shoulder": 600})
            self.assertIn("shoulder", str(caught.exception))
            self.assertTrue(self.follow(stream, robot, {"shoulder": 400}))
            self.assertTrue(self.follow(stream, robot, {"shoulder": -900}))
        self.assertIsNone(robot._fault)

    def test_r4_the_elbow_cannot_be_streamed_past_its_pose_limits(self):
        # Source model, not measured: the elbow reaches -140.8 degrees at +5196
        # counts; at negative counts the wrist pitch reaches its limit first, at
        # -2366. Neither is a limit of the elbow's count alone.
        robot = self.robot()
        with robot.start_stream() as stream:
            for refused in ({"elbow": 7500}, {"elbow": -3100}):
                with self.subTest(target=refused), self.assertRaises(StreamRefused) as caught:
                    stream.set_target(refused)
                self.assertIn("vendor limit (USBC.dll EncLimit / ROB_4u.INI)", str(caught.exception))
            # inside their own windows, but together the elbow angle is past -5.16
            with self.assertRaises(StreamRefused):
                stream.set_target({"shoulder": -17000, "elbow": -10000})
            self.assertTrue(self.follow(stream, robot, {"elbow": 400}))
            self.assertTrue(self.follow(stream, robot, {"elbow": -400}))
        self.assertIsNone(robot._fault)

    def test_r4_the_pose_check_uses_the_measured_wrist_not_an_assumed_home(self):
        # The pitch follows the wrist motors too. With the wrist 1000 counts
        # from home an elbow target of -2000 is past the pitch limit; with the
        # wrist at home it is not. A wrist moved after homing must count.
        robot = self.robot()
        with robot.start_stream() as stream:
            stream.set_target({"elbow": -2000})                    # wrist at home: fine
        robot = self.robot()
        with robot.start_stream(wrist=True) as stream:
            self.assertTrue(self.follow(stream, robot, {"wrist_motor_1": 1000}))
        with robot.start_stream(resume=True) as stream:
            with self.assertRaises(StreamRefused) as caught:
                stream.set_target({"elbow": -2000})
            self.assertIn("pitch", str(caught.exception))
        self.assertIsNone(robot._fault)

    def test_a_wrist_stream_follows_both_wrist_motors(self):
        robot = self.robot()
        with robot.start_stream(wrist=True) as stream:
            self.assertEqual(stream.core.motors, ("base", "shoulder", "elbow", "wrist_motor_1",
                                                  "wrist_motor_2"))
            self.assertTrue(self.follow(stream, robot, {"wrist_motor_1": 120, "wrist_motor_2": -80}))
            self.assertTrue(self.follow(stream, robot, {"base": 50, "wrist_motor_1": 0,
                                                        "wrist_motor_2": 0}))
        self.assertIsNone(robot._fault)
        self.assertTrue(all(len(step) == 5 for step in robot.sim.stream_sent))
        final = stream.final_state.signed_encoder_counts
        self.assertEqual((final["base"], final["wrist_motor_1"], final["wrist_motor_2"]), (50, 0, 0))

    def test_a_wrist_motor_bound_is_pitch_plus_roll_at_the_cap(self):
        # Each wrist motor carries pitch and roll (m1 = p + r, m2 = r - p), so its bound is
        # twice the cap in the wrist's counts per degree, plus a count of rounding per joint.
        from scorbot import source_model
        robot = self.robot()
        with robot.start_stream(wrist=True, travel_cap_deg=2.0) as stream:
            bound = 2 * 2.0 * source_model.COUNTS_PER_DEGREE["wrist_motor_1"] + 2
            self.assertEqual(stream.core.bounds["wrist_motor_1"], (-bound, bound))
            with self.assertRaises(StreamRefused):
                stream.set_target({"wrist_motor_1": int(bound) + 1})
            self.assertTrue(self.follow(stream, robot, {"wrist_motor_1": int(bound)}))
        self.assertIsNone(robot._fault)

    def test_a_wrist_target_inside_each_motor_bound_but_past_the_roll_cap_is_refused(self):
        robot = self.robot()
        with robot.start_stream(wrist=True, travel_cap_deg=2.0) as stream:
            with self.assertRaises(StreamRefused) as caught:
                stream.set_target({"wrist_motor_1": -110, "wrist_motor_2": -13})   # roll about -2.2 degrees
            self.assertIn("travel cap", str(caught.exception))
        self.assertIsNone(robot._fault)

    def test_a_stalled_wrist_faults_on_pitch_or_roll_lead_not_on_the_doubled_motor_limit(self):
        # Review 2026-11-02: 3 degrees of pure pitch (or roll) is 83 counts per motor, inside each motor's
        # doubled limit of 111.6, but past the 2 degree lead limit of pitch (or roll) itself.
        for target in ({"wrist_motor_1": -83, "wrist_motor_2": 83}, {"wrist_motor_1": 83, "wrist_motor_2": 83}):
            with self.subTest(target=target):
                robot = self.robot()
                robot.sim.stream_stuck = True
                stream = robot.start_stream(wrist=True, travel_cap_deg=10.0, lead_limit_deg=2.0)
                stream.set_target(target)
                time.sleep(0.5)
                with self.assertRaises(ScorbotError) as caught:
                    stream.close()
                self.assertIn("not following", str(caught.exception))
                self.assertTrue(all(abs(m1) + abs(m2) <= 2 * 2.0 * 27.9
                                    for *_, m1, m2 in robot.sim.stream_sent))

    def test_a_wrist_stream_checks_the_pitch_limit_on_the_streamed_wrist(self):
        # Source model, not measured: home pitch is -88.8 degrees and its limit -109.65, so
        # about 21 degrees of pitch down (m1 up, m2 down, 27.9 counts per degree each) is the edge.
        robot = self.robot()
        with robot.start_stream(wrist=True) as stream:
            with self.assertRaises(StreamRefused) as caught:
                stream.set_target({"wrist_motor_1": 800, "wrist_motor_2": -800})
            self.assertIn("pitch", str(caught.exception))
            self.assertTrue(self.follow(stream, robot, {"wrist_motor_1": 280, "wrist_motor_2": -280}))
        self.assertIsNone(robot._fault)

    def test_a_wrist_controller_error_word_latches_a_fault_only_on_a_wrist_stream(self):
        robot = self.robot()
        robot.sim.stream_wrist_error_word = 71              # above ImpactDetect, 70 (LEDGER.md PL98)
        with robot.start_stream() as stream:              # the wrist is not streamed: not its business
            self.assertTrue(self.follow(stream, robot, {"base": 50}))
            self.assertTrue(self.follow(stream, robot, {"base": 0}))
        self.assertIsNone(robot._fault)
        stream = robot.start_stream(wrist=True)
        stream.set_target({"wrist_motor_1": 50})
        time.sleep(0.1)
        with self.assertRaises(ScorbotError) as caught:
            stream.close()
        self.assertIn("error word", str(caught.exception))
        self.assert_latched(robot)

    def test_r4_the_cap_cannot_be_raised_past_its_stage(self):
        robot = self.robot()
        queued = list(robot.sim.commands)
        # A long period makes each step a jump; a long hold timeout switches the hold off.
        for kwargs in ({"travel_cap_deg": 0}, {"lead_limit_deg": 6},
                       {"travel_cap_deg": True}, {"period_s": 1.0}, {"period_s": 0.001},
                       {"hold_timeout_s": 1e9}, {"hold_timeout_s": 0.001},
                       {"period_s": float("nan")}, {"hold_timeout_s": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                robot.start_stream(**kwargs)
        self.assertEqual(robot.sim.commands, queued)
        self.assertIsNone(robot._stream)

    def test_r5_stop_ends_the_stream_short_of_the_target_without_a_fault(self):
        robot = self.robot(step_delay_s=0.004)
        stream = robot.start_stream()
        stream.set_target({"base": 1400})
        time.sleep(0.15)
        final = stream.stop()
        reached = final.signed_encoder_counts["base"]
        self.assertGreater(reached, 0)
        self.assertLess(reached, 1400)
        self.assertIsNone(robot._fault)
        self.assertTrue(robot.get_state().homed)
        self.assertIn("stream_stopped", self.events())
        self.assertNotIn([16, 1, 1], robot.sim.commands[1:])
        self.assertEqual(robot.jog_joint("base", -1.0).signed_encoder_counts["base"], reached - 142)

    def test_r5_request_stop_on_the_robot_stops_a_stream(self):
        robot = self.robot(step_delay_s=0.004)
        stream = robot.start_stream()
        stream.set_target({"base": 1400})
        time.sleep(0.1)
        robot.request_stop()
        final = stream.stop()
        self.assertLess(final.signed_encoder_counts["base"], 1400)
        self.assertFalse(robot._stop_event.is_set())
        robot.jog_joint("base", 1.0)       # no stop is left pending

    def test_r3_r6_arm_that_does_not_follow_latches_a_fault(self):
        robot = self.robot()
        robot.sim.stream_stuck = True
        stream = robot.start_stream()
        deadline = time.monotonic() + 3
        while stream.core.fault is None and time.monotonic() < deadline:
            try:
                stream.set_target({"base": 1400})
            except StreamRefused:
                break
            time.sleep(0.002)
        with self.assertRaises(ScorbotError) as caught:
            stream.close()
        self.assertIn("not following", str(caught.exception))
        self.assert_latched(robot)
        self.assertIn("stream_fault", self.events())
        self.assertEqual(self.events().count("stream_fault"), 1)
        # the command never ran ahead of the stuck arm by more than 2 degrees of lead
        self.assertLessEqual(max(abs(step[0]) for step in robot.sim.stream_sent), 2 * 142)

    def test_r6_a_fault_latches_the_session_even_if_the_caller_never_ends_the_stream(self):
        robot = self.robot()
        robot.sim.stream_stuck = True
        stream = robot.start_stream()
        deadline = time.monotonic() + 3
        while robot._fault is None and time.monotonic() < deadline:
            try:
                stream.set_target({"base": 1400})
            except StreamRefused:
                pass
            time.sleep(0.002)
        self.assertIn("not following", robot._fault or "")
        self.assert_latched(robot)
        self.assertEqual(self.events().count("stream_fault"), 1)
        self.assertIsNotNone(robot.get_state().fault)
        with self.assertRaises(ScorbotError):
            stream.close()                 # ending it later is safe and adds no second row
        self.assertEqual(self.events().count("stream_fault"), 1)
        self.assertEqual(robot.sim.commands.count([16, 1, 1]), 2)   # connect's, and one more

    def test_r6_a_stream_that_does_not_end_in_time_is_stopped_not_left_running(self):
        robot = self.robot(step_delay_s=0.004)
        stream = robot.start_stream()
        stream.set_target({"base": 1400})
        time.sleep(0.15)
        robot.command_timeout = 0.02       # slowing to rest takes longer than this
        with self.assertRaises(ScorbotError) as caught:
            stream.close()
        self.assertIn("did not end", str(caught.exception))
        self.assertEqual(stream.core.state, "faulted")
        deadline = time.monotonic() + 2
        while robot.sim._moving and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(robot.sim._moving, "the worker must leave the stream")
        self.assertEqual(stream.steps[-1]["action"], "stop")
        self.assertIsNotNone(robot._fault)
        self.assertEqual(self.events().count("stream_fault"), 1)

    def test_steps_are_never_closer_together_than_the_planned_period(self):
        robot = self.robot()               # the simulated loop alone runs at about 1 ms
        with robot.start_stream(period_s=0.02) as stream:
            self.assertTrue(self.follow(stream, robot, {"base": 40}))
        stamps = [step["host_monotonic_ns"] for step in stream.steps]
        gaps = [(b - a) / 1e9 for a, b in zip(stamps, stamps[1:])]
        self.assertGreater(len(gaps), 5)
        # a clock that ticks every 15.6 ms (Windows before Python 3.13) reads a true gap as little as one tick
        # short; the simulated loop alone runs at about 1 ms, which reads as 0 and still fails this floor
        tick = time.get_clock_info("monotonic").resolution
        self.assertGreaterEqual(min(gaps), 0.019 - tick)

    def assert_latched(self, robot):
        self.assertIsNotNone(robot._fault)
        self.assertIsNone(robot._enabled)
        self.assertFalse(robot._homed)
        deadline = time.monotonic() + 2
        while robot.sim.commands[-1] != [16, 1, 1] and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(robot.sim.commands[-1], [16, 1, 1], "best-effort disable expected")
        queued = len(robot.sim.commands)
        for call in (lambda: robot.jog_joint("base", 1.0), robot.start_stream):
            with self.assertRaises(ScorbotError):
                call()
        self.assertEqual(len(robot.sim.commands), queued, "a latched session queues nothing")

    def test_r6_controller_error_word_latches_a_fault(self):
        robot = self.robot()
        stream = robot.start_stream()
        stream.set_target({"base": 50})
        robot.sim.stream_error_word = 0xFFB9                # -71: past ImpactDetect, 70, read signed (PL98)
        time.sleep(0.1)
        with self.assertRaises(ScorbotError) as caught:
            stream.close()
        self.assertIn("error word", str(caught.exception))
        self.assert_latched(robot)

    def test_r6_emergency_bit_faults_by_default_without_the_caller_ending_the_stream(self):
        robot = self.robot()
        stream = robot.start_stream()
        self.assertTrue(self.running(stream))
        robot.sim.stream_emergency = True
        deadline = time.monotonic() + 2
        while robot._fault is None and time.monotonic() < deadline:
            time.sleep(0.002)
        self.assertIn("emergency", robot._fault or "")
        self.assert_latched(robot)
        with self.assertRaises(ScorbotError) as caught:
            stream.close()
        self.assertIn("emergency", str(caught.exception))
        self.assert_latched(robot)

    def test_r6_collision_flag_on_a_streamed_motor_latches_a_fault(self):
        robot = self.robot()
        stream = robot.start_stream()
        self.assertTrue(self.running(stream))
        robot.sim.stream_collision_bits = 0x02          # shoulder
        deadline = time.monotonic() + 2
        while robot._fault is None and time.monotonic() < deadline:
            time.sleep(0.002)
        self.assertIn("collision flag is set for shoulder", robot._fault or "")
        self.assert_latched(robot)
        with self.assertRaises(ScorbotError):
            stream.close()

    # count-frame.md CT15
    def test_one_reply_with_a_corrupt_top_byte_sends_no_setpoint_and_names_the_motor(self):
        for motor, top in (("shoulder", 0x81), ("elbow", 0x7F)):
            with self.subTest(motor=motor, top=top):
                robot = self.robot()
                stream = robot.start_stream()
                self.assertTrue(self.follow(stream, robot, {motor: 40}))     # top byte 0x80
                robot.sim.stream_sign_override = {motor: top}                # reads 40 +/- 65536
                deadline = time.monotonic() + 2
                while robot._fault is None and time.monotonic() < deadline:
                    time.sleep(0.002)
                self.assertRegex(robot._fault or "", f"{motor}.*corrupt reply")
                self.assert_latched(robot)
                index = ("base", "shoulder", "elbow").index(motor)
                self.assertTrue(all(abs(step[index] - 40) <= 40 for step in robot.sim.stream_sent),
                                "no setpoint from the corrupt reply")
                with self.assertRaises(ScorbotError):
                    stream.close()

    def test_r6_collision_flag_of_a_motor_not_streamed_is_ignored(self):
        robot = self.robot()
        robot.sim.stream_collision_bits = 0x20 | 0x18   # gripper and the wrist, not streamed by default
        with robot.start_stream() as stream:
            self.assertTrue(self.follow(stream, robot, {"base": 40}))
        self.assertIsNone(robot._fault)

    def test_r6_emergency_bit_check_can_be_explicitly_disabled_on_the_simulator(self):
        robot = self.robot()
        robot.sim.stream_emergency = True
        with robot.start_stream(use_emergency_bit=False) as stream:
            self.assertTrue(self.follow(stream, robot, {"base": 40}))
        self.assertIsNone(robot._fault)

    def test_r7_arm_holds_when_targets_stop_arriving(self):
        robot = self.robot(step_delay_s=0.004)
        with robot.start_stream(hold_timeout_s=0.05) as stream:
            stream.set_target({"base": 1400})
            time.sleep(0.6)                                 # the caller goes quiet
            held = robot.get_state().signed_encoder_counts["base"]
            self.assertEqual(stream.core.state, "holding")
            self.assertGreater(held, 0)
            self.assertLess(held, 1400)
            time.sleep(0.1)
            self.assertEqual(robot.get_state().signed_encoder_counts["base"], held)
        self.assertIsNone(robot._fault)

    def test_r10_the_log_holds_target_command_and_measurement_for_every_step(self):
        robot = self.robot()
        with robot.start_stream() as stream:
            self.assertTrue(self.follow(stream, robot, {"base": 100, "elbow": -40}))
        trace = next(row for row in self.rows() if row["event"] == "stream_trace")
        self.assertGreater(len(trace["steps"]), 10)
        self.assertEqual(trace["dropped_steps"], 0)
        for step in trace["steps"]:
            for key in ("host_monotonic_ns", "action", "state", "target", "commanded",
                        "measured", "lead"):
                self.assertIn(key, step)
        last = trace["steps"][-1]
        self.assertEqual(last["action"], "end")
        self.assertEqual(last["commanded"], {"base": 100, "shoulder": 0, "elbow": -40})
        start = next(row for row in self.rows() if row["event"] == "stream_start")
        self.assertEqual(start["period_s"], 0.024)
        self.assertTrue(all(row.get("simulated") for row in self.rows()))

    def test_r11_a_lagging_arm_is_followed_with_a_bounded_lead(self):
        robot = self.robot()
        robot.sim.stream_follow = 0.5
        with robot.start_stream() as stream:
            # a simulated arm that covers half the gap each step stops one count short
            self.assertTrue(self.follow(stream, robot, {"base": 1000}, within=1))
        self.assertIsNone(robot._fault)
        leads = [abs(step["lead"]["base"]) for step in stream.steps]
        self.assertGreater(max(leads), 5)
        self.assertLess(max(leads), 2 * 142)

    def test_r12_streaming_needs_enable_and_home_and_queues_nothing_otherwise(self):
        from scorbot.simulated import SimulatedScorbot
        robot = SimulatedScorbot().connect()
        self.addCleanup(self.disconnect, robot)
        for prepare in (lambda: None, robot.enable):
            prepare()
            queued = list(robot.sim.commands)
            with self.assertRaises(ScorbotError):
                robot.start_stream()
            self.assertEqual(robot.sim.commands, queued)
            self.assertIsNone(robot._stream)

    def test_r12_other_commands_are_refused_while_a_stream_is_active(self):
        robot = self.robot()
        with robot.start_stream() as stream:
            self.assertTrue(self.running(stream))
            queued = len(robot.sim.commands)
            rows = len(self.rows())
            for call in (lambda: robot.jog_joint("base", 1.0), robot.disable, robot.enable,
                         robot.start_stream,
                         lambda: robot.home(start_position_confirmed=True)):
                with self.assertRaises(ScorbotError) as caught:
                    call()
                self.assertIn("stream", str(caught.exception).lower())
            self.assertEqual(len(robot.sim.commands), queued)
            self.assertEqual(len(self.rows()), rows, "a refused call writes no log row")
            self.assertIsNone(robot._fault, "refusing is not a fault")
            self.assertTrue(robot.get_state().simulated)        # reading state still works
            self.assertTrue(self.follow(stream, robot, {"base": 30}))
        robot.disable()

    def test_a_pending_stop_refuses_the_stream_before_anything_is_sent(self):
        robot = self.robot()
        robot.request_stop()
        queued = list(robot.sim.commands)
        with self.assertRaises(MotionStopped) as caught:
            robot.start_stream()
        self.assertFalse(caught.exception.started)
        self.assertEqual(robot.sim.commands, queued)
        with robot.start_stream() as stream:
            self.assertTrue(self.follow(stream, robot, {"base": 20}))

    def test_disconnect_ends_a_stream_left_open(self):
        robot = self.robot()
        stream = robot.start_stream()
        stream.set_target({"base": 100})
        robot.disconnect()
        self.assertIsNone(robot._stream)
        self.assertIsNotNone(stream.final_state)
        self.assertEqual(robot.sim.commands[-1], [528, 1, 1])

    def test_an_exception_in_the_callers_block_stops_the_stream(self):
        robot = self.robot(step_delay_s=0.004)
        with self.assertRaises(KeyError):
            with robot.start_stream() as stream:
                stream.set_target({"base": 1400})
                time.sleep(0.1)
                raise KeyError("caller bug")
        self.assertIn("stream_stopped", self.events())
        self.assertLess(stream.final_state.signed_encoder_counts["base"], 1400)
        self.assertIsNone(robot._fault)

    def test_closing_twice_returns_the_same_state(self):
        robot = self.robot()
        stream = robot.start_stream()
        first = stream.close()
        self.assertIs(stream.close(), first)

    def test_streaming_is_in_the_motion_fingerprint(self):
        from scorbot import provenance
        self.assertIn("scorbot/streaming.py", provenance._SOURCE_FILES)
        # the stream's starting limits are computed from the vendor profile
        self.assertIn("scorbot/vendor_profile.py", provenance._SOURCE_FILES)
        # signed_count_delta now decides the setpoint bytes of a stream
        self.assertIn("scorbot/calibration.py", provenance._SOURCE_FILES)

    def wait_until_idle(self, robot, seconds=2.0):
        deadline = time.monotonic() + seconds
        while robot.sim._moving and time.monotonic() < deadline:
            time.sleep(0.01)
        return not robot.sim._moving

    def test_an_interrupt_while_a_stream_is_ending_faults_the_session(self):
        # A second Ctrl-C while the first one's stop is being collected.
        robot = self.robot(step_delay_s=0.004)
        stream = robot.start_stream()
        stream.set_target({"base": 1400})
        self.assertTrue(self.running(stream))
        with mock.patch.object(robot, "_next_result", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                stream.stop()
        self.assertIn("interrupted", robot._fault)
        self.assertIsNone(robot._enabled)
        self.assertTrue(robot._cancel_event.is_set())
        self.assertEqual(stream.core.state, "faulted")
        self.assertTrue(self.wait_until_idle(robot), "the worker must leave the stream")
        self.assertEqual(self.events().count("stream_fault"), 1)
        queued = len(robot.sim.commands)
        with self.assertRaises(ScorbotError):
            robot.jog_joint("base", 1.0)       # no later command can take the stream's answer
        self.assertEqual(len(robot.sim.commands), queued)

    def test_a_stop_request_after_the_stream_has_ended_is_logged_not_dropped(self):
        robot = self.robot()
        stream = robot.start_stream()
        self.assertTrue(self.follow(stream, robot, {"base": 20}))
        stream.core.finish()                    # the worker slows to rest and ends
        self.assertTrue(self.wait_until_idle(robot))
        robot.request_stop()                    # too late: nothing is left to cut short
        stream.close()
        events = self.events()
        self.assertIn("stop_too_late", events)
        self.assertIn("stream_complete", events)
        self.assertNotIn("stream_stopped", events)
        self.assertFalse(robot._stop_event.is_set())
        self.assertIsNone(robot._fault)
        robot.jog_joint("base", 1.0)            # and no stop is left pending

    def test_a_session_fault_from_elsewhere_stops_a_running_stream(self):
        robot = self.robot(step_delay_s=0.004)
        stream = robot.start_stream()
        stream.set_target({"base": 1400})
        self.assertTrue(self.running(stream))
        robot._latch_fault("feedback was lost")          # as _motion_state does on another thread
        self.assertTrue(self.wait_until_idle(robot), "the worker must stop following")
        self.assertEqual(stream.steps[-1]["action"], "stop")
        self.assertIn("session faulted", stream.core.fault)
        self.assertLess(robot.get_state().signed_encoder_counts["base"], 1400)
        with self.assertRaises(ScorbotError):
            stream.close()
        self.assertEqual(robot._fault, "feedback was lost")

    def test_a_stream_whose_record_cannot_be_written_faults_the_session(self):
        # The worker answered, but the row holding every step was lost and the
        # final state was never read: the session must not carry on as if it had.
        robot = self.robot()
        stream = robot.start_stream()
        self.assertTrue(self.follow(stream, robot, {"base": 40}))
        record = robot._record

        def failing(event, **fields):
            if event == "stream_trace":
                raise OSError("disk full")
            return record(event, **fields)

        with mock.patch.object(robot, "_record", side_effect=failing):
            with self.assertRaises(ScorbotError) as caught:
                stream.close()
        self.assertIn("could not be written", str(caught.exception))
        self.assert_latched(robot)
        self.assertIsNone(stream.final_state)
        self.assertIn("stream_fault", self.events())

    def test_a_failed_log_write_cannot_stop_a_timed_out_stream_from_faulting(self):
        robot = self.robot(step_delay_s=0.004)
        stream = robot.start_stream()
        stream.set_target({"base": 1400})
        time.sleep(0.15)
        robot.command_timeout = 0.02
        record = robot._record

        def failing(event, **fields):
            if event == "stream_trace":
                raise OSError("disk full")
            return record(event, **fields)

        with mock.patch.object(robot, "_record", side_effect=failing):
            with self.assertRaises(ScorbotError) as caught:
                stream.close()
        self.assertIn("did not end", str(caught.exception))
        self.assertIsNotNone(robot._fault)
        self.assertIsNone(robot._stream)
        events = self.events()
        self.assertIn("stream_fault", events)
        self.assertNotIn("stream_trace", events)

    # Start rules, docs/requirements/streaming.md ST01, ST05, ST18 (issue #170).
    FIVE = ("base", "shoulder", "elbow", "wrist_motor_1", "wrist_motor_2")

    def test_a_stream_starts_only_within_20_counts_of_home(self):
        # ST01: every arm motor, streamed or held, within limits.DRIFT_COUNTS of home; refused
        # otherwise with nothing queued and a stream_refused_not_home row naming the motor.
        from scorbot import limits
        self.assertEqual(limits.DRIFT_COUNTS, 20)
        robot = self.robot()
        home = dict(robot.sim.counts)
        for wrist in (False, True):
            for motor in self.FIVE:
                for offset in (19, 20, 21, -19, -20, -21):
                    with self.subTest(wrist=wrist, motor=motor, offset=offset):
                        robot.sim.counts.update(home)
                        robot.sim.counts[motor] = home[motor] + offset
                        queued, seen = list(robot.sim.commands), len(self.rows())
                        if abs(offset) <= 20:
                            robot.start_stream(wrist=wrist).close()
                            continue
                        try:
                            started = robot.start_stream(wrist=wrist)
                        except ScorbotError as exc:
                            self.assertRegex(str(exc), rf"{motor} is {offset} counts from home")
                        else:
                            started.close()             # keep the next case independent
                            self.fail(f"a stream started with {motor} {offset} counts from home")
                        self.assertEqual(robot.sim.commands, queued)
                        self.assertIsNone(robot._stream)
                        new = self.rows()[seen:]
                        refused = [r for r in new if r["event"] == "stream_refused_not_home"]
                        self.assertEqual(len(refused), 1)
                        self.assertEqual(refused[0]["counts_from_home"], {motor: offset})
                        self.assertEqual(refused[0]["tolerance_counts"], 20)
                        self.assertNotIn("stream_start", [r["event"] for r in new])
        robot.sim.counts.update(home)
        self.assertIsNone(robot._fault)

    def test_near_home_reads_the_whole_count_not_the_low_word(self):
        # ST01 with CT14: a motor a whole low word (65536 counts) plus 5 from home reads 5 in the
        # low word. It must still be refused. Guard: passes before #170 (the reach check), and
        # must keep passing after it.
        robot = self.robot()
        home = dict(robot.sim.counts)
        robot.sim.counts["wrist_motor_2"] = home["wrist_motor_2"] + 65541
        queued = list(robot.sim.commands)
        for wrist in (False, True):
            with self.subTest(wrist=wrist), \
                    self.assertRaisesRegex(ScorbotError, "wrist_motor_2 is 65541 counts from home"):
                robot.start_stream(wrist=wrist)
        self.assertEqual(robot.sim.commands, queued)
        self.assertIsNone(robot._stream)

    def drifting(self, robot, replies):
        """Make the base move 10 counts between replies (+/-5 about home), for ``replies`` replies
        (None: for ever). Returns the list of replies read, for counting."""
        home, original, reads = robot.sim.counts["base"], robot.sim.snapshot, []
        left = {"n": replies}

        def snapshot(*args, **kwargs):
            if left["n"] is None or left["n"] > 0:
                robot.sim.counts["base"] = home + (5 if len(reads) % 2 else -5)
                if left["n"] is not None:
                    left["n"] -= 1
            reads.append(robot.sim.counts["base"])
            return original(*args, **kwargs)

        patch = mock.patch.object(robot.sim, "snapshot", side_effect=snapshot)
        patch.start()
        self.addCleanup(patch.stop)
        return reads, left

    def test_a_stream_waits_for_rest_and_refuses_an_arm_still_moving(self):
        # ST05 (Q5): at rest is STOP_SETTLE_QUIET_PAIRS (3) reply pairs in a row moving at most
        # STOP_SETTLE_COUNTS (2) counts; not at rest within STOP_SETTLE_TIMEOUT_S: refused,
        # nothing queued, no fault latched (a refusal, not the stop-settle fault).
        robot = self.robot()
        reads, left = self.drifting(robot, 8)
        with robot.start_stream() as stream:
            waited = len(reads)
            start = next(r for r in self.rows() if r["event"] == "stream_start")
        self.assertGreaterEqual(waited, 8 + robot.STOP_SETTLE_QUIET_PAIRS)
        self.assertEqual(start["state"]["signed_encoder_counts"]["base"], reads[waited - 1])
        self.assertIsNone(robot._fault)
        del stream

        left["n"] = None                                   # never comes to rest
        queued, seen = list(robot.sim.commands), len(self.rows())
        began = time.monotonic()
        with self.assertRaisesRegex(ScorbotError, "not at rest"):
            robot.start_stream()
        self.assertGreaterEqual(time.monotonic() - began, robot.STOP_SETTLE_TIMEOUT_S)
        self.assertEqual(robot.sim.commands, queued)
        self.assertIsNone(robot._stream)
        self.assertIsNone(robot._fault)
        new = [r["event"] for r in self.rows()[seen:]]
        self.assertIn("stream_refused_not_at_rest", new)
        self.assertNotIn("stream_start", new)
        self.assertNotIn("stop_settle_failed", new)

        left["n"] = 0                                      # at rest again: the session still works
        robot.start_stream().close()
        self.assertIsNone(robot._fault)

    def test_the_stream_start_row_logs_the_resume_flag_and_all_five_motors(self):
        # ST18, stream_start part: every setting (including resume and the ST01 tolerance), the
        # start counts from home of all five motors ST01 checked (held ones too), and the state.
        robot = self.robot()
        robot.sim.counts["wrist_motor_1"] += 7
        robot.start_stream().close()
        row = next(r for r in self.rows() if r["event"] == "stream_start")
        for key in ("travel_cap_deg", "speed_fraction", "lead_limit_deg", "hold_timeout_s",
                    "period_s", "use_emergency_bit", "adaptive_pacing", "wrist", "state"):
            self.assertIn(key, row)
        self.assertIs(row["resume"], False)
        self.assertEqual(row["home_tolerance_counts"], 20)
        self.assertEqual(set(row["start_counts_from_home"]), set(self.FIVE))
        self.assertEqual(row["start_counts_from_home"]["wrist_motor_1"], 7)


class WristReversalTests(unittest.TestCase):
    """Review 2026-11-02: a pitch and roll reversal made Ruckig overshoot one wrist motor, past the roll cap
    while inside that motor's own bound (63 counts against a cap of 56.8 without the check). Replayed on the
    core with a perfect arm, one action per 24 ms step: the stream must stop, never send past the cap."""

    @unittest.skipUnless(HAS_RUCKIG, "ruckig not installed (pip install .[planning])")
    def test_no_setpoint_puts_pitch_or_roll_past_the_cap(self):
        from scorbot import source_model
        from scorbot.policy_arm import clamp, wrist_counts
        from scorbot.stream_control import wrist_cap_problem
        from scorbot.streaming import WRIST_STREAM_MOTORS, StreamCore
        cap_deg, per = 2.0, source_model.COUNTS_PER_DEGREE["wrist_motor_1"]
        cap_counts = cap_deg * per + 1
        motors = WRIST_STREAM_MOTORS
        core = StreamCore(dict.fromkeys(motors, 0),
                          travel_cap={m: (2 * cap_deg * per + 2) if m.startswith("wrist") else 1000.0
                                      for m in motors},
                          lead_limit={m: (4 * per) if m.startswith("wrist") else 300.0 for m in motors},
                          pose_check=lambda counts: wrist_cap_problem(counts, cap_counts),
                          period_s=0.024, now=0.0, motors=motors)
        target, measured, now = {"pitch": 0.0, "roll": 0.0}, dict.fromkeys(motors, 0), 0.0
        for goal in [{"pitch": 9.0, "roll": -9.0}] * 8 + [{"pitch": -9.0, "roll": -9.0}] * 44:
            seen = {"pitch": (measured["wrist_motor_1"] - measured["wrist_motor_2"]) / (-2 * per),
                    "roll": (measured["wrist_motor_1"] + measured["wrist_motor_2"]) / (2 * per)}
            target, _ = clamp({j: goal[j] - target[j] for j in goal}, target, seen, cap_deg=cap_deg)
            core.set_target(wrist_counts(target["pitch"], target["roll"]), now)
            now += 0.024
            step = core.step(measured, now)
            if step.action != "send":
                break
            self.assertIsNone(wrist_cap_problem(step.commanded, cap_counts), step.commanded)
            measured = dict(step.commanded)
        self.assertIn("travel cap", core.fault or "")


class _QuietRobot:
    """The parts of ``Scorbot`` a ``Stream`` touches from the worker."""

    def __init__(self):
        self._cancel_event = threading.Event()
        self._stop_event = threading.Event()   # read on every step since the pendant Abort change
        self._fault = None
        self.faults = []

    def _stream_faulted(self, stream):
        self.faults.append(stream.core.fault)
        self.seen = (stream.fault_reported, stream.report_lock.locked())


@unittest.skipUnless(HAS_RUCKIG, "ruckig not installed (pip install .[planning])")
class SetpointConversionTests(unittest.TestCase):
    """Counts from home back to the signed count the legacy message carries."""

    def stream(self, home_signed, cap=200.0):
        from scorbot.simulated import encode_packet
        from scorbot.state import decode_state
        from scorbot.streaming import MOTORS, Stream, StreamCore
        home = decode_state(encode_packet(dict.fromkeys(MOTORS, home_signed)), connected=True,
                            enabled=True, homed=True, fault=None).encoder_counts
        core = StreamCore(dict.fromkeys(MOTORS, 0), travel_cap=dict.fromkeys(MOTORS, cap),
                          lead_limit=dict.fromkeys(MOTORS, 100.0), period_s=0.001,
                          now=time.monotonic())
        self.robot = _QuietRobot()
        stream = Stream(self.robot, core, home, None, home_signed=dict.fromkeys(MOTORS, home_signed))
        stream._pace = lambda: None     # the conversion is the subject here, not the timing
        return stream, encode_packet

    def drive(self, stream, encode, home_signed, target, steps=400):
        """A perfect arm: what was sent is what the next reply reports."""
        from scorbot.streaming import MOTORS
        signed = dict.fromkeys(MOTORS, home_signed)
        sent = []
        for _ in range(steps):
            if stream.core.state in ("tracking", "holding"):
                stream.set_target({"base": target})
            answer = stream.source(encode(signed))
            if answer[0] != "send":
                break
            sent.append(answer[1][0])
            signed = dict(zip(MOTORS, answer[1]))
        return sent

    def test_setpoints_stay_continuous_across_the_zero_of_the_counter(self):
        for home_signed, target in ((-3, 60), (3, -60), (0, 60), (0, -60), (-1, 1), (1, -1)):
            with self.subTest(home=home_signed, target=target):
                stream, encode = self.stream(home_signed)
                sent = self.drive(stream, encode, home_signed, target)
                self.assertIsNone(stream.core.fault)
                self.assertEqual(sent[-1], home_signed + target)
                steps = [b - a for a, b in zip([home_signed] + sent, sent)]
                self.assertLessEqual(max(map(abs, steps)), 5, "no jump at the seam")
                self.assertEqual(sent, [home_signed + step["commanded"]["base"]
                                        for step in stream.steps[:len(sent)]])

    def test_a_fault_counts_as_reported_only_once_the_session_has_latched_it(self):
        # The caller's timeout path reads fault_reported to decide whether it
        # still has to latch. It must never read True before the latch is done.
        stream, encode = self.stream(0)
        stream.core.fail("made up")
        self.assertEqual(stream.source(encode({})), ("stop",))
        self.assertEqual(self.robot.seen, (False, True))
        self.assertTrue(stream.fault_reported)
        stream.source(encode({}))
        self.assertEqual(self.robot.faults, ["made up"])

    # count-frame.md CT15
    def test_a_corrupt_top_byte_is_a_fault_naming_the_motor_not_a_setpoint(self):
        # Home 1000 is raw 0x8003E7. A top byte of 0x81 or 0x7F reads 65536 away while the low word,
        # which `measured` comes from, has not moved: before this check the setpoint jumped 65536.
        from scorbot.streaming import MOTORS
        for motor in MOTORS:
            for top in (0x7F, 0x81):
                with self.subTest(motor=motor, top=top):
                    stream, encode = self.stream(1000)
                    sent = self.drive(stream, encode, 1000, 30, steps=5)
                    self.assertEqual(len(sent), 5)
                    there = {m: 1000 + stream.steps[-1]["commanded"][m] for m in MOTORS}   # where it was sent
                    reply = encode(there, sign_override={motor: top})
                    self.assertEqual(stream.source(reply), ("stop",))
                    self.assertRegex(stream.core.fault, f"{motor}.*corrupt reply")
                    self.assertEqual(self.robot.faults, [stream.core.fault])

    def test_the_end_of_the_counter_range_is_a_fault_not_a_wrap(self):
        # The controller's 24-bit counter runs out at -0x7FFFFF and 0x800000
        # (docs/requirements/count-frame.md; until 2026-11-04 this stopped at
        # +/-65535): a setpoint past it would wrap, so it is a fault.
        for home_signed, target in ((0x800000 - 35, 60), (-0x7FFFFF + 35, -60)):
            with self.subTest(home=home_signed):
                stream, encode = self.stream(home_signed)
                sent = self.drive(stream, encode, home_signed, target)
                self.assertIn("counter range", stream.core.fault or "")
                self.assertEqual(self.robot.faults, [stream.core.fault])
                self.assertTrue(all(-0x7FFFFF <= value <= 0x800000 for value in sent))
                self.assertTrue(all(abs(value - home_signed) <= 60 for value in sent))


if __name__ == "__main__":
    unittest.main()
