"""Manual (nominal) values, the soft-limit span bound and the fit scale warning."""

import contextlib
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scorbot import nominal
from scorbot.calibration import CALIBRATED_JOINTS, load_calibration
from scripts import fit_calibration
from scripts.fit_calibration import fit, scale_warnings


class NominalValueTests(unittest.TestCase):
    def test_manual_values(self):
        self.assertEqual(nominal.BASE_HEIGHT_MM.value, 364.0)
        self.assertEqual(nominal.UPPER_ARM_MM.value, 220.0)
        self.assertEqual(nominal.FOREARM_MM.value, 220.0)
        self.assertEqual(nominal.VERTICAL_ENVELOPE_MM.value, 1040.0)
        self.assertEqual(nominal.MAX_OPERATING_RADIUS_MM.value, 610.0)
        self.assertEqual(nominal.REPEATABILITY_MM.value, 0.18)
        self.assertEqual(nominal.MAX_PAYLOAD_KG.value, 1.0)
        self.assertEqual(nominal.MAX_PATH_SPEED_MM_S.value, 600.0)
        self.assertEqual(nominal.GEAR_RATIO_ARM.value, 127.1)
        self.assertEqual(nominal.GEAR_RATIO_WRIST.value, 65.5)
        self.assertEqual(nominal.GEAR_RATIO_GRIPPER.value, 19.5)
        spans = {name: r.span_deg for name, r in nominal.AXIS_RANGES.items()}
        self.assertEqual(spans, {"base": 310.0, "shoulder": 165.0, "elbow": 260.0,
                                 "wrist_pitch": 260.0, "wrist_roll": 1140.0})
        self.assertIsNone(nominal.axis_range("base").signed_min_deg)
        self.assertEqual((nominal.axis_range("shoulder").signed_min_deg,
                          nominal.axis_range("shoulder").signed_max_deg), (-35.0, 130.0))
        for r in nominal.AXIS_RANGES.values():
            if r.signed_min_deg is not None:
                self.assertEqual(r.signed_max_deg - r.signed_min_deg, r.span_deg)

    def test_every_value_has_a_source_and_nominal_status(self):
        values = [v for v in vars(nominal).values()
                  if isinstance(v, (nominal.NominalValue, nominal.AxisRange))]
        values += list(nominal.AXIS_RANGES.values())
        self.assertGreater(len(values), 15)
        for value in values:
            with self.subTest(value=value):
                self.assertIn("#100343", value.source.catalog)
                self.assertTrue(value.source.manual and value.source.page)
                self.assertEqual(value.status, "nominal, from manual, not measured")

    def test_values_are_frozen(self):
        with self.assertRaises(AttributeError):
            nominal.BASE_HEIGHT_MM.value = 1
        with self.assertRaises(TypeError):
            nominal.AXIS_RANGES["base"] = None

    def test_every_calibrated_joint_has_a_range(self):
        for joint in CALIBRATED_JOINTS:
            self.assertEqual(nominal.axis_range(joint).joint, joint)
        with self.assertRaises(ValueError):
            nominal.axis_range("gripper")

    def test_span_check_ignores_sign_and_zero(self):
        nominal.check_soft_limit_span("base", -155, 155)
        nominal.check_soft_limit_span("base", 0, 310)
        # Beyond the manual's signed -35 but inside its 165 degree span: allowed,
        # because the manual's zero/sign may not match our home angle.
        nominal.check_soft_limit_span("shoulder", -100, 60)
        with self.assertRaisesRegex(ValueError, r"310 degree.*#100343 Rev\. B, pp\. 4-6"):
            nominal.check_soft_limit_span("base", -160, 160)
        with self.assertRaises(ValueError):
            nominal.check_soft_limit_span("shoulder", -40, 130)

    def test_counts_per_degree_prior(self):
        self.assertAlmostEqual(nominal.counts_per_degree(360, "base"), 127.1)
        self.assertAlmostEqual(nominal.counts_per_degree(360, "wrist_roll"), 65.5)
        self.assertAlmostEqual(nominal.HYPOTHESIS_COUNTS_PER_MOTOR_REV, 401.8, places=1)
        self.assertAlmostEqual(
            nominal.counts_per_degree(nominal.HYPOTHESIS_COUNTS_PER_MOTOR_REV, "base"),
            2837 / 20)
        self.assertAlmostEqual(nominal.implied_counts_per_motor_rev(-2837 / 20, "base"),
                               nominal.HYPOTHESIS_COUNTS_PER_MOTOR_REV)
        for bad in (0, -1, float("nan"), True):
            with self.assertRaises(ValueError):
                nominal.counts_per_degree(bad, "base")


