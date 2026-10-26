"""examples/teleop.py against FakeArm and a scripted key stream: no arm, no USB."""

import contextlib
import io
import unittest
from unittest import mock

from examples import teleop
from scorbot.bridge import ArmError, FakeArm
from scorbot.keys import keyboard_commands


def keys(text):
    chars = iter(text)
    return lambda: next(chars)


class Recorder(FakeArm):
    def __init__(self):
        super().__init__()
        self.calls = []

    def move_by(self, dx=0.0, dy=0.0, dz=0.0):
        self.calls.append(("by", dx, dy, dz))
        return super().move_by(dx, dy, dz)

    def wrist_by(self, dpitch=0.0, droll=0.0):
        self.calls.append(("wrist", dpitch, droll))
        return super().wrist_by(dpitch, droll)

    def gripper(self, open):  # noqa: A002
        self.calls.append(("gripper", open))
        return super().gripper(open)


class DriveTests(unittest.TestCase):
    def test_keys_become_moves(self):
        arm = Recorder()
        done = teleop.drive(arm, keyboard_commands(keys("dwfocq"), 5))
        self.assertEqual(done, 5)
        self.assertEqual(arm.calls, [("by", 5, 0, 0), ("by", 0, 5, 0), ("by", 0, 0, -5), ("gripper", True), ("gripper", False)])

    def test_wrist_keys_need_a_wrist_step(self):
        arm = Recorder()
        teleop.drive(arm, keyboard_commands(keys("tgyhq"), 5, wrist_step=200))
        self.assertEqual(arm.calls, [("wrist", 200, 0), ("wrist", -200, 0), ("wrist", 0, 200), ("wrist", 0, -200)])
        off = Recorder()
        teleop.drive(off, keyboard_commands(keys("tgyhq"), 5))
        self.assertEqual(off.calls, [])

    def test_plain_refusal_goes_on(self):
        arm = Recorder()
        refused = ArmError("outside the box", {"error": "outside the box"})
        said = []
        with mock.patch.object(arm, "move_by", side_effect=[refused, None]):
            done = teleop.drive(arm, keyboard_commands(keys("ddq"), 5), said.append)
        self.assertEqual(done, 1)
        self.assertEqual(len(said), 1)

    def test_dll_error_ends_the_loop(self):
        arm = Recorder()
        failed = ArmError("move failed", {"error": "move failed", "code": 911})
        with mock.patch.object(arm, "move_by", side_effect=failed):
            with self.assertRaises(ArmError):
                teleop.drive(arm, keyboard_commands(keys("ddq"), 5))

    def test_a_busy_reply_ends_the_loop(self):
        arm = Recorder()
        busy = ArmError("busy: another client", {"ok": False, "busy": True, "error": "busy: another client"})
        with mock.patch.object(arm, "move_by", side_effect=busy):
            with self.assertRaises(ArmError):
                teleop.drive(arm, keyboard_commands(keys("ddq"), 5))

    def test_status_line_after_each_key(self):
        arm, lines = Recorder(), []
        refused = ArmError("outside the box", {"error": "outside the box"})
        with mock.patch.object(arm, "move_by", side_effect=[arm.where(), refused, arm.where()]):
            teleop.drive(arm, keyboard_commands(keys("dddq"), 5), lambda _: None, lines.append)
        self.assertEqual(len(lines), 3)
        self.assertIn("step 1: ok", lines[0])
        self.assertIn("refused 0", lines[0])
        self.assertIn(f"x {arm.where().x:.1f} y {arm.where().y:.1f} z {arm.where().z:.1f} mm", lines[0])
        self.assertIn("refused: outside the box", lines[1])
        self.assertIn("refused 1", lines[1])
        self.assertIn("step 2: ok", lines[2])      # a refused key is not a step
        self.assertIn("refused 1", lines[2])

    def test_status_line_shortens_a_long_refusal(self):
        arm, lines = Recorder(), []
        refused = ArmError("x" * 200, {"error": "x" * 200})
        with mock.patch.object(arm, "move_by", side_effect=refused):
            teleop.drive(arm, keyboard_commands(keys("dq"), 5), lambda _: None, lines.append)
        self.assertLess(len(lines[0]), 100)

    def test_status_line_sends_the_same_requests(self):
        plain, with_status = Recorder(), Recorder()
        text = "dwfotgq"
        teleop.drive(plain, keyboard_commands(keys(text), 5, wrist_step=200))
        teleop.drive(with_status, keyboard_commands(keys(text), 5, wrist_step=200), print, lambda _: None)
        self.assertEqual(plain.calls, with_status.calls)


class MainTests(unittest.TestCase):
    def test_needs_go(self):
        self.assertEqual(teleop.main(["--fake"], read=lambda _: "no", keys=keys("q")), 1)

    def test_step_cap(self):
        with self.assertRaises(SystemExit):
            teleop.main(["--fake", "--step-mm", "21"], read=lambda _: "go", keys=keys("q"))

    def test_wrist_step_cap(self):
        with self.assertRaises(SystemExit):
            teleop.main(["--fake", "--wrist-step", "5001"], read=lambda _: "go", keys=keys("q"))

    def test_an_arm_error_says_to_check_where(self):
        failed = ArmError("move failed", {"error": "move failed", "code": 911})
        said = io.StringIO()
        with mock.patch.object(FakeArm, "move_by", side_effect=failed), contextlib.redirect_stdout(said):
            code = teleop.main(["--fake"], read=lambda _: "go", keys=keys("dq"), waiting=lambda: False)
        self.assertEqual(code, 1)
        self.assertIn("stopped: move failed", said.getvalue())
        self.assertIn("may have moved", said.getvalue())
        self.assertIn("where", said.getvalue())

    def test_fake_run_ends_on_q(self):
        self.assertEqual(teleop.main(["--fake"], read=lambda _: "go", keys=keys("dq"), waiting=lambda: False), 0)


if __name__ == "__main__":
    unittest.main()
