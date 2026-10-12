"""The public surface of scorbot.robot, pinned so a refactor cannot drop or rename anything."""

import inspect
import json
import os
import re
import unittest
from pathlib import Path

import scorbot
from scorbot.robot import Scorbot

SNAPSHOT = Path(__file__).resolve().parent / "fixtures" / "scorbot_surface.json"
SIMPLE = (int, float, str, bool, tuple, type(None))
MODULE_PATH = re.compile(r"\bscorbot(\.[a-z_]+)+\.")   # a class that moves modules keeps its public name


PRIVATE_PATH = re.compile(r"\bpathlib\._local\.")      # Python 3.13 spells pathlib.Path this way; 3.12 does not


def surface():
    """Every public attribute of Scorbot (inherited ones included) with its signature or simple value.

    Names with a leading underscore are implementation, free to move between modules; the full suite
    still catches a private method that a caller or a test needs.
    """
    out = {}
    for name, member in inspect.getmembers(Scorbot):
        if name.startswith("_") and name != "__init__":
            continue
        if inspect.isfunction(member):
            out[name] = "def" + MODULE_PATH.sub("", PRIVATE_PATH.sub("pathlib.", str(inspect.signature(member))))
        elif isinstance(member, SIMPLE):
            out[name] = "value " + repr(member)
        elif isinstance(member, (dict, frozenset, set, list)):
            out[name] = "value " + repr(sorted(member, key=repr) if not isinstance(member, dict) else sorted(member.items(), key=repr))
    out["__all__"] = repr(sorted(scorbot.__all__))
    return out


class SurfaceTests(unittest.TestCase):
    def test_the_public_surface_is_unchanged(self):
        current = surface()
        if os.environ.get("UPDATE_SURFACE") == "1":
            SNAPSHOT.write_text(json.dumps(current, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        self.assertEqual(sorted(set(expected) - set(current)), [], "names removed from Scorbot")
        self.assertEqual(sorted(set(current) - set(expected)), [], "names added to Scorbot")
        changed = {name for name in expected if name in current and expected[name] != current[name]}
        self.assertEqual(sorted(changed), [], "a signature or constant changed")


class MovedExceptionTests(unittest.TestCase):
    """The exceptions moved to errors.py, but nothing a caller can observe may change."""

    def test_they_still_say_scorbot_robot_in_tracebacks_and_pickles(self):
        import pickle
        import traceback

        from scorbot import errors
        from scorbot.robot import MotionStopped, ScorbotError, _WorkerCrashed

        for cls in (ScorbotError, MotionStopped, _WorkerCrashed):
            with self.subTest(cls.__name__):
                self.assertIs(getattr(errors, cls.__name__), cls)
                self.assertEqual(cls.__module__, "scorbot.robot")
                self.assertEqual(traceback.format_exception_only(cls, cls("x")),
                                 [f"scorbot.robot.{cls.__name__}: x\n"])
                data = pickle.dumps(cls("x"))
                self.assertIn(b"scorbot.robot", data)
                self.assertNotIn(b"scorbot.errors", data)
                self.assertEqual(pickle.loads(data).args, ("x",))

    def test_the_annotations_of_motion_stopped_resolve_at_run_time(self):
        import typing

        from scorbot.robot import MotionStopped
        from scorbot.state import RobotState

        hints = typing.get_type_hints(MotionStopped.__init__)
        self.assertEqual(hints["state"], typing.Optional[RobotState])
        self.assertIs(hints["started"], bool)


class MovedClassTests(unittest.TestCase):
    def test_gripper_move_keeps_its_identity_after_moving_to_gripper_py(self):
        import pickle

        from scorbot import GripperMove
        from scorbot.gripper import GripperMove as FromGripper
        from scorbot.robot import GripperMove as FromRobot
        from scorbot.state import ENCODER_OFFSETS, decode_state

        self.assertIs(GripperMove, FromRobot)
        self.assertIs(GripperMove, FromGripper)
        self.assertEqual(GripperMove.__module__, "scorbot.robot")        # what a pickle will name
        packet = bytearray(64)
        for offset in ENCODER_OFFSETS:
            packet[offset + 2] = 128
        state = decode_state(bytes(packet), connected=True, enabled=True, homed=True, fault=None)
        move = GripperMove("open", 2700, 2700, True, state)
        data = pickle.dumps(move)
        self.assertIn(b"scorbot.robot", data)
        self.assertNotIn(b"scorbot.gripper", data)
        self.assertEqual(pickle.loads(data), move)
