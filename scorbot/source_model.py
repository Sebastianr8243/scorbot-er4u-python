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
``outside_window`` is the check for that. It is a model of the arm, not a
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

Pure: standard library only, no USB, wired into no motion command by itself.
"""

from __future__ import annotations

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


def motion_window() -> dict[str, tuple[float, float]]:
    """Counts from home each arm motor may be sent to under this tier."""
    return {motor: (-limits.TRAVEL_CAP_DEG * COUNTS_PER_DEGREE[motor],
                    limits.TRAVEL_CAP_DEG * COUNTS_PER_DEGREE[motor])
            for motor in limits.ARM_MOTORS}


def outside_window(angles) -> dict[str, int]:
    """Motors a pose would take outside what this tier may command; empty if none.

    Arm motors must stay inside the travel cap. Wrist motors must stay at
    home (within the drift band): wrist motion is disabled until measured.
    """
    counts = counts_from_angles(angles)
    window = motion_window()
    outside = {motor: counts[motor] for motor, (low, high) in window.items()
               if not low <= counts[motor] <= high}
    outside.update({motor: counts[motor] for motor in limits.WRIST_MOTORS
                    if abs(counts[motor]) > limits.DRIFT_COUNTS})
    return outside
