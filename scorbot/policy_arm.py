"""The arm as a policy sees it: joint degrees from home in, clamped joint targets out.

``clamp`` is the one safety clamp between any policy and the arm (``examples/policy_loop.py`` and
the LeRobot plugin both use it). ``PolicyArm`` is the small connect / observe / act / close loop
around ``Scorbot.start_stream``, so a LeRobot ``Robot`` needs no motion code of its own.
Degrees come from the vendor prior (``preview_jog``), uncalibrated. Never run on the arm.
"""

from __future__ import annotations

from contextlib import ExitStack
import math
from time import monotonic

from . import limits
from .calibration import signed_count_delta
from .streaming import DEFAULT_HOLD_TIMEOUT_S, VENDOR_MAX_SPEED_COUNTS_S

MOTORS = limits.ARM_MOTORS
DEFAULT_TRAVEL_CAP_DEG = 2.0
LEAD_LIMIT_DEG = 2.0            # the SDK's own lead limit; our targets stay inside LEAD_MARGIN_DEG of it
LEAD_MARGIN_DEG = 1.5           # a target never leads the measured position by more than this
STEP_MAX_DEG = 0.5              # largest delta applied in one call
DEFAULT_SPEED_PCT = 25          # percent of the vendor's MaxSpeed (streaming.VENDOR_MAX_SPEED_COUNTS_S)
# ours: "fixed" is STEP_MAX_DEG per call (the default); "speed" is what the vendor's MaxSpeed
# (vendor: ER4AxN.ini MaxSpeed, via streaming) at speed_pct covers since the previous call.
STEP_LIMITS = ("fixed", "speed")


def speed_step_deg(speed_pct, elapsed_s, per_degree):
    """Per motor, the degrees MaxSpeed x speed_pct covers in ``elapsed_s``. Pure.

    The time counts up to the stream's hold timeout (ours, streaming.DEFAULT_HOLD_TIMEOUT_S: after
    that the stream holds anyway); none, negative or non-finite time is no step. Never above
    LEAD_MARGIN_DEG (ours), so never above the stream's lead limit either.
    """
    dt = min(elapsed_s, DEFAULT_HOLD_TIMEOUT_S) if math.isfinite(elapsed_s) and elapsed_s > 0 else 0.0
    counts = VENDOR_MAX_SPEED_COUNTS_S * speed_pct / 100 * dt
    return {m: min(LEAD_MARGIN_DEG, counts / abs(per_degree[m])) for m in MOTORS}


def _check_speed(speed_pct):
    if isinstance(speed_pct, bool) or not isinstance(speed_pct, (int, float)) \
            or not math.isfinite(speed_pct) or not 1 <= speed_pct <= 100:
        raise ValueError(f"speed_pct must be a number from 1 to 100, not {speed_pct!r}")
    return speed_pct


def clamp(delta_deg, target_deg, measured_deg, *, cap_deg, step_max_deg=STEP_MAX_DEG,
          lead_margin_deg=LEAD_MARGIN_DEG):
    """The new commanded target (degrees from home) per motor. Pure.

    ``step_max_deg`` is one number for every motor or a mapping with a finite value of at least 0
    per motor (else ``ValueError``). Each delta is cut to +-step_max_deg, added to the last commanded target, cut to +-cap_deg from
    home, and kept within lead_margin_deg of the measured position. The step limit always wins:
    when feedback jumps or the last target sits past the cap, the target walks back one step at a
    time (the lead and the overshoot only shrink). Unknown motors, non-finite numbers (delta,
    measured or cap) and missing motors are ignored (the motor holds). A non-finite last target
    raises ``ValueError``: holding it would hand it on. Returns (target, clamped_motors).
    """
    def finite(v):
        return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)

    if isinstance(step_max_deg, dict):
        steps = step_max_deg
        if any(not finite(steps.get(m)) or steps[m] < 0 for m in MOTORS):
            raise ValueError(f"step_max_deg needs a finite step of at least 0 per motor: {steps!r}")
    else:
        steps = dict.fromkeys(MOTORS, step_max_deg)
    out, clamped = dict(target_deg), []
    for motor, asked in (delta_deg or {}).items():
        if motor in MOTORS and not finite(target_deg.get(motor)):
            raise ValueError(f"last target for {motor} is not a finite number: {target_deg.get(motor)!r}")
        if motor not in MOTORS or not all(finite(v) for v in (asked, measured_deg.get(motor), cap_deg)) \
                or cap_deg < 0:
            clamped.append(motor)
            continue
        base, step_max_deg = target_deg[motor], steps[motor]
        step = max(-step_max_deg, min(step_max_deg, asked))
        wanted = max(-cap_deg, min(cap_deg, base + step))
        lo, hi = measured_deg[motor] - lead_margin_deg, measured_deg[motor] + lead_margin_deg
        wanted = max(lo, min(hi, wanted))
        # One allowed range that always holds the last target: one step from it, never further past the cap.
        lo = max(base - step_max_deg, min(-cap_deg, base))
        hi = min(base + step_max_deg, max(cap_deg, base))
        wanted = max(lo, min(hi, wanted))
        if abs(wanted - (target_deg[motor] + asked)) > 1e-9:
            clamped.append(motor)
        out[motor] = wanted
    return out, clamped


