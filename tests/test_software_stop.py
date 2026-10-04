"""The software stop: legacy jog loops against fake endpoints, then the SDK
through the simulator. No USB, no hardware. Not an emergency stop."""

import contextlib
from importlib.util import find_spec
import io
import json
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from scorbot import MotionStopped, Scorbot, ScorbotError
from scorbot.state import ENCODER_OFFSETS, JOINTS

STOPPED = 14
OPEN, STEP, MODE, CONTROL_OFF, MOTOR = 0x47, 0x0D, 0x4F, 0x73, 0x42
# (legacy mover, positive order, index in JOINTS, OUT region offset)
MOVERS = (("move_hips", 5, 0, 12), ("move_shoulder", 6, 1, 16), ("move_elbow", 9, 2, 20))
START = 1000


class FollowingController:
    """Fake endpoints: the reply reports whatever target the last OUT carried.

    That lets the legacy settle loop converge, as it would on an arm that
    follows its setpoints. ``stop_after`` sets the stop event while the
    message with that 1-based index is being written.
    """

    def __init__(self, stop_event=None, stop_after=None):
        self.packets = []
        self.stop_event = stop_event
        self.stop_after = stop_after
        self.reply = bytearray(64)
        self.reply[1] = 13
        for offset in ENCODER_OFFSETS:
            self.reply[offset:offset + 2] = START.to_bytes(2, "little")
            self.reply[offset + 2] = 128

    def write(self, data, timeout):
        data = bytes(data)
        self.packets.append(data)
        for index, offset in enumerate(ENCODER_OFFSETS):
            region = data[12 + 4 * index:16 + 4 * index]
            self.reply[offset:offset + 2] = region[:2]
            self.reply[offset + 2] = 127 if region[2:] == b"\xff\xff" else 128
        if self.stop_after is not None and len(self.packets) == self.stop_after:
            self.stop_event.set()
        return len(data)

    def read(self, buffer, timeout):
        buffer[:len(self.reply)] = self.reply
        return len(buffer)

    def commands(self):
        return [packet[4] for packet in self.packets]


@unittest.skipUnless(find_spec("usb"), "PyUSB is needed to import the legacy modules")
class LegacyStopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        robot = Scorbot()
        robot._legacy("conf").setup()
        cls.comm = robot._legacy("libcomm")
        cls.profile = robot._legacy("motion_profile")

    def jog(self, mover, order, *, stop_after=None, pass_event=True):
        stop_event = threading.Event()
        controller = FollowingController(stop_event, stop_after)
        buffer = bytearray(controller.reply)
        reads, results = queue.Queue(), queue.Queue()
        reads.put([START] * len(JOINTS))
        args = (1, controller, controller, buffer, order, reads, results, 10, 1.0)
        if pass_event:
            getattr(self.comm, mover)(*args, stop_event)
        else:
            getattr(self.comm, mover)(*args)
        self.assertEqual(reads.qsize(), 1, "the smoothed position must go back on its queue")
        return controller, list(results.queue)

    def test_without_a_stop_the_jog_is_open_steps_close(self):
        for mover, order, _, _ in MOVERS:
            for pass_event in (True, False):
                with self.subTest(mover=mover, pass_event=pass_event):
                    controller, results = self.jog(mover, order, pass_event=pass_event)
                    commands = controller.commands()
                    self.assertEqual(commands[0], OPEN)
                    self.assertEqual(commands[-3:], [MODE, CONTROL_OFF, MOTOR])
                    self.assertEqual(set(commands[1:-3]), {STEP})
                    steps = len(self.profile.plan_jog(order, 1.0, 10)["increments"])
                    self.assertGreaterEqual(commands.count(STEP), steps)
                    self.assertEqual(results, [])

    def test_stop_mid_jog_sends_clear_buffer_then_close_and_reports_it(self):
        for mover, order, _, region in MOVERS:
            with self.subTest(mover=mover):
                # message 1 opens the move; messages 2..6 are five steps
                controller, results = self.jog(mover, order, stop_after=6)
                self.assertEqual(controller.commands(),
                                 [OPEN] + [STEP] * 5 + [OPEN, MODE, CONTROL_OFF, MOTOR])
                self.assertEqual(results, [STOPPED])
                last_step = controller.packets[5]
                for packet in controller.packets[6:]:
                    self.assertEqual(packet[region:region + 4], last_step[region:region + 4],
                                     "the stop must hold the last target already sent")
                mode = controller.packets[7]
                self.assertEqual(mode[4:7], bytes([0x4F, 0x3F, 0x53]))
                self.assertEqual(controller.packets[8][4:6], bytes([0x73, 0x20]))
                self.assertEqual(controller.packets[9][4:6], bytes([0x42, 0x20]))

    def test_stop_moves_the_joint_less_than_the_full_jog(self):
        mover, order, index, region = MOVERS[0]
        full, _ = self.jog(mover, order)
        stopped, _ = self.jog(mover, order, stop_after=6)

        def target(controller):
            return int.from_bytes(controller.packets[-1][region:region + 2], "little")

        self.assertEqual(target(full) - START, 142)
        self.assertGreater(target(stopped), START)
        self.assertLess(target(stopped) - START, 142)

    def test_stop_already_requested_ends_after_the_first_step(self):
        mover, order, _, _ = MOVERS[0]
        controller, results = self.jog(mover, order, stop_after=1)
        self.assertEqual(controller.commands(), [OPEN, STEP, OPEN, MODE, CONTROL_OFF, MOTOR])
        self.assertEqual(results, [STOPPED])

    def test_sequence_bytes_stay_consecutive_through_a_stop(self):
        mover, order, _, _ = MOVERS[0]
        controller, _ = self.jog(mover, order, stop_after=6)
        self.assertEqual([packet[0] for packet in controller.packets], list(range(2, 12)))

    def test_execute_hands_the_stop_event_to_the_jog(self):
        stop_event = threading.Event()
        controller = FollowingController(stop_event, stop_after=6)
        buffer = bytearray(controller.reply)
        sync, orders, reads, results = (queue.Queue() for _ in range(4))
        sync.put(1)
        reads.put([START] * len(JOINTS))
        worker = threading.Thread(
            target=self.comm.execute,
            args=(sync, orders, reads, controller, controller, buffer, results,
                  threading.Event(), stop_event), daemon=True)
        worker.start()
        orders.put([5, 10, 1.0])
        self.assertEqual(results.get(timeout=10), STOPPED)
        self.assertEqual(results.get(timeout=10), 0)
        self.assertEqual(controller.commands()[-4:], [OPEN, MODE, CONTROL_OFF, MOTOR])
        orders.put([528, 1, 1])
        worker.join(timeout=30)
        self.assertFalse(worker.is_alive())


