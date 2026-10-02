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
        self.assertEqual(nominal.SHOULDER_AXIS_HEIGHT_MM.value, 364.0)
        self.assertEqual(nominal.BASE_PEDESTAL_HEIGHT_MM.value, 190.0)
        self.assertEqual(nominal.UPPER_ARM_MM.value, 220.0)
        self.assertEqual(nominal.FOREARM_MM.value, 220.0)
        self.assertEqual(nominal.VERTICAL_ENVELOPE_MM.value, 1040.0)
        self.assertEqual(nominal.MAX_OPERATING_RADIUS_MM.value, 610.0)
        self.assertEqual(nominal.REPEATABILITY_MM.value, 0.18)
        self.assertEqual(nominal.MAX_PAYLOAD_KG.value, 1.0)
        self.assertEqual(nominal.MAX_PATH_SPEED_MM_S.value, 600.0)
        self.assertEqual(nominal.GEAR_RATIO_ARM.value, 127.1)
        self.assertEqual(nominal.GEAR_RATIO_ARM_PARTS_LIST.value, 127.7)
        self.assertIn("p. 28", nominal.GEAR_RATIO_ARM_PARTS_LIST.source.page)
        self.assertEqual((nominal.ARM_AMBIENT_MIN_C.value, nominal.ARM_AMBIENT_MAX_C.value),
                         (2.0, 40.0))
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

    def test_vendor_priors_are_labelled_and_separate(self):
        cpd = nominal.VENDOR_COUNTS_PER_DEGREE
        self.assertAlmostEqual(cpd["base"].value, 12770 / 90, places=6)
        self.assertAlmostEqual(cpd["shoulder"].value, 10216 / 90, places=6)
        self.assertAlmostEqual(cpd["elbow"].value, 10216 / 90, places=6)
        self.assertAlmostEqual(cpd["wrist_pitch"].value, 2511 / 90, places=6)
        self.assertAlmostEqual(cpd["wrist_roll"].value, 2511 / 90, places=6)
        geometry = (nominal.VENDOR_BASE_HEIGHT_MM, nominal.VENDOR_UPPER_ARM_MM,
                    nominal.VENDOR_FOREARM_MM, nominal.VENDOR_GRIPPER_LENGTH_MM)
        self.assertEqual([g.value for g in geometry], [349.0, 221.0, 221.0, 145.0])
        for prior in [*cpd.values(), *geometry]:
            with self.subTest(prior=prior):
                self.assertIsInstance(prior, nominal.VendorPrior)
                self.assertNotIsInstance(prior, nominal.NominalValue)
                self.assertEqual(prior.status, "vendor default, not measured")
                self.assertIn("parameter file", prior.source)
        with self.assertRaises(TypeError):
            cpd["base"] = None

    def test_values_are_frozen(self):
        with self.assertRaises(AttributeError):
            nominal.SHOULDER_AXIS_HEIGHT_MM.value = 1
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
        for wrist in ("wrist_pitch", "wrist_roll"):
            self.assertIsNone(nominal.axis_range(wrist).gear_ratio)
            with self.assertRaisesRegex(ValueError, "no joint gear ratio"):
                nominal.counts_per_degree(360, wrist)
            with self.assertRaisesRegex(ValueError, "no joint gear ratio"):
                nominal.implied_counts_per_motor_rev(27.9, wrist)
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


