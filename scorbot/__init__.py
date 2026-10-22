"""Python interface for the ScorBot ER-4U.

Experimental: this package is "Stack 2", our own USB protocol (``Scorbot``, ``SimulatedScorbot``). It has never
moved the arm. The arm moves today through the vendor-DLL path: ``scorbot.bridge`` (``Arm``, ``FakeArm``),
documented in docs/API.md and docs/lab/RUN_THE_ARM.md.
"""

from .robot import GripperMove, MotionStopped, Scorbot, ScorbotError
from .simulated import SimulatedScorbot
from .state import RobotState
from .streaming import Stream, StreamRefused

__all__ = ["GripperMove", "MotionStopped", "Scorbot", "ScorbotError", "RobotState", "SimulatedScorbot",
           "Stream", "StreamRefused"]
