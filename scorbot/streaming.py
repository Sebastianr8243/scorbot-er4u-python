"""Streaming target follower: the logic, with no USB, threads or sleeps in it.

A caller keeps updating a target (motor counts from home); once per period
``StreamCore.step`` moves the commanded position a small, limited step toward
it and says what to send. ``Scorbot.start_stream`` connects this to the arm
through the legacy worker; the simulator drives the same code.

Requirements and decisions:
specs/2026-10-04-streaming-driver-requirements.md (in git history) (R1-R12).
**Never run on the arm.** Every limit and period here is a prior.

Smoothing uses Ruckig (jerk-limited online trajectory generation). It is an
optional extra (``pip install .[planning]``), imported when a stream is
created.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import math
import threading
import time

from . import limits
from .calibration import signed_count_delta
from .state import (COUNT_MAX, COUNT_MIN, IMPACT_DETECT_COUNTS, collision_motors, decode_state, signed_error_word,
                    teach_mode_after)
from .vendor_profile import MANUAL_ACCEL_FRACTION, MANUAL_JERK_FRACTION, VendorProfile

MOTORS = limits.ARM_MOTORS
WRIST_STREAM_MOTORS = MOTORS + limits.WRIST_MOTORS   # a wrist stream: setpoint order
DEFAULT_PERIOD_S = 0.024          # vendor planner period (PCPeriod x USBCPeriod); a prior
DEFAULT_HOLD_TIMEOUT_S = 0.5      # ours
# What Scorbot.start_stream accepts (ours). A long period turns each step into
# a jump the size of the lead limit; a long hold timeout switches the hold off.
PERIOD_RANGE_S = (0.01, 0.05)
HOLD_TIMEOUT_RANGE_S = (0.05, 2.0)
VENDOR_MAX_SPEED_COUNTS_S = 6500.0   # ER4AxN.ini MaxSpeed; counts/s is inferred from the DLL
VENDOR_MAX_ACCEL_COUNTS_S2 = 11000.0  # ER4AxN.ini MaxAccel, counts/s^2 (ledger PL56)
# The DLL compares 1.5 x the profile's peak with MaxAccel, so its real peak stays at MaxAccel / 1.5 (PL56).
VENDOR_PEAK_ACCEL_COUNTS_S2 = VENDOR_MAX_ACCEL_COUNTS_S2 / 1.5
DEFAULT_SPEED_FRACTION = 0.25     # ours: start at a quarter of the vendor limit
STREAM_ORDER = 21                 # openScorbot/libcomm.py:STREAM
MAX_STREAM_STEPS = 20000          # step records kept for the log (8 minutes at 24 ms)
TRACKING, HOLDING, STOPPED, FAULTED, ENDED = "tracking", "holding", "stopped", "faulted", "ended"
SEND, STOP, END = "send", "stop", "end"


class StreamRefused(ValueError):
    """A target or a setting was rejected before anything was sent."""


@dataclass(frozen=True)
class StreamLimits:
    """Per-motor limits in counts per second, per second squared and cubed."""

    max_velocity: float
    max_acceleration: float
    max_jerk: float

    def __post_init__(self):
        for name in ("max_velocity", "max_acceleration", "max_jerk"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not math.isfinite(value) or value <= 0:
                raise StreamRefused(f"{name} must be a positive finite number")


def prior_limits(speed_fraction: float = DEFAULT_SPEED_FRACTION) -> StreamLimits:
    """Starting limits: a fraction of the vendor's per-motor speed.

    Acceleration and jerk use the times of the vendor's velocity-jog profile
    (``vendor_profile.py``: acceleration fraction 0.3, jerk fraction 0.05) for
    a one-second move: acceleration = speed / 0.285 s, jerk = acceleration /
    0.015 s, with acceleration never above the vendor's real peak, MaxAccel / 1.5
    (from about 32 percent speed up; ledger PL56). All priors.
    """
    if isinstance(speed_fraction, bool) or not isinstance(speed_fraction, (int, float)) \
            or not 0 < speed_fraction <= 1:
        raise StreamRefused("speed_fraction must be above 0 and at most 1")
    velocity = VENDOR_MAX_SPEED_COUNTS_S * speed_fraction
    jerk_up, _, speeding_up, *_ = VendorProfile(1.0, MANUAL_ACCEL_FRACTION,
                                                MANUAL_JERK_FRACTION).times
    acceleration = min(velocity / (speeding_up - jerk_up), VENDOR_PEAK_ACCEL_COUNTS_S2)
    return StreamLimits(velocity, acceleration, acceleration / jerk_up)


@dataclass(frozen=True)
class Step:
    """What to do this period, and the numbers behind it (for the log)."""

    action: str                    # send, stop or end
    commanded: dict[str, int]      # counts from home; what to send when action is send
    target: dict[str, float]
    measured: dict[str, int]
    lead: dict[str, int]           # commanded minus measured, before this step
    state: str
    fault: str | None = None


class StreamCore:
    """One stream. ``set_target`` and ``request_stop`` may come from another thread."""

    def __init__(self, start: dict[str, int], *, travel_cap: dict[str, float],
                 window: dict[str, tuple[float, float]] | None = None,
                 pose_check: Callable[[dict[str, float]], str | None] | None = None,
                 lead_limit: dict[str, float], limits: StreamLimits | None = None,
                 period_s: float = DEFAULT_PERIOD_S,
                 hold_timeout_s: float = DEFAULT_HOLD_TIMEOUT_S,
                 error_limit: dict[str, int] | None = None, now: float = 0.0,
                 motors: tuple[str, ...] = MOTORS):
        from ruckig import ControlInterface, InputParameter, OutputParameter, Ruckig
        # The motors this stream drives, in setpoint order: base, shoulder, elbow, or those and
        # both wrist motors (Scorbot.start_stream(wrist=True)). Every other motor is left alone.
        if tuple(motors) not in (MOTORS, WRIST_STREAM_MOTORS):
            raise StreamRefused(f"motors must be {MOTORS} or {WRIST_STREAM_MOTORS}")
        self.motors = tuple(motors)
        for name, value in (("period_s", period_s), ("hold_timeout_s", hold_timeout_s)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not math.isfinite(value) or value <= 0:
                raise StreamRefused(f"{name} must be a positive finite number")
        for name, mapping in (("travel_cap", travel_cap), ("lead_limit", lead_limit)):
            if set(mapping) != set(self.motors) or any(
                    isinstance(v, bool) or not isinstance(v, (int, float))
                    or not math.isfinite(v) or v <= 0 for v in mapping.values()):
                raise StreamRefused(f"{name} needs a positive value for each of {self.motors}")
        if set(start) != set(self.motors) or any(type(v) is not int for v in start.values()):
            raise StreamRefused(f"start needs an integer count for each of {self.motors}")
        # Allowed counts from home per motor: the travel cap either side, cut
        # short where the vendor encoder window is nearer.
        self.bounds = {m: (max(-travel_cap[m], (window or {}).get(m, (-math.inf, math.inf))[0]),
                           min(travel_cap[m], (window or {}).get(m, (-math.inf, math.inf))[1]))
                       for m in self.motors}
        if any(not self.bounds[m][0] <= start[m] <= self.bounds[m][1] for m in self.motors):
            raise StreamRefused("the arm is already outside the travel cap or a vendor limit (USBC.dll EncLimit / ROB_4u.INI)")
        # ``pose_check`` sees every streamed count at once and returns a sentence if
        # they put a joint past its limit: a joint's angle can depend on another
        # motor's count, which ``window`` (one motor at a time) cannot see.
        self._pose_check = pose_check
        if pose_check is not None and (problem := pose_check(dict(start))):
            raise StreamRefused(f"the arm is already past a vendor limit (USBC.dll EncLimit / ROB_4u.INI): {problem}")
        limits = limits or prior_limits()
        self.period_s = period_s
        self.travel_cap = dict(travel_cap)
        self.lead_limit = dict(lead_limit)
        self.hold_timeout_s = hold_timeout_s
        # Per motor: a signed error word whose absolute value is above this faults (vendor ImpactDetect, PL98).
        self.error_limit = dict(IMPACT_DETECT_COUNTS if error_limit is None else error_limit)
        self.state = TRACKING
        self.fault: str | None = None
        self._lock = threading.Lock()
        self._position_interface = ControlInterface.Position
        self._velocity_interface = ControlInterface.Velocity
        self._otg = Ruckig(len(self.motors), period_s)
        self._input = InputParameter(len(self.motors))
        self._output = OutputParameter(len(self.motors))
        positions = [float(start[m]) for m in self.motors]
        self._input.current_position = positions
        self._input.target_position = positions
        self._input.max_velocity = [limits.max_velocity] * len(self.motors)
        self._input.max_acceleration = [limits.max_acceleration] * len(self.motors)
        self._input.max_jerk = [limits.max_jerk] * len(self.motors)
        self._target = {m: float(start[m]) for m in self.motors}
        self._commanded = dict(start)
        self._last_target_time = now
        self._stop_requested = False
        self._finish_requested = False
        self._rest_sent = False

    # -- caller side -----------------------------------------------------

    def set_target(self, target: dict, now: float) -> None:
        """Replace the target. Refused, with nothing changed, if it is invalid."""
        if not isinstance(target, dict) or not target or set(target) - set(self.motors):
            raise StreamRefused(f"a target names one or more of {self.motors}")
        for motor, value in target.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not math.isfinite(value):
                raise StreamRefused(f"{motor} target must be a finite number")
            low, high = self.bounds[motor]
            if not low <= value <= high:
                raise StreamRefused(f"{motor} target {value:g} is outside the travel cap or "
                                    f"vendor limit (USBC.dll EncLimit / ROB_4u.INI): {low:g} to {high:g} counts from home")
        with self._lock:
            if self.state not in (TRACKING, HOLDING) or self._finish_requested:
                raise StreamRefused(f"the stream is {self.state}; it takes no more targets")
            if self._pose_check is not None:
                problem = self._pose_check({**self._target,
                                            **{m: float(v) for m, v in target.items()}})
                if problem:
                    raise StreamRefused(f"that target would put the arm past a vendor limit (USBC.dll EncLimit / ROB_4u.INI): "
                                        f"{problem}")
            self._target.update({m: float(v) for m, v in target.items()})
            self._input.control_interface = self._position_interface
            self._input.target_position = [self._target[m] for m in self.motors]
            self._input.target_velocity = [0.0] * len(self.motors)
            self._last_target_time = now
            self.state = TRACKING

    def set_speed(self, speed_fraction: float) -> None:
        """Change the speed (a fraction of the vendor's MaxSpeed) from the next period on.

        Slowing down mid-move brakes within the new limits. Refused once the stream has stopped,
        faulted or ended.
        """
        limits = prior_limits(speed_fraction)
        with self._lock:
            if self.state not in (TRACKING, HOLDING):
                raise StreamRefused(f"the stream is {self.state}; its speed is fixed")
            self._input.max_velocity = [limits.max_velocity] * len(self.motors)
            # Never lower acceleration or jerk mid-stream: with less jerk, an acceleration already
            # under way takes longer to end and the speed overshoots past MaxSpeed (review 2026-11-01).
            self._input.max_acceleration = [max(a, limits.max_acceleration)
                                            for a in self._input.max_acceleration]
            self._input.max_jerk = [max(j, limits.max_jerk) for j in self._input.max_jerk]

    def request_stop(self) -> None:
        with self._lock:
            self._stop_requested = True

    def finish(self) -> None:
        """Take no more targets, slow to rest, then end."""
        with self._lock:
            self._finish_requested = True

    def fail(self, reason: str) -> None:
        """Latch a fault found outside ``step`` (a bad reply, an adapter error)."""
        with self._lock:
            self._fault(reason)

    # -- worker side -----------------------------------------------------

    def _fault(self, reason: str) -> None:
        if self.fault is None:
            self.fault = reason
        self.state = FAULTED

    def _leads_too_far(self, lead: dict) -> bool:
        """Any motor past its lead limit, or, on a wrist stream, pitch or roll past theirs.

        A wrist motor's limit is pitch's plus roll's, and pitch is half the wrist motors' difference,
        roll half their sum, so each stays within half that limit exactly when |m1| + |m2| is within it.
        """
        if any(abs(lead[m]) > self.lead_limit[m] for m in self.motors):
            return True
        return self.motors == WRIST_STREAM_MOTORS and             abs(lead["wrist_motor_1"]) + abs(lead["wrist_motor_2"]) > self.lead_limit["wrist_motor_1"]

    def _decelerate(self) -> None:
        self._input.control_interface = self._velocity_interface
        self._input.target_velocity = [0.0] * len(self.motors)

    def step(self, measured: dict[str, int], now: float, *, error_counts: dict | None = None,
             emergency: bool = False) -> Step:
        """One period. Never raises: a problem becomes a fault and a stop."""
        from ruckig import Result
        with self._lock:
            lead = {m: self._commanded[m] - int(measured[m]) for m in self.motors}

            def result(action):
                return Step(action, dict(self._commanded), dict(self._target),
                            {m: int(measured[m]) for m in self.motors}, lead, self.state, self.fault)

            if self.state in (FAULTED, STOPPED):
                return result(STOP)
            if self.state == ENDED:
                return result(END)
            if emergency:
                self._fault("the controller reports an emergency stop")
            elif error_counts and any(abs(error_counts.get(m, 0)) > self.error_limit[m] for m in self.motors):
                self._fault(f"controller error word above ImpactDetect (vendor error 201), "
                            f"{ {m: self.error_limit[m] for m in self.motors} }: {error_counts}")
            elif self._leads_too_far(lead):
                self._fault(f"the arm is not following: command leads by {lead} counts")
            if self.state == FAULTED:
                return result(STOP)
            if self._stop_requested:
                self.state = STOPPED
                return result(STOP)
            if self._finish_requested:
                self._decelerate()
            elif self.state == TRACKING and now - self._last_target_time > self.hold_timeout_s:
                self.state = HOLDING       # no fresh target: slow to rest and hold
                self._decelerate()
            outcome = self._otg.update(self._input, self._output)
            if outcome not in (Result.Working, Result.Finished):
                self._fault(f"the trajectory generator failed: {outcome}")
                return result(STOP)
            self._output.pass_to_input(self._input)
            proposed = {}
            for m, p, velocity in zip(self.motors, self._output.new_position, self._input.max_velocity):
                max_step = math.floor(velocity * self.period_s + 1e-9)
                step = math.trunc(p - self._commanded[m])
                end_step = round(p) - self._commanded[m]
                # A finished position can be a fraction short of its integer endpoint.
                if outcome == Result.Finished and abs(end_step) <= max_step:
                    step = end_step
                proposed[m] = self._commanded[m] + max(-max_step, min(max_step, step))
            if any(not self.bounds[m][0] <= proposed[m] <= self.bounds[m][1] for m in self.motors):
                self._fault(f"commanded position left the travel cap: {proposed}")
                return result(STOP)
            if self._pose_check is not None and (problem := self._pose_check(dict(proposed))):
                self._fault(f"commanded position is past a vendor limit (USBC.dll EncLimit / ROB_4u.INI): {problem}: {proposed}")
                return result(STOP)
            # Checked before it is sent: the limit bounds every setpoint, so a
            # stalled arm is never asked for one more step past it.
            ahead = {m: proposed[m] - int(measured[m]) for m in self.motors}
            if self._leads_too_far(ahead):
                self._fault(f"the arm is not following: the next command would lead by "
                            f"{ahead} counts")
                return result(STOP)
            self._commanded = proposed
            if self._finish_requested and outcome == Result.Finished:
                if self._rest_sent:
                    self.state = ENDED
                    return result(END)
                self._rest_sent = True     # send the resting position once, then end
            return result(SEND)


class Stream:
    """A running stream on a ``Scorbot``. Made by ``Scorbot.start_stream``.

    ``set_target`` takes motor counts from this session's home, for any of
    base, shoulder and elbow (and both wrist motors on a wrist stream), at any time. ``close`` slows to rest and ends;
    ``stop`` ends at once with the stop sequence. Neither is an emergency
    stop. Use it as a context manager so it always ends.
    """

    def __init__(self, robot, core: StreamCore, home_counts: dict, before, *, home_signed: dict,
                 use_emergency_bit: bool = False):
        self.robot, self.core = robot, core
        self._home = dict(home_counts)
        self._home_signed = dict(home_signed)    # home as the controller count (stream_control's ``home``)
        self._before = before
        self._use_emergency_bit = use_emergency_bit
        self._next_step_time = None
        self.fault_reported = False     # True once the session has latched this stream's fault
        self.report_lock = threading.Lock()   # the worker and _end_stream both report
        self.steps: list[dict] = []
        self.dropped_steps = 0
        self.final_state = None
        self.stop_reason = None

    def set_target(self, target: dict) -> None:
        self.core.set_target(target, time.monotonic())

    def set_speed(self, speed_fraction: float) -> None:
        """Change the speed while streaming: a fraction of the vendor's MaxSpeed, above 0 to 1."""
        self.core.set_speed(speed_fraction)
        self.robot._record("stream_speed", speed_fraction=speed_fraction)

    def request_stop(self) -> None:
        self.core.request_stop()

    def close(self):
        """Slow to rest, end the stream and return the final state."""
        self.core.finish()
        return self.robot._end_stream(self)

    def stop(self):
        """End now with the stop sequence and return the final state."""
        self.core.request_stop()
        return self.robot._end_stream(self)

    def __enter__(self):
        return self

    def __exit__(self, kind, _value, _traceback):
        if self.final_state is None and self.robot._stream is self:
            if kind is None:
                self.close()
            else:
                self.stop()

    def source(self, reply: bytes):
        """Called by the worker once per period with the last reply. Never raises.

        A fault is reported to the session here, on the worker, so it latches
        whether or not the caller ever ends the stream.
        """
        answer = self._step(reply)
        if self.core.fault:
            self.interrupt(self.core.fault)
        # Timed from the answer, the last thing before the worker writes, so
        # a slow step here cannot shorten the gap to the next write.
        self._next_step_time = time.monotonic() + self.core.period_s
        return answer

    def __call__(self, reply: bytes):
        return self.source(reply)

    def interrupt(self, message: str) -> None:
        """Report a step or USB fault on the worker, even if close is never called. Never raises."""
        try:
            self.core.fail(message)
            with self.report_lock:
                if not self.fault_reported:
                    self.robot._stream_faulted(self)
                    self.fault_reported = True
        except Exception:
            pass  # a reporting failure must not skip the worker's recovery stop

    def _pace(self) -> None:
        # The core plans one step per period, so steps must not come faster
        # than that or the arm would be driven past its limits. The legacy
        # loop's own delays are shorter than the vendor period. A slow loop is
        # not caught up: the arm then moves slower than planned, never faster.
        # The reply the lead check uses is older by this wait, on top of the
        # one loop it is always behind.
        now = time.monotonic()
        if self._next_step_time is not None and now < self._next_step_time:
            time.sleep(self._next_step_time - now)

    def _step(self, reply: bytes):
        try:
            if self.robot._cancel_event.is_set():
                self.core.fail("the session was cancelled while streaming")
            elif self.robot._fault:
                # Latched on another thread (lost feedback, say): a faulted
                # session must not go on following targets.
                self.core.fail(f"the session faulted while streaming: {self.robot._fault}")
            if self.robot._stop_event.is_set():
                # A stop that landed after start_stream consumed the last one but before the stream was
                # published (request_stop then found no stream): honour it here. Ending clears the event.
                self.core.request_stop()
            self._pace()
            state = decode_state(reply, connected=True, enabled=None, homed=True, fault=None)
            # A stream starts in Auto; the first teach-on reply ends it through the normal stop path.
            if teach_mode_after(False, reply):
                self.stop_reason = "teach mode"
                self.core.request_stop()
            # The controller's collision flag (IN byte 59) on a motor this stream drives: a fault, like
            # the error limit. Another motor's bit (the gripper's move ends against a stop) is not.
            hit = [m for m in collision_motors(reply) if m in self.core.motors]
            if hit:
                self.core.fail(f"the controller's collision flag is set for {', '.join(hit)}")
            measured = {m: signed_count_delta(state.encoder_counts[m], self._home[m])
                        for m in self.core.motors}
            # The setpoint below adds the reply's 24-bit count, so its top byte must agree with the low
            # word `measured` comes from. One corrupt top byte moves the count by 65536 while the low
            # word stays: a fault, never a setpoint (docs/requirements/count-frame.md CT15).
            for m in self.core.motors:
                if state.signed_encoder_counts[m] - self._home_signed[m] != measured[m]:
                    self.core.fail(
                        f"{m}: the reply's count {state.signed_encoder_counts[m]} (top byte "
                        f"{state.encoder_sign_bytes[m]:#04x}) is not the {self._home_signed[m] + measured[m]} "
                        "its low word gives: a corrupt reply, or the motor is past the reach from home; "
                        "no setpoint sent")
                    return (STOP,)
            errors = {m: signed_error_word(state.controller_error_counts[m]) for m in self.core.motors}
            emergency = self._use_emergency_bit and bool(reply[2] & 1)
            step = self.core.step(measured, time.monotonic(), error_counts=errors,
                                  emergency=emergency)
            if len(self.steps) < MAX_STREAM_STEPS:
                self.steps.append({"host_monotonic_ns": time.monotonic_ns(),
                                   "action": step.action, "state": step.state,
                                   "target": step.target, "commanded": step.commanded,
                                   "measured": step.measured, "lead": step.lead})
            else:
                self.dropped_steps += 1
            if step.action != SEND:
                return (step.action,)
            # Absolute setpoints: where the arm is now, plus how far the
            # command is from where the arm is, both in counts from home. The
            # signed count is continuous through zero, so this is home plus the
            # command there. It runs out at the ends of the controller's 24-bit
            # counter (from disassembly, unverified): that is a fault, never a
            # wrapped value (docs/requirements/count-frame.md).
            absolute = tuple(state.signed_encoder_counts[m] + step.commanded[m] - measured[m]
                             for m in self.core.motors)
            if any(not COUNT_MIN <= value <= COUNT_MAX for value in absolute):
                self.core.fail(f"setpoint outside the counter range: {absolute}")
                return (STOP,)
            return (SEND, absolute)
        except Exception as exc:   # the worker thread must never see an exception
            self.core.fail(f"stream step failed: {type(exc).__name__}: {exc}")
            return (STOP,)
