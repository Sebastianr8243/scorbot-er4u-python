"""Validated, per-arm encoder-to-angle mappings for the first three joints."""

from dataclasses import dataclass
import json
import math
from pathlib import Path


CALIBRATED_JOINTS = ("base", "shoulder", "elbow")


def signed_count_delta(value: int, origin: int) -> int:
    """Shortest difference under the legacy one's-complement 65535 wrap."""
    if not all(type(item) is int and 0 <= item <= 65535 for item in (value, origin)):
        raise ValueError("Encoder counts must be unsigned 16-bit integers")
    delta = (value - origin) % 65535
    if delta in (32767, 32768):
        raise ValueError("Encoder difference is ambiguous near half the counter range")
    return delta if delta < 32767 else delta - 65535


def _finite(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


@dataclass(frozen=True)
class JointCalibration:
    encoder: str
    home_count: int
    home_angle_deg: float
    counts_per_degree: float
    soft_min_deg: float
    soft_max_deg: float
    home_tolerance_counts: int
    validation_max_error_deg: float
    motion_max_error_deg: float

    def angle(self, count: int, session_home_count: int) -> float:
        return self.home_angle_deg + signed_count_delta(count, session_home_count) / self.counts_per_degree

    def validate_home(self, count: int) -> None:
        if abs(signed_count_delta(count, self.home_count)) > self.home_tolerance_counts:
            raise ValueError(f"{self.encoder} home count is outside measured repeatability")


@dataclass(frozen=True)
class Calibration:
    robot_id: str
    source_sha256: str
    joints: dict[str, JointCalibration]


def load_calibration(path: str | Path, *, robot_id: str) -> Calibration:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or data.get("status") != "validated":
        raise ValueError("Calibration must be version 1 and validated from physical measurements")
    if not robot_id or data.get("robot_id") != robot_id:
        raise ValueError("Calibration robot ID does not match this arm")
    source = data.get("source_sha256")
    if not isinstance(source, str) or len(source) != 64 or any(c not in "0123456789abcdef" for c in source):
        raise ValueError("Calibration needs a source CSV SHA-256")
    entries = data.get("joints")
    if not isinstance(entries, dict) or not entries or set(entries) - set(CALIBRATED_JOINTS):
        raise ValueError("Only measured base, shoulder and elbow joints are supported")
    joints = {}
    for name, row in entries.items():
        if not isinstance(row, dict) or row.get("encoder") != name:
            raise ValueError(f"Invalid encoder mapping for {name}")
        home = row.get("home_count")
        tolerance = row.get("home_tolerance_counts")
        if type(home) is not int or not 0 <= home <= 65535:
            raise ValueError(f"Invalid home count for {name}")
        if type(tolerance) is not int or not 0 <= tolerance < 32768:
            raise ValueError(f"Invalid home tolerance for {name}")
        home_angle = _finite(row.get("home_angle_deg"), "home angle")
        scale = _finite(row.get("counts_per_degree"), "counts per degree")
        lower = _finite(row.get("soft_min_deg"), "soft minimum")
        upper = _finite(row.get("soft_max_deg"), "soft maximum")
        error = _finite(row.get("validation_max_error_deg"), "validation error")
        motion_error = _finite(row.get("motion_max_error_deg"), "motion error")
        if (type(row.get("fit_points")) is not int or row["fit_points"] < 4
                or type(row.get("validation_points")) is not int or row["validation_points"] < 3
                or type(row.get("motion_validation_points")) is not int
                or row["motion_validation_points"] < 3
                or type(row.get("home_points")) is not int or row["home_points"] < 3):
            raise ValueError(f"Missing physical validation counts for {name}")
        if abs(scale) < 1 or lower >= upper or not lower <= home_angle <= upper or error < 0 or error > 2 or motion_error < 0 or motion_error > 2:
            raise ValueError(f"Invalid fitted range or error for {name}")
        joints[name] = JointCalibration(
            name, home, home_angle, scale, lower, upper, tolerance, error, motion_error)
    return Calibration(robot_id, source, joints)
