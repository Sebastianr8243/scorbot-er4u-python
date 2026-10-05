"""Streaming target follower: the logic, with no USB, threads or sleeps in it.

A caller keeps updating a target (motor counts from home); once per period
``StreamCore.step`` moves the commanded position a small, limited step toward
it and says what to send. ``Scorbot.start_stream`` connects this to the arm
through the legacy worker; the simulator drives the same code.

Requirements and decisions:
docs/superpowers/specs/2026-10-04-streaming-driver-requirements.md (R1-R12).
**Never run on the arm.** Every limit and period here is a prior.

Smoothing uses Ruckig (jerk-limited online trajectory generation), the same
library as ``scorbot/planning.py``. It is an optional extra
(``pip install .[planning]``), imported when a stream is created.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import threading
import time

from .calibration import signed_count_delta
from .state import decode_state

MOTORS = ("base", "shoulder", "elbow")
DEFAULT_PERIOD_S = 0.024          # vendor planner period (PCPeriod x USBCPeriod); a prior
DEFAULT_HOLD_TIMEOUT_S = 0.5      # ours
DEFAULT_ERROR_LIMIT = 40          # openScorbot conf MAX_ERROR, the legacy joint-limit threshold
VENDOR_MAX_SPEED_COUNTS_S = 6500.0   # ER4AxN.ini MaxSpeed; counts/s is inferred from the DLL
DEFAULT_SPEED_FRACTION = 0.25     # ours: start at a quarter of the vendor limit
STREAM_ORDER = 21                 # openScorbot/libcomm.py:STREAM
MAX_STREAM_STEPS = 20000          # step records kept for the log (8 minutes at 24 ms)
TRACKING, HOLDING, STOPPED, FAULTED, ENDED = "tracking", "holding", "stopped", "faulted", "ended"
SEND, WAIT, STOP, END = "send", "wait", "stop", "end"


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

    Acceleration and jerk use the ratios of the vendor's velocity-jog profile
    (acceleration fraction 0.3, jerk fraction 0.05) for a one-second move:
    acceleration = speed / 0.285 s, jerk = acceleration / 0.015 s. All priors.
    """
    if isinstance(speed_fraction, bool) or not isinstance(speed_fraction, (int, float)) \
            or not 0 < speed_fraction <= 1:
        raise StreamRefused("speed_fraction must be above 0 and at most 1")
    velocity = VENDOR_MAX_SPEED_COUNTS_S * speed_fraction
    acceleration = velocity / 0.285
    return StreamLimits(velocity, acceleration, acceleration / 0.015)


@dataclass(frozen=True)
class Step:
    """What to do this period, and the numbers behind it (for the log)."""

    action: str                    # send, wait, stop or end
    commanded: dict[str, int]      # counts from home; what to send when action is send
    target: dict[str, float]
    measured: dict[str, int]
    lead: dict[str, int]           # commanded minus measured, before this step
    state: str
    fault: str | None = None


