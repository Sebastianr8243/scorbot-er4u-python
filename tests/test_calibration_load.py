"""load_calibration and JointCalibration: every rejection, and one good file."""

import json
import tempfile
import unittest
from pathlib import Path

from scorbot.calibration import JointCalibration, load_calibration

GOOD = {
    "schema_version": 1, "status": "validated", "robot_id": "arm-1", "source_sha256": "a" * 64,
    "joints": {"base": {
        "encoder": "base", "home_count": 1000, "home_tolerance_counts": 5, "home_angle_deg": 0,
        "counts_per_degree": 10, "soft_min_deg": -10, "soft_max_deg": 10,
        "validation_max_error_deg": 1, "motion_max_error_deg": 1,
        "fit_points": 4, "validation_points": 3, "motion_validation_points": 3, "home_points": 3}},
}


def copy(**joint_changes):
    data = json.loads(json.dumps(GOOD))
    data["joints"]["base"].update(joint_changes)
    return data


class LoadTests(unittest.TestCase):
    def load(self, data, robot_id="arm-1"):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "calibration.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return load_calibration(path, robot_id=robot_id)

    def test_a_good_file_loads(self):
        calibration = self.load(GOOD)
        self.assertEqual(calibration.joints["base"].counts_per_degree, 10.0)

    def test_every_bad_field_is_refused(self):
        bad = {
            "home count": copy(home_count=70000), "home count type": copy(home_count="1000"),
            "tolerance": copy(home_tolerance_counts=40000), "mapping": copy(encoder="shoulder"),
            "scale": copy(counts_per_degree=0.5), "nan": copy(home_angle_deg=float("nan")),
            "bool": copy(soft_min_deg=True), "range": copy(soft_min_deg=5, soft_max_deg=-5),
            "error too big": copy(validation_max_error_deg=3), "few points": copy(fit_points=3),
            "motion error too big": copy(motion_max_error_deg=3),
            "zero span": copy(soft_min_deg=0, soft_max_deg=0),     # only the span-order check rejects this one
            "span beyond the manual's travel": copy(soft_min_deg=-200, soft_max_deg=200),   # reaches check_soft_limit_span
            "few home points": copy(home_points=2),
        }
        for label, data in bad.items():
            with self.subTest(label):
                with self.assertRaises(ValueError):
                    self.load(data)

    def test_the_header_is_checked(self):
        for label, change in (("version", {"schema_version": 2}), ("status", {"status": "draft"}),
                              ("robot", {"robot_id": "other"}), ("hash", {"source_sha256": "xyz"}),
                              ("short hash", {"source_sha256": "a" * 63}),      # valid hex, wrong length
                              ("joints", {"joints": {}}), ("wrist", {"joints": {"wrist_pitch": GOOD["joints"]["base"]}})):
            with self.subTest(label):
                data = json.loads(json.dumps(GOOD))
                data.update(change)
                with self.assertRaises(ValueError):
                    self.load(data)
        with self.assertRaises(ValueError):
            self.load(GOOD, robot_id="")

    def test_validate_home_uses_the_tolerance_and_the_wrap(self):
        joint = JointCalibration("base", 1000, 0.0, 10.0, -10.0, 10.0, 5, 1.0, 1.0)
        joint.validate_home(1004)
        with self.assertRaisesRegex(ValueError, "home count"):
            joint.validate_home(1006)
        near_seam = JointCalibration("base", 65534, 0.0, 10.0, -10.0, 10.0, 5, 1.0, 1.0)
        near_seam.validate_home(2)             # 65534 to 2 is 3 counts across the 0/65535 seam


if __name__ == "__main__":
    unittest.main()
