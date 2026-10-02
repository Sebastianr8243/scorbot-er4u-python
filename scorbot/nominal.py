"""Nominal ER-4u values copied from the Intelitek manual. NOT measured.

Every value here is **nominal, from the manual, not measured** on any of our
arms. Each carries its source (manual title, catalog number, page) so a
reader can check it. Use these only as priors and sanity bounds; per-arm
calibration still comes from physical measurement (docs/PHYSICAL_CALIBRATION.md).

What the manual does NOT give: encoder counts per revolution, encoder zero,
where each joint's 0 degrees is, and which direction is positive. Vendor
parameter-file defaults for some of these are kept separately below as
``VendorPrior`` values, which are priors too, never calibration. That is why
joint ranges are enforced as spans (max - min) and never as signed limits.

Standard library only: this module is imported by calibration loading and
must stay cheap.
"""

from dataclasses import dataclass
import math
from types import MappingProxyType


@dataclass(frozen=True)
class Source:
    manual: str
    catalog: str
    page: str

    def cite(self) -> str:
        return f"{self.manual}, {self.catalog}, {self.page}"


ER4U_MANUAL = "SCORBOT-ER 4u User Manual"
ER4U_CATALOG = "#100343 Rev. B"
SPECS = Source(ER4U_MANUAL, ER4U_CATALOG, "pp. 4-6 (specifications)")
SIDE_VIEW = Source(ER4U_MANUAL, ER4U_CATALOG, "pp. 4-6 (side-view working envelope figure)")
PARTS_LIST = Source(ER4U_MANUAL, ER4U_CATALOG, "p. 28 (parts list, S309/S310 motors)")

STATUS = "nominal, from manual, not measured"


@dataclass(frozen=True)
class NominalValue:
    value: float
    unit: str
    source: Source
    status: str = STATUS


@dataclass(frozen=True)
class AxisRange:
    """Manual joint travel. ``span_deg`` is the only part this code enforces.

    ``signed_min_deg``/``signed_max_deg`` are the manual's figures, kept for
    reference. The manual does not define the zero or the positive direction,
    and our encoder home is not the manual's zero, so a calibration's signed
    soft limits cannot be compared with them.
    """

    joint: str
    axis: int
    span_deg: float
    signed_min_deg: float | None
    signed_max_deg: float | None
    gear_ratio: float | None
    source: Source
    note: str
    status: str = STATUS


# Motor gearbox ratios (manual: motors 1-3 127.1:1, motors 4-5 65.5:1, gripper 19.5:1).
# The manual contradicts itself for motors 1-3: 127.1:1 in the specification table
# (p. 4) and 127.7:1 for S309/S310 in the parts list (p. 28). Neither is verified;
# the vendor parameter files below fit 127.7 exactly. The 127.1 prior stays the
# default only so existing hypotheses keep their meaning.
GEAR_RATIO_ARM = NominalValue(127.1, "motor rev per gearbox output rev", SPECS)
GEAR_RATIO_ARM_PARTS_LIST = NominalValue(127.7, "motor rev per gearbox output rev", PARTS_LIST)
GEAR_RATIO_WRIST = NominalValue(65.5, "motor rev per gearbox output rev", SPECS)
GEAR_RATIO_GRIPPER = NominalValue(19.5, "motor rev per gearbox output rev", SPECS)

# The wrist joints have no joint gear ratio: 65.5:1 is the motor gearbox, and the
# motor 4/5 differential ratio and signs are not in the manual. So gear_ratio is None.
_SIGN_NOTE = "Signed limits given, but zero and sign convention are unverified; only the span is enforced."

AXIS_RANGES = MappingProxyType({
    "base": AxisRange(
        "base", 1, 310.0, None, None, GEAR_RATIO_ARM.value, SPECS,
        "Manual gives a 310 degree total only; it does not say where zero is."),
    "shoulder": AxisRange(
        "shoulder", 2, 165.0, -35.0, 130.0, GEAR_RATIO_ARM.value, SPECS, _SIGN_NOTE),
    "elbow": AxisRange(
        "elbow", 3, 260.0, -130.0, 130.0, GEAR_RATIO_ARM.value, SPECS, _SIGN_NOTE),
    "wrist_pitch": AxisRange(
        "wrist_pitch", 4, 260.0, -130.0, 130.0, None, SPECS,
        _SIGN_NOTE + " Pitch is driven by motors 4 and 5 together (differential)."),
    "wrist_roll": AxisRange(
        "wrist_roll", 5, 1140.0, -570.0, 570.0, None, SPECS,
        "Unlimited mechanically; +/-570 degrees electrically. Driven by motors 4 and 5."),
})