class SdkStopTests(unittest.TestCase):
    """``Scorbot.request_stop`` through the simulator and the real facade."""

    def robot(self, **controller_kwargs):
        from scorbot.simulated import SimulatedController, SimulatedScorbot
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.log = Path(folder.name) / "run.controller.jsonl"
        robot = SimulatedScorbot(controller=SimulatedController(**controller_kwargs),
                                 log_path=self.log)
        robot.STOP_SETTLE_INTERVAL_S = 0.01
        robot.STOP_SETTLE_TIMEOUT_S = 0.2
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
            pass  # a latched session may refuse a clean exit; the worker is a daemon

    def events(self):
        return [json.loads(line)["event"]
                for line in self.log.read_text(encoding="utf-8").splitlines()]

    def stop_soon(self, robot):
        timer = threading.Timer(0.1, robot.request_stop)
        timer.start()
        self.addCleanup(timer.join)

    def test_stop_mid_jog_ends_early_without_a_fault(self):
        robot = self.robot(step_delay_s=0.02)
        start = robot.get_state().signed_encoder_counts["base"]
        self.stop_soon(robot)
        with self.assertRaises(MotionStopped) as caught:
            robot.jog_joint("base", 1.0)
        reached = caught.exception.state.signed_encoder_counts["base"]
        self.assertGreater(reached, start)
        self.assertLess(reached - start, 142)
        state = robot.get_state()
        self.assertIsNone(state.fault)
        self.assertTrue(state.enabled)
        self.assertTrue(state.homed)
        self.assertEqual(state.signed_encoder_counts["base"], reached)
        self.assertNotIn([16, 1, 1], robot.sim.commands[1:], "a clean stop must not disable")
        events = self.events()
        for name in ("stop_requested", "command_stopped", "motion_stopped"):
            self.assertIn(name, events)
        self.assertNotIn("command_error", events)
        self.assertNotIn("motion_complete", events)

    def test_session_stays_usable_after_a_stop(self):
        robot = self.robot(step_delay_s=0.02)
        self.stop_soon(robot)
        with self.assertRaises(MotionStopped) as caught:
            robot.jog_joint("base", 1.0)
        reached = caught.exception.state.signed_encoder_counts["base"]
        after = robot.jog_joint("base", 1.0)
        self.assertEqual(after.signed_encoder_counts["base"], reached + 142)

    def test_stop_requested_while_idle_refuses_the_next_jog_once(self):
        robot = self.robot()
        robot.request_stop()
        queued = list(robot.sim.commands)
        with self.assertRaises(MotionStopped) as caught:
            robot.jog_joint("base", 1.0)
        self.assertIn("not started", str(caught.exception))
        self.assertEqual(robot.sim.commands, queued, "nothing may be queued")
        self.assertTrue(robot._commands.empty())
        self.assertIsNone(robot.get_state().fault)
        self.assertIn("stop_before_motion", self.events())
        after = robot.jog_joint("base", 1.0)
        self.assertEqual(after.signed_encoder_counts["base"], 142)

    def test_a_finished_jog_leaves_no_stop_pending(self):
        robot = self.robot()
        robot.jog_joint("base", 1.0)       # instant in the simulator
        self.assertFalse(robot._stop_event.is_set())
        robot.jog_joint("base", 1.0)
        self.assertEqual(robot.get_state().signed_encoder_counts["base"], 284)

    def test_arm_that_keeps_moving_after_a_stop_latches_a_fault(self):
        robot = self.robot(step_delay_s=0.02)
        controller = robot.sim
        plain = controller.snapshot

        def coasting(**kwargs):
            with controller._lock:
                controller.counts["base"] += 50
            return plain(**kwargs)

        settle = robot._settled_after_stop

        def settle_on_a_coasting_arm(before):
            controller.snapshot = coasting
            try:
                return settle(before)
            finally:
                controller.snapshot = plain

        robot._settled_after_stop = settle_on_a_coasting_arm
        self.stop_soon(robot)
        with self.assertRaises(ScorbotError) as caught:
            robot.jog_joint("base", 1.0)
        self.assertNotIsInstance(caught.exception, MotionStopped)
        self.assertIn("still moving", str(caught.exception))
        self.assertIsNotNone(robot._fault)
        self.assertIsNone(robot._enabled)
        self.assertFalse(robot._homed)
        self.assertIn("stop_settle_failed", self.events())
        deadline = time.monotonic() + 2
        while controller.commands[-1] != [16, 1, 1] and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(controller.commands[-1], [16, 1, 1], "best-effort disable expected")
        queued = len(controller.commands)
        with self.assertRaises(ScorbotError):
            robot.jog_joint("base", 1.0)
        self.assertEqual(len(controller.commands), queued, "a latched session queues nothing")

    def bench(self, *extra, step_delay_s=0.0):
        """Run examples/bench_joint.py in process against the simulator."""
        from examples import bench_joint
        from scorbot.simulated import SimulatedController, SimulatedScorbot
        from scripts.review_lab_logs import review_bench

        class SlowArm(SimulatedScorbot):
            def __init__(self, **kwargs):
                super().__init__(controller=SimulatedController(step_delay_s=step_delay_s),
                                 **kwargs)

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        output = Path(folder.name) / "bench.jsonl"
        argv = ["bench_joint.py", "--output", str(output), "--robot-id", "rehearsal-arm",
                "--arm-label", "arm-plate", "--controller-label", "controller-plate",
                "--driver", "none (simulated)", "--operator", "tester",
                "--start-pose-note", "desk rehearsal", "--joint", "base", "--delta", "1",
                "--simulate", "--acknowledge-supervised-motion", *extra]
        answers = ("n\ng\n" "HOME\n" "y\ng\n" "home looked normal\nHOME_OK\nMOVE\n" "y\ng\n"
                   "toward door\nnone\nno sounds\nnone\n" "n\ng\n").splitlines()
        printed = io.StringIO()
        with mock.patch.object(sys, "argv", argv), \
                mock.patch.object(bench_joint, "SimulatedScorbot", SlowArm), \
                mock.patch("builtins.input", side_effect=answers), \
                contextlib.redirect_stdout(printed):
            code = bench_joint.main()
        rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
        return code, rows, printed.getvalue(), review_bench(output)

    def test_bench_stop_trial_records_an_early_stop(self):
        code, rows, printed, review = self.bench("--stop-after-ms", "100", step_delay_s=0.02)
        self.assertEqual(code, 0, printed)
        self.assertIn("NOT an emergency stop", printed)
        self.assertIn("ended early on the stop request", printed)
        by_type = {row["type"]: row for row in rows}
        self.assertEqual(by_type["session"]["stop_after_ms"], 100)
        self.assertTrue(by_type["after_jog"]["stopped_on_request"])
        self.assertTrue(review["stopped_on_request"])
        self.assertEqual(review["problems"], [])
        observed = review["count_deltas"]["base"]["observed"]
        self.assertGreater(observed, 0)
        self.assertLess(observed, 142)
        self.assertIn("disabled", by_type)

    def test_bench_stop_trial_that_comes_too_late_reports_a_completed_jog(self):
        code, rows, printed, review = self.bench("--stop-after-ms", "5000")
        self.assertEqual(code, 0, printed)
        self.assertIn("The jog completed", printed)
        self.assertFalse(review["stopped_on_request"])
        self.assertEqual(review["count_deltas"]["base"]["observed"], 142)

    def test_bench_without_the_option_is_unchanged(self):
        code, rows, printed, review = self.bench()
        self.assertEqual(code, 0, printed)
        self.assertNotIn("Stop trial", printed)
        self.assertIsNone(review["stop_after_ms"])
        self.assertFalse(review["stopped_on_request"])

    def test_bench_rejects_an_out_of_range_stop_delay(self):
        for value in ("0", "5001"):
            with self.subTest(value=value), self.assertRaises(SystemExit) as caught, \
                    contextlib.redirect_stderr(io.StringIO()):
                self.bench("--stop-after-ms", value)
            self.assertEqual(caught.exception.code, 2)

    def test_motion_stopped_is_a_scorbot_error_with_a_state(self):
        self.assertTrue(issubclass(MotionStopped, ScorbotError))
        self.assertIsNone(MotionStopped("x").state)

    def test_request_stop_is_documented_as_not_an_emergency_stop(self):
        self.assertIn("NOT an emergency stop", Scorbot.request_stop.__doc__)


if __name__ == "__main__":
    unittest.main()
