"""Offline point-to-point trajectory planning in encoder counts. NOT wired to motion.

Same status as ``scorbot/kinematics.py``: nothing in ``Scorbot`` calls this.
It exists so the streaming motion layer (S2) starts from a tested,
library-backed planner once the USB captures show whether the controller can
follow a stream of targets. Plans use Ruckig (MIT community edition):
time-optimal, jerk-limited, all axes arriving together.

Every limit is a prior. Velocity priors come from the datasheet joint speeds
times the vendor counts per degree; neither is measured on our arms, and no
source gives acceleration or jerk, so those have no defaults. The default
period is the vendor host period (``ER4CONF.INI`` ``PCPeriod`` 16 ms), which
is not a measured packet rate.

Ruckig is an optional extra (``pip install .[planning]``) imported only when
a plan is computed.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

from .nominal import DATASHEET_JOINT_SPEED_DEG_S, VENDOR_COUNTS_PER_DEGREE

PLAN_STATUS = "offline plan, priors only, not sent"
DEFAULT_PERIOD_S = 0.016
ARM_MOTORS = ("base", "shoulder", "elbow")


def _positive(name: str, value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return float(value)


@dataclass(frozen=True)
class AxisLimits:
    """Per-motor limits in encoder counts per second, per second squared and cubed."""

    max_velocity: float
    max_acceleration: float
    max_jerk: float

    def __post_init__(self):
        for name in ("max_velocity", "max_acceleration", "max_jerk"):
            _positive(name, getattr(self, name))


@dataclass(frozen=True)
class PlannedTrajectory:
    motors: tuple[str, ...]
    period_s: float
    duration_s: float
    targets: tuple[tuple[int, ...], ...]
    status: str = PLAN_STATUS

    def as_dict(self) -> dict:
        return {"motors": list(self.motors), "period_s": self.period_s,
                "duration_s": self.duration_s,
                "targets": [list(row) for row in self.targets], "status": self.status}


def datasheet_velocity_counts_per_s(motors) -> dict[str, float]:
    """Datasheet joint speed x vendor counts per degree, per arm motor (priors)."""
    velocity = {}
    for motor in motors:
        if motor not in ARM_MOTORS:
            raise ValueError(f"No velocity prior for {motor!r}: arm motors only "
                             "(the wrist is a two-motor differential)")
        velocity[motor] = (DATASHEET_JOINT_SPEED_DEG_S[motor].value
                           * VENDOR_COUNTS_PER_DEGREE[motor].value)
    return velocity


def plan_point_to_point(start: dict[str, int], goal: dict[str, int],
                        limits: dict[str, AxisLimits], *,
                        period_s: float = DEFAULT_PERIOD_S) -> PlannedTrajectory:
    """Sample a synchronised trajectory from ``start`` to ``goal`` every ``period_s``.

    Positions are signed encoder counts (use ``calibration.signed_count_delta``
    to get them, never raw unsigned counts). The first sample is ``start`` and
    the last is exactly ``goal``.
    """
    period_s = _positive("period_s", period_s)
    motors = tuple(start)
    if set(goal) != set(motors):
        raise ValueError("start and goal must name the same motors")
    missing = [motor for motor in motors if motor not in limits]
    if missing:
        raise ValueError(f"No limits for {missing}")
    for name, counts in (("start", start), ("goal", goal)):
        for motor in motors:
            if type(counts[motor]) is not int:
                raise ValueError(f"{name} counts for {motor} must be an int")

    begin = tuple(start[motor] for motor in motors)
    end = tuple(goal[motor] for motor in motors)
    if begin == end:
        return PlannedTrajectory(motors, period_s, 0.0, (begin,))

    from ruckig import InputParameter, Result, Ruckig, Trajectory

    dofs = len(motors)
    parameters = InputParameter(dofs)
    parameters.current_position = [float(v) for v in begin]
    parameters.target_position = [float(v) for v in end]
    parameters.max_velocity = [limits[m].max_velocity for m in motors]
    parameters.max_acceleration = [limits[m].max_acceleration for m in motors]
    parameters.max_jerk = [limits[m].max_jerk for m in motors]
    trajectory = Trajectory(dofs)
    result = Ruckig(dofs).calculate(parameters, trajectory)
    if result not in (Result.Working, Result.Finished):
        raise ValueError(f"Ruckig could not plan this move: {result}")

    duration = float(trajectory.duration)
    steps = math.ceil(duration / period_s)
    samples = [begin]
    for step in range(1, steps):
        position = trajectory.at_time(step * period_s)[0]
        samples.append(tuple(round(value) for value in position))
    samples.append(end)
    return PlannedTrajectory(motors, period_s, duration, tuple(samples))
