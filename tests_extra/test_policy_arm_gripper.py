"""PolicyArm binary gripper on the simulator; recorded DLL fixture, no USB or DLL loading."""

from contextlib import ExitStack
from importlib.util import find_spec
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scorbot import ScorbotError, SimulatedScorbot, vendor_gripper as vg
from scorbot.policy_arm import PolicyArm
from scorbot.robot import GripperMove, MotionStopped
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


if __name__ == "__main__":
    unittest.main()
