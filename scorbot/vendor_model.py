"""How Intelitek's own software converts ER-4u encoder counts and joint angles.

Read from ``USBC.dll`` by static analysis (docs/VENDOR_DLL_PROTOCOL.md,
section 10) and re-implemented here. Everything in this module is a **prior
from disassembly, not measured on our arm**: it says what the vendor software
would compute, with the vendor's default parameter files. Per-arm calibration
still comes from physical measurement (docs/PHYSICAL_CALIBRATION.md).

Offline and pure: standard library only, wired into no motion command.

Angles are the DLL's internal joint values, in radians:

- ``base``, ``shoulder``, ``elbow``, ``pitch`` are joint angles relative to the
  previous link; ``roll`` is the gripper roll.
- The USNA toolboxes report the same values with shoulder, elbow and pitch
  negated (their ``ScorGetJt``), to match the teach pendant's directions.

Two facts follow from the formulas **with the ER-4u default parameters** and
matter for anything built on them:

- The elbow motor sets the forearm's angle to the horizontal, not to the upper
  arm: ``shoulder + elbow`` depends only on the elbow encoder. Likewise the
  two wrist motors set the gripper's pitch to the horizontal:
  ``shoulder + elbow + pitch`` depends only on the wrist encoders. Moving the
  shoulder alone therefore leaves the forearm and gripper orientation as they
  were, while the relative elbow and pitch angles change. This is not a
  general property of the formula: it needs gearing ``(1, -1, ...)`` and
  shoulder and elbow scales that are equal and opposite, which the defaults
  have. ``orientation_is_decoupled`` checks a parameter set for it.
- Pitch and roll come from the two wrist motors together: pitch from half
  their difference, roll from half their sum.

All counts zero gives the pose the USNA toolboxes publish as "home". That is
the vendor's nominal zero-count pose. Whether our arm's homing leaves the
counters at zero is a separate, unverified question: the legacy homing does
not zero them, and the SDK records the counts it observes after homing.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

STATUS = "vendor formula and INI defaults, read from USBC.dll; not measured on our arm"
MOTORS = ("base", "shoulder", "elbow", "wrist_motor_1", "wrist_motor_2")
JOINTS = ("base", "shoulder", "elbow", "pitch", "roll")


@dataclass(frozen=True)
class VendorAxes:
    """Per-axis values from Intelitek's parameter files, in motor order.

    ``no_enc_90`` is the INI ``NoEnc90``: encoder counts for 90 degrees of the
    axis (signed). ``horiz_pos`` is ``HorizPos``: the count at which the DLL's
    angle for that axis is zero. ``gearing`` is ``[Gearing]`` 1-4, the signs
    that say how the encoders combine. Defaults are the ``$Default`` ER-4u set
    shipped with the USNA ScorBot Toolbox (``ER4Ax1-5.ini``, ``ROB_4u.INI``).
    """

    no_enc_90: tuple[int, int, int, int, int] = (-12770, -10216, 10216, 2511, 2511)
    horiz_pos: tuple[int, int, int, int, int] = (0, -13653, -10786, -1773, 0)
    gearing: tuple[int, int, int, int] = (1, -1, -1, 1)

    def __post_init__(self):
        if any(value == 0 for value in self.no_enc_90):
            raise ValueError("NoEnc90 must be nonzero for every axis")
        if any(value not in (-1, 0, 1) for value in self.gearing):
            raise ValueError("Gearing values must be -1, 0 or 1")

    def radians_per_count(self, axis: int) -> float:
        return (math.pi / 2) / self.no_enc_90[axis]

    def counts_per_radian(self, axis: int) -> float:
        return self.no_enc_90[axis] / (math.pi / 2)

    def counts_per_degree(self, axis: int) -> float:
        return self.no_enc_90[axis] / 90.0

    def orientation_is_decoupled(self) -> bool:
        """Whether a shoulder-motor move leaves forearm and gripper orientation alone.

        True for the ER-4u defaults. It needs the elbow to add the shoulder
        encoder (gearing 1 = 1), the pitch to subtract the elbow (gearing 2 =
        -1), and shoulder and elbow scales that are equal and opposite.
        """
        return (self.gearing[0] == 1 and self.gearing[1] == -1
                and self.no_enc_90[1] == -self.no_enc_90[2])


ER4U = VendorAxes()


def _half(value: int) -> int:
    """Integer halving that truncates toward zero, as the DLL's does."""
    return -((-value) // 2) if value < 0 else value // 2


def _truncate(value: float) -> int:
    """The DLL converts with ``__ftol``: truncation toward zero."""
    return math.trunc(value)


def _combine(sign: int, first: int, second: int, alone: int) -> int:
    """Half-difference, one encoder alone, or half-sum, by a gearing sign."""
    if sign == -1:
        return _half(first - second)
    if sign == 1:
        return _half(first + second)
    return alone


def counts_to_joints(counts, axes: VendorAxes = ER4U) -> dict[str, float]:
    """Encoder counts (motor order) to the DLL's joint angles in radians.

    Follows the DLL's function at 0x100303db (2018 build) step by step.
    """
    e = [int(value) for value in counts]
    if len(e) != 5:
        raise ValueError("Five encoder counts are required, in motor order")
    g1, g2, g3, g4 = axes.gearing
    k = [axes.radians_per_count(i) for i in range(5)]
    h = axes.horiz_pos

    base = (e[0] - h[0]) * k[0]
    shoulder = (e[1] - h[1]) * k[1]
    # The elbow encoder is combined with the shoulder encoder first.
    elbow = (e[2] + g1 * e[1] - h[2]) * k[2]
    pitch = -g1 * shoulder
    pitch += (_combine(g3, e[3], e[4], e[3]) - h[3]) * k[3]
    pitch += g2 * elbow
    roll = (_combine(g4, e[3], e[4], e[4]) - h[4]) * k[4]
    return dict(zip(JOINTS, (base, shoulder, elbow, pitch, roll)))


def joints_to_counts(joints, axes: VendorAxes = ER4U) -> dict[str, int]:
    """The DLL's joint angles in radians to encoder counts (motor order).

    Follows the DLL's function at 0x10030ed2 (2018 build). Each term is
    truncated toward zero on its own, as there, so a round trip through
    ``counts_to_joints`` can differ from the input by a count or two.
    Only the ER-4u gearing (1, -1, -1, 1) is implemented.
    """
    if axes.gearing != (1, -1, -1, 1):
        raise ValueError("joints_to_counts implements the ER-4u gearing only")
    j = [float(joints[name]) for name in JOINTS]
    k = [axes.counts_per_radian(i) for i in range(5)]
    h = axes.horiz_pos

    e0 = _truncate(h[0] + k[0] * j[0])
    e1 = _truncate(h[1] + k[1] * j[1])
    e2 = _truncate(h[2] + k[2] * j[2]) - e1
    pitch_counts = _truncate(h[3] + k[3] * (j[3] + j[1] + j[2]))
    roll_counts = _truncate(h[4] + k[4] * j[4])
    return dict(zip(MOTORS, (e0, e1, e2, pitch_counts + roll_counts,
                             roll_counts - pitch_counts)))


def absolute_angles(joints) -> dict[str, float]:
    """Angles to the horizontal, each of which one motor group sets alone."""
    return {
        "upper_arm": joints["shoulder"],
        "forearm": joints["shoulder"] + joints["elbow"],
        "gripper_pitch": joints["shoulder"] + joints["elbow"] + joints["pitch"],
    }


def toolbox_degrees(joints) -> dict[str, float]:
    """The same pose as the USNA toolboxes report it: degrees, with shoulder,
    elbow and pitch negated (MTIS ``ScorGetJt``, Kutzer ``ScorGetBSEPR``)."""
    signs = {"base": 1, "shoulder": -1, "elbow": -1, "pitch": -1, "roll": 1}
    return {name: signs[name] * math.degrees(joints[name]) for name in JOINTS}
