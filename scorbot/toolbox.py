"""A teaching API for the arm in degrees and millimetres. Simulator only.

    from scorbot.toolbox import Arm

    with Arm() as arm:
        arm.go_home()
        arm.move_by_angles(base=5)
        print(arm.get_xyz())

The names follow the USNA ScorBot Toolbox for MATLAB, which has taught on this
arm for years: ``go_home`` (ScorHome / ScorGoHome), ``get_angles``
(ScorGetBSEPR), ``get_xyz`` (ScorGetXYZPR), ``move_to_angles`` (ScorSetBSEPR),
``move_by_angles`` (ScorSetDeltaBSEPR), ``move_to_xyz`` (ScorSetXYZPR),
``set_gripper`` (ScorSetGripper), ``wait_for_move`` (ScorWaitForMove),
``is_moving`` (ScorIsMoving), ``set_speed`` (ScorSetSpeed).

Every angle and position comes from ``source_model.py``: the vendor's formula
and parameter files, not measured on our arm. Motion stays inside the travel
cap (180 degrees from home since 2026-10-06, so the joint limits bind, shown
by ``Mover.angle_limits``) and only the base,
shoulder and elbow move; the wrist is read-only. Never run on the arm.

Design: docs/specs/2026-10-05-three-front-doors-design.md.
"""

from __future__ import annotations

import math
import time

from . import source_model
from .mover import Mover, MoverRefused
from .robot import ScorbotError
from .simulated import SimulatedScorbot
from .streaming import DEFAULT_SPEED_FRACTION

POLL_S = 0.05


class Arm:
    def __init__(self, *, robot=None, log_path=None, move_timeout_s: float = 30.0):
        if robot is None:
            robot = SimulatedScorbot(log_path=log_path)
        elif not isinstance(robot, SimulatedScorbot):
            raise MoverRefused("Arm drives a simulated robot only; the real arm waits for "
                               "the lab acceptance run")
        self.robot = robot
        self.move_timeout_s = move_timeout_s
        self.status = f"SIMULATED; {source_model.STATUS}"
        self._mover: Mover | None = None
        self._speed_fraction = DEFAULT_SPEED_FRACTION
        robot.connect()
        robot.enable()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    def close(self) -> None:
        """End any move and disconnect. Safe to call twice."""
        try:
            if self._mover is not None:
                self._mover.close()
        finally:
            self.robot.disconnect()

    def _need_mover(self) -> Mover:
        if self._mover is None:
            raise ScorbotError("Call go_home() first")
        return self._mover

    # -- reading ---------------------------------------------------------------

    def get_angles(self) -> dict[str, float]:
        """Base, shoulder, elbow, pitch and roll in degrees."""
        return self._need_mover().angles()

    def get_xyz(self) -> tuple[float, float, float, float, float]:
        """Tool point x, y, z in mm, then tool pitch and roll in degrees."""
        return self._need_mover().xyz()

    def is_moving(self) -> bool:
        """True while a move is under way. Safe to loop on; raises if the session faults.

        A move started with ``wait=False`` only keeps going while the script
        keeps calling this or ``wait_for_move``. Left alone, the arm slows to
        rest after about half a second.
        """
        if self._mover is None:
            return False
        self._mover.poll()
        if self.robot._fault is not None:
            raise ScorbotError(f"Session faulted: {self.robot._fault}")
        return self._mover.moving() and not self._mover.arrived()

    # -- moving ----------------------------------------------------------------

    def go_home(self, wait: bool = True, timeout_s: float | None = None) -> None:
        """Home the arm; once homed, move back to the home pose."""
        if self._mover is None:
            self.robot.home(start_position_confirmed=True)
            self._mover = Mover(self.robot, speed_fraction=self._speed_fraction)
            return
        self._mover.home()
        self._after(wait, timeout_s)

    def move_to_angles(self, *, base: float | None = None, shoulder: float | None = None,
                       elbow: float | None = None, wait: bool = True,
                       timeout_s: float | None = None) -> None:
        """Move the joints named to these angles in degrees.

        With ``wait=False`` this returns at once; see ``is_moving``.
        """
        self._need_mover().set_target_angles(base=base, shoulder=shoulder, elbow=elbow)
        self._after(wait, timeout_s)

    def move_by_angles(self, *, base: float = 0.0, shoulder: float = 0.0, elbow: float = 0.0,
                       wait: bool = True, timeout_s: float | None = None) -> None:
        """Move the joints by these amounts in degrees from where the arm is."""
        now = self.get_angles()
        self.move_to_angles(base=now["base"] + base, shoulder=now["shoulder"] + shoulder,
                            elbow=now["elbow"] + elbow, wait=wait, timeout_s=timeout_s)

    def move_to_xyz(self, x: float, y: float, z: float, *, wait: bool = True,
                    timeout_s: float | None = None) -> None:
        """Move the tool point to (x, y, z) in mm; the tool keeps its angle to the floor."""
        self._need_mover().set_target_xyz(x, y, z)
        self._after(wait, timeout_s)

    def _after(self, wait: bool, timeout_s: float | None) -> None:
        if wait:
            self.wait_for_move(timeout_s)

    def wait_for_move(self, timeout_s: float | None = None) -> None:
        """Return when the arm has arrived.

        Raises ``TimeoutError`` (after stopping the move) if it takes longer
        than ``timeout_s``, and the session's error at once if it faults.
        """
        mover = self._need_mover()
        deadline = time.monotonic() + (self.move_timeout_s if timeout_s is None else timeout_s)
        while True:
            mover.poll()
            if self.robot._fault is not None:
                raise ScorbotError(f"Session faulted: {self.robot._fault}")
            if mover.target_angles() is None:
                return                       # nothing asked for, or stopped
            if mover.arrived():
                mover.close()
                return
            if time.monotonic() > deadline:
                mover.stop()
                raise TimeoutError("The move did not finish in time and was stopped")
            time.sleep(POLL_S)

    def set_gripper(self, state: str):
        """``"open"`` or ``"close"``. Ends any move first."""
        if self._mover is not None:
            return self._mover.gripper(state)
        return self.robot.move_gripper(state)

    def set_speed(self, percent: float) -> None:
        """Speed for the moves that follow: 1 to 100 percent of the default, never more."""
        if isinstance(percent, bool) or not isinstance(percent, (int, float)) \
                or not math.isfinite(percent) or not 1 <= percent <= 100:
            raise ValueError("Speed is a percentage from 1 to 100")
        self._speed_fraction = DEFAULT_SPEED_FRACTION * percent / 100.0
        if self._mover is not None:
            self._mover.speed_fraction = self._speed_fraction
