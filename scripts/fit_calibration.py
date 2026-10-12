"""Fit per-arm joint calibration from independently measured angles.

The tool refuses vendor-display rows and never commands the robot.
"""

import argparse
import csv
from hashlib import sha256
import json
import math
from pathlib import Path
from statistics import mean, median
from typing import NamedTuple

from openScorbot.motion_profile import COUNTS_PER_DEGREE as LEGACY_COUNTS_PER_DEGREE
from scorbot.calibration import CALIBRATED_JOINTS, signed_count_delta
from scorbot.nominal import (HYPOTHESIS_COUNTS_PER_MOTOR_REV, VENDOR_COUNTS_PER_DEGREE,
                             axis_range, check_soft_limit_span,
                             implied_counts_per_motor_rev)


COLUMNS = {"robot_id", "joint", "role", "approach", "reference_source",
           "encoder_count", "measured_angle_deg", "requested_target_deg"}


ROLES = ("home", "fit", "verify", "move_verify")

# What a joint's rows must show before a calibration is written (the messages repeat these numbers)
HOME_SPREAD_COUNTS = 50          # homes may differ from the median home by this many counts...
HOME_SPREAD_DEG = 2              # ...and by this many degrees
MIN_FIT_SPAN_COUNTS = 20         # the fit rows must cover at least this many counts...
MIN_FIT_SPAN_DEG = 2             # ...and this many degrees
MIN_ANGLE_PER_COUNT = 1 / 32768  # a slope below this means the counts do not map to the angle
MAX_ERROR_DEG = 2                # holdout error and motion error must stay within this
LIMIT_MARGIN_DEG = 2.5           # soft limits sit this far inside the measured range


class Measurement(NamedTuple):
    """One validated row of the measurements CSV."""

    joint: str
    role: str
    approach: str
    count: int
    angle: float
    requested: float | None      # only move_verify rows carry a requested target


def _read_measurement_rows(measurements: Path) -> list[dict]:
    with measurements.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not COLUMNS <= set(reader.fieldnames):
            raise ValueError(f"Measurement CSV needs columns: {', '.join(sorted(COLUMNS))}")
        rows = list(reader)
    if not rows:
        raise ValueError("Measurement CSV is empty")
    return rows


def _read_limits(limits_path: Path) -> dict:
    limits = json.loads(limits_path.read_text(encoding="utf-8"))
    if not isinstance(limits, dict) or not limits:
        raise ValueError("Limits JSON must contain at least one joint")
    if set(limits) - set(CALIBRATED_JOINTS):
        raise ValueError("Only base, shoulder and elbow are supported")
    return limits


def _parse_measurement(row: dict, robot_id: str) -> Measurement:
    if row["robot_id"] != robot_id or row["reference_source"] != "physical":
        raise ValueError("All rows must match robot_id and use a physical angle reference")
    if row["joint"] not in CALIBRATED_JOINTS or row["role"] not in ROLES:
        raise ValueError("Unknown joint or role")
    if row["role"] != "home" and row["approach"] not in ("+", "-"):
        raise ValueError("Fit and verify rows need an approach of + or -")
    count = int(row["encoder_count"])
    if not 0 <= count <= 65535:
        raise ValueError("Counts must be unsigned 16-bit")
    angle = float(row["measured_angle_deg"])
    requested = (float(row["requested_target_deg"])
                 if row["role"] == "move_verify" else None)
    if requested is not None and not math.isfinite(requested):
        raise ValueError("Requested targets must be finite")
    if not math.isfinite(angle):
        raise ValueError("Measured angles must be finite")
    return Measurement(row["joint"], row["role"], row["approach"], count, angle, requested)


def _split_by_role(joint: str, subset: list[Measurement]):
    """The joint's home, fit, verify and move_verify rows, once there are enough of each."""
    homes = [m for m in subset if m.role == "home"]
    training = [m for m in subset if m.role == "fit"]
    verifying = [m for m in subset if m.role == "verify"]
    moves = [m for m in subset if m.role == "move_verify"]
    if len(homes) < 3 or len(training) < 4 or len(verifying) < 3 or len(moves) < 3:
        raise ValueError(f"{joint}: need 3 homes, 4 fit, 3 verify and 3 move verification rows")
    for group in (training, verifying, moves):
        if {m.approach for m in group} != {"+", "-"}:
            raise ValueError(f"{joint}: both approach directions are required")
    return homes, training, verifying, moves