def _write_csv(path, joint, scale):
    """Synthetic format-example rows (not ER-4U data) at `scale` counts/deg."""
    rows = [("home", "", 0, 0), ("home", "", 1, 0), ("home", "", -1, 0),
            ("fit", "-", -10, -10), ("fit", "+", -5, -5), ("fit", "+", 5, 5),
            ("fit", "-", 10, 10), ("verify", "-", -7.5, -7.5),
            ("verify", "+", 2.5, 2.5), ("verify", "-", 7.5, 7.5),
            ("move_verify", "-", -5, -5), ("move_verify", "+", 5, 5),
            ("move_verify", "-", -2.5, -2.5)]
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["robot_id", "joint", "role", "approach", "reference_source",
                         "encoder_count", "measured_angle_deg", "requested_target_deg"])
        for role, direction, degrees, angle in rows:
            count = 10000 + (degrees if role == "home" else round(degrees * scale))
            writer.writerow(["arm-1", joint, role, direction, "physical", count, angle,
                             angle if role == "move_verify" else ""])


class ManualBoundsIntegrationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def fitted(self, joint, scale):
        csv_path, limits = self.root / f"{joint}.csv", self.root / f"{joint}.json"
        _write_csv(csv_path, joint, scale)
        limits.write_text(json.dumps({joint: {"soft_min_deg": -7, "soft_max_deg": 7}}),
                          encoding="utf-8")
        return fit(csv_path, limits, "arm-1")

    def load_with_limits(self, result, joint, lower, upper):
        result["joints"][joint].update(soft_min_deg=lower, soft_max_deg=upper)
        path = self.root / "calibration.json"
        path.write_text(json.dumps(result), encoding="utf-8")
        return load_calibration(path, robot_id="arm-1")

    def test_load_rejects_span_beyond_manual(self):
        result = self.fitted("base", 10)
        self.assertEqual(self.load_with_limits(result, "base", -150, 150)
                         .joints["base"].soft_max_deg, 150)
        with self.assertRaisesRegex(ValueError, r"base.*exceeds the manual.*pp\. 4-6"):
            self.load_with_limits(result, "base", -200, 200)

    def test_load_checks_shoulder_span_not_signed_limits(self):
        result = self.fitted("shoulder", 10)
        self.load_with_limits(result, "shoulder", -100, 60)
        with self.assertRaisesRegex(ValueError, "165 degree"):
            self.load_with_limits(result, "shoulder", -100, 70)

    def test_scale_warning_reports_implied_cpr(self):
        lines = scale_warnings(self.fitted("base", 10))
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("WARNING base:"))
        self.assertIn("implies 28.3 counts/motor rev", lines[0])
        self.assertIn("hypothesis ~402", lines[0])
        # Within 10% of the legacy scale: informational only, no warning.
        lines = scale_warnings(self.fitted("base", 2837 / 20))
        self.assertFalse(lines[0].startswith("WARNING"))
        self.assertIn("implies 401.", lines[0])

    def test_main_prints_warning_but_still_writes(self):
        self.fitted("base", 10)
        output = self.root / "out.json"
        argv = ["fit_calibration", "--measurements", str(self.root / "base.csv"),
                "--limits", str(self.root / "base.json"), "--robot-id", "arm-1",
                "--output", str(output)]
        stdout = io.StringIO()
        with patch("sys.argv", argv), contextlib.redirect_stdout(stdout):
            self.assertEqual(fit_calibration.main(), 0)
        self.assertTrue(output.exists())
        self.assertIn("WARNING base", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