class StreamCore:
    """One stream. ``set_target`` and ``request_stop`` may come from another thread."""

    def __init__(self, start: dict[str, int], *, travel_cap: dict[str, float],
                 lead_limit: dict[str, float], limits: StreamLimits | None = None,
                 period_s: float = DEFAULT_PERIOD_S,
                 hold_timeout_s: float = DEFAULT_HOLD_TIMEOUT_S,
                 error_limit: int = DEFAULT_ERROR_LIMIT, queue_limit: int | None = None,
                 now: float = 0.0):
        from ruckig import ControlInterface, InputParameter, OutputParameter, Ruckig
        for name, value in (("period_s", period_s), ("hold_timeout_s", hold_timeout_s)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not math.isfinite(value) or value <= 0:
                raise StreamRefused(f"{name} must be a positive finite number")
        for name, mapping in (("travel_cap", travel_cap), ("lead_limit", lead_limit)):
            if set(mapping) != set(MOTORS) or any(
                    isinstance(v, bool) or not isinstance(v, (int, float))
                    or not math.isfinite(v) or v <= 0 for v in mapping.values()):
                raise StreamRefused(f"{name} needs a positive value for each of {MOTORS}")
        if set(start) != set(MOTORS) or any(type(v) is not int for v in start.values()):
            raise StreamRefused(f"start needs an integer count for each of {MOTORS}")
        if any(abs(start[m]) > travel_cap[m] for m in MOTORS):
            raise StreamRefused("the arm is already outside the travel cap")
        limits = limits or prior_limits()
        self.period_s = period_s
        self.travel_cap = dict(travel_cap)
        self.lead_limit = dict(lead_limit)
        self.hold_timeout_s = hold_timeout_s
        self.error_limit = error_limit
        self.queue_limit = queue_limit
        self.state = TRACKING
        self.fault: str | None = None
        self._lock = threading.Lock()
        self._position_interface = ControlInterface.Position
        self._velocity_interface = ControlInterface.Velocity
        self._otg = Ruckig(len(MOTORS), period_s)
        self._input = InputParameter(len(MOTORS))
        self._output = OutputParameter(len(MOTORS))
        positions = [float(start[m]) for m in MOTORS]
        self._input.current_position = positions
        self._input.target_position = positions
        self._input.max_velocity = [limits.max_velocity] * len(MOTORS)
        self._input.max_acceleration = [limits.max_acceleration] * len(MOTORS)
        self._input.max_jerk = [limits.max_jerk] * len(MOTORS)
        self._target = {m: float(start[m]) for m in MOTORS}
        self._commanded = dict(start)
        self._last_target_time = now
        self._stop_requested = False
        self._finish_requested = False
        self._rest_sent = False

    # -- caller side -----------------------------------------------------

    def set_target(self, target: dict, now: float) -> None:
        """Replace the target. Refused, with nothing changed, if it is invalid."""
        if not isinstance(target, dict) or not target or set(target) - set(MOTORS):
            raise StreamRefused(f"a target names one or more of {MOTORS}")
        for motor, value in target.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not math.isfinite(value):
                raise StreamRefused(f"{motor} target must be a finite number")
            if abs(value) > self.travel_cap[motor]:
                raise StreamRefused(f"{motor} target {value:g} is outside the travel cap of "
                                    f"{self.travel_cap[motor]:g} counts from home")
        with self._lock:
            if self.state not in (TRACKING, HOLDING) or self._finish_requested:
                raise StreamRefused(f"the stream is {self.state}; it takes no more targets")
            self._target.update({m: float(v) for m, v in target.items()})
            self._input.control_interface = self._position_interface
            self._input.target_position = [self._target[m] for m in MOTORS]
            self._input.target_velocity = [0.0] * len(MOTORS)
            self._last_target_time = now
            self.state = TRACKING

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

    def _decelerate(self) -> None:
        self._input.control_interface = self._velocity_interface
        self._input.target_velocity = [0.0] * len(MOTORS)

    def step(self, measured: dict[str, int], now: float, *, error_counts: dict | None = None,
             emergency: bool = False, queued: int | None = None) -> Step:
        """One period. Never raises: a problem becomes a fault and a stop."""
        from ruckig import Result
        with self._lock:
            lead = {m: self._commanded[m] - int(measured[m]) for m in MOTORS}

            def result(action):
                return Step(action, dict(self._commanded), dict(self._target),
                            {m: int(measured[m]) for m in MOTORS}, lead, self.state, self.fault)

            if self.state in (FAULTED, STOPPED):
                return result(STOP)
            if self.state == ENDED:
                return result(END)
            if emergency:
                self._fault("the controller reports an emergency stop")
            elif error_counts and any(error_counts.get(m, 0) >= self.error_limit for m in MOTORS):
                self._fault(f"controller error word at or above {self.error_limit}: {error_counts}")
            elif any(abs(lead[m]) > self.lead_limit[m] for m in MOTORS):
                self._fault(f"the arm is not following: command leads by {lead} counts")
            if self.state == FAULTED:
                return result(STOP)
            if self._stop_requested:
                self.state = STOPPED
                return result(STOP)
            if self.queue_limit is not None and queued is not None and queued >= self.queue_limit:
                return result(WAIT)
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
            self._commanded = {m: round(p) for m, p in zip(MOTORS, self._output.new_position)}
            if any(abs(self._commanded[m]) > self.travel_cap[m] for m in MOTORS):
                self._fault(f"commanded position left the travel cap: {self._commanded}")
                return result(STOP)
            if self._finish_requested and outcome == Result.Finished:
                if self._rest_sent:
                    self.state = ENDED
                    return result(END)
                self._rest_sent = True     # send the resting position once, then end
            return result(SEND)


def _legacy_error(raw: int) -> int:
    """The controller error word as openScorbot/libdef.py:getError reads it."""
    return 65535 - raw if raw >= 65500 else raw


class Stream:
    """A running stream on a ``Scorbot``. Made by ``Scorbot.start_stream``.

    ``set_target`` takes motor counts from this session's home, for any of
    base, shoulder and elbow, at any time. ``close`` slows to rest and ends;
    ``stop`` ends at once with the stop sequence. Neither is an emergency
    stop. Use it as a context manager so it always ends.
    """

    def __init__(self, robot, core: StreamCore, home_counts: dict, before, *,
                 use_emergency_bit: bool = False):
        self.robot, self.core = robot, core
        self._home = dict(home_counts)
        self._before = before
        self._use_emergency_bit = use_emergency_bit
        self.steps: list[dict] = []
        self.dropped_steps = 0
        self.final_state = None

    def set_target(self, target: dict) -> None:
        self.core.set_target(target, time.monotonic())

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
        """Called by the worker once per period with the last reply. Never raises."""
        try:
            state = decode_state(reply, connected=True, enabled=None, homed=True, fault=None)
            measured = {m: signed_count_delta(state.encoder_counts[m], self._home[m])
                        for m in MOTORS}
            errors = {m: _legacy_error(state.controller_error_counts[m]) for m in MOTORS}
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
            # command is from where the arm is, both in counts from home.
            absolute = tuple(state.signed_encoder_counts[m] + step.commanded[m] - measured[m]
                             for m in MOTORS)
            if any(abs(value) > 65535 for value in absolute):
                self.core.fail(f"setpoint outside the counter range: {absolute}")
                return (STOP,)
            return (SEND, absolute)
        except Exception as exc:   # the worker thread must never see an exception
            self.core.fail(f"stream step failed: {type(exc).__name__}: {exc}")
            return (STOP,)
