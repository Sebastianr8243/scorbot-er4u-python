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
    gear_ratio: float
    source: Source
    note: str
    status: str = STATUS


# Motor gearbox ratios (manual: motors 1-3 127.1:1, motors 4-5 65.5:1, gripper 19.5:1).
GEAR_RATIO_ARM = NominalValue(127.1, "motor rev per gearbox output rev", SPECS)
GEAR_RATIO_WRIST = NominalValue(65.5, "motor rev per gearbox output rev", SPECS)
GEAR_RATIO_GRIPPER = NominalValue(19.5, "motor rev per gearbox output rev", SPECS)

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
        "wrist_pitch", 4, 260.0, -130.0, 130.0, GEAR_RATIO_WRIST.value, SPECS,
        _SIGN_NOTE + " Pitch is driven by motors 4 and 5 together (differential)."),
    "wrist_roll": AxisRange(
        "wrist_roll", 5, 1140.0, -570.0, 570.0, GEAR_RATIO_WRIST.value, SPECS,
        "Unlimited mechanically; +/-570 degrees electrically. Driven by motors 4 and 5."),
})

# Side-view figure dimensions (mm).
BASE_HEIGHT_MM = NominalValue(364.0, "mm", SIDE_VIEW)
UPPER_ARM_MM = NominalValue(220.0, "mm", SIDE_VIEW)
FOREARM_MM = NominalValue(220.0, "mm", SIDE_VIEW)
VERTICAL_ENVELOPE_MM = NominalValue(1040.0, "mm", SIDE_VIEW)
MAX_OPERATING_RADIUS_MM = NominalValue(610.0, "mm", SPECS)

REPEATABILITY_MM = NominalValue(0.18, "mm (+/-, at TCP)", SPECS)
MAX_PAYLOAD_KG = NominalValue(1.0, "kg (including gripper)", SPECS)
MAX_PATH_SPEED_MM_S = NominalValue(600.0, "mm/s", SPECS)

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
# reproduces every axis (inference). Wrist values are per motor; the legacy pitch
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
VENDOR_BASE_HEIGHT_MM = VendorPrior(349.0, "mm", f"{VENDOR_INI}, ROB_4u.INI")
VENDOR_UPPER_ARM_MM = VendorPrior(221.0, "mm", f"{VENDOR_INI}, ROB_4u.INI")
VENDOR_FOREARM_MM = VendorPrior(221.0, "mm", f"{VENDOR_INI}, ROB_4u.INI")
VENDOR_GRIPPER_LENGTH_MM = VendorPrior(145.0, "mm", f"{VENDOR_INI}, ROB_4u.INI")


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


def counts_per_degree(counts_per_motor_rev: float, joint: str) -> float:
    """Expected joint counts/degree = cpr * gear_ratio / 360 (motor-side encoder).

    Encoder counts per revolution are not in the manuals, so ``cpr`` is an
    input. Ignores any belt or lead-screw stage after the gearbox, which the
    manual says exists for some axes; treat the result as a prior only.
    """
    if (isinstance(counts_per_motor_rev, bool) or not isinstance(counts_per_motor_rev, (int, float))
            or not math.isfinite(counts_per_motor_rev) or counts_per_motor_rev <= 0):
        raise ValueError("counts_per_motor_rev must be a positive finite number")
    return counts_per_motor_rev * axis_range(joint).gear_ratio / 360.0


def implied_counts_per_motor_rev(counts_per_degree_value: float, joint: str) -> float:
    """Inverse of :func:`counts_per_degree`: cpr implied by a joint scale (magnitude)."""
    return abs(counts_per_degree_value) * 360.0 / axis_range(joint).gear_ratio
