"""The velocity jog: ``Scorbot.start_velocity_jog`` and the ``VelocityJog`` handle it returns (``scorbot/vendor_manual.py``).

The messages are the lab DLL's ``EnterManual(0)``, ``MoveManual`` and ``CloseManual`` (``docs/protocol/LEDGER.md``).
The wall-clock deadline, the window margin and the legacy stop are ours. Unverified on the arm.
"""

from dataclasses import asdict
import queue

from . import limits, nominal, source_model, vendor_manual
from .calibration import MAX_COUNTS_FROM_HOME, signed_count_delta
from .errors import MotionStopped, ScorbotError
from .state import RobotState
from .vendor_home_source import VendorHomeSource

MAX_SPEED = 20
"""Ours: the largest speed the SDK takes for a first trial (the DLL takes 100). Speed 20 jogs the base about
19 counts per message."""
WINDOW_MARGIN_COUNTS = 2
"""Ours: the window is cut this much short of the travel cap and the source-model and vendor limits."""


def _edge(now: dict, motor: str, sign: int) -> int:
    """How far ``motor`` may go from ``now`` toward ``sign`` before the pose gets further past a joint limit
    or leaves the travel cap (counts from home). Bisection, like ``source_model.home_window``."""
    low_cap, high_cap = source_model.motion_window()[motor]
    low_cap = max(low_cap, -MAX_COUNTS_FROM_HOME)
    high_cap = min(high_cap, MAX_COUNTS_FROM_HOME)
    room = int(high_cap - now[motor]) if sign > 0 else int(now[motor] - low_cap)
    if room <= 0:
        return 0
    was = source_model.pose_limit_excess(now)

    def worse(distance):
        excess = source_model.pose_limit_excess({**now, motor: now[motor] + sign * distance})
        return any(over > was.get(joint, 0.0) + 1e-6 for joint, over in excess.items())

    low, high = 0, room
    if worse(high):
        while high - low > 1:
            middle = (low + high) // 2
            if worse(middle):
                high = middle
            else:
                low = middle
        high = low
    return high


def _window(now: dict, motor: str, absolute: dict) -> tuple:
    """The narrower source-model and absolute vendor window, cut ``WINDOW_MARGIN_COUNTS`` short."""
    was = nominal.vendor_limit_excess(absolute)
    edges = []
    for sign in (-1, 1):
        low, high = 0, _edge(now, motor, sign)

        def worse(distance, sign=sign):
            after = {**absolute, motor: absolute[motor] + sign * distance}
            return nominal.vendor_limit_problem(after) and any(
                over > was.get(name, 0.0) + 1e-6 for name, over in nominal.vendor_limit_excess(after).items())

        if worse(high):
            while high - low > 1:
                middle = (low + high) // 2
                if worse(middle):
                    high = middle
                else:
                    low = middle
            high = low
        edges.append(sign * max(0, high - WINDOW_MARGIN_COUNTS))
    return tuple(edges)


def _from_home_at(first, now: dict, before: RobotState) -> dict:
    """``now`` (counts from home at ``before``) moved by what the worker's first reply reads since then, so the
    window is measured from where the jog really starts. Both replies hold controller counts (CT13)."""
    shifted = dict(now)
    for slot, motor in enumerate(limits.RECORDED_MOTORS):
        then = before.signed_encoder_counts[motor]
        shifted[motor] += first.counts[slot] - then
    return shifted


