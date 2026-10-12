"""scripts/fit_calibration.py:fit pinned exactly: every refusal with the input that triggers it, the order the
checks run in, the boundaries, and the full output for a clean and for a noisy dataset.

fit() decides the soft limits the arm is jogged inside, and it was one 110-line function. These tests describe
what it does today so it can be split into small functions without anyone having to trust the split: each
message below is the exact text an operator sees, and each ordering test would fail if two checks swapped.
No USB, no robot.
"""

import csv
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from scripts.fit_calibration import fit

HEADER = ["robot_id", "joint", "role", "approach", "reference_source", "encoder_count",
          "measured_angle_deg", "requested_target_deg"]
LIMITS = {"base": {"soft_min_deg": -7, "soft_max_deg": 7}}

# (role, approach, encoder count, measured angle, requested target)
PERFECT = [
    ("home", "", 1000, 0, ""), ("home", "", 1001, 0, ""), ("home", "", 999, 0, ""),
    ("fit", "-", 900, -10, ""), ("fit", "+", 950, -5, ""), ("fit", "+", 1050, 5, ""), ("fit", "-", 1100, 10, ""),
    ("verify", "-", 925, -7.5, ""), ("verify", "+", 1025, 2.5, ""), ("verify", "-", 1075, 7.5, ""),
    ("move_verify", "-", 950, -5, -5), ("move_verify", "+", 1050, 5, 5), ("move_verify", "-", 975, -2.5, -2.5),
]
# the same shape with measurement noise, so every residual, bias and error is a non-trivial number, and with
# 3 home, 4 fit, 5 verify and 6 move rows (and 3 verify rows one way) so a mix-up of two counts or of mean and
# median shows
NOISY = [
    ("home", "", 1000, 0.1, ""), ("home", "", 1003, -0.2, ""), ("home", "", 997, 0.2, ""),
    ("fit", "-", 900, -10.2, ""), ("fit", "+", 960, -3.7, ""), ("fit", "+", 1045, 4.7, ""), ("fit", "-", 1110, 10.9, ""),
    ("verify", "-", 930, -6.9, ""), ("verify", "+", 1020, 2.3, ""), ("verify", "-", 1080, 7.4, ""),
    ("verify", "+", 990, -0.4, ""), ("verify", "+", 1060, 5.9, ""),
    ("move_verify", "-", 950, -5.2, -5), ("move_verify", "+", 1050, 5.1, 5), ("move_verify", "-", 975, -2.4, -2.5),
    ("move_verify", "+", 1010, 0.9, 1.0), ("move_verify", "+", 1070, 6.7, 7.0), ("move_verify", "-", 925, -7.4, -7.5),
]


def make_rows(data=PERFECT, joint="base", robot_id="arm-1", source="physical"):
    return [{"robot_id": robot_id, "joint": joint, "role": role, "approach": approach, "reference_source": source,
             "encoder_count": count, "measured_angle_deg": angle, "requested_target_deg": requested}
            for role, approach, count, angle, requested in data]


class FitCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.csv_path = self.root / "m.csv"
        self.limits_path = self.root / "l.json"

    def write(self, rows, header=HEADER, encoding="utf-8"):
        with self.csv_path.open("w", newline="", encoding=encoding) as stream:
            writer = csv.writer(stream)
            writer.writerow(header)
            for row in rows:
                writer.writerow([row.get(column, "") for column in header])

    def run_fit(self, rows=None, limits=LIMITS, robot_id="arm-1", header=HEADER):
        self.write(make_rows() if rows is None else rows, header=header)
        text = limits if isinstance(limits, str) else json.dumps(limits)
        self.limits_path.write_text(text, encoding="utf-8")
        return fit(self.csv_path, self.limits_path, robot_id)

    def refuses(self, message, rows=None, limits=LIMITS, robot_id="arm-1", exception: type[Exception] = ValueError,
                header=HEADER):
        with self.assertRaisesRegex(exception, message):
            self.run_fit(rows, limits, robot_id, header)

    def edit(self, index, **changes):
        rows = make_rows()
        rows[index].update(changes)
        return rows