class VendorLimitsAndContradictionTests(unittest.TestCase):
    def test_vendor_joint_and_encoder_limits(self):
        joint = nominal.VENDOR_JOINT_LIMITS_DEG
        self.assertEqual({name: (r.minimum, r.maximum) for name, r in joint.items()},
                         {"base": (-132.0, 174.0), "shoulder": (-124.0, 31.0),
                          "elbow": (-115.0, 160.0), "wrist_pitch": (-113.0, 115.0),
                          "wrist_roll": (-570.0, 570.0)})
        self.assertEqual(joint["elbow"].span, 275.0)
        encoder = nominal.VENDOR_ENCODER_SOFT_LIMITS
        self.assertEqual({name: (r.minimum, r.maximum) for name, r in encoder.items()},
                         {"base": (-25000, 20000), "shoulder": (-18000, 1500),
                          "elbow": (-25000, 20000), "wrist_pitch": (-15000, 15000)})
        for limit in [*joint.values(), *encoder.values()]:
            with self.subTest(limit=limit):
                self.assertEqual(limit.status, "vendor default, not measured")
                self.assertIn("parameter file", limit.source)
                self.assertLess(limit.minimum, limit.maximum)
        with self.assertRaises(TypeError):
            encoder["base"] = None

    def test_datasheet_values_are_priors(self):
        speeds = nominal.DATASHEET_JOINT_SPEED_DEG_S
        self.assertEqual({name: s.value for name, s in speeds.items()},
                         {"base": 20.0, "shoulder": 26.3, "elbow": 26.3,
                          "wrist_pitch": 83.0, "wrist_roll": 106.0})
        self.assertEqual(nominal.DATASHEET_PATH_SPEED_MM_S.value, 700.0)
        self.assertEqual(nominal.DATASHEET_SHOULDER_SPAN_DEG.value, 158.0)
        for prior in [*speeds.values(), nominal.DATASHEET_PATH_SPEED_MM_S,
                      nominal.DATASHEET_SHOULDER_SPAN_DEG]:
            with self.subTest(prior=prior):
                self.assertIsInstance(prior, nominal.VendorPrior)
                self.assertIn("datasheet", prior.source)

    def test_every_contradiction_has_a_verdict_and_reason(self):
        topics = {c.topic for c in nominal.CONTRADICTIONS}
        self.assertEqual(topics, {"shoulder axis height", "link lengths", "elbow span",
                                  "shoulder span", "path speed",
                                  "wrist pitch counts per degree"})
        for item in nominal.CONTRADICTIONS:
            with self.subTest(topic=item.topic):
                self.assertIn(item.verdict, nominal.VERDICTS)
                self.assertTrue(item.ours and item.theirs and item.reason)
                self.assertNotIn("measured", item.verdict)

    def test_vendor_values_never_loosen_the_calibration_gate(self):
        # The vendor elbow span (275) is wider than the manual's 260; the gate stays 260.
        self.assertEqual(nominal.axis_range("elbow").span_deg, 260.0)
        with self.assertRaises(ValueError):
            nominal.check_soft_limit_span("elbow", -135.0, 135.0)
        for name, vendor in nominal.VENDOR_JOINT_LIMITS_DEG.items():
            with self.subTest(joint=name):
                gate = nominal.axis_range(name).span_deg
                if vendor.span > gate:
                    with self.assertRaises(ValueError):
                        nominal.check_soft_limit_span(name, vendor.minimum, vendor.maximum)


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
        self.assertEqual(len(lines), 2)   # legacy line, then the vendor-prior warning
        self.assertTrue(lines[0].startswith("WARNING base:"))
        self.assertIn("implies 28.3 counts/motor rev", lines[0])
        self.assertIn("hypothesis ~402", lines[0])
        # Within 10% of the legacy scale: informational only, no warning.
        lines = scale_warnings(self.fitted("base", 2837 / 20))
        self.assertFalse(lines[0].startswith("WARNING"))
        self.assertIn("implies 401.", lines[0])

    def test_scale_warning_compares_with_vendor_prior(self):
        vendor = 12770 / 90
        lines = scale_warnings(self.fitted("base", vendor * 1.08))   # within legacy 10%
        self.assertEqual(len(lines), 2)
        self.assertFalse(lines[0].startswith("WARNING"))
        self.assertTrue(lines[1].startswith("WARNING base:"))
        self.assertIn("vendor default 141.89", lines[1])
        self.assertIn("+8%", lines[1])
        self.assertIn("not measured", lines[1])
        # Within 5% of the vendor value: no vendor line.
        self.assertEqual(len(scale_warnings(self.fitted("base", vendor * 1.01))), 1)
        # A reversed sign is still compared by magnitude.
        self.assertEqual(len(scale_warnings(self.fitted("base", -vendor))), 1)

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
