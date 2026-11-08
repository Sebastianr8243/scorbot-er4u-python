"""PolicyArm binary gripper on the simulator; recorded DLL fixture, no USB or DLL loading."""

from contextlib import ExitStack
from importlib.util import find_spec
import json
from pathlib import Path
import tempfile
from time import monotonic, sleep
import unittest
from unittest import mock

from scorbot import ScorbotError, SimulatedScorbot, vendor_gripper as vg
from scorbot.policy_arm import PolicyArm
from scorbot.robot import GripperMove, MotionStopped
from scorbot.state import decode_state
from scorbot.streaming import StreamRefused
from scripts import make_gripper_fixture as reduce
from tests.desk_wire import record_gripper

GOLDEN = json.loads((Path(__file__).resolve().parents[1] / "tests/fixtures/dll_lab_gripper.json")
                    .read_text(encoding="utf-8"))["calls"]
CONFIRMED = dict(operator_at_stop=True, jaws_empty=True, wrist_clear=True)


@unittest.skipUnless(find_spec("ruckig"), "needs the planning extra")
class PolicyArmGripperTests(unittest.TestCase):
    def arm(self, *, connect=True, **kwargs):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        robot = SimulatedScorbot(log_path=Path(tmp.name) / "e.jsonl", robot_id="t")
        arm = PolicyArm(robot, **kwargs)
        self.addCleanup(arm.close)
        if connect:
            arm.connect(**CONFIRMED)
        return arm

    def test_open_close_and_stream_resume(self):
        arm = self.arm(wrist=True, step_limit="speed", speed_pct=40)
        arm.act({"base": -0.2, "pitch": 0.1})
        target, home = dict(arm.target), dict(arm._home)
        for method, count in ((arm.open_gripper, vg.RANGE_ENC), (arm.close_gripper, 0)):
            old = arm._stream
            result = method()
            self.assertIsInstance(result, GripperMove)
            self.assertTrue(result.full_travel)
            self.assertAlmostEqual(result.state.signed_encoder_counts["gripper"], count,
                                   delta=vg.AT_TARGET_COUNTS)
            self.assertIsNotNone(old.final_state)
            self.assertIs(arm.robot._stream, arm._stream)
            self.assertIsNot(arm._stream, old)
            self.assertEqual(arm._stream.core.motors[-2:], ("wrist_motor_1", "wrist_motor_2"))
            self.assertEqual(arm.speed_pct, 40)
            self.assertEqual(arm.target, target)
            self.assertEqual(arm._home, home)
            self.assertEqual(arm.act(target), target)
        arm.close()
        self.assertIsNone(arm.robot._stream)

    def test_set_gripper_thresholds_holds_and_returns_the_applied_value(self):
        arm = self.arm(wrist=True)
        self.assertEqual(arm.gripper, 0.0)                    # vendor Home closes the jaw
        self.assertEqual(arm.observe(gripper=True)["gripper"], 0.0)
        self.assertNotIn("gripper", arm.observe())            # the joint schema is unchanged
        stream = arm._stream
        for asked in (0.0, 49.9, -5.0, float("nan"), None, True, "open"):
            self.assertEqual(arm.set_gripper(asked), 0.0)     # closed or not a number: no move
        self.assertIs(arm._stream, stream)
        self.assertEqual(arm.set_gripper(50.0), 100.0)
        self.assertIsNot(arm._stream, stream)                 # stream paused for the move, restarted
        self.assertIs(arm.robot._stream, arm._stream)
        self.assertAlmostEqual(arm.observe(gripper=True)["gripper"], 100.0, delta=1.0)
        stream = arm._stream
        self.assertEqual(arm.set_gripper(250.0), 100.0)       # unchanged: no move
        self.assertIs(arm._stream, stream)
        self.assertEqual(arm.set_gripper(10.0), 0.0)
        self.assertAlmostEqual(arm.observe(gripper=True)["gripper"], 0.0, delta=1.0)
        self.assertEqual(arm.act({"base": 0.3}), {**dict.fromkeys(arm.joints, 0.0), "base": 0.3})

    def test_act_after_a_gripper_move_reads_the_arm_again(self):
        arm = self.arm()
        arm.observe(gripper=True)                 # a reading from before the move (cached by PR #113)
        arm.set_gripper(100.0)
        with mock.patch.object(arm.robot, "_motion_state", wraps=arm.robot._motion_state) as read:
            arm.act({"base": 0.2})
        self.assertGreaterEqual(read.call_count, 1)

    def test_direct_gripper_calls_keep_set_gripper_in_step(self):
        arm = self.arm()
        arm.open_gripper()
        self.assertEqual(arm.gripper, 100.0)
        self.assertEqual(arm.set_gripper(0.0), 0.0)
        self.assertAlmostEqual(arm.observe(gripper=True)["gripper"], 0.0, delta=1.0)
        arm.open_gripper()
        arm.close_gripper()
        self.assertEqual(arm.gripper, 0.0)
        self.assertEqual(arm.set_gripper(100.0), 100.0)
        self.assertAlmostEqual(arm.observe(gripper=True)["gripper"], 100.0, delta=1.0)

    def test_gripper_percent_is_the_jaw_count_over_range_enc(self):
        arm = self.arm()
        for counts, pct in ((2857, 57.14), (714, 14.28), (vg.RANGE_ENC, 100.0)):
            arm.robot.sim.counts["gripper"] = counts
            self.assertAlmostEqual(arm.observe(gripper=True)["gripper"], pct, delta=0.01)

    def test_jaw_is_get_jaws_whole_percent_and_mm(self):
        arm = self.arm()
        for counts, reading in ((2857, (57, 40)), (714, (14, 10)), (0, (0, 0)), (vg.RANGE_ENC, (100, 70))):
            arm.robot.sim.counts["gripper"] = counts
            self.assertEqual(arm.jaw(), reading)

    def test_jaw_reads_the_dll_count_from_raw_positive_feedback(self):
        # Raw 0x800CB1 is 3250 DLL counts (raw - 0x7FFFFF, count-frame CT13); the old low-word decode gave 3249,
        # (64, 45). Built from wire bytes, not the simulator's own encoding.
        arm = self.arm()
        packet = bytearray(64)
        for n in range(6):
            packet[19 + 5 * n:22 + 5 * n] = (0x800CB1 if n == 5 else 0x7FFFFF).to_bytes(3, "little")
        reading = decode_state(bytes(packet), connected=True, enabled=None, homed=True, fault=None)
        self.assertEqual(reading.signed_encoder_counts["gripper"], 3250)
        with mock.patch.object(arm.robot, "_motion_state", return_value=reading):
            self.assertEqual(arm.jaw(), (65, 46))

    def test_set_gripper_needs_a_connection(self):
        arm = self.arm(connect=False)
        with self.assertRaises(ConnectionError):
            arm.set_gripper(100.0)
        with self.assertRaises(ConnectionError):
            arm.observe(gripper=True)

    def test_disconnected_and_unhomed_refuse_without_commands(self):
        arm = self.arm(connect=False)
        for method in (arm.open_gripper, arm.close_gripper):
            with self.assertRaises(ConnectionError):
                method()
        self.assertEqual(arm.robot.sim.commands, [])
        arm.connect(**CONFIRMED)
        for flags in ((False, False), (True, False), (False, True)):
            arm.robot._homed, arm.robot._gripper_homed = flags
            before = list(arm.robot.sim.commands)
            for method in (arm.open_gripper, arm.close_gripper):
                with self.assertRaisesRegex(ScorbotError, "Home"):
                    method()
                self.assertEqual(arm.robot.sim.commands, before)
        arm.robot._homed = arm.robot._gripper_homed = True
        arm.close()
        for method in (arm.open_gripper, arm.close_gripper):
            with self.assertRaises(ConnectionError):
                method()

    def test_packets_match_the_recorded_dll(self):
        def move(robot, direction):
            # record_gripper supplies a connected, enabled, post-Home fake (it cannot run Home).
            arm = PolicyArm(robot)
            with ExitStack() as stack:
                arm._stack = stack
                arm._start_stream()
                stack.push(arm._exit_stream)
                return getattr(arm, direction + "_gripper")()

        calls = record_gripper([("open", 0, lambda r: move(r, "open")),
                                ("close", vg.RANGE_ENC, lambda r: move(r, "close"))])
        for name, golden in zip(("open", "close"), GOLDEN[:2]):
            packets, outcome = calls[name]
            self.assertIsInstance(outcome, GripperMove, outcome)
            found = reduce.calls(packets)
            self.assertEqual(len(found), 1)
            ours = reduce.describe(found[0])
            self.assertEqual(ours["commands"], golden["commands"])
            self.assertLessEqual(abs(ours["nop_count"] - golden["nop_count"]), 1)
            self.assertEqual((ours["gripper"][0], ours["gripper"][-1]),
                             (golden["gripper"][0], golden["gripper"][-1]))
            self.assertEqual(ours["arm"], [(0, 0, 0, 0, 0)])
            self.assertEqual(len(ours["gripper"]), len(golden["gripper"]))
            self.assertLessEqual(max(abs(a - b) for a, b in zip(ours["gripper"], golden["gripper"])), 8)

    def test_failed_gripper_does_not_resume_stream(self):
        arm = self.arm()
        old = arm._stream
        with mock.patch.object(arm.robot, "move_gripper", side_effect=ScorbotError("test refusal")):
            with self.assertRaisesRegex(ScorbotError, "test refusal"):
                arm.open_gripper()
        self.assertIsNone(arm.robot._stream)
        self.assertIs(arm._stream, old)
        with self.assertRaises(StreamRefused):
            arm.act({"base": -0.2})

    def test_stop_during_stream_handoff_prevents_gripper(self):
        arm = self.arm()
        arm.robot.request_stop()
        with mock.patch.object(arm.robot, "move_gripper") as move:
            with self.assertRaises(MotionStopped):
                arm.open_gripper()
            move.assert_not_called()
        self.assertIsNone(arm.robot._stream)


    def test_a_stop_too_late_for_the_jaw_near_home_still_prevents_the_resume(self):
        """Near home a fresh start needs no resume point, so the cleared one alone did not stop it.
        The away-from-home case: test_a_stop_that_lands_as_the_gripper_finishes_clears_the_resume."""
        arm = self.arm()
        robot, command = arm.robot, arm.robot._command

        def late_stop(payload, *args, **kwargs):
            reply = command(payload, *args, **kwargs)
            if payload[0] == vg.GRIPPER_ORDER:
                robot.request_stop()        # after the move ran, before its stop check
            return reply

        with mock.patch.object(robot, "_command", side_effect=late_stop):
            with self.assertRaisesRegex(MotionStopped, "during the gripper move"):
                arm.open_gripper()
        self.assertIsNone(robot._stream)
        self.assertFalse(robot._stop_event.is_set())       # the gripper cleared it; the refusal did not need it

    def test_a_stop_after_the_stream_is_unpublished_still_prevents_the_gripper(self):
        arm = self.arm()
        robot, inner = arm.robot, arm.robot._stop_lock

        class StopJustBeforeTheClear:
            # request_stop() landing after _end_stream unpublished the stream but before it clears the event
            def __enter__(self):
                inner.acquire()
                if robot._stream is None:
                    robot._stop_event.set()
                return self

            def __exit__(self, *exc):
                inner.release()

        robot._stop_lock = StopJustBeforeTheClear()
        with mock.patch.object(robot, "move_gripper") as move:
            with self.assertRaises(MotionStopped):
                arm.open_gripper()
            move.assert_not_called()

    def test_a_stop_after_the_handoff_guard_still_prevents_the_gripper(self):
        arm = self.arm()
        stream, close = arm._stream, arm._stream.close

        def close_then_stop():
            state = close()
            arm.robot.request_stop()        # lands with no stream: only the session's stop event is set
            return state

        stream.close = close_then_stop
        with self.assertRaises(MotionStopped):
            arm.open_gripper()
        self.assertLess(abs(arm.robot.sim.counts["gripper"]), vg.AT_TARGET_COUNTS)

    # ST02 (docs/requirements/streaming.md, pick for Q1, issue #170): the same-session gripper resume
    # is exempt from ST01 (near home); anything else that ends the session's trust clears it.
    FAR = {"base": 600}         # about 4 degrees, far past ST01's 20 counts

    @staticmethod
    def reach(robot, stream, target, seconds=3.0):
        deadline = monotonic() + seconds
        while monotonic() < deadline:
            stream.set_target(target)
            counts = robot.get_state().signed_encoder_counts
            if all(counts[m] == v for m, v in target.items()):
                return
            sleep(0.002)
        raise AssertionError(f"the simulator did not reach {target}")

    @staticmethod
    def starts(robot):
        rows = [json.loads(line) for line in robot.log_path.read_text(encoding="utf-8").splitlines()]
        return [r for r in rows if r["event"] == "stream_start"]

    def test_gripper_resume_away_from_home_restarts_the_stream(self):
        arm = self.arm()
        self.reach(arm.robot, arm._stream, self.FAR)
        old = arm._stream
        arm.open_gripper()
        self.assertIsNot(arm._stream, old)
        self.assertIs(arm.robot._stream, arm._stream)
        arm.close_gripper()                      # a resume of a resumed stream: still the same chain
        self.assertIs(arm.robot._stream, arm._stream)
        starts = self.starts(arm.robot)
        self.assertEqual([r["resume"] for r in starts], [False, True, True])
        self.assertEqual(starts[-1]["start_counts_from_home"]["base"], self.FAR["base"])
        self.assertIsNone(arm.robot._fault)

    def session(self):
        """A homed simulated Scorbot whose stream was closed cleanly at FAR: the resume case."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        robot = SimulatedScorbot(log_path=Path(tmp.name) / "e.jsonl", robot_id="t").__enter__()
        self.addCleanup(robot.__exit__, None, None, None)
        robot.enable()
        robot.vendor_home(**CONFIRMED)
        stream = robot.start_stream()
        self.reach(robot, stream, self.FAR)
        return robot, stream

    def test_a_stop_or_fault_clears_the_resume_exemption(self):
        far = rf"base is {self.FAR['base']} counts from home"
        with self.subTest("control: a clean close, then a resume, starts"):
            robot, stream = self.session()
            stream.close()
            robot.start_stream(resume=True).close()
        with self.subTest("a plain start is never a resume"):
            robot, stream = self.session()
            stream.close()
            with self.assertRaisesRegex(ScorbotError, far):
                robot.start_stream()
        with self.subTest("a stream that was stopped"):
            robot, stream = self.session()
            stream.stop()
            with self.assertRaisesRegex(ScorbotError, far):
                robot.start_stream(resume=True)
        with self.subTest("a stop requested after a clean close"):
            robot, stream = self.session()
            stream.close()
            robot.request_stop()
            with self.assertRaises(MotionStopped):
                robot.start_stream(resume=True)      # the stop refuses this start (ST04) ...
            with self.assertRaisesRegex(ScorbotError, far):
                robot.start_stream(resume=True)      # ... and the exemption went with it
        with self.subTest("another arm motion in between (not a gripper move)"):
            robot, stream = self.session()
            stream.close()
            robot.jog_joint("base", -1.0)
            with self.assertRaisesRegex(ScorbotError, "counts from home"):
                robot.start_stream(resume=True)
        with self.subTest("a disconnect and reconnect"):
            robot, stream = self.session()
            stream.close()
            left = dict(robot.sim.counts)
            robot.disconnect()
            robot.connect()
            robot.enable()
            robot.vendor_home(**CONFIRMED)
            robot.sim.counts.update(left)            # back exactly where the stream ended
            with self.assertRaisesRegex(ScorbotError, far):
                robot.start_stream(resume=True)
        with self.subTest("a fault"):
            robot, stream = self.session()
            stream.close()
            robot._latch_fault("injected for the test")
            with self.assertRaises(ScorbotError):
                robot.start_stream(resume=True)

    def test_a_stop_that_lands_as_the_gripper_finishes_clears_the_resume(self):
        # ST02: the gripper move ends "too late" for the stop and clears the stop event
        # itself, so the start never consumes it. The request alone must clear ST02's exemption.
        arm = self.arm()
        self.reach(arm.robot, arm._stream, self.FAR)
        robot, command = arm.robot, arm.robot._command

        def command_then_stop(payload, *args, **kwargs):
            result = command(payload, *args, **kwargs)
            if payload[0] == vg.GRIPPER_ORDER:
                robot.request_stop()            # lands after the jaw finished, before the restart
            return result

        with mock.patch.object(robot, "_command", side_effect=command_then_stop):
            # Reported as the stop it is, before start_stream would refuse the pose.
            with self.assertRaisesRegex(MotionStopped, "during the gripper move"):
                arm.open_gripper()
        self.assertIsNone(robot._stream)

    def test_any_jog_in_between_clears_the_resume(self):
        # ST02: any arm motion in between clears it, even one that ends within
        # 20 counts of where the stream ended (a small jog, or out and back).
        far = "counts from home"
        for name, jogs in (("small jog", [("base", 0.05)]),
                           ("out and back", [("base", 1.0), ("base", -1.0)])):
            with self.subTest(name):
                robot, stream = self.session()
                stream.close()
                for joint, degrees in jogs:
                    robot.jog_joint(joint, degrees)
                with self.assertRaisesRegex(ScorbotError, far):
                    robot.start_stream(resume=True)

if __name__ == "__main__":
    unittest.main()
