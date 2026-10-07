"""Joint angles for the ER-4U from what is already known about it. Not measured.

The second calibration tier. The first is a physical calibration of one arm
(``calibration.py``, measured with an angle gauge, none exists yet). This one
is the same for every ER-4U: the vendor's own conversion between encoder
counts and joint angles, read from ``USBC.dll`` (``vendor_model.py``), with
the vendor's parameter files. It reproduces the home position the USNA
ScorBot Toolbox publishes to within 0.3 mm (``tests/test_source_model.py``).

The owner allowed this tier to drive motion on 2026-10-05, **inside the
travel cap only** (``limits.TRAVEL_CAP_DEG`` from the session home, base,
shoulder and elbow), until a lab acceptance run confirms or corrects it.
On 2026-10-06 the owner lifted the cap from 10 to 180 degrees without that
run, so the joint limits below are the real travel bound and the whole pose
is checked (``pose_limit_excess``). ``outside_window`` is the simulator's
check. It is a model of the arm, not a
measurement of ours: a joint may be a little off in scale or zero, and what
it assumes about our arm is listed below.

Angles are degrees in the convention of the USNA toolboxes (their BSEPR):
base, shoulder, elbow, pitch, roll, each relative to the previous link, with
positive shoulder, elbow and pitch lifting.

What it assumes about our arm, each to be checked at the lab:

* the session home (where the legacy homing ends) is the vendor's home pose,
  where the vendor zeroes its counters. The vendor backs off from each switch
  by a set offset and the legacy code runs on for twelve messages, so the two
  differ by a small amount per joint;
* the encoders count the way the vendor's parameter files say;
* the arm is an ER-4u with the default parameter set.

No USB. The count and limit half is standard library only; the geometry half
loads ``kinematics.py`` (NumPy) when it is first used.
"""

from __future__ import annotations

import functools
import math

from . import limits, vendor_model

TIER = "sources"
STATUS = ("from sources (vendor DLL formula and parameter files; USNA ScorBot Toolbox "
          "home pose); not measured on this arm")
JOINTS = vendor_model.JOINTS                       # base, shoulder, elbow, pitch, roll
MOTORS = limits.RECORDED_MOTORS                    # the five motors behind them
_AXES = vendor_model.ER4U
# Encoder counts per degree of each motor's own axis, as a magnitude.
COUNTS_PER_DEGREE = {motor: abs(_AXES.counts_per_degree(index))
                     for index, motor in enumerate(MOTORS)}


def _numbers(values, names, what):
    try:
        numbers = [values[name] for name in names]
    except (KeyError, TypeError):
        raise ValueError(f"{what} need a value for each of {names}") from None
    for name, value in zip(names, numbers):
        if isinstance(value, bool) or not isinstance(value, (int, float)) \
                or not math.isfinite(value):
            raise ValueError(f"{name} must be a finite number")
    return numbers


def angles_from_counts(counts_from_home) -> dict[str, float]:
    """Motor counts from the session home to joint angles in degrees."""
    counts = _numbers(counts_from_home, MOTORS, "Counts")
    joints = vendor_model.counts_to_joints([round(value) for value in counts], _AXES)
    return vendor_model.toolbox_degrees(joints)


def counts_from_angles(angles) -> dict[str, int]:
    """Joint angles in degrees to motor counts from the session home.

    Each term is truncated toward zero as the vendor's code does, so a round
    trip can differ from the input by a count or two.
    """
    degrees = _numbers(angles, JOINTS, "Angles")
    signs = (1, -1, -1, -1, 1)                     # toolbox convention back to the DLL's
    radians = {name: sign * math.radians(value)
               for name, sign, value in zip(JOINTS, signs, degrees)}
    counts = vendor_model.joints_to_counts(radians, _AXES)
    return {motor: counts[name] for motor, name in zip(MOTORS, vendor_model.MOTORS)}


HOME_ANGLES = angles_from_counts(dict.fromkeys(MOTORS, 0))


def arm_counts_from_angles(*, base: float | None = None, shoulder: float | None = None,
                           elbow: float | None = None) -> dict[str, int]:
    """Counts from home for the three arm motors, for these joint angles.

    A joint left out stays at its home angle. Pitch and roll are not asked
    for: the wrist motors are left where they are, see ``pose_for_arm``.
    """
    wanted = dict(HOME_ANGLES)
    for name, value in (("base", base), ("shoulder", shoulder), ("elbow", elbow)):
        if value is not None:
            wanted[name] = value
    counts = counts_from_angles(wanted)
    return {motor: counts[motor] for motor in limits.ARM_MOTORS}