class VelocityJog:
    """A running velocity jog. Made by ``Scorbot.start_velocity_jog``; until ``stop`` returns, every other command
    is refused. Use it as a context manager, or call ``stop`` yourself."""

    def __init__(self, robot, joint, speed, control, source, before):
        self.robot, self.joint, self.speed = robot, joint, speed
        self._control, self._source, self._before = control, source, before
        self.final_state = None

    @property
    def reason(self):
        """Why it ended ("stop", "deadline", "keepalive", "limit", "total", "stopped"), or None while running."""
        run = self._source.result
        return run.reason if run is not None else None

    def renew(self) -> None:
        """Re-arm the 300-message keep-alive and ``deadline_s``. Sends nothing. Raises once the jog has ended by itself
        (deadline, window edge, fault): call ``stop`` to collect the result."""
        if self.final_state is not None or self._source.result is not None or self._source.error is not None:
            raise ScorbotError(f"The velocity jog has ended ({self.reason or self._source.error}); call stop()")
        self._control.renew()

    def request_stop(self) -> None:
        """Called by ``Scorbot.request_stop``; the stop event already reaches the jog, so nothing more to do."""

    def stop(self) -> RobotState:
        """Ramp down and close the vendor's way, wait for the arm to settle and return its state. A stop request
        (``request_stop``, the pendant's Abort) during the jog raises ``MotionStopped`` here instead."""
        self._control.stop()
        return self.robot._end_velocity_jog(self)

    close = stop

    def __enter__(self):
        return self

    def __exit__(self, _type, _value, _traceback):
        if self.final_state is None and self.robot._stream is self:
            self.stop()