# Side-view figure dimensions (mm). 364 mm is base bottom to the shoulder axis;
# the base pedestal itself is 190 mm.
SHOULDER_AXIS_HEIGHT_MM = NominalValue(364.0, "mm, base bottom to shoulder axis", SIDE_VIEW)
BASE_PEDESTAL_HEIGHT_MM = NominalValue(190.0, "mm", SIDE_VIEW)
UPPER_ARM_MM = NominalValue(220.0, "mm", SIDE_VIEW)
FOREARM_MM = NominalValue(220.0, "mm", SIDE_VIEW)
VERTICAL_ENVELOPE_MM = NominalValue(1040.0, "mm", SIDE_VIEW)
MAX_OPERATING_RADIUS_MM = NominalValue(610.0, "mm, top-view envelope radius", SPECS)

REPEATABILITY_MM = NominalValue(0.18, "mm (+/-, at TCP)", SPECS)
MAX_PAYLOAD_KG = NominalValue(1.0, "kg (including gripper)", SPECS)
MAX_PATH_SPEED_MM_S = NominalValue(600.0, "mm/s", SPECS)
ARM_AMBIENT_MIN_C = NominalValue(2.0, "deg C", SPECS)
ARM_AMBIENT_MAX_C = NominalValue(40.0, "deg C", SPECS)

# Legacy openScorbot base scale 2837/20 counts/deg (motion_profile.COUNTS_PER_DEGREE).
# 2837/20 * 360 / 127.1 = 401.8 counts per motor revolution. HYPOTHESIS ONLY:
# it assumes the base has no reduction beyond the 127.1:1 gearbox and that the
# legacy scale was right. Real fits (scripts/fit_calibration.py) test it.
LEGACY_BASE_COUNTS_PER_DEGREE = 2837 / 20
HYPOTHESIS_COUNTS_PER_MOTOR_REV = LEGACY_BASE_COUNTS_PER_DEGREE * 360.0 / GEAR_RATIO_ARM.value


# -- Vendor defaults (not from the user manual) ---------------------------------
#
# Intelitek's own ER-4u controller parameter files (ER4Ax1-6.ini, ROB_4u.INI,
# dated 2001-2003) as bundled in the USNA Kutzer ScorBot Toolbox. They are the
# vendor's defaults, not measurements of our arms: use them as priors to sanity
# check a physical calibration, never as one. They supersede the ~402 counts per
# motor revolution hypothesis above: 20-slot disk x 4 = 80 counts per motor rev,
# 127.7:1 gearbox, then a final stage (base 5:1, shoulder/elbow 4:1, wrist 23:12)
# reproduces every axis (inference: neither manual gives the x4 decode or 80). Wrist values are per motor; the legacy pitch
# scale (33.8) disagrees and is unresolved. See docs/MANUAL_AND_PRIOR_ART_FINDINGS.md.

VENDOR_STATUS = "vendor default, not measured"
VENDOR_INI = ("Intelitek ER-4u controller parameter files (ER4Ax*.ini, ROB_4u.INI), "
              "via github.com/kutzer/ScorBotToolbox ScorBotToolboxSupport/Par/er4u")


@dataclass(frozen=True)
class VendorPrior:
    """A vendor default value. Deliberately not a NominalValue (manual) type."""

    value: float
    unit: str
    source: str
    status: str = VENDOR_STATUS


def _per_degree(counts_per_90: int, axis: str) -> VendorPrior:
    return VendorPrior(abs(counts_per_90) / 90.0, "encoder counts per joint degree",
                       f"{VENDOR_INI}, {axis} NoEnc90={counts_per_90}")


# Magnitudes only; the files' signs (base and shoulder negative) are the vendor's
# direction convention and are not verified on our arms.
VENDOR_COUNTS_PER_DEGREE = MappingProxyType({
    "base": _per_degree(-12770, "ER4Ax1.ini"),
    "shoulder": _per_degree(-10216, "ER4Ax2.ini"),
    "elbow": _per_degree(10216, "ER4Ax3.ini"),
    "wrist_pitch": _per_degree(2511, "ER4Ax4.ini"),
    "wrist_roll": _per_degree(2511, "ER4Ax5.ini"),
})
# The INI's "base height" is probably (inferred) the DH offset to the shoulder axis, comparable with
# SHOULDER_AXIS_HEIGHT_MM (364), not with the 190 mm pedestal.
VENDOR_BASE_HEIGHT_MM = VendorPrior(349.0, "mm", f"{VENDOR_INI}, ROB_4u.INI")
VENDOR_UPPER_ARM_MM = VendorPrior(221.0, "mm", f"{VENDOR_INI}, ROB_4u.INI")
VENDOR_FOREARM_MM = VendorPrior(221.0, "mm", f"{VENDOR_INI}, ROB_4u.INI")
VENDOR_GRIPPER_LENGTH_MM = VendorPrior(145.0, "mm", f"{VENDOR_INI}, ROB_4u.INI")


