"""What the browser page does, without the browser. No Viser import here.

``app.py`` turns widget events into calls on a ``Panel`` and draws what
``view()`` returns. Keeping the two apart means every rule on this page is
tested with plain Python: what a slider may ask for, what a refused drag
shows, what Stop does while the gripper is moving, what happens when the
browser goes away.

Simulator only (the ``Mover`` refuses anything else). The stop here is a
software stop, never an emergency stop.

Design: docs/specs/2026-10-05-three-front-doors-design.md.
"""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time

from .. import arm_view, source_model
from ..mover import Mover
from ..robot import ScorbotError

JOINTS = ("base", "shoulder", "elbow")
BUTTONS = ("home", "open", "close", "stop")
STOP_LABEL = "Software stop"
STOP_HOLD_S = 0.5      # after Stop, slider and handle events already in flight are ignored
STOP_NOTE = ("Software stop is not an emergency stop: it needs this program and the link "
             "to be working. Use the physical stop button.")


@dataclass(frozen=True)
class View:
    """Everything the page draws."""

    actual: arm_view.Scene                 # where the arm is
    ghost: arm_view.Scene | None           # where it has been told to go, while it is going
    angles: dict[str, float]               # degrees
    xyz: tuple[float, float, float, float, float]    # mm, mm, mm, tool pitch, tool roll
    status: str
    busy: str | None                       # "gripper" while it moves
    fault: str | None


class Panel:
    def __init__(self, mover, robot, *, now=time.monotonic):
        self.mover, self.robot = mover, robot
        self._now = now
        self._stopped_until = 0.0
        self._stop_sent = False            # the gripper has been told to stop because the browser went
        self._clients = 0
        self._busy: str | None = None
        self._busy_lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self.message = ""                  # the last refusal or failure, for the page

    # -- drawing ---------------------------------------------------------------

    def view(self) -> View:
        angles = self.mover.angles()
        order = ("base", "shoulder", "elbow", "pitch", "roll")
        target = self.mover.target_angles()
        ghost = None
        if target is not None and self.mover.moving() and not self.mover.arrived():
            pose = source_model.pose_for_arm(**target)
            ghost = arm_view.scene(tuple(pose[name] for name in order))
        fault = self.robot._fault
        return View(actual=arm_view.scene(tuple(angles[name] for name in order)), ghost=ghost,
                    angles=angles, xyz=source_model.xyzpr_from_angles(angles),
                    status=f"SIMULATED; {source_model.STATUS}", busy=self._busy,
                    fault=str(fault) if fault is not None else None)

    @staticmethod
    def slider_limits() -> dict[str, tuple[float, float]]:
        """Degrees each slider may show: the travel window, as joint angles."""
        return Mover.angle_limits()

    # -- controls --------------------------------------------------------------

    def _try(self, action) -> str | None:
        """Run a quick Mover call; return why it was refused, or None."""
        if self._busy:
            self.message = f"Busy: {self._busy}"
            return self.message
        try:
            action()
        except (ValueError, ScorbotError) as exc:      # MoverRefused is a ValueError
            self.message = str(exc)
            return self.message
        self.message = ""
        return None

    def _just_stopped(self) -> bool:
        # Viser runs callbacks on a thread pool, so a drag's last events can
        # land after Stop; they must not start the arm again.
        return self._now() < self._stopped_until

    def set_joint(self, name: str, degrees: float) -> str | None:
        if name not in JOINTS:
            raise ValueError(f"No slider for {name!r}")
        if self._just_stopped():
            return self.message
        return self._try(lambda: self.mover.set_target_angles(**{name: degrees}))

    def nudge_joint(self, name: str, delta: float) -> str | None:
        """Move one joint by a small amount from its latest requested angle."""
        if name not in JOINTS:
            raise ValueError(f"No joint control for {name!r}")
        if self._just_stopped():
            return self.message

        def move_one_step() -> None:
            angles = self.mover.target_angles() if self.mover.moving() else None
            if angles is None:
                angles = self.mover.angles()
            self.mover.set_target_angles(**{name: angles[name] + float(delta)})

        return self._try(move_one_step)

    def drag_tool(self, x: float, y: float, z: float) -> str | None:
        if self._just_stopped():
            return self.message
        return self._try(lambda: self.mover.set_target_xyz(x, y, z))

    def press(self, button: str) -> str | None:
        if button not in BUTTONS:
            raise ValueError(f"No button {button!r}")
        if button == "stop":
            return self._stop()
        if self._just_stopped():
            return self.message
        if button == "home":
            return self._try(self.mover.home)
        with self._busy_lock:
            if self._busy:
                self.message = f"Busy: {self._busy}"
                return self.message
            self._busy = "gripper"
        self._worker = threading.Thread(target=self._gripper, args=(button,), daemon=True)
        self._worker.start()
        return None

    def _gripper(self, direction: str) -> None:
        # Seconds long, so off the page's thread: Stop and the picture stay live.
        try:
            self.mover.gripper(direction)
            self.message = ""
        except (ValueError, ScorbotError) as exc:
            self.message = str(exc)
        finally:
            self._busy = None
            self._stop_sent = False

    def _stop(self) -> str | None:
        """Software stop: never queued, never waits for the gripper."""
        self._stopped_until = self._now() + STOP_HOLD_S
        if self._busy:
            self.robot.request_stop()      # ends the gripper move at its next message
        try:
            self.mover.stop()
        except ScorbotError as exc:
            self.message = str(exc)
            return self.message
        self.message = "Stopped"
        return None

    # -- the clock -------------------------------------------------------------

    def clients(self, count: int) -> None:
        """How many browser tabs are connected."""
        self._clients = max(0, int(count))

    def tick(self) -> None:
        """Call about ten times a second. With no browser, the move is given up."""
        if self._busy:
            if self._clients == 0 and not self._stop_sent:
                self._stop_sent = True      # nobody is watching a fixed-travel move
                self.robot.request_stop()
            return
        try:
            self.mover.poll(driving=self._clients > 0)
        except ScorbotError as exc:
            self.message = str(exc)

    def close(self) -> None:
        worker = self._worker
        if worker is not None:
            worker.join(timeout=30)
        self.mover.close()