class OutputTests(FitCase):
    def test_a_clean_fit_is_exactly_this(self):
        result = self.run_fit()
        self.assertEqual(result, {
            "schema_version": 1, "status": "validated", "robot_id": "arm-1",
            "reference": "independent physical angle measurements", "source_file": "m.csv",
            "source_sha256": sha256(self.csv_path.read_bytes()).hexdigest(),
            "joints": {"base": {
                "encoder": "base", "home_count": 1000, "home_angle_deg": 0.0, "counts_per_degree": 10.0,
                "soft_min_deg": -7.0, "soft_max_deg": 7.0, "home_tolerance_counts": 6,
                "validation_max_error_deg": 0.0, "motion_max_error_deg": 0.0,
                "direction_bias_deg": {"+": 0.0, "-": 0.0},
                "fit_points": 4, "validation_points": 3, "motion_validation_points": 3, "home_points": 3}}})

    def test_a_noisy_fit_pins_every_number(self):
        result = self.run_fit(make_rows(NOISY))
        self.assertEqual(result["joints"]["base"], {
            "encoder": "base", "home_count": 1000, "home_angle_deg": 0.1,
            "counts_per_degree": 9.9825378346915, "soft_min_deg": -7.0, "soft_max_deg": 7.0,
            "home_tolerance_counts": 8, "validation_max_error_deg": 0.7139941690962104,
            "motion_max_error_deg": 0.2999999999999998,
            "direction_bias_deg": {"+": -0.16258503401360525, "-": 0.35087463556851306},
            "fit_points": 4, "validation_points": 5, "motion_validation_points": 6, "home_points": 3})

    def test_three_joints_come_out_sorted_and_each_as_if_fitted_alone(self):
        rows = make_rows() + make_rows(NOISY, joint="shoulder") + make_rows(joint="elbow")
        limits = {name: {"soft_min_deg": -7, "soft_max_deg": 7} for name in ("base", "shoulder", "elbow")}
        result = self.run_fit(rows, limits)
        self.assertEqual(list(result["joints"]), ["base", "elbow", "shoulder"])
        alone = self.run_fit(make_rows(NOISY, joint="shoulder"), {"shoulder": limits["shoulder"]})
        self.assertEqual(result["joints"]["shoulder"], alone["joints"]["shoulder"])

    def test_rows_of_different_joints_may_be_interleaved(self):
        rows = make_rows() + make_rows(joint="elbow")
        limits = {name: {"soft_min_deg": -7, "soft_max_deg": 7} for name in ("base", "elbow")}
        interleaved = [row for pair in zip(rows[:13], rows[13:]) for row in pair]
        self.assertEqual(self.run_fit(interleaved, limits)["joints"], self.run_fit(rows, limits)["joints"])

    def test_an_even_number_of_homes_truncates_the_median_count(self):
        rows = make_rows()
        rows[2]["encoder_count"] = 1002
        rows.insert(3, {**rows[0], "encoder_count": 1003})          # homes 1000, 1001, 1002, 1003
        joint = self.run_fit(rows)["joints"]["base"]
        self.assertEqual(joint["home_count"], 1001)                 # median 1001.5: int() gives 1001, round() would give 1002
        self.assertEqual(joint["home_points"], 4)
        self.assertEqual(joint["home_tolerance_counts"], 2 + 5)     # the worst home is 2 counts from 1001

    def test_the_csv_may_have_extra_columns_a_different_order_and_a_byte_order_mark(self):
        header = ["robot_id", "note"] + list(reversed(HEADER[1:]))        # the mark sits on robot_id, a column fit needs
        self.write([{**row, "note": "x"} for row in make_rows()], header=header, encoding="utf-8-sig")
        self.limits_path.write_text(json.dumps(LIMITS), encoding="utf-8")
        self.assertEqual(fit(self.csv_path, self.limits_path, "arm-1")["joints"]["base"]["counts_per_degree"], 10.0)

    def test_a_home_row_needs_no_approach_and_only_a_move_row_reads_its_requested_target(self):
        rows = make_rows()
        rows[0]["approach"] = "anything"
        rows[3]["requested_target_deg"] = "not a number"            # a fit row: the target is never read
        self.assertEqual(self.run_fit(rows)["joints"]["base"]["home_count"], 1000)