def _home_reference(joint: str, homes: list[Measurement]):
    """The home count and angle (medians) and how far the homes stray from the median count."""
    home_count = int(median(m.count for m in homes))
    home_angle = median(m.angle for m in homes)
    home_spread = max(abs(signed_count_delta(m.count, home_count)) for m in homes)
    if (home_spread > HOME_SPREAD_COUNTS
            or max(abs(m.angle - home_angle) for m in homes) > HOME_SPREAD_DEG):
        raise ValueError(f"{joint}: home repeatability is too poor to validate")
    return home_count, home_angle, home_spread


def _counts_per_degree(joint: str, training: list[Measurement], home_count: int, home_angle: float) -> float:
    """Least-squares fit through the home point of angle against counts, as counts per degree."""
    xs = [signed_count_delta(m.count, home_count) for m in training]
    ys = [m.angle - home_angle for m in training]
    if max(xs) - min(xs) < MIN_FIT_SPAN_COUNTS or max(ys) - min(ys) < MIN_FIT_SPAN_DEG:
        raise ValueError(f"{joint}: calibration span is too small")
    denominator = sum(x * x for x in xs)
    angle_per_count = sum(x * y for x, y in zip(xs, ys)) / denominator
    if abs(angle_per_count) < MIN_ANGLE_PER_COUNT:
        raise ValueError(f"{joint}: counts do not map reliably to angle")
    counts_per_degree = 1 / angle_per_count
    if abs(counts_per_degree) < 1:
        raise ValueError(f"{joint}: fitted count scale is implausible")
    return counts_per_degree


def _holdout_residuals(joint: str, verifying: list[Measurement], home_count: int, home_angle: float,
                       counts_per_degree: float):
    """Predicted minus measured angle for the verify rows, and the worst of them."""
    residuals = [home_angle + signed_count_delta(m.count, home_count)
                 / counts_per_degree - m.angle for m in verifying]
    worst = max(abs(error) for error in residuals)
    if worst > MAX_ERROR_DEG:
        raise ValueError(f"{joint}: holdout error {worst:.2f} degrees exceeds 2 degrees")
    return residuals, worst


def _motion_error(joint: str, moves: list[Measurement]) -> float:
    """The worst difference between where a move was asked to go and where it was measured to end."""
    motion_error = max(abs(m.angle - m.requested) for m in moves)
    if motion_error > MAX_ERROR_DEG:
        raise ValueError(f"{joint}: measured motion error exceeds 2 degrees")
    return motion_error


def _direction_bias(verifying: list[Measurement], residuals: list[float]) -> dict:
    """The mean holdout error when approaching from each direction."""
    return {direction: mean(error for m, error in zip(verifying, residuals) if m.approach == direction)
            for direction in ("+", "-")}


def _soft_limits(joint: str, limit: dict, subset: list[Measurement], home_angle: float):
    """The operator's soft limits, checked against the measured range and the manual's travel."""
    lower = float(limit["soft_min_deg"])
    upper = float(limit["soft_max_deg"])
    angles = [m.angle for m in subset]
    if not all(math.isfinite(value) for value in (lower, upper)):
        raise ValueError(f"{joint}: soft limits must be finite")
    if not (min(angles) + LIMIT_MARGIN_DEG <= lower < home_angle < upper
            <= max(angles) - LIMIT_MARGIN_DEG):
        raise ValueError(f"{joint}: soft limits must lie inside measured range with 2.5 degree margin")
    # The loader enforces this too; refuse here so no unloadable file is written.
    check_soft_limit_span(joint, lower, upper)
    return lower, upper