@dataclass(frozen=True)
class VendorRange:
    """A vendor min/max pair, in the vendor's zero and sign convention (unverified here)."""

    minimum: float
    maximum: float
    unit: str
    source: str
    status: str = VENDOR_STATUS

    @property
    def span(self) -> float:
        return self.maximum - self.minimum


def _vendor_range(minimum: float, maximum: float, unit: str) -> VendorRange:
    return VendorRange(minimum, maximum, unit, f"{VENDOR_INI}, ER4Ax*.ini")


# Controller angle limits. Reference only: no check uses them. The calibration gate
# stays the manual's span in AXIS_RANGES (the elbow's 275 here is wider than 260).
VENDOR_JOINT_LIMITS_DEG = MappingProxyType({
    "base": _vendor_range(-132.0, 174.0, "joint degrees"),
    "shoulder": _vendor_range(-124.0, 31.0, "joint degrees"),
    "elbow": _vendor_range(-115.0, 160.0, "joint degrees"),
    "wrist_pitch": _vendor_range(-113.0, 115.0, "joint degrees"),
    "wrist_roll": _vendor_range(-570.0, 570.0, "joint degrees"),
})
# Encoder soft limits, counts from the vendor's zero (at hard home, SCORBASE p. 23).
# Whether our homing ends at the same zero, with the same signs, is unverified.
VENDOR_ENCODER_SOFT_LIMITS = MappingProxyType({
    "base": _vendor_range(-25000, 20000, "encoder counts"),
    "shoulder": _vendor_range(-18000, 1500, "encoder counts"),
    "elbow": _vendor_range(-25000, 20000, "encoder counts"),
    "wrist_pitch": _vendor_range(-15000, 15000, "encoder counts"),
})

VENDOR_LIMIT_REPORT_STATUS = ("diagnostic only, not a guard: targets are counts from this "
                              "session's home; vendor encoder zero (hard home) and sign "
                              "convention are unverified on our arms")


def _side(value: int, limit: VendorRange) -> str:
    if value < limit.minimum:
        return "below_min"
    if value > limit.maximum:
        return "above_max"
    return "inside"


def vendor_limit_report(target_from_home: dict[str, int | None]) -> dict:
    """Where each jog target sits against the vendor encoder limits, logged with a jog.

    ``target_from_home`` is wrap-aware counts from the session home (None if
    unknown). Evidence for whether our homing zero and signs match the
    vendor's, judged under both sign hypotheses. Never used to allow or refuse
    motion. Wrist motors have no per-motor vendor limit (pitch is a two-motor
    differential).
    """
    motors = {}
    for motor, target in target_from_home.items():
        limit = VENDOR_ENCODER_SOFT_LIMITS.get(motor)
        if limit is None or target is None:
            verdict = "no vendor limit" if limit is None else "indeterminate"
            motors[motor] = {"target_from_home": target, "same_sign": verdict,
                             "flipped_sign": verdict}
            continue
        motors[motor] = {"target_from_home": target,
                         "vendor_min": limit.minimum, "vendor_max": limit.maximum,
                         "same_sign": _side(target, limit),
                         "flipped_sign": _side(-target, limit)}
    return {"status": VENDOR_LIMIT_REPORT_STATUS, "motors": motors}


DATASHEET = "Intelitek ER-4u datasheet 35-1005-8600 Rev K"
# Effective joint speeds, used as velocity priors for offline planning only.
DATASHEET_JOINT_SPEED_DEG_S = MappingProxyType({
    name: VendorPrior(speed, "deg/s", DATASHEET)
    for name, speed in (("base", 20.0), ("shoulder", 26.3), ("elbow", 26.3),
                        ("wrist_pitch", 83.0), ("wrist_roll", 106.0))
})
DATASHEET_PATH_SPEED_MM_S = VendorPrior(700.0, "mm/s", DATASHEET)
DATASHEET_SHOULDER_SPAN_DEG = VendorPrior(158.0, "joint degrees", DATASHEET)


