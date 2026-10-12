"""The exceptions Scorbot raises. A leaf module: it imports nothing at run time, so any module can use it.

Moved out of robot.py unchanged (docs/specs/2026-10-12-code-health-design.md); robot.py still exports
the names, so ``from scorbot.robot import ScorbotError`` keeps working.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .state import RobotState


class ScorbotError(RuntimeError):
    """A command or controller operation failed."""


class _WorkerCrashed(ScorbotError):
    """The command worker reported an exception and is exiting; it answers nothing more."""


class MotionStopped(ScorbotError):
    """A jog ended early because ``request_stop`` was called. Not a fault.

    ``state`` is the controller state read after the arm settled, or the state
    before the jog when it was never started (``started`` is then False and
    nothing was sent). A stop does not promise the arm travelled less than the
    jog: compare ``state`` with the state before.
    """

    def __init__(self, message: str, state: RobotState | None = None, *,
                 started: bool = True):
        super().__init__(message)
        self.state = state
        self.started = started
