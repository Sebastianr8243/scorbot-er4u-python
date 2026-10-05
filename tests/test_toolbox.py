"""The teaching API: ``scorbot.toolbox.Arm`` on the simulator. No USB.

Design: docs/specs/2026-10-05-three-front-doors-design.md.
"""

import contextlib
from importlib.util import find_spec
import io
import json
from pathlib import Path
import runpy
import tempfile
import time
import unittest

from scorbot import ScorbotError, SimulatedScorbot, source_model
from scorbot.mover import MoverRefused
from scorbot.simulated import SimulatedController
from scorbot.toolbox import Arm

HOME = source_model.HOME_ANGLES
EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "teaching_demo.py"


@unittest.skipUnless(find_spec("ruckig"), "ruckig not installed (pip install .[planning])")
class ArmTests(unittest.TestCase):
    def arm(self, *, home=True, **controller_kwargs):
        controller_kwargs.setdefault("step_delay_s", 0.001)
        robot = SimulatedScorbot(controller=SimulatedController(**controller_kwargs))
        robot.STOP_SETTLE_INTERVAL_S = 0.01
        arm = Arm(robot=robot)
        self.addCleanup(self.close, arm)
        if home:
            arm.go_home()
        return arm

    @staticmethod
    def close(arm):
        try:
            arm.close()
        except ScorbotError:
            pass      # a latched session may refuse a clean exit

    def test_a_move_blocks_until_the_arm_is_there(self):
        arm = self.arm()
        arm.move_to_angles(base=5.0, elbow=HOME["elbow"] + 3.0)
        angles = arm.get_angles()
        self.assertEqual(set(angles), {"base", "shoulder", "elbow", "pitch", "roll"})
        self.assertAlmostEqual(angles["base"], 5.0, delta=0.1)
        self.assertAlmostEqual(angles["elbow"], HOME["elbow"] + 3.0, delta=0.1)
        self.assertFalse(arm.is_moving())
        self.assertIsNone(arm.robot._stream, "a finished blocking move leaves no stream open")

    def test_wait_false_returns_first_and_wait_for_move_finishes_it(self):
        arm = self.arm(step_delay_s=0.004)
        arm.move_to_angles(base=8.0, wait=False)
        self.assertTrue(arm.is_moving())
        self.assertLess(arm.get_angles()["base"], 7.0)
        arm.wait_for_move()
        self.assertAlmostEqual(arm.get_angles()["base"], 8.0, delta=0.1)
        self.assertFalse(arm.is_moving())

    def test_a_script_can_loop_on_is_moving(self):
        arm = self.arm()
        arm.move_to_angles(base=4.0, wait=False)
        deadline = time.monotonic() + 10
        while arm.is_moving() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertAlmostEqual(arm.get_angles()["base"], 4.0, delta=0.1)

    def test_a_relative_move_adds_to_where_the_arm_is(self):
        arm = self.arm()
        arm.move_to_angles(base=3.0)
        arm.move_by_angles(base=-5.0, shoulder=-2.0)
        self.assertAlmostEqual(arm.get_angles()["base"], -2.0, delta=0.1)
        self.assertAlmostEqual(arm.get_angles()["shoulder"], HOME["shoulder"] - 2.0, delta=0.1)

    def test_the_tool_point_moves_in_millimetres(self):
        arm = self.arm()
        x, y, z, pitch, _roll = arm.get_xyz()
        arm.move_to_xyz(x + 20.0, y + 10.0, z)
        here = arm.get_xyz()
        self.assertAlmostEqual(here[0], x + 20.0, delta=1.0)
        self.assertAlmostEqual(here[1], y + 10.0, delta=1.0)
        self.assertAlmostEqual(here[2], z, delta=1.0)
        self.assertAlmostEqual(here[3], pitch, delta=0.2)

    def test_go_home_again_returns_to_the_home_pose(self):
        arm = self.arm()
        arm.move_to_angles(base=6.0, shoulder=HOME["shoulder"] - 4.0)
        arm.go_home()
        for joint in ("base", "shoulder", "elbow"):
            self.assertAlmostEqual(arm.get_angles()[joint], HOME[joint], delta=0.1)

    def test_a_bad_target_raises_and_the_arm_can_carry_on(self):
        arm = self.arm()
        with self.assertRaises(MoverRefused):
            arm.move_to_angles(base=45.0)
        with self.assertRaises(ValueError):                  # MoverRefused is a ValueError
            arm.move_to_xyz(900.0, 0.0, 349.0)
        arm.move_to_angles(base=2.0)
        self.assertIsNone(arm.robot._fault)

    def test_a_move_that_takes_too_long_is_stopped_and_raises(self):
        arm = self.arm(step_delay_s=0.02)
        with self.assertRaises(TimeoutError):
            arm.move_to_angles(base=9.0, timeout_s=0.2)
        self.assertFalse(arm.is_moving())
        self.assertLess(arm.get_angles()["base"], 8.0)
        self.assertIsNone(arm.robot._fault, "a timeout is not a fault")
        arm.move_to_angles(base=0.0)                         # and the arm still works

    def test_a_fault_during_a_move_raises_without_waiting_for_the_timeout(self):
        arm = self.arm()
        arm.robot.sim.stream_stuck = True
        started = time.monotonic()
        with self.assertRaises(ScorbotError) as caught:
            arm.move_to_angles(base=9.0, timeout_s=60.0)
        self.assertNotIsInstance(caught.exception, TimeoutError)
        self.assertLess(time.monotonic() - started, 20.0)
        self.assertIn("not following", str(caught.exception))
        with self.assertRaises(ScorbotError):
            arm.move_to_angles(base=1.0)

    def test_a_loop_on_is_moving_sees_a_fault_instead_of_a_finished_move(self):
        arm = self.arm()
        arm.robot.sim.stream_stuck = True
        arm.move_to_angles(base=9.0, wait=False)
        deadline = time.monotonic() + 20
        with self.assertRaises(ScorbotError) as caught:
            while arm.is_moving() and time.monotonic() < deadline:
                time.sleep(0.01)
        self.assertIn("not following", str(caught.exception))

    def test_moving_or_reading_before_go_home_is_refused(self):
        arm = self.arm(home=False)
        for call in (lambda: arm.move_to_angles(base=1.0), arm.get_angles, arm.get_xyz,
                     arm.wait_for_move, lambda: arm.move_to_xyz(180.0, 0.0, 500.0)):
            with self.assertRaises(ScorbotError) as caught:
                call()
            self.assertIn("go_home", str(caught.exception))
        self.assertFalse(arm.is_moving())

    def test_the_gripper_opens_and_closes_between_moves(self):
        arm = self.arm()
        arm.move_to_angles(base=2.0, wait=False)
        self.assertEqual(arm.set_gripper("close").direction, "close")
        self.assertEqual(arm.set_gripper("open").direction, "open")
        with self.assertRaises(ValueError):
            arm.set_gripper("squeeze")
        arm.move_to_angles(base=0.0)

    def test_speed_is_a_percentage_of_the_default_and_never_more(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        log = Path(folder.name) / "run.controller.jsonl"
        arm = Arm(robot=SimulatedScorbot(controller=SimulatedController(step_delay_s=0.001),
                                         log_path=log))
        self.addCleanup(self.close, arm)
        arm.go_home()
        for bad in (0, -5, 101, float("nan"), "fast", True):
            with self.assertRaises(ValueError):
                arm.set_speed(bad)
        arm.set_speed(50)
        arm.move_to_angles(base=3.0)
        rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        starts = [row for row in rows if row["event"] == "stream_start"]
        self.assertAlmostEqual(starts[-1]["speed_fraction"], 0.125)

    def test_only_a_simulated_robot_is_accepted(self):
        with self.assertRaises(MoverRefused):
            Arm(robot=object())

    def test_the_with_block_leaves_nothing_running(self):
        robot = SimulatedScorbot(controller=SimulatedController(step_delay_s=0.001))
        with Arm(robot=robot) as arm:
            self.assertIn("SIMULATED", arm.status)
            self.assertIn("not measured", arm.status)
            arm.go_home()
            arm.move_to_angles(base=5.0, wait=False)
        self.assertIsNone(robot._stream)
        with self.assertRaises(ScorbotError) as caught:
            robot.get_state()
        self.assertIn("Not connected", str(caught.exception))

    def test_the_example_script_runs_and_says_it_is_simulated(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            runpy.run_path(str(EXAMPLE), run_name="__main__")
        self.assertIn("SIMULATED", out.getvalue())
        self.assertIn("mm", out.getvalue())


if __name__ == "__main__":
    unittest.main()