# -- Where sources disagree ----------------------------------------------------
#
# One verdict per numeric contradiction in docs/MANUAL_AND_PRIOR_ART_FINDINGS.md:
#   adopted    one source is clearly right for our use (reason says why)
#   both kept  both values stay available; nothing here picks one
#   measure    only a physical measurement can settle it
# No verdict loosens a safety bound, and none makes a value "measured".

VERDICTS = ("adopted", "both kept", "measure")


@dataclass(frozen=True)
class Contradiction:
    topic: str
    ours: str
    theirs: str
    verdict: str
    reason: str


CONTRADICTIONS = (
    Contradiction(
        "shoulder axis height", "364 mm, base bottom to shoulder axis (manual side view)",
        "349 mm base height (ROB_4u.INI, Kutzer DH table)", "measure",
        "Probably different reference points (the 15 mm may be the base plate); "
        "the kinematics prior needs a tape measurement on our arm."),
    Contradiction(
        "link lengths", "220 mm upper arm and forearm (manual side view)",
        "221 mm (ROB_4u.INI)", "both kept",
        "1 mm is within drawing precision; kinematics is offline and either value "
        "is a prior."),
    Contradiction(
        "elbow span", "260 degrees (manual), the calibration gate",
        "275 degrees (vendor INI angle limits +160/-115)", "adopted",
        "The manual's 260 stays the gate because it is the tighter bound; a "
        "calibration may not exceed the manual's travel (CLAUDE.md)."),
    Contradiction(
        "shoulder span", "165 degrees (manual), the calibration gate",
        "158 (datasheet), 155 (vendor INI angle limits)", "both kept",
        "The gate keeps the manual value; operators should set calibration soft "
        "limits inside 155, the tightest source. Tightening the gate is a "
        "separate reviewed change."),
    Contradiction(
        "path speed", "600 mm/s (manual)", "700 mm/s (datasheet)", "both kept",
        "Not used by any check; Cartesian motion is out of scope."),
    Contradiction(
        "wrist pitch counts per degree", "33.8 (legacy motion_profile)",
        "27.9 per motor (vendor INI NoEnc90=2511)", "measure",
        "Pitch is a two-motor differential; wrist jogs stay disabled until a "
        "measurement settles the scale."),
)


def axis_range(joint: str) -> AxisRange:
    try:
        return AXIS_RANGES[joint]
    except KeyError:
        raise ValueError(f"No manual range for joint {joint!r}") from None


def check_soft_limit_span(joint: str, soft_min_deg: float, soft_max_deg: float) -> None:
    """Raise ValueError if soft limits span more than the manual's travel.

    Only the span is checked: the manual's zero and sign convention are not
    known to match this code's home angle, so signed values cannot be compared.
    """
    limit = axis_range(joint)
    span = soft_max_deg - soft_min_deg
    if not math.isfinite(span) or span > limit.span_deg:
        raise ValueError(
            f"{joint}: soft-limit span {span:g} degrees exceeds the manual's "
            f"{limit.span_deg:g} degree travel ({limit.source.cite()})")


def _arm_gear_ratio(joint: str) -> float:
    ratio = axis_range(joint).gear_ratio
    if ratio is None:
        raise ValueError(
            f"{joint}: the manual gives no joint gear ratio (motors 4 and 5 drive the "
            "wrist through an unspecified differential)")
    return ratio


def counts_per_degree(counts_per_motor_rev: float, joint: str) -> float:
    """Expected joint counts/degree = cpr * gear_ratio / 360 (motor-side encoder).

    Encoder counts per revolution are not in the manuals, so ``cpr`` is an
    input. Ignores any belt or lead-screw stage after the gearbox, which the
    manual says exists for some axes, and uses the 127.1:1 spec-table ratio
    although the parts list says 127.7:1; treat the result as a prior only.
    Wrist joints raise ValueError: their joint ratio is not in the manual.
    """
    if (isinstance(counts_per_motor_rev, bool) or not isinstance(counts_per_motor_rev, (int, float))
            or not math.isfinite(counts_per_motor_rev) or counts_per_motor_rev <= 0):
        raise ValueError("counts_per_motor_rev must be a positive finite number")
    return counts_per_motor_rev * _arm_gear_ratio(joint) / 360.0


def implied_counts_per_motor_rev(counts_per_degree_value: float, joint: str) -> float:
    """Inverse of :func:`counts_per_degree`: cpr implied by a joint scale (magnitude)."""
    return abs(counts_per_degree_value) * 360.0 / _arm_gear_ratio(joint)