def _fit_joint(joint: str, subset: list[Measurement], limit: dict) -> dict:
    homes, training, verifying, moves = _split_by_role(joint, subset)
    home_count, home_angle, home_spread = _home_reference(joint, homes)
    counts_per_degree = _counts_per_degree(joint, training, home_count, home_angle)
    residuals, worst = _holdout_residuals(joint, verifying, home_count, home_angle, counts_per_degree)
    motion_error = _motion_error(joint, moves)
    approach_error = _direction_bias(verifying, residuals)
    lower, upper = _soft_limits(joint, limit, subset, home_angle)
    return {
        "encoder": joint,
        "home_count": home_count,
        "home_angle_deg": home_angle,
        "counts_per_degree": counts_per_degree,
        "soft_min_deg": lower,
        "soft_max_deg": upper,
        "home_tolerance_counts": home_spread + 5,
        "validation_max_error_deg": worst,
        "motion_max_error_deg": motion_error,
        "direction_bias_deg": approach_error,
        "fit_points": len(training),
        "validation_points": len(verifying),
        "motion_validation_points": len(moves),
        "home_points": len(homes),
    }


def fit(measurements: Path, limits_path: Path, robot_id: str) -> dict:
    """Fit each joint's counts per degree from independent physical angle measurements.

    Refuses, with a message naming the problem and the joint, anything the calibration cannot stand on.
    The checks run in a fixed order, so when two things are wrong the operator sees the first.
    """
    raw = measurements.read_bytes()
    rows = _read_measurement_rows(measurements)
    limits = _read_limits(limits_path)
    parsed = [_parse_measurement(row, robot_id) for row in rows]
    if set(limits) != {m.joint for m in parsed}:
        raise ValueError("Limits and measured joints must match")
    joints = {joint: _fit_joint(joint, [m for m in parsed if m.joint == joint], limits[joint])
              for joint in sorted(limits)}
    return {
        "schema_version": 1,
        "status": "validated",
        "robot_id": robot_id,
        "reference": "independent physical angle measurements",
        "source_file": measurements.name,
        "source_sha256": sha256(raw).hexdigest(),
        "joints": joints,
    }


def scale_warnings(result: dict, tolerance: float = 0.10,
                   vendor_tolerance: float = 0.05) -> list[str]:
    """Advisory lines comparing fitted scales with the legacy and vendor scales.

    Never blocks a fit: the legacy scale is an inherited assumption and the
    vendor value is a parameter-file default, neither is a measurement. The
    implied counts per motor revolution (manual gear ratio, ignoring stages
    after the gearbox) tests the old ~400 CPR hypothesis. A second WARNING line
    appears when the fit is more than vendor_tolerance off the vendor default,
    which usually means a measurement or reference-angle mistake.
    """
    lines = []
    for joint, row in result["joints"].items():
        fitted = abs(row["counts_per_degree"])
        legacy = LEGACY_COUNTS_PER_DEGREE[joint]
        cpr = implied_counts_per_motor_rev(fitted, joint)
        ratio = axis_range(joint).gear_ratio
        difference = fitted / legacy - 1
        if abs(difference) > tolerance:
            lines.append(
                f"WARNING {joint}: fitted {fitted:.2f} counts/deg differs from legacy "
                f"{legacy:.2f} by {difference:+.0%}; implies {cpr:.1f} counts/motor rev "
                f"at {ratio}:1 (hypothesis ~{HYPOTHESIS_COUNTS_PER_MOTOR_REV:.0f})")
        else:
            lines.append(f"{joint}: {fitted:.2f} counts/deg (legacy {legacy:.2f}); "
                         f"implies {cpr:.1f} counts/motor rev at {ratio}:1")
        vendor = VENDOR_COUNTS_PER_DEGREE.get(joint)
        if vendor is not None and abs(fitted / vendor.value - 1) > vendor_tolerance:
            lines.append(
                f"WARNING {joint}: fitted {fitted:.2f} counts/deg differs from vendor default "
                f"{vendor.value:.2f} by {fitted / vendor.value - 1:+.0%} ({vendor.status}); "
                "check the reference angles and the sign before trusting this fit")
    return lines


def write_result(result: dict, output: Path) -> None:
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--measurements", required=True, type=Path)
    parser.add_argument("--limits", required=True, type=Path,
                        help="JSON with measured soft_min_deg and soft_max_deg for each joint")
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = fit(args.measurements, args.limits, args.robot_id)
    write_result(result, args.output)
    print(f"Validated {', '.join(result['joints'])}; wrote {args.output}")
    for line in scale_warnings(result):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