class PolicyArm:
    """``connect()``, then ``observe()`` and ``act()`` at your own rate, then ``close()``.

    ``robot`` is an unconnected ``Scorbot`` or ``SimulatedScorbot``. ``act`` takes absolute targets
    in degrees from home for any of base, shoulder, elbow, and moves the stream toward them by at
    most ``STEP_MAX_DEG`` per call (``step_limit="fixed"``, the default) or, opt-in with
    ``step_limit="speed"``, by what MaxSpeed at ``speed_pct`` covers since the previous call
    (``speed_step_deg``). Call it at least every 0.5 s or the stream holds in place.
    Needs the ``planning`` extra (Ruckig). ``simulate=False`` runs are the caller's to supervise.
    """

    def __init__(self, robot, *, travel_cap_deg: float = DEFAULT_TRAVEL_CAP_DEG,
                 speed_pct: float = DEFAULT_SPEED_PCT, step_limit: str = "fixed"):
        if not isinstance(step_limit, str) or step_limit not in STEP_LIMITS:
            raise ValueError(f"step_limit must be one of {STEP_LIMITS}, not {step_limit!r}")
        self.robot, self.cap, self.step_limit = robot, travel_cap_deg, step_limit
        self.speed_pct = _check_speed(speed_pct)
        self._stack: ExitStack | None = None
        self._stream = None

    def set_speed(self, speed_pct: float) -> None:
        """Change the speed (percent of the vendor's MaxSpeed, 1 to 100), live if connected."""
        speed_pct = _check_speed(speed_pct)
        if self._stream is not None:
            self._stream.set_speed(speed_pct / 100)
            self._last_act = monotonic()    # the speed step never charges earlier time at the new speed
        self.speed_pct = speed_pct

    @property
    def is_connected(self) -> bool:
        return self._stack is not None

    def _require_connected(self) -> None:
        if self._stack is None:
            raise ConnectionError("PolicyArm is not connected")

    def connect(self) -> None:
        if self._stack is not None:
            raise RuntimeError("already connected")
        with ExitStack() as stack:           # any failure below unwinds what was opened
            stack.enter_context(self.robot)  # connects; leaving disconnects
            self.robot.enable()
            stack.callback(self._disable)
            self.robot.home(start_position_confirmed=True)
            self._home = dict(self.robot.get_state().encoder_counts)
            self._per_degree = {m: self.robot.preview_jog(m, 1.0)["motor_count_deltas"][m]
                                for m in MOTORS}
            self._stream = stack.enter_context(self.robot.start_stream(
                travel_cap_deg=self.cap, lead_limit_deg=LEAD_LIMIT_DEG,
                speed_fraction=self.speed_pct / 100))
            self.target = dict.fromkeys(MOTORS, 0.0)
            self._last_act = monotonic()
            self._stack = stack.pop_all()

    def _disable(self) -> None:
        try:
            self.robot.disable()    # not an emergency stop
        except Exception:
            pass                    # the session is already faulted or gone

    def observe(self) -> dict[str, float]:
        """Degrees from home, measured."""
        self._require_connected()
        counts = self.robot._motion_state().encoder_counts     # latches the session if feedback is unavailable
        return {m: signed_count_delta(counts[m], self._home[m]) / self._per_degree[m] for m in MOTORS}

    def act(self, goal_deg: dict) -> dict[str, float]:
        """Move toward ``goal_deg`` (absolute degrees from home); return the target now commanded.

        Raises ``StreamRefused`` if the stream has faulted or ended. Bad values hold their motor.
        """
        self._require_connected()
        delta = {m: g - self.target[m] if isinstance(g, (int, float)) and not isinstance(g, bool)
                 else math.nan for m, g in goal_deg.items() if m in self.target}
        measured = self.observe()
        if self.step_limit == "speed":
            now = monotonic()
            step = speed_step_deg(self.speed_pct, now - self._last_act, self._per_degree)
            self._last_act = now
            self.target, _ = clamp(delta, self.target, measured, cap_deg=self.cap, step_max_deg=step)
        else:
            self.target, _ = clamp(delta, self.target, measured, cap_deg=self.cap)
        self._stream.set_target({m: round(self.target[m] * self._per_degree[m]) for m in MOTORS})
        return dict(self.target)

    def close(self) -> None:
        """Slow to rest, motors off, disconnect. Safe to call twice."""
        stack, self._stack = self._stack, None
        if stack is not None:
            stack.close()
