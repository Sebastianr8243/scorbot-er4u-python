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

from scorbot.calibration import CALIBRATED_JOINTS, signed_count_delta


COLUMNS = {"robot_id", "joint", "role", "approach", "reference_source",
           "encoder_count", "measured_angle_deg", "requested_target_deg"}


def fit(measurements: Path, limits_path: Path, robot_id: str) -> dict:
    raw = measurements.read_bytes()
    with measurements.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not COLUMNS <= set(reader.fieldnames):
            raise ValueError(f"Measurement CSV needs columns: {', '.join(sorted(COLUMNS))}")
        rows = list(reader)
    if not rows:
        raise ValueError("Measurement CSV is empty")
    limits = json.loads(limits_path.read_text(encoding="utf-8"))
    if not isinstance(limits, dict) or not limits:
        raise ValueError("Limits JSON must contain at least one joint")
    if set(limits) - set(CALIBRATED_JOINTS):
        raise ValueError("Only base, shoulder and elbow are supported")
    parsed = []
    for row in rows:
        if row["robot_id"] != robot_id or row["reference_source"] != "physical":
            raise ValueError("All rows must match robot_id and use a physical angle reference")
        if row["joint"] not in CALIBRATED_JOINTS or row["role"] not in ("home", "fit", "verify", "move_verify"):
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
        parsed.append((row["joint"], row["role"], row["approach"], count, angle, requested))
    if set(limits) != {row[0] for row in parsed}:
        raise ValueError("Limits and measured joints must match")
    joints = {}
    for joint in sorted(limits):
        subset = [row for row in parsed if row[0] == joint]
        homes = [row for row in subset if row[1] == "home"]
        training = [row for row in subset if row[1] == "fit"]
        verifying = [row for row in subset if row[1] == "verify"]
        moves = [row for row in subset if row[1] == "move_verify"]
        if len(homes) < 3 or len(training) < 4 or len(verifying) < 3 or len(moves) < 3:
            raise ValueError(f"{joint}: need 3 homes, 4 fit, 3 verify and 3 move verification rows")
        for group in (training, verifying, moves):
            if {row[2] for row in group} != {"+", "-"}:
                raise ValueError(f"{joint}: both approach directions are required")
        home_count = int(median(row[3] for row in homes))
        home_angle = median(row[4] for row in homes)
        home_spread = max(abs(signed_count_delta(row[3], home_count)) for row in homes)
        if home_spread > 50 or max(abs(row[4] - home_angle) for row in homes) > 2:
            raise ValueError(f"{joint}: home repeatability is too poor to validate")
        xs = [signed_count_delta(row[3], home_count) for row in training]
        ys = [row[4] - home_angle for row in training]
        if max(xs) - min(xs) < 20 or max(ys) - min(ys) < 2:
            raise ValueError(f"{joint}: calibration span is too small")
        denominator = sum(x * x for x in xs)
        angle_per_count = sum(x * y for x, y in zip(xs, ys)) / denominator
        if abs(angle_per_count) < 1 / 32768:
            raise ValueError(f"{joint}: counts do not map reliably to angle")
        counts_per_degree = 1 / angle_per_count
        if abs(counts_per_degree) < 1:
            raise ValueError(f"{joint}: fitted count scale is implausible")
        residuals = [home_angle + signed_count_delta(row[3], home_count)
                     / counts_per_degree - row[4] for row in verifying]
        worst = max(abs(error) for error in residuals)
        if worst > 2:
            raise ValueError(f"{joint}: holdout error {worst:.2f} degrees exceeds 2 degrees")
        motion_error = max(abs(row[4] - row[5]) for row in moves)
        if motion_error > 2:
            raise ValueError(f"{joint}: measured motion error exceeds 2 degrees")
        approach_error = {direction: mean(error for row, error in zip(verifying, residuals)
                                          if row[2] == direction) for direction in ("+", "-")}
        lower = float(limits[joint]["soft_min_deg"])
        upper = float(limits[joint]["soft_max_deg"])
        angles = [row[4] for row in subset]
        if not all(math.isfinite(value) for value in (lower, upper)):
            raise ValueError(f"{joint}: soft limits must be finite")
        if not min(angles) + 0.5 <= lower < home_angle < upper <= max(angles) - 0.5:
            raise ValueError(f"{joint}: soft limits must lie inside measured range with 0.5 degree margin")
        joints[joint] = {
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
    return {
        "schema_version": 1,
        "status": "validated",
        "robot_id": robot_id,
        "reference": "independent physical angle measurements",
        "source_file": measurements.name,
        "source_sha256": sha256(raw).hexdigest(),
        "joints": joints,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--measurements", required=True, type=Path)
    parser.add_argument("--limits", required=True, type=Path,
                        help="JSON with measured soft_min_deg and soft_max_deg for each joint")
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = fit(args.measurements, args.limits, args.robot_id)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(f"Validated {', '.join(result['joints'])}; wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