def pose_for_arm(*, base: float | None = None, shoulder: float | None = None,
                 elbow: float | None = None) -> dict[str, float]:
    """The pose the arm takes for these angles with the wrist motors left alone.

    The wrist motors hold the gripper's angle to the floor, so with them at
    home the pitch relative to the forearm changes as the shoulder and elbow
    move: the gripper keeps pointing where it did. The returned pitch is that
    one, not the home pitch.
    """
    counts = dict.fromkeys(MOTORS, 0)
    counts.update(arm_counts_from_angles(base=base, shoulder=shoulder, elbow=elbow))
    return angles_from_counts(counts)


def limit_window() -> dict[str, tuple[float, float]]:
    """Counts from home at the joint limits, for the motors whose own count sets them.

    That is the base and the shoulder. The elbow's angle also depends on the
    shoulder's count, so its limit has to be checked on a pose.
    """
    window = {}
    for motor in ("base", "shoulder"):
        low, high = sorted(arm_counts_from_angles(**{motor: limit})[motor]
                           for limit in LIMITS_DEG[motor])
        window[motor] = (float(low), float(high))
    return window


def motion_window() -> dict[str, tuple[float, float]]:
    """Counts from home each arm motor may be sent to under this tier.

    The travel cap either side of home, cut short where a joint limit is
    nearer. Only the shoulder's is: home has the upper arm 120 degrees up and
    its limit is 124, so there are under four degrees of travel upward (a
    rising count).
    The base and shoulder counts fix their own joint angles. The elbow's angle
    also depends on the shoulder's count, so its limit is checked on the pose
    (``outside_window``), not here.
    """
    at_limits = limit_window()
    window = {}
    for motor in limits.ARM_MOTORS:
        cap = limits.TRAVEL_CAP_DEG * COUNTS_PER_DEGREE[motor]
        low, high = at_limits.get(motor, (-cap, cap))
        window[motor] = (max(-cap, low), min(cap, high))
    return window


def outside_window(angles) -> dict[str, int]:
    """Motors a pose would take outside what this tier may command; empty if none.

    Arm motors must stay inside the travel cap. Wrist motors must stay at
    home (within the drift band): wrist motion is disabled until measured.
    """
    counts = counts_from_angles(angles)
    window = motion_window()
    outside = {motor: counts[motor] for motor, (low, high) in window.items()
               if not low - 1 <= counts[motor] <= high + 1}       # a count of rounding
    # A joint limit reached through another motor (the elbow's angle depends
    # on the shoulder's count) is charged to the motor of that name.
    for name in outside_limits(angles):
        motor = name if name in limits.ARM_MOTORS else "wrist_motor_1"
        outside.setdefault(motor, counts[motor])
    outside.update({motor: counts[motor] for motor in limits.WRIST_MOTORS
                    if abs(counts[motor]) > limits.DRIFT_COUNTS})
    return outside


# -- geometry ---------------------------------------------------------------
#
# The same five dimensions in the vendor's ROB_4u.INI and in the USNA
# toolbox's DH table (ScorDHtable.m): shoulder axis 349 mm up and 16 mm ahead
# of the base axis, upper arm and forearm 221 mm, wrist to tool point 145.125.
# The community CAD model says 346 and 29 instead; it is the odd one out and
# is used only to place its own meshes (arm_chain.MESH_MODEL).
DH_OFFSETS_MM = (349.0, 0.0, 0.0, 0.0, 145.125)
DH_LENGTHS_MM = (16.0, 221.0, 221.0, 0.0, 0.0)


def geometry():
    """The dimensions as ``kinematics.DHParameters``.

    Imported here, not at the top: ``kinematics.py`` needs NumPy, and
    importing the SDK must not load it.
    """
    from . import kinematics
    return kinematics.DHParameters(d=DH_OFFSETS_MM, a=DH_LENGTHS_MM)


def _dh_angles(angles) -> list[float]:
    # kinematics.py measures wrist pitch from 90 degrees below the forearm, as
    # the toolbox's DH table does (its theta 4 is P + 90).
    base, shoulder, elbow, pitch, roll = _numbers(angles, JOINTS, "Angles")
    return [base, shoulder, elbow, pitch + 90.0, roll]


def xyzpr_from_angles(angles) -> tuple[float, float, float, float, float]:
    """Tool point (x, y, z in mm) and tool pitch and roll (degrees) for joint angles.

    Pitch is the tool's angle to the horizontal, shoulder + elbow + pitch, as
    in the toolbox's XYZPR.
    """
    from . import kinematics
    q = _dh_angles(angles)
    x, y, z = (float(value) for value in kinematics.tool_position(q, geometry()))
    tool_pitch, tool_roll = kinematics.tool_pitch_roll(q)
    return x, y, z, tool_pitch, tool_roll


