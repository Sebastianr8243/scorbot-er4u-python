"""The motion profile Intelitek's software uses, read from ``USBC.dll``.

A move is a normalised S-curve: position goes from 0 to 1 over a total time,
with jerk-limited ramps at both ends. The DLL scales that 0..1 by the distance
of each axis, so all axes start and finish together. Read from the profile
set-up routine at 0x1001295d and its evaluator at 0x10012e48 (2018 build);
see docs/protocol/VENDOR_DLL_PROTOCOL.md section 11.

This is a **prior from disassembly, not measured on our arm**. It describes
what the vendor software asks for, not how the arm follows it. Pure, standard
library only. One thing reads it: ``streaming.prior_limits`` takes a stream's
starting acceleration and jerk from the velocity-jog time fractions below, so
this file is in the motion fingerprint.

Parameters, with the vendor's ``ROB_4u.INI`` ``[Motion]`` defaults:

- ``total_time`` (``TotalTimeA`` = 3.0 s): duration of the move.
- ``accel_fraction`` (``AccelA`` = 0.3): the share of the total time spent
  speeding up; the same share is spent slowing down.
- ``jerk_fraction`` (``AccelAccelA`` = 0.3): the share of each speed-up or
  slow-down phase spent ramping the acceleration up, and again ramping it down.

The seven segments are: jerk up, constant acceleration, jerk down, cruise,
then the mirror image. This is the standard "double S" velocity profile
(Biagiotti and Melchiorri, Trajectory Planning for Automatic Machines and
Robots), parametrised by time fractions instead of by speed, acceleration and
jerk limits.
"""

from __future__ import annotations

from dataclasses import dataclass

MIN_TOTAL_TIME = 0.001
DEFAULT_TOTAL_TIME = 3.0
DEFAULT_ACCEL_FRACTION = 0.3
DEFAULT_JERK_FRACTION = 0.3
# What MoveManual passes for a velocity jog (0x1001ba0c): a much shorter jerk ramp.
MANUAL_ACCEL_FRACTION = 0.3
MANUAL_JERK_FRACTION = 0.05


def speed_factor(percent: int) -> float:
    """What ``Speed(group, percent)`` stores: 0.3 + 0.007 x percent, so 1-100
    maps to 0.307-1.0 (0x10016f93). How that factor becomes a move duration
    was not traced. ``Time(group, ms)`` instead stores ms / 1000 seconds."""
    if isinstance(percent, bool) or not isinstance(percent, int) or not 1 <= percent <= 100:
        raise ValueError("percent must be an integer from 1 to 100")
    return percent * ((1.0 - 0.3) / 100.0) + 0.3


@dataclass(frozen=True)
class Sample:
    """Normalised position (0..1), velocity (1/s) and acceleration (1/s^2)."""

    position: float
    velocity: float
    acceleration: float


class VendorProfile:
    """One normalised move. ``sample(t)`` follows the DLL's evaluator."""

    def __init__(self, total_time: float = DEFAULT_TOTAL_TIME,
                 accel_fraction: float = DEFAULT_ACCEL_FRACTION,
                 jerk_fraction: float = DEFAULT_JERK_FRACTION):
        # The DLL accepts fractions from 0 to 0.5. Zero would divide by zero
        # there (no ramp at all), so it is refused here.
        if not total_time >= MIN_TOTAL_TIME:
            raise ValueError("total_time must be at least 0.001 s")
        if not 0 < accel_fraction <= 0.5 or not 0 < jerk_fraction <= 0.5:
            raise ValueError("fractions must be above 0 and at most 0.5")
        self.total_time = total_time
        self.accel_fraction = accel_fraction
        self.jerk_fraction = jerk_fraction

        t1 = jerk_fraction * accel_fraction * total_time     # end of jerk up
        t3 = accel_fraction * total_time                     # end of speeding up
        t2 = t3 - t1                                         # end of constant acceleration
        t4 = total_time - t3                                 # start of slowing down
        self.times = (t1, t2, t3, t4, t4 + t1, total_time - t1)

        self.peak_velocity = 1.0 / ((1.0 - accel_fraction) * total_time)
        self.peak_acceleration = self.peak_velocity / (t3 - t1)
        self.jerk = self.peak_acceleration / t1

        self._v1 = self.jerk / 2.0 * t1 * t1
        self._p1 = self.jerk / 6.0 * t1 * t1 * t1
        span = t2 - t1
        self._v2 = span * self.peak_acceleration + self._v1
        self._p2 = span * self._v1 + self._p1 + self.peak_acceleration / 2.0 * span * span
        ramp = t3 - t2
        self._p3 = (self.peak_velocity - self.jerk / 6.0 * ramp * ramp) * ramp + self._p2
        self._p4 = (t4 - t3) * self.peak_velocity + self._p3

    def sample(self, t: float) -> Sample:
        t1, t2, t3, t4, t5, t6 = self.times
        jerk, a_max, v_max = self.jerk, self.peak_acceleration, self.peak_velocity
        # No guard for t < 0: like the DLL, a negative time runs the first cubic
        # backwards (small negative position). Callers keep t in 0..total_time.
        if t < t1:
            return Sample(jerk / 6.0 * t ** 3, jerk / 2.0 * t * t, jerk * t)
        if t < t2:
            dt = t - t1
            return Sample(dt * self._v1 + self._p1 + a_max / 2.0 * dt * dt,
                          dt * a_max + self._v1, a_max)
        if t < t3:
            left = t3 - t
            return Sample(self._p3 - (v_max - jerk / 6.0 * left * left) * left,
                          v_max - jerk / 2.0 * left * left, jerk * left)
        if t < t4:
            return Sample((t - t3) * v_max + self._p3, v_max, 0.0)
        if t < t5:
            dt = t - t4
            return Sample(dt * v_max + self._p4 - jerk / 6.0 * dt ** 3,
                          v_max - jerk / 2.0 * dt * dt, -jerk * dt)
        if t < t6:
            dt = t - t5
            v5 = v_max - self._v1
            p5 = t1 * v_max + self._p4 - self._p1
            return Sample(dt * v5 + p5 - a_max / 2.0 * dt * dt, v5 - a_max * dt, -a_max)
        if t < self.total_time:
            left = self.total_time - t
            return Sample(1.0 - jerk / 6.0 * left ** 3, jerk / 2.0 * left * left, -jerk * left)
        return Sample(1.0, 0.0, 0.0)

    def setpoints(self, distance: float, period: float) -> list[float]:
        """Positions for one axis moving ``distance``, one per ``period`` seconds.

        Convenience for comparison with other planners; the DLL's own sampling
        and rounding of setpoints to counts were not traced.
        """
        if period <= 0:
            raise ValueError("period must be positive")
        points, step = [], 0
        while step * period < self.total_time:
            points.append(distance * self.sample(step * period).position)
            step += 1
        points.append(distance * self.sample(self.total_time).position)
        return points
