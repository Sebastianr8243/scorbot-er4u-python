"""The streaming driver wired in: the legacy loop against fake endpoints, then
``Scorbot.start_stream`` through the simulator. No USB, no hardware.

Requirement numbers are from
docs/superpowers/specs/2026-10-04-streaming-driver-requirements.md.
"""

from importlib.util import find_spec
import json
from pathlib import Path
import queue
import tempfile
import threading
import time
import unittest

from scorbot import MotionStopped, Scorbot, ScorbotError, StreamRefused
from scorbot.state import ENCODER_OFFSETS, JOINTS

HAS_RUCKIG = find_spec("ruckig") is not None
OPEN, STEP, MODE, CONTROL_OFF, MOTOR = 0x47, 0x0D, 0x4F, 0x73, 0x42
STOPPED = 14
START = 1000


class Controller:
    """Fake endpoints whose reply reports the targets of the last message."""

    def __init__(self):
        self.packets = []
        self.reply = bytearray(64)
        self.reply[1] = 13
        for offset in ENCODER_OFFSETS:
            self.reply[offset:offset + 2] = START.to_bytes(2, "little")
            self.reply[offset + 2] = 128

    def write(self, data, timeout):
        data = bytes(data)
        self.packets.append(data)
        for index, offset in enumerate(ENCODER_OFFSETS):
            self.reply[offset:offset + 2] = data[12 + 4 * index:14 + 4 * index]
            self.reply[offset + 2] = 127 if data[14 + 4 * index:16 + 4 * index] == b"\xff\xff" \
                else 128
        return len(data)

    def read(self, buffer, timeout):
        buffer[:len(self.reply)] = self.reply
        return len(buffer)

    def commands(self):
        return [packet[4] for packet in self.packets]


def field(packet, index):
    """One joint's setpoint in an OUT packet, as a signed count."""
    value = int.from_bytes(packet[12 + 4 * index:14 + 4 * index], "little")
    return value - 65535 if packet[14 + 4 * index:16 + 4 * index] == b"\xff\xff" else value


@unittest.skipUnless(find_spec("usb"), "PyUSB is needed to import the legacy modules")
class LegacyStreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        robot = Scorbot()
        robot._legacy("conf").setup()
        cls.comm = robot._legacy("libcomm")
        cls.lib = robot._legacy("libdef")

    def stream(self, script):
        """Run the legacy loop with a source that plays back ``script``."""
        controller = Controller()
        buffer = bytearray(controller.reply)
        reads, results = queue.Queue(), queue.Queue()
        reads.put([START] * len(JOINTS))
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

    def test_negative_setpoints_use_the_legacy_sign_form(self):
        controller, _, _ = self.stream([("send", (-1, 0, -142)), ("end",)])
        step = controller.packets[1]
        self.assertEqual(step[12:16], bytes([0xFE, 0xFF, 0xFF, 0xFF]))
        self.assertEqual(step[16:20], bytes([0x00, 0x00, 0x00, 0x00]))
        self.assertEqual([field(step, i) for i in range(3)], [-1, 0, -142])
        with self.assertRaises(ValueError):
            self.lib.setpointField(65536)

    def test_r5_stop_ends_with_the_stop_sequence_on_the_measured_position(self):
        script = [("send", (1300, 1000, 1000)), ("stop",)]
        controller, results, _ = self.stream(script)
        self.assertEqual(controller.commands(), [OPEN, STEP, OPEN, MODE, CONTROL_OFF, MOTOR])
        self.assertEqual(results, [STOPPED])
        # the stop asks for where the arm is reported to be, not for the target
        for packet in controller.packets[2:]:
            self.assertLessEqual(field(packet, 0), 1300)
            self.assertEqual([field(packet, i) for i in (1, 2)], [1000, 1000])

    def test_ending_before_anything_was_sent_closes_on_the_measured_position(self):
        controller, results, _ = self.stream([("end",)])
        self.assertEqual(controller.commands(), [OPEN, OPEN, MODE, CONTROL_OFF, MOTOR])
        self.assertEqual(results, [])

    def test_r8_wait_sends_nothing(self):
        script = [("send", (1010, 1000, 1000)), ("wait",), ("wait",), ("send", (1020, 1000, 1000)),
                  ("end",)]
        controller, _, seen = self.stream(script)
        self.assertEqual(controller.commands().count(STEP), 2)
        self.assertEqual(len(seen), 5)

    def test_sequence_bytes_stay_consecutive(self):
        controller, _, _ = self.stream([("send", (1010, 1000, 1000))] * 5 + [("stop",)])
        self.assertEqual([p[0] for p in controller.packets], list(range(2, 2 + 10)))

    def test_execute_runs_a_stream_for_order_21(self):
        controller = Controller()
        buffer = bytearray(controller.reply)
        sync, orders, reads, results = (queue.Queue() for _ in range(4))
        sync.put(1)
        reads.put([START] * len(JOINTS))
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
class SdkStreamTests(unittest.TestCase):
    def robot(self, **controller_kwargs):
        from scorbot.simulated import SimulatedController, SimulatedScorbot
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.log = Path(folder.name) / "run.controller.jsonl"
        controller_kwargs.setdefault("step_delay_s", 0.001)
        robot = SimulatedScorbot(controller=SimulatedController(**controller_kwargs),
                                 log_path=self.log)
        robot.STOP_SETTLE_INTERVAL_S = 0.01
        robot.STOP_SETTLE_TIMEOUT_S = 0.5
        robot.connect()
        self.addCleanup(self.disconnect, robot)
        robot.enable()
        robot.home(start_position_confirmed=True)
        return robot

    @staticmethod
    def disconnect(robot):
        try:
            robot.disconnect()
        except ScorbotError:
            pass

    def rows(self):
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def events(self):
        return [row["event"] for row in self.rows()]

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
                stream.set_target({"base": 1500})          # 10 degrees is 1420 counts
            with self.assertRaises(StreamRefused):
                stream.set_target({"wrist_motor_1": 5})
            self.assertTrue(self.follow(stream, robot, {"base": 120}))
            self.assertGreater(len(robot.sim.stream_sent), sent)
            self.assertLessEqual(max(abs(step[0]) for step in robot.sim.stream_sent), 120)

    def test_r4_the_cap_cannot_be_raised_past_its_stage(self):
        robot = self.robot()
        queued = list(robot.sim.commands)
        for kwargs in ({"travel_cap_deg": 10.5}, {"travel_cap_deg": 0}, {"lead_limit_deg": 6},
                       {"travel_cap_deg": True}):
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
        # the command never ran far ahead of the stuck arm: 2 degrees of lead
        self.assertLessEqual(max(abs(step[0]) for step in robot.sim.stream_sent), 2 * 142 + 45)

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
        robot.sim.stream_error_word = 40
        time.sleep(0.1)
        with self.assertRaises(ScorbotError) as caught:
            stream.close()
        self.assertIn("error word", str(caught.exception))
        self.assert_latched(robot)

    def test_r6_emergency_bit_faults_only_when_enabled(self):
        robot = self.robot()
        robot.sim.stream_emergency = True
        with robot.start_stream() as stream:               # the bit is unverified: off by default
            self.assertTrue(self.follow(stream, robot, {"base": 40}))
        self.assertIsNone(robot._fault)
        stream = robot.start_stream(use_emergency_bit=True)
        time.sleep(0.1)
        with self.assertRaises(ScorbotError) as caught:
            stream.close()
        self.assertIn("emergency", str(caught.exception))
        self.assert_latched(robot)

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


if __name__ == "__main__":
    unittest.main()
