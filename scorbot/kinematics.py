"""UNVALIDATED offline kinematics reference for the ScorBot ER-4U.

Status
------
This module is an offline reference for validating the legacy inverse
kinematics (``openScorbot/libdef.cIn`` and ``moveXYZ.controlXYZ``). It is
**not wired into any motion command** and must not be used to command the arm
until frames, dimensions, joint zeros and joint signs have been verified
against measured poses (docs/design/ARCHITECTURE.md sections 14 and 15).

* Geometry is nominal: every DH value below is copied from legacy
  ``openScorbot/conf.py``; none has been measured on our arm.
* Joint zero and sign conventions are unverified.
* ``conf.py`` lists only four twist angles; ``alpha5 = 0`` is an assumption.

Joint angle convention (ASSUMPTION)
-----------------------------------
Angles are standard (distal) DH joint variables, in degrees, for
``(base, shoulder, elbow, wrist_pitch, wrist_roll)`` with zero DH offsets:

* ``base``: rotation about the vertical base axis; 0 points along +x.
* ``shoulder``: elevation of the upper arm above horizontal; 90 is vertical.
* ``elbow``: forearm angle relative to the upper arm; negative bends the
  forearm down ("elbow up" posture).
* ``wrist_pitch``: tool axis relative to the forearm; with 0 the tool axis is
  90 degrees below the forearm.
* ``wrist_roll``: rotation about the tool axis.

We assume these are the same angles as legacy ``angRef = [0, 90, -90, 0, 0]``
(the pose reported after HOME: upper arm vertical, forearm horizontal). That
matches the planar geometry inside ``libdef.cIn`` (shoulder angle from
horizontal, ``q3 = -acos(...)``), but nobody has checked it against the real
arm, and the wrist entries of ``angRef`` have no geometric definition in the
legacy code. With this assumption the HOME pose puts the tool pointing
straight down; that too is unverified.

Tool pitch used by :func:`inverse` is the elevation of the tool approach axis
(DH ``z5``) above horizontal, in the arm's vertical plane:
``pitch = shoulder + elbow + wrist_pitch - 90``; ``-90`` points straight down.
"""

from dataclasses import dataclass
import math

import numpy as np


JOINT_NAMES = ("base", "shoulder", "elbow", "wrist_pitch", "wrist_roll")


@dataclass(frozen=True)
class DHParameters:
    """Standard DH table (mm, radians). Nominal, from legacy conf.py, not measured."""

    # link-offset d: nominal, from legacy conf.py, not measured.
    d: tuple = (364.0, 0.0, 0.0, 0.0, 145.125)
    # link-length a: nominal, from legacy conf.py, not measured.
    a: tuple = (16.0, 220.0, 220.0, 0.0, 0.0)
    # link-twist-angle alpha: nominal, from legacy conf.py, not measured.
    # conf.py has four entries; the fifth (0) is an assumption.
    alpha: tuple = (math.pi / 2, 0.0, 0.0, math.pi / 2, 0.0)
    # Joint zero offsets: assumed 0 (legacy code defines none), not measured.
    offset_deg: tuple = (0.0, 0.0, 0.0, 0.0, 0.0)


NOMINAL = DHParameters()


def _joints(q):
    values = np.asarray(q, dtype=float)
    if values.shape != (5,) or not np.all(np.isfinite(values)):
        raise ValueError("Expected five finite joint angles in degrees")
    return values


def _finite(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float, np.floating, np.integer)) \
            or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def dh_transform(d, theta, a, alpha):
    """Standard DH link transform Rz(theta) Tz(d) Tx(a) Rx(alpha)."""
    ct, st = math.cos(theta), math.sin(theta)
    ca, sa = math.cos(alpha), math.sin(alpha)
    return np.array([[ct, -st * ca, st * sa, a * ct],
                     [st, ct * ca, -ct * sa, a * st],
                     [0.0, sa, ca, d],
                     [0.0, 0.0, 0.0, 1.0]])


def link_frames(q, params=NOMINAL):
    """Base-to-link transforms T01..T05 for joint angles in degrees."""
    values = _joints(q)
    frames, pose = [], np.eye(4)
    for i in range(5):
        theta = math.radians(values[i] + params.offset_deg[i])
        pose = pose @ dh_transform(params.d[i], theta, params.a[i], params.alpha[i])
        frames.append(pose)
    return frames


def forward(q, params=NOMINAL):
    """4x4 base-to-tool pose (mm) for five joint angles in degrees. UNVALIDATED."""
    return link_frames(q, params)[-1]


def tool_position(q, params=NOMINAL):
    """Tool-flange position (x, y, z) in mm. UNVALIDATED."""
    return forward(q, params)[:3, 3].copy()


def wrist_center(q, params=NOMINAL):
    """Wrist pitch axis position (origin of DH frame 3) in mm. UNVALIDATED."""
    return link_frames(q, params)[2][:3, 3].copy()


def tool_pitch_roll(q):
    """Tool (pitch, roll) in degrees implied by joint angles, per the module convention."""
    values = _joints(q)
    return (_wrap(values[1] + values[2] + values[3] - 90.0), _wrap(values[4]))


def _wrap(angle_deg):
    wrapped = (angle_deg + 180.0) % 360.0 - 180.0
    return 180.0 if wrapped == -180.0 else wrapped