class ManualJogMixin:

    def start_velocity_jog(self, joint: str, speed: int, *, deadline_s: float = vendor_manual.DEFAULT_DEADLINE_S
                           ) -> VelocityJog:
        """Jog ``joint`` (base, shoulder or elbow) at ``speed``: the lab DLL's joint jog, unverified on the arm.

        ``speed`` is the DLL's ``MoveManual`` speed, a signed percent of the axis' manual speed, here 1 to
        ``MAX_SPEED`` in size; its sign is the DLL's (a positive base speed lowers the count). To change speed or
        direction, ``stop`` and start a new jog: the DLL ignores a new speed while a jog runs. The jog ends with
        the vendor's ramp-down and close when ``stop`` is called, when ``renew`` was not called for
        ``deadline_s`` (0.1 to 3 s), after 300 messages without ``renew``, after 30 s, or before the remaining
        ramp down would leave the narrower source-model and vendor-limit window, cut 2 counts short.
        Vendor limits read absolute controller counts; existing violations may shrink, never grow.

        Needs enabled motors, vendor Home in this session and the arm at rest; refused while a stream or another
        motion runs. ``request_stop`` and the pendant's Abort hold the arm where it is with the legacy jog's stop
        and ``stop`` raises ``MotionStopped``; that is not an emergency stop. The emergency input, an impact flag,
        a setpoint far ahead of the arm, a USB error or an unreadable reply switch the motors off and latch a
        fault. The step jog ``jog_joint`` is unchanged.
        """
        if not self._motion_lock.acquire(blocking=False):
            raise ScorbotError("Another motion is running; wait for it to end")
        try:
            self._refuse_if_streaming()
            if joint not in vendor_manual.AXES:
                raise ValueError(f"Velocity jog joint must be one of {', '.join(vendor_manual.AXES)}, not {joint!r}")
            if isinstance(speed, bool) or not isinstance(speed, int) or not 1 <= abs(speed) <= MAX_SPEED:
                raise ValueError(f"Speed must be a whole number from 1 to {MAX_SPEED} in size, either sign")
            control = vendor_manual.JogControl(deadline_s)
            if self._device is None:
                raise ScorbotError("Not connected")
            if not self._enabled or not self._homed or self._home_counts is None:
                raise ScorbotError("Enable and home before a velocity jog")
            if not (self._homed and self._gripper_homed):
                raise ScorbotError("Run vendor Home before jogging with vendor limits")
            before = self._motion_state()
            if self._consume_stop():
                self._record("stop_before_motion", joint=joint, speed=speed)
                raise MotionStopped("A stop was requested; the velocity jog was not started",
                                    state=before, started=False)
            before = self._settled_after_stop(before, fault="The arm was not at rest before a velocity jog")
            axis = vendor_manual.AXES[joint]
            try:
                now = {m: signed_count_delta(before.encoder_counts[m], self._home_counts[m])
                       for m in limits.RECORDED_MOTORS}
            except (KeyError, ValueError) as exc:
                raise ScorbotError(f"Cannot place the arm relative to home: {exc}") from exc
            window = _window(now, joint, before.signed_encoder_counts)
            direction = 1 if vendor_manual.slot(axis, speed) > 0 else -1
            if window[direction > 0] == 0:
                raise ValueError(f"The {joint} is at the edge of its travel cap, source-model or vendor limits "
                                 "that way (not measured)")
            def start_jog(first):
                run = yield from vendor_manual.velocity_jog(
                    first, axis, speed, control,
                    _window(_from_home_at(first, now, before), joint,
                            dict(zip(limits.RECORDED_MOTORS, first.counts))), settled=True)
                self._vendor_joint_hold = b"".join(int(v).to_bytes(4, "little", signed=True)
                                                 for v in (*run.setpoints[:5], 0, 0, 0))
                return run

            source = VendorHomeSource(start=start_jog,
                stop_event=self._stop_event, cancel_event=self._cancel_event,
                total_seconds=vendor_manual.MAX_SECONDS + 10.0)
            jog = VelocityJog(self, joint, speed, control, source, before)
            self._record("velocity_jog_start", joint=joint, speed=speed, deadline_s=deadline_s,
                         window_counts=list(window), state=asdict(before))
            if self._trace is not None:
                self._trace.start()
            control.renew()     # the settle wait above must not eat into the first deadline
            with self._lock:
                self._vendor_joint_target = None
                self._vendor_joint_hold = None
                self._stream = jog
                self._commands.put([vendor_manual.MANUAL_ORDER, source, 0])
            return jog
        finally:
            self._motion_lock.release()

    def _end_velocity_jog(self, jog: VelocityJog) -> RobotState:
        with self._motion_lock:
            if self._stream is not jog:
                if jog.final_state is None:
                    raise ScorbotError("The velocity jog has already ended")
                return jog.final_state
            failure, interrupt = None, None
            try:
                with self._lock:
                    result = self._next_result(self.command_timeout)
                    if result != 0:
                        while self._next_result(self.command_timeout) != 0:
                            pass
                        failure = f"Velocity jog failed: {jog._source.error or f'error code {result}'}"
            except queue.Empty:
                failure = "The velocity jog did not end; physical stop may be required"
                self._cancel_event.set()
            except ScorbotError as exc:
                failure = str(exc)
            except (KeyboardInterrupt, SystemExit) as exc:
                failure = "Python interrupted while a velocity jog was ending; physical stop may be required"
                interrupt = exc
                self._cancel_event.set()
            finally:
                self._stream = None
                packets, dropped = self._trace.stop() if self._trace is not None else ([], 0)
            run = jog._source.result
            with self._stop_lock:
                too_late = self._stop_event.is_set() and failure is None and not (run and run.stopped)
                self._stop_event.clear()
            if failure is not None:
                self._latch_fault(failure)
                if self._link_alive():
                    self._commands.put([16, 1, 1])  # Best effort; never an emergency stop.
                self._record("velocity_jog_fault", error=self._fault, messages=jog._source.messages)
                if interrupt is not None:
                    raise interrupt
                raise ScorbotError(f"{self._fault}. Motors-off was sent but is not confirmed: check the MOTORS LED")
            if too_late:
                self._record("stop_too_late", joint=jog.joint)
            self._record("motion_trace", joint=jog.joint, packets=packets, dropped_packets=dropped)
            after = self._settled_after_stop(jog._before, fault="The arm was still moving after a velocity jog")
            jog.final_state = after
            self._record("velocity_jog_stopped" if run.stopped else "velocity_jog_complete", joint=jog.joint,
                         speed=jog.speed, reason=run.reason, setpoint_counts=run.end - run.start,
                         state=asdict(after))
            if run.stopped:
                raise MotionStopped("The velocity jog ended early on a stop request", state=after)
            return after