class FileAndRowRefusals(FitCase):
    def test_a_missing_column(self):
        self.refuses("Measurement CSV needs columns", header=[c for c in HEADER if c != "requested_target_deg"])

    def test_an_empty_file(self):
        self.refuses("Measurement CSV is empty", rows=[])

    def test_limits_that_are_not_a_non_empty_object(self):
        for text in ("[]", "{}", "null", "3"):
            with self.subTest(text):
                self.refuses("Limits JSON must contain at least one joint", limits=text)

    def test_limits_for_a_joint_that_is_not_supported(self):
        self.refuses("Only base, shoulder and elbow are supported",
                     limits={"wrist_motor_1": {"soft_min_deg": -7, "soft_max_deg": 7}})

    def test_rows_for_another_robot_or_a_non_physical_reference(self):
        self.refuses("All rows must match robot_id and use a physical angle reference", robot_id="other")
        for index in (0, 12):
            with self.subTest(index):
                self.refuses("All rows must match robot_id and use a physical angle reference",
                             rows=self.edit(index, reference_source="vendor_display"))

    def test_an_unknown_joint_or_role(self):
        self.refuses("Unknown joint or role", rows=self.edit(3, joint="wrist_motor_1"))
        self.refuses("Unknown joint or role", rows=self.edit(3, role="calibrate"))

    def test_a_non_home_row_needs_a_plus_or_minus_approach(self):
        for index in (3, 7, 10):
            for approach in ("", "x", "+-"):
                with self.subTest(index=index, approach=approach):
                    self.refuses("Fit and verify rows need an approach of \\+ or -",
                                 rows=self.edit(index, approach=approach))

    def test_counts_must_be_unsigned_16_bit_integers(self):
        for count in (70000, -1, 65536):
            with self.subTest(count):
                self.refuses("Counts must be unsigned 16-bit", rows=self.edit(3, encoder_count=count))
        self.refuses("invalid literal for int", rows=self.edit(3, encoder_count="abc"))
        self.refuses("invalid literal for int", rows=self.edit(3, encoder_count="12.5"))

    def test_angles_must_be_finite_numbers(self):
        for angle in ("nan", "inf", "-inf"):
            with self.subTest(angle):
                self.refuses("Measured angles must be finite", rows=self.edit(3, measured_angle_deg=angle))
        self.refuses("could not convert string to float", rows=self.edit(3, measured_angle_deg="abc"))

    def test_a_move_row_needs_a_finite_requested_target(self):
        self.refuses("Requested targets must be finite", rows=self.edit(10, requested_target_deg="nan"))
        self.refuses("Requested targets must be finite", rows=self.edit(10, requested_target_deg="inf"))
        self.refuses("could not convert string to float", rows=self.edit(10, requested_target_deg=""))

    def test_the_limits_and_the_measured_joints_must_be_the_same_set(self):
        both = {"base": LIMITS["base"], "shoulder": LIMITS["base"]}
        self.refuses("Limits and measured joints must match", limits=both)
        self.refuses("Limits and measured joints must match",
                     rows=make_rows() + make_rows(joint="shoulder"), limits=LIMITS)

    def test_missing_files_and_keys(self):
        with self.assertRaises(FileNotFoundError):
            fit(self.root / "nope.csv", self.limits_path, "arm-1")
        self.write(make_rows())
        with self.assertRaises(FileNotFoundError):
            fit(self.csv_path, self.root / "nope.json", "arm-1")
        self.refuses("soft_max_deg", limits={"base": {"soft_min_deg": -7}}, exception=KeyError)
        self.refuses("could not convert string to float",
                     limits={"base": {"soft_min_deg": "x", "soft_max_deg": 7}})


