"""examples/teleop.py against FakeArm and a scripted key stream: no arm, no USB."""

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

    def gripper(self, open):  # noqa: A002
        self.calls.append(("gripper", open))
        return super().gripper(open)


class DriveTests(unittest.TestCase):
    def test_keys_become_moves(self):
        arm = Recorder()
        done = teleop.drive(arm, keyboard_commands(keys("dwfocq"), 5))
        self.assertEqual(done, 5)
        self.assertEqual(arm.calls, [("by", 5, 0, 0), ("by", 0, 5, 0), ("by", 0, 0, -5), ("gripper", True), ("gripper", False)])

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


class MainTests(unittest.TestCase):
    def test_needs_go(self):
        self.assertEqual(teleop.main(["--fake"], read=lambda _: "no", keys=keys("q")), 1)

    def test_step_cap(self):
        with self.assertRaises(SystemExit):
            teleop.main(["--fake", "--step-mm", "21"], read=lambda _: "go", keys=keys("q"))

    def test_fake_run_ends_on_q(self):
        self.assertEqual(teleop.main(["--fake"], read=lambda _: "go", keys=keys("dq"), waiting=lambda: False), 0)


if __name__ == "__main__":
    unittest.main()
