"""The arm as a policy sees it: joint degrees from home in, clamped joint targets out.

``clamp`` is the one safety clamp between any policy and the arm (``examples/policy_loop.py`` and
the LeRobot plugin both use it). ``PolicyArm`` is the small connect / observe / act / close loop
around ``Scorbot.start_stream``, so a LeRobot ``Robot`` needs no motion code of its own.
Degrees come from the vendor prior (``preview_jog``), uncalibrated. Never run on the arm.
"""

from __future__ import annotations

from contextlib import ExitStack
import math

from . import limits
from .calibration import signed_count_delta

MOTORS = limits.ARM_MOTORS
DEFAULT_TRAVEL_CAP_DEG = 2.0
LEAD_LIMIT_DEG = 2.0            # the SDK's own lead limit; our targets stay inside LEAD_MARGIN_DEG of it
LEAD_MARGIN_DEG = 1.5           # a target never leads the measured position by more than this
STEP_MAX_DEG = 0.5              # largest delta applied in one call


def clamp(delta_deg, target_deg, measured_deg, *, cap_deg, step_max_deg=STEP_MAX_DEG,
          lead_margin_deg=LEAD_MARGIN_DEG):
    """The new commanded target (degrees from home) per motor. Pure.

    Each delta is cut to +-step_max_deg, added to the last commanded target, cut to +-cap_deg from
    home, and kept within lead_margin_deg of the measured position. Unknown motors, non-finite
    numbers and missing motors are ignored (the motor holds). Returns (target, clamped_motors).
    """
    out, clamped = dict(target_deg), []
    for motor, asked in (delta_deg or {}).items():
        if motor not in MOTORS or isinstance(asked, bool) or not isinstance(asked, (int, float)) \
                or not math.isfinite(asked):
            clamped.append(motor)
            continue
        step = max(-step_max_deg, min(step_max_deg, asked))
        wanted = max(-cap_deg, min(cap_deg, target_deg[motor] + step))
        lo, hi = measured_deg[motor] - lead_margin_deg, measured_deg[motor] + lead_margin_deg
        wanted = max(lo, min(hi, wanted))
        # The lead window can sit beyond one step when feedback jumps: walk toward it, a step at a time.
        base = target_deg[motor]
        wanted = max(-cap_deg, min(cap_deg, max(base - step_max_deg, min(base + step_max_deg, wanted))))
        if abs(wanted - (target_deg[motor] + asked)) > 1e-9:
            clamped.append(motor)
        out[motor] = wanted
    return out, clamped


class PolicyArm:
    """``connect()``, then ``observe()`` and ``act()`` at your own rate, then ``close()``.

    ``robot`` is an unconnected ``Scorbot`` or ``SimulatedScorbot``. ``act`` takes absolute targets
    in degrees from home for any of base, shoulder, elbow, and moves the stream toward them by at
    most ``STEP_MAX_DEG`` per call. Call it at least every 0.5 s or the stream holds in place.
    Needs the ``planning`` extra (Ruckig). ``simulate=False`` runs are the caller's to supervise.
    """

    def __init__(self, robot, *, travel_cap_deg: float = DEFAULT_TRAVEL_CAP_DEG):
        self.robot, self.cap = robot, travel_cap_deg
        self._stack: ExitStack | None = None
        self._stream = None

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
                travel_cap_deg=self.cap, lead_limit_deg=LEAD_LIMIT_DEG))
            self.target = dict.fromkeys(MOTORS, 0.0)
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
        self.target, _ = clamp(delta, self.target, self.observe(), cap_deg=self.cap)
        self._stream.set_target({m: round(self.target[m] * self._per_degree[m]) for m in MOTORS})
        return dict(self.target)

    def close(self) -> None:
        """Slow to rest, motors off, disconnect. Safe to call twice."""
        stack, self._stack = self._stack, None
        if stack is not None:
            stack.close()