class JointRefusals(FitCase):
    def test_too_few_rows_of_any_kind(self):
        message = "base: need 3 homes, 4 fit, 3 verify and 3 move verification rows"
        for index in (0, 3, 7, 10):
            with self.subTest(index=index):
                rows = make_rows()
                del rows[index]
                self.refuses(message, rows=rows)

    def test_every_group_needs_both_approach_directions(self):
        for indexes, approach in (((3, 4, 5, 6), "+"), ((7, 8, 9), "-"), ((10, 11, 12), "+")):
            with self.subTest(indexes=indexes):
                rows = make_rows()
                for index in indexes:
                    rows[index]["approach"] = approach
                self.refuses("base: both approach directions are required", rows=rows)

    def test_home_repeatability(self):
        message = "base: home repeatability is too poor to validate"
        self.refuses(message, rows=self.edit(2, encoder_count=1100))             # 99 counts from the median
        self.refuses(message, rows=self.edit(2, measured_angle_deg=3))           # 3 degrees from the median

    def test_home_repeatability_boundaries_pass(self):
        self.run_fit(self.edit(2, encoder_count=1051))                           # exactly 50 counts from the median 1001
        self.run_fit(self.edit(2, measured_angle_deg=2.0))                       # exactly 2 degrees from the median 0

    def test_calibration_span_too_small(self):
        narrow_counts = make_rows()
        for index, count in zip((3, 4, 5, 6), (996, 998, 1002, 1004)):
            narrow_counts[index]["encoder_count"] = count
        self.refuses("base: calibration span is too small", rows=narrow_counts)
        narrow_angles = make_rows()
        for index, angle in zip((3, 4, 5, 6), (-0.5, -0.25, 0.25, 0.5)):
            narrow_angles[index]["measured_angle_deg"] = angle
        self.refuses("base: calibration span is too small", rows=narrow_angles)

    def test_counts_that_do_not_map_to_angle(self):
        rows = make_rows()
        for index, count, angle, approach in ((3, 2000, 0, "-"), (4, 31000, 2, "+"), (5, 33000, 0, "+"), (6, 62000, 0, "-")):
            rows[index].update(encoder_count=count, measured_angle_deg=angle, approach=approach)
        for index in (0, 1, 2):
            rows[index]["encoder_count"] = 32000
        self.refuses("base: counts do not map reliably to angle", rows=rows)

    def test_an_implausible_count_scale(self):
        rows = make_rows()
        for index, count, angle in ((3, 990, -30), (4, 995, -15), (5, 1005, 15), (6, 1010, 30)):
            rows[index].update(encoder_count=count, measured_angle_deg=angle)
        self.refuses("base: fitted count scale is implausible", rows=rows)

    def test_holdout_error(self):
        self.refuses("base: holdout error 3.00 degrees exceeds 2 degrees", rows=self.edit(8, measured_angle_deg=5.5))
        self.run_fit(self.edit(8, measured_angle_deg=4.4))                       # 1.9 degrees: fine

    def test_motion_error(self):
        self.refuses("base: measured motion error exceeds 2 degrees", rows=self.edit(10, requested_target_deg=-2))
        self.run_fit(self.edit(10, requested_target_deg=-3.5))                   # 1.5 degrees: fine

    def test_soft_limits_must_be_finite(self):
        self.refuses("base: soft limits must be finite", limits='{"base": {"soft_min_deg": NaN, "soft_max_deg": 7}}')
        self.refuses("base: soft limits must be finite", limits='{"base": {"soft_min_deg": -7, "soft_max_deg": Infinity}}')

    def test_soft_limits_must_lie_inside_the_measured_range_with_a_margin_around_home(self):
        message = "base: soft limits must lie inside measured range with 2.5 degree margin"
        for lower, upper in ((-8, 7), (-7, 8), (0, 7), (-7, 0), (7, -7), (-7.6, 7), (-7, 7.6)):
            with self.subTest(lower=lower, upper=upper):
                self.refuses(message, limits={"base": {"soft_min_deg": lower, "soft_max_deg": upper}})
        self.run_fit(limits={"base": {"soft_min_deg": -7.5, "soft_max_deg": 7.5}})   # exactly at the margin

    def test_soft_limits_may_not_span_more_than_the_manuals_travel(self):
        data = [("home", "", 30000, 0, "")] * 3 + [
            ("fit", "-", 27900, -210, ""), ("fit", "+", 29000, -100, ""), ("fit", "+", 31000, 100, ""),
            ("fit", "-", 32100, 210, ""),
            ("verify", "-", 28500, -150, ""), ("verify", "+", 30500, 50, ""), ("verify", "-", 31500, 150, ""),
            ("move_verify", "-", 29000, -100, -100), ("move_verify", "+", 31000, 100, 100),
            ("move_verify", "-", 30000, 0, 0)]
        rows = make_rows(data)
        self.refuses("exceeds the manual's 310 degree travel", rows=rows,
                     limits={"base": {"soft_min_deg": -200, "soft_max_deg": 200}})
        self.run_fit(rows, {"base": {"soft_min_deg": -150, "soft_max_deg": 150}})


