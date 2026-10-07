"""Joint-angle targets in degrees on top of ``Scorbot.start_stream``. Simulator only.

The teaching API (``toolbox.py``) and the browser page (``ui/``) both move the
arm through one ``Mover``, so there is a single owner of the stream. Angles
come from ``source_model.py``: the vendor's formula and parameter files, not
measured on our arm. Never run on the arm; the constructor refuses anything
but a ``SimulatedScorbot``.

A stream lives only while something is driving it. While one is open
``Scorbot`` refuses every other command (gripper, home), and its packet trace
covers well under a minute, so the ``Mover`` closes a stream that has arrived,
that nobody is driving, or that has grown old, and opens the next one itself.

Design: docs/specs/2026-10-05-three-front-doors-design.md.
"""

from __future__ import annotations

import threading
import time

from . import limits, source_model
from .calibration import signed_count_delta
from .robot import MotionStopped, ScorbotError
from .simulated import SimulatedScorbot
from .streaming import DEFAULT_SPEED_FRACTION, StreamRefused

ARM_JOINTS = limits.ARM_MOTORS        # base, shoulder, elbow: joint and motor share a name


class MoverRefused(ValueError):
    """A target the Mover will not send. Nothing moved and the session is not faulted."""


class Mover:
    IDLE_CLOSE_S = 1.0        # close a stream that has arrived, or that nobody is driving
    MAX_STREAM_S = 30.0       # a stream's packet trace covers about 48 s (docs/project/BACKLOG.md)
    ARRIVE_COUNTS = 5

    def __init__(self, robot, *, speed_fraction: float = DEFAULT_SPEED_FRACTION,
                 now=time.monotonic):
        if not isinstance(robot, SimulatedScorbot):
            raise MoverRefused("The Mover drives a simulated robot only; the real arm waits "
                               "for the lab acceptance run")
        if robot._home_counts is None:
            raise ScorbotError("Enable and home before making a Mover")
        self.robot = robot
        # Kept here: a fault clears the robot's own copy, and the page still
        # has to show where the arm is.
        self._home = dict(robot._home_counts)
        self.speed_fraction = speed_fraction
        self._now = now
        self._lock = threading.RLock()
        self._stream = None
        self._opened = self._last_target = self._last_driven = 0.0
        self._target_deg: dict[str, float] | None = None
        self._target: dict[str, int] | None = None
        self._pending = False          # the target has not been reached and given up on
        self._stops = 0                # how many times stop() has been asked for

    # -- reading ---------------------------------------------------------------

    def _counts(self) -> dict[str, int]:
        raw, home = self.robot.get_state().encoder_counts, self._home
        return {m: signed_count_delta(raw[m], home[m]) for m in limits.RECORDED_MOTORS}

    def angles(self) -> dict[str, float]:
        """The five joint angles now, in degrees (source model, not measured)."""
        return source_model.angles_from_counts(self._counts())

    def target_angles(self) -> dict[str, float] | None:
        """The base, shoulder and elbow angles last asked for, or None."""
        with self._lock:
            return dict(self._target_deg) if self._target_deg else None

    def arrived(self) -> bool:
        with self._lock:
            if self._target is None:
                return False
            counts = self._counts()
            return all(abs(counts[m] - self._target[m]) <= self.ARRIVE_COUNTS
                       for m in ARM_JOINTS)

    def moving(self) -> bool:
        """True while a stream is open."""
        with self._lock:
            return self._live() is not None

    # -- targets ---------------------------------------------------------------

    def set_target_angles(self, *, base: float | None = None, shoulder: float | None = None,
                          elbow: float | None = None) -> dict[str, int]:
        """Head for these joint angles (degrees). Returns the motor-count target.

        A joint left out keeps its target while a move is under way, and
        otherwise stays where the arm is now. Raises ``MoverRefused`` for a target outside the travel cap or a
        joint limit; the arm carries on with what it was doing.
        """
        with self._lock:
            if self.robot._fault is not None:
                raise ScorbotError(f"Session faulted: {self.robot._fault}")
            if self._pending and self._target_deg is not None:
                wanted = dict(self._target_deg)
            else:                      # no move under way: start from where the arm is
                now = self.angles()
                wanted = {joint: now[joint] for joint in ARM_JOINTS}
            for joint, value in (("base", base), ("shoulder", shoulder), ("elbow", elbow)):
                if value is not None:
                    wanted[joint] = value
            try:
                pose = source_model.pose_for_arm(**wanted)
                outside = source_model.outside_window(pose)
                target = source_model.arm_counts_from_angles(**wanted)
            except (TypeError, ValueError) as exc:
                raise MoverRefused(f"Not a usable target: {exc}") from exc
            if outside:
                raise MoverRefused(self._outside_message(outside))
            try:
                self._send(target)
            except StreamRefused as exc:
                raise MoverRefused(str(exc)) from exc
            self._target_deg, self._target, self._pending = wanted, target, True
            self._last_target = self._last_driven = self._now()
            return dict(target)

    @staticmethod
    def angle_limits() -> dict[str, tuple[float, float]]:
        """Degrees each of base, shoulder and elbow may be asked for, alone from home.

        The whole-pose limits, with the other motors at home: the elbow's
        angle also depends on the shoulder's motor, and the wrist pitch on
        both, so the ends move with the others; every target is still checked.
        """
        limits = {}
        for joint, (low, high) in source_model.home_window().items():
            ends = sorted(source_model.angles_from_counts(
                dict(dict.fromkeys(source_model.MOTORS, 0), **{joint: round(edge)}))[joint]
                for edge in (low, high))
            limits[joint] = (ends[0], ends[1])
        return limits

    @staticmethod
    def _outside_message(outside: dict) -> str:
        window, per_degree = source_model.home_window(), source_model.COUNTS_PER_DEGREE
        angles = Mover.angle_limits()
        parts = []
        # A wrist motor outside its window is usually a side effect of an arm joint being
        # far out; name it only when nothing else is the reason.
        reasons = [motor for motor in outside if motor in window] or list(outside)
        for motor in reasons:
            if motor in window:
                low, high = (edge / per_degree[motor] for edge in window[motor])
                parts.append(f"{motor} (it may be {angles[motor][0]:.1f} to {angles[motor][1]:.1f} "
                             f"degrees: {low:+.1f} to {high:+.1f} from home)")
            else:
                parts.append(f"{motor} (the wrist pitch would pass its limit while the wrist "
                             "motors stand still; wrist motion is disabled)")
        return ("Target is outside the travel cap or a joint limit: " + "; ".join(parts)
                + ". Source model, not measured.")

    def xyz(self) -> tuple[float, float, float, float, float]:
        """Tool point (x, y, z in mm) and tool pitch and roll (degrees) now."""
        return source_model.xyzpr_from_angles(self.angles())

    def set_target_xyz(self, x: float, y: float, z: float) -> dict[str, int]:
        """Head the tool point for (x, y, z) in mm. Returns the motor-count target.

        The wrist motors do not move, so the tool keeps the angle to the floor
        it has now. The joint angles come from the source model's geometry and
        are sent as count targets like any other; this exists for the
        simulator only (project rule: no Cartesian motion on the arm).
        """
        with self._lock:
            pitch, roll = self.xyz()[3:]
            try:
                angles = source_model.angles_from_xyzpr(x, y, z, pitch, roll)
            except (TypeError, ValueError) as exc:
                raise MoverRefused(f"The arm cannot reach that point: {exc}") from exc
            return self.set_target_angles(**{joint: angles[joint] for joint in ARM_JOINTS})

    def home(self) -> dict[str, int]:
        """Head back to the home pose. (Homing itself is ``Scorbot.home``, done before.)"""
        return self.set_target_angles(**{joint: source_model.HOME_ANGLES[joint]
                                         for joint in ARM_JOINTS})

    # -- the stream ------------------------------------------------------------

    def _live(self):
        """The open stream, or None; forgets one that has ended on its own."""
        if self._stream is not None and self.robot._stream is not self._stream:
            self._stream = None
        return self._stream

    def _send(self, target: dict) -> None:
        if self._live() is None:
            self._stream = self.robot.start_stream(speed_fraction=self.speed_fraction)
            self._opened = self._now()
        self._stream.set_target({motor: float(target[motor]) for motor in ARM_JOINTS})

    def _end(self, *, stop: bool = False, quiet: bool = True) -> None:
        stream, self._stream = self._live(), None
        if stream is None:
            return
        try:
            stream.stop() if stop else stream.close()
        except MotionStopped:
            pass
        except ScorbotError:
            self._forget()        # the session has latched its fault; it stays latched
            if not quiet:
                raise

    def _forget(self) -> None:
        self._target = self._target_deg = None
        self._pending = False

    def poll(self, driving: bool = True) -> None:
        """Keep the move going. Call about ten times a second while one matters.

        ``driving`` says somebody still wants the move: a waiting script, or a
        browser that is connected. A stream nobody has driven for
        ``IDLE_CLOSE_S`` is closed wherever the arm is.
        """
        with self._lock:
            now = self._now()
            if driving:
                self._last_driven = now
            stream = self._live()
            if self.robot._fault is not None or (stream and stream.core.fault):
                self._end()
                self._forget()
                return
            if stream is None:
                if driving and self._pending and not self.arrived():
                    self._send(self._target)
                return
            try:
                arrived = self.arrived()
            except (ScorbotError, ValueError) as exc:
                # No trustworthy reading of the arm: as for a jog, that ends
                # the move and latches the session.
                self._end(stop=True)
                self._forget()
                if self.robot._fault is None:
                    self.robot._latch_fault(f"Controller feedback is unavailable: {exc}")
                raise ScorbotError(f"Session faulted: {self.robot._fault}") from exc
            if now - self._last_driven > self.IDLE_CLOSE_S or \
                    (arrived and now - self._last_target > self.IDLE_CLOSE_S):
                self._pending = False
                self._end()
            elif now - self._opened > self.MAX_STREAM_S:
                self._end()               # still pending: the next poll opens a new one
            else:
                try:
                    stream.set_target({m: float(self._target[m]) for m in ARM_JOINTS})
                except StreamRefused:
                    pass                  # it ended under us; the next poll sorts it out

    def gripper(self, direction: str):
        """Open or close the gripper (``Scorbot.move_gripper``), ending any move first."""
        stops = self._stops
        with self._lock:
            self._pending = False
            self._end()
        # Ending the stream clears the session's stop request, so a stop asked
        # for while it was ending would otherwise be lost and the gripper run.
        if self._stops != stops:
            raise MotionStopped("A stop was requested; the gripper was not moved",
                                state=None, started=False)
        # Outside the lock: the move takes seconds, and reading the arm or
        # asking for a stop must not wait for it.
        return self.robot.move_gripper(direction)

    def stop(self) -> None:
        """Software stop: end the move now and forget the target.

        Not an emergency stop. It needs this program and the link to be
        working; the physical stop button is the one that counts.
        Raises ``ScorbotError`` if the stream could not be ended; the session
        is then faulted.
        """
        self._stops += 1
        stream = self._stream
        if stream is not None:
            stream.request_stop()      # before the lock: a poll may be closing the stream
        with self._lock:
            self._forget()
            self._end(stop=True, quiet=False)

    def close(self) -> None:
        """Slow to rest and end any stream. Safe to call twice."""
        with self._lock:
            self._pending = False
            self._end()