def angles_from_xyzpr(x: float, y: float, z: float, pitch: float,
                      roll: float = 0.0) -> dict[str, float]:
    """Joint angles reaching a tool point (mm) with a tool pitch and roll (degrees).

    Elbow-up, facing the point: the solution the toolbox uses on hardware.
    A pose that reaches back over the base is not returned. Raises ValueError
    if the point is out of reach or on the base axis. Limits
    are a separate question: see ``outside_limits`` and ``outside_window``.
    """
    from . import kinematics
    q = kinematics.inverse(x, y, z, pitch, roll, elbow="up", params=geometry())
    base, shoulder, elbow, wrist, tool_roll = (float(value) for value in q)
    return {"base": base, "shoulder": shoulder, "elbow": elbow,
            "pitch": kinematics._wrap(wrist - 90.0), "roll": tool_roll}


# -- limits -----------------------------------------------------------------
#
# The narrower, joint by joint, of two sources:
#   - what the vendor's controller accepted when the USNA toolbox stepped a
#     degree at a time (ScorBSEPRLimits.m, which calls them "an estimate"):
#     base -133.77/175.81, shoulder -28.28/126.30, elbow -140.80/-5.16,
#     pitch -109.65/134.13, roll +/-360 (its guess);
#   - the vendor's parameter file (ROB_4u.INI, turned into this sign
#     convention): base -132/174, shoulder -31/124, elbow -160/115,
#     pitch -115/113, roll +/-570.
# Both are narrower than the manual's travel. The real limits are coupled:
# the toolbox notes that shoulder, elbow and pitch near their upper limits
# together can fail. The elbow's upper limit is the toolbox refusing
# elbow-down, not a mechanical stop.
LIMITS_DEG = {
    "base": (-132.0, 174.0),
    "shoulder": (-28.28, 124.0),
    "elbow": (-140.80, -5.16),
    "pitch": (-109.65, 113.0),
    "roll": (-360.0, 360.0),
}


def outside_limits(angles) -> dict[str, float]:
    """Joints a pose puts outside ``LIMITS_DEG``; empty if none."""
    values = _numbers(angles, JOINTS, "Angles")
    return {name: value for name, value in zip(JOINTS, values)
            if not LIMITS_DEG[name][0] <= value <= LIMITS_DEG[name][1]}


def limit_excess(angles) -> dict[str, float]:
    """How far, in degrees, each joint is past its limit; empty if none are."""
    values = _numbers(angles, JOINTS, "Angles")
    excess = {name: max(LIMITS_DEG[name][0] - value, value - LIMITS_DEG[name][1])
              for name, value in zip(JOINTS, values)}
    return {name: over for name, over in excess.items() if over > 0}


def pose_limit_excess(counts_from_home) -> dict[str, float]:
    """``limit_excess`` for the pose these motor counts from home give.

    Counts left out count as home. This is the whole-pose check the real arm
    needs: the elbow's angle follows the shoulder's count, and the wrist
    pitch follows the shoulder and elbow while the wrist motors stand still,
    so a count that is inside its own window can still put another joint past
    its limit. Wrist motors left out are taken at home, as for a stream.
    """
    counts = dict.fromkeys(MOTORS, 0)
    counts.update(counts_from_home)
    return limit_excess(angles_from_counts(counts))


@functools.cache
def home_window() -> dict[str, tuple[int, int]]:
    """Counts each arm motor may move from home alone, by the whole-pose limits.

    The other motors stay at home. Found by bisection from home out to the
    travel cap. This is the range to show a person (sliders, messages); every
    target is still checked with ``pose_limit_excess``, because the window of
    one motor moves with the others.
    """
    window = {}
    for motor in limits.ARM_MOTORS:
        cap = int(limits.TRAVEL_CAP_DEG * COUNTS_PER_DEGREE[motor])
        edges = []
        for sign in (-1, 1):
            low, high = 0, cap
            if pose_limit_excess({motor: sign * high}):
                while high - low > 1:
                    middle = (low + high) // 2
                    if pose_limit_excess({motor: sign * middle}):
                        high = middle
                    else:
                        low = middle
                high = low
            edges.append(sign * high)
        window[motor] = (edges[0], edges[1])
    return window


def pose_limit_problem(counts_from_home) -> str | None:
    """A sentence naming the joints these counts would put past a limit, or None."""
    excess = pose_limit_excess(counts_from_home)
    if not excess:
        return None
    return "; ".join(f"{name} {over:.1f} degrees past its {LIMITS_DEG[name][0]:g} to "
                     f"{LIMITS_DEG[name][1]:g} degree limit" for name, over in excess.items())