class CheckOrder(FitCase):
    """When two things are wrong at once the operator sees the first check's message. A split must keep that."""

    def test_row_checks_run_in_this_order(self):
        wrong_robot_and_joint = self.edit(3, joint="wrist_motor_1")
        self.refuses("All rows must match robot_id", rows=wrong_robot_and_joint, robot_id="other")
        self.refuses("Unknown joint or role", rows=self.edit(3, role="calibrate", approach=""))
        self.refuses("Fit and verify rows need an approach", rows=self.edit(3, approach="", encoder_count=70000))
        self.refuses("Counts must be unsigned 16-bit", rows=self.edit(3, encoder_count=70000, measured_angle_deg="abc"))
        self.refuses("could not convert string to float", rows=self.edit(10, measured_angle_deg="abc", requested_target_deg="nan"))
        self.refuses("Requested targets must be finite", rows=self.edit(10, requested_target_deg="nan", measured_angle_deg="nan"))

    def test_a_bad_row_is_reported_before_a_limits_mismatch(self):
        rows = self.edit(3, encoder_count=70000)
        self.refuses("Counts must be unsigned 16-bit", rows=rows, limits={"shoulder": LIMITS["base"]})

    def test_the_limits_file_is_judged_before_any_row(self):
        self.refuses("Limits JSON must contain at least one joint", rows=self.edit(3, encoder_count=70000), limits="{}")
        self.refuses("Only base, shoulder and elbow", rows=self.edit(3, encoder_count=70000),
                     limits={"wrist_motor_1": LIMITS["base"]})

    def test_joint_checks_run_in_this_order(self):
        def few_homes_and_one_approach():
            rows = make_rows()
            del rows[0]
            for index in range(2, 6):           # the fit rows after the deletion
                rows[index]["approach"] = "+"
            return rows
        self.refuses("need 3 homes", rows=few_homes_and_one_approach())

        poor_home_and_narrow = self.edit(2, encoder_count=1100)
        for index, count in zip((3, 4, 5, 6), (996, 998, 1002, 1004)):
            poor_home_and_narrow[index]["encoder_count"] = count
        self.refuses("home repeatability", rows=poor_home_and_narrow)

        holdout_and_motion = self.edit(8, measured_angle_deg=5.5)
        holdout_and_motion[10]["requested_target_deg"] = -2
        self.refuses("holdout error", rows=holdout_and_motion)

        motion_and_limits = self.edit(10, requested_target_deg=-2)
        self.refuses("measured motion error", rows=motion_and_limits, limits={"base": {"soft_min_deg": -9, "soft_max_deg": 9}})

        self.refuses("soft limits must be finite",
                     limits='{"base": {"soft_min_deg": NaN, "soft_max_deg": 20}}')

    def test_joints_are_judged_in_name_order(self):
        rows = make_rows(joint="shoulder", data=PERFECT) + make_rows(joint="elbow", data=PERFECT)
        broken_shoulder = [row for row in rows if row["joint"] == "shoulder"]
        broken_elbow = [row for row in rows if row["joint"] == "elbow"]
        broken_shoulder[2]["encoder_count"] = 1100
        broken_elbow[2]["encoder_count"] = 1100
        limits = {name: {"soft_min_deg": -7, "soft_max_deg": 7} for name in ("shoulder", "elbow")}
        self.refuses("elbow: home repeatability", rows=broken_shoulder + broken_elbow, limits=limits)


if __name__ == "__main__":
    unittest.main()