def _planar_wrist_target(x, y, z, pitch_deg, params):
    radius = math.hypot(x, y)
    if radius < 1e-9:
        raise ValueError("Target on the base axis: base angle is undefined")
    pitch = math.radians(pitch_deg)
    wrist_r = radius - params.d[4] * math.cos(pitch) - params.a[0]
    wrist_z = z - params.d[4] * math.sin(pitch) - params.d[0]
    return math.atan2(y, x), wrist_r, wrist_z


def inverse(x, y, z, pitch_deg, roll_deg=0.0, elbow="up", params=NOMINAL):
    """Joint angles in degrees reaching tool position (mm) and pitch/roll. UNVALIDATED.

    Only the front-facing base solution is returned. Raises ValueError when the
    target is geometrically unreachable or singular. Joint limits are NOT
    checked: no measured limits exist yet.
    """
    x, y, z = (_finite(v, n) for v, n in ((x, "x"), (y, "y"), (z, "z")))
    pitch_deg = _finite(pitch_deg, "pitch_deg")
    roll_deg = _finite(roll_deg, "roll_deg")
    if elbow not in ("up", "down"):
        raise ValueError('elbow must be "up" or "down"')
    if any(params.offset_deg) or params.d[1:4] != (0.0, 0.0, 0.0) \
            or params.a[3:] != (0.0, 0.0) or params.alpha[1:3] != (0.0, 0.0):
        raise ValueError("Closed-form inverse only supports the nominal planar-arm layout")
    q1, wrist_r, wrist_z = _planar_wrist_target(x, y, z, pitch_deg, params)
    l2, l3 = params.a[1], params.a[2]
    cos_q3 = (wrist_r ** 2 + wrist_z ** 2 - l2 ** 2 - l3 ** 2) / (2 * l2 * l3)
    if abs(cos_q3) > 1.0 + 1e-12:
        raise ValueError(f"Target ({x:g}, {y:g}, {z:g}) pitch {pitch_deg:g} is out of reach")
    q3 = math.acos(max(-1.0, min(1.0, cos_q3)))
    if elbow == "up":
        q3 = -q3
    q2 = math.atan2(wrist_z, wrist_r) - math.atan2(l3 * math.sin(q3), l2 + l3 * math.cos(q3))
    q2, q3 = math.degrees(q2), math.degrees(q3)
    q4 = pitch_deg + 90.0 - q2 - q3
    return np.array([_wrap(math.degrees(q1)), _wrap(q2), _wrap(q3), _wrap(q4), _wrap(roll_deg)])


def reachable(x, y, z, pitch_deg, params=NOMINAL):
    """True when the nominal geometry can reach the target (joint limits ignored)."""
    try:
        inverse(x, y, z, pitch_deg, params=params)
    except ValueError:
        return False
    return True


def trapezoid(q0, q1, vmax_deg_s, amax_deg_s2, dt_s):
    """Synchronized trapezoidal joint trajectory from q0 to q1 (degrees).

    Every joint follows the same normalized trapezoid, so all joints start and
    stop together; the joint with the largest travel runs at the given limits
    and no joint exceeds ``vmax_deg_s`` or ``amax_deg_s2``. Moves too short to
    reach ``vmax`` become triangular. Returns ``(times, positions)`` with shape
    ``(n,)`` and ``(n, len(q0))``; the final sample is exactly ``q1`` at the
    exact end time. ``dt_s`` is the sample interval; the real controller update
    interval is UNKNOWN (ARCHITECTURE section 15).
    """
    start = np.asarray(q0, dtype=float)
    end = np.asarray(q1, dtype=float)
    if start.ndim != 1 or start.shape != end.shape or not start.size \
            or not (np.all(np.isfinite(start)) and np.all(np.isfinite(end))):
        raise ValueError("q0 and q1 must be equal-length finite joint vectors")
    vmax = _finite(vmax_deg_s, "vmax_deg_s")
    amax = _finite(amax_deg_s2, "amax_deg_s2")
    dt = _finite(dt_s, "dt_s")
    if vmax <= 0 or amax <= 0 or dt <= 0:
        raise ValueError("vmax_deg_s, amax_deg_s2 and dt_s must be positive")
    delta = end - start
    distance = float(np.max(np.abs(delta)))
    if distance == 0.0:
        return np.array([0.0]), start[np.newaxis, :].copy()
    if distance >= vmax ** 2 / amax:
        t_acc = vmax / amax
        t_cruise = (distance - vmax * t_acc) / vmax
        peak = vmax
    else:
        t_acc = math.sqrt(distance / amax)
        t_cruise = 0.0
        peak = amax * t_acc
    total = 2 * t_acc + t_cruise
    steps = max(1, math.ceil(total / dt - 1e-9))
    times = np.minimum(np.arange(steps + 1) * dt, total)
    times[-1] = total

    def travelled(t):
        if t <= t_acc:
            return 0.5 * amax * t * t
        if t <= t_acc + t_cruise:
            return 0.5 * amax * t_acc ** 2 + peak * (t - t_acc)
        remaining = total - t
        return distance - 0.5 * amax * remaining * remaining

    fraction = np.array([travelled(t) / distance for t in times])
    fraction[-1] = 1.0
    positions = start + fraction[:, np.newaxis] * delta
    positions[-1] = end
    return times, positions
