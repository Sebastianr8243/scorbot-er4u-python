"""The calibration CSV builder (scripts/build_calibration_csv.py). No USB, no arm.

Synthetic lab session logs in the real row shapes; the real fitter judges the output.
"""

import contextlib
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest

from scripts import build_calibration_csv as bcc
from scripts.fit_calibration import fit

PER_DEGREE = {"base": 141.89, "shoulder": 113.51, "elbow": 113.51}
HOME_COUNTS = {"base": 20, "shoulder": -40, "elbow": 60}
ALL_MOTORS = ("base", "shoulder", "elbow", "wrist_motor_1", "wrist_motor_2", "gripper")


def raw(count):
    """A signed count as the log holds it: unsigned 16-bit, negative values below 65535."""
    return count if count >= 0 else 65535 + count


def counts_row(joint_counts):
    return {motor: raw(joint_counts.get(motor, 0)) for motor in ALL_MOTORS}


class LogBuilder:
    """Writes the rows a guided lab session writes, for steps given as (joint, angle, from_side)."""

    def __init__(self, robot_id="lab-er4u-1", data_source="real", home=None):
        self.rows = [{"type": "session", "robot_id": robot_id, "data_source": data_source}]
        self.home = dict(HOME_COUNTS if home is None else home)
        self.rows.append({"type": "home_complete", "state": {"encoder_counts": counts_row(self.home)}})
        self.angle = dict.fromkeys(PER_DEGREE, 0.0)
        self.n = 0

    def step(self, joint, to_angle, raw_counts=None):
        self.n += 1
        delta = to_angle - self.angle[joint]
        counts = dict(self.home)
        counts[joint] = (self.home[joint] + round(PER_DEGREE[joint] * to_angle)
                         if raw_counts is None else raw_counts)
        self.rows.append({"type": "jog_preview", "n": self.n, "joint": joint, "delta_deg": delta})
        self.rows.append({"type": "after_jog", "n": self.n,
                          "state": {"encoder_counts": counts_row(counts)}})
        self.angle[joint] = to_angle
        return self.n

    def reach(self, joint, angle, approach):
        """Two steps so the last one arrives from the given side; returns the second step's number."""
        self.step(joint, angle - 1.0 if approach == "+" else angle + 1.0)
        return self.step(joint, angle)


def reading(log, point, joint, role, angle, requested=""):
    return {"log": log, "point": str(point), "joint": joint, "role": role,
            "physical_angle_deg": str(angle), "requested_target_deg": str(requested)}


def full_set():
    """Three homing trials (three logs) and every other row the fitter needs, per joint."""
    logs, readings = {}, []
    for index, name in enumerate(("a.jsonl", "b.jsonl", "c.jsonl")):
        home = {joint: count + index for joint, count in HOME_COUNTS.items()}
        logs[name] = LogBuilder(home=home)
        for joint in PER_DEGREE:
            readings.append(reading(name, "home", joint, "home", 0.0))
    first = logs["a.jsonl"]
    plan = {"fit": [(-10, "+"), (-5, "-"), (5, "+"), (10, "-")],
            "verify": [(-7.5, "-"), (2.5, "+"), (7.5, "-")],
            "move_verify": [(-5, "-"), (5, "+"), (-2.5, "-")]}
    for joint in PER_DEGREE:
        for role, points in plan.items():
            for angle, approach in points:
                n = first.reach(joint, angle, approach)
                readings.append(reading("a.jsonl", n, joint, role, angle + 0.1,
                                        angle if role == "move_verify" else ""))
    return {name: builder.rows for name, builder in logs.items()}, readings


class BuildRowsTests(unittest.TestCase):
    def test_a_full_set_builds_rows_the_real_fitter_accepts(self):
        logs, readings = full_set()
        rows = bcc.build_rows(logs, readings)
        with tempfile.TemporaryDirectory() as folder:
            csv_path = Path(folder) / "m.csv"
            bcc.write_csv(rows, csv_path)
            limits = Path(folder) / "limits.json"
            limits.write_text(json.dumps({joint: {"soft_min_deg": -7, "soft_max_deg": 7}
                                          for joint in PER_DEGREE}), encoding="utf-8")
            result = fit(csv_path, limits, "lab-er4u-1")
        for joint, expected in PER_DEGREE.items():
            self.assertAlmostEqual(result["joints"][joint]["counts_per_degree"], expected,
                                   delta=expected * 0.02)

    def test_each_row_gets_its_count_robot_and_approach_from_the_log(self):
        logs, readings = full_set()
        rows = bcc.build_rows(logs, readings)
        self.assertEqual({row["robot_id"] for row in rows}, {"lab-er4u-1"})
        self.assertEqual({row["reference_source"] for row in rows}, {"physical"})
        home = next(row for row in rows if row["role"] == "home" and row["joint"] == "shoulder")
        self.assertEqual((home["approach"], home["encoder_count"]), ("", raw(HOME_COUNTS["shoulder"])))
        move = next(row for row in rows if row["role"] == "move_verify" and row["joint"] == "base")
        self.assertEqual(move["approach"], "-")
        self.assertEqual(move["requested_target_deg"], -5.0)

    def test_summary_shows_a_short_set_before_leaving_the_lab(self):
        logs, readings = full_set()
        short = [r for r in readings if not (r["joint"] == "elbow" and r["role"] == "verify")]
        text = "\n".join(bcc.summary(bcc.build_rows(logs, short)))
        self.assertIn("elbow: home 3/3, fit 4/4, verify 0/3", text)
        self.assertIn("<- not enough yet", text)
        full = "\n".join(bcc.summary(bcc.build_rows(logs, readings)))
        self.assertNotIn("not enough", full)

    def test_a_reading_for_the_wrong_joint_is_refused(self):
        log = LogBuilder()
        n = log.step("base", 5.0)
        with self.assertRaisesRegex(bcc.ReadingsError, "moved base, not shoulder"):
            bcc.build_rows({"a.jsonl": log.rows},
                           [reading("a.jsonl", n, "shoulder", "fit", 5.0)])

    def test_a_missing_step_is_refused_and_the_steps_that_exist_are_listed(self):
        log = LogBuilder()
        log.step("base", 5.0)
        with self.assertRaisesRegex(bcc.ReadingsError, r"no step '9'.*1=base \+5"):
            bcc.build_rows({"a.jsonl": log.rows}, [reading("a.jsonl", 9, "base", "fit", 5.0)])

    def test_a_log_that_was_not_passed_is_refused(self):
        with self.assertRaisesRegex(bcc.ReadingsError, "was not passed"):
            bcc.build_rows({"a.jsonl": LogBuilder().rows},
                           [reading("other.jsonl", "home", "base", "home", 0.0)])

    def test_simulated_logs_and_mixed_robots_are_refused(self):
        with self.assertRaisesRegex(bcc.ReadingsError, "real-arm logs only"):
            bcc.build_rows({"a.jsonl": LogBuilder(data_source="simulated").rows},
                           [reading("a.jsonl", "home", "base", "home", 0.0)])
        with self.assertRaisesRegex(bcc.ReadingsError, "different robot ids"):
            bcc.build_rows({"a.jsonl": LogBuilder().rows,
                            "b.jsonl": LogBuilder(robot_id="other").rows}, [])

    def test_counts_are_kept_raw_across_the_seam(self):
        log = LogBuilder(home={"base": 65530, "shoulder": 0, "elbow": 0})
        n = log.step("base", 1.0, raw_counts=137)           # 65530 + 142 wrapped at 65535
        rows = bcc.build_rows({"a.jsonl": log.rows}, [
            reading("a.jsonl", "home", "base", "home", 0.0),
            reading("a.jsonl", n, "base", "fit", 1.0)])
        self.assertEqual([row["encoder_count"] for row in rows], [65530, 137])

    def test_a_count_outside_unsigned_16_bit_is_refused(self):
        log = LogBuilder()
        log.rows[1]["state"]["encoder_counts"]["base"] = -5
        with self.assertRaisesRegex(bcc.ReadingsError, "unsigned 16-bit"):
            bcc.build_rows({"a.jsonl": log.rows},
                           [reading("a.jsonl", "home", "base", "home", 0.0)])

    def test_bad_readings_are_refused(self):
        log = LogBuilder()
        n = log.step("base", 5.0)
        cases = [
            (reading("a.jsonl", n, "base", "fit", "nan"), "finite"),
            (reading("a.jsonl", n, "base", "fit", "five"), "finite"),
            (reading("a.jsonl", n, "base", "other", 5.0), "role"),
            (reading("a.jsonl", n, "wrist", "fit", 5.0), "joint"),
            (reading("a.jsonl", n, "base", "move_verify", 5.0), "needs a home reading"),
            (reading("a.jsonl", n, "base", "fit", 5.0, "x"), "number or empty"),
            (reading("a.jsonl", "01", "base", "fit", 5.0), "step number"),
            (reading("a.jsonl", "+1", "base", "fit", 5.0), "step number"),
            (reading("a.jsonl", "1.0", "base", "fit", 5.0), "step number"),
            (reading("a.jsonl", "home", "base", "fit", 0.0), "'home'"),
            (reading("a.jsonl", n, "base", "home", 0.0), "'home'"),
        ]
        for bad, message in cases:
            with self.subTest(message=message, row=bad):
                with self.assertRaisesRegex(bcc.ReadingsError, message):
                    bcc.build_rows({"a.jsonl": log.rows}, [bad])
        twice = [reading("a.jsonl", n, "base", "fit", 5.0)] * 2
        with self.assertRaisesRegex(bcc.ReadingsError, "twice"):
            bcc.build_rows({"a.jsonl": log.rows}, twice)

    def test_a_log_with_no_homing_cannot_give_a_home_row(self):
        log = LogBuilder()
        log.rows = [row for row in log.rows if row["type"] != "home_complete"]
        with self.assertRaisesRegex(bcc.ReadingsError, "no completed homing"):
            bcc.build_rows({"a.jsonl": log.rows},
                           [reading("a.jsonl", "home", "base", "home", 0.0)])


class TrustTests(unittest.TestCase):
    def test_a_log_must_be_one_real_session_with_at_most_one_homing(self):
        real, simulated = LogBuilder(), LogBuilder(data_source="simulated")
        joined = real.rows + simulated.rows          # two sessions pasted into one file
        with self.assertRaisesRegex(bcc.ReadingsError, "exactly one session row"):
            bcc.build_rows({"a.jsonl": joined}, [reading("a.jsonl", "home", "base", "home", 0.0)])
        twice = LogBuilder()
        twice.rows.append(dict(twice.rows[1]))       # a second home_complete
        with self.assertRaisesRegex(bcc.ReadingsError, "2 homings"):
            bcc.build_rows({"a.jsonl": twice.rows}, [reading("a.jsonl", "home", "base", "home", 0.0)])

    def test_the_requested_target_comes_from_the_logged_steps_not_from_the_operator(self):
        log = LogBuilder()
        n = log.step("base", 1.0)                    # commanded one degree
        home = reading("a.jsonl", "home", "base", "home", 0.5)
        honest = bcc.build_rows({"a.jsonl": log.rows},
                                [home, reading("a.jsonl", n, "base", "move_verify", 1.6, 1.5)])
        self.assertEqual(honest[1]["requested_target_deg"], 1.5)       # 0.5 at home + 1 commanded
        derived = bcc.build_rows({"a.jsonl": log.rows},
                                 [home, reading("a.jsonl", n, "base", "move_verify", 1.6)])
        self.assertEqual(derived[1]["requested_target_deg"], 1.5)      # left empty: worked out
        with self.assertRaisesRegex(bcc.ReadingsError, "does not match the logged steps"):
            bcc.build_rows({"a.jsonl": log.rows},      # the arm landed at 4.5 and the sheet says so too
                           [home, reading("a.jsonl", n, "base", "move_verify", 4.5, 4.5)])


class CommandTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        logs, readings = full_set()
        self.logs = []
        for name, rows in logs.items():
            path = self.folder / name
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            self.logs.append(path)
        self.readings = self.folder / "readings.csv"
        with self.readings.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(readings[0]))
            writer.writeheader()
            writer.writerows(readings)
        self.limits = self.folder / "limits.json"
        self.limits.write_text(json.dumps({joint: {"soft_min_deg": -7, "soft_max_deg": 7}
                                           for joint in PER_DEGREE}), encoding="utf-8")

    def run_main(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = bcc.main([*args])
        return code, out.getvalue()

    def arguments(self, output):
        logs = [item for path in self.logs for item in ("--log", str(path))]
        return [*logs, "--readings", str(self.readings), "--output", str(output)]

    def test_it_writes_the_csv_and_prints_the_summary_and_next_command(self):
        output = self.folder / "m.csv"
        code, text = self.run_main(*self.arguments(output))
        self.assertEqual(code, 0)
        self.assertTrue(output.exists())
        self.assertIn("base: home 3/3, fit 4/4, verify 3/3, move_verify 3/3", text)
        self.assertIn("fit_calibration.py --measurements", text)

    def test_with_limits_it_also_fits_and_never_overwrites(self):
        output, calibration = self.folder / "m.csv", self.folder / "cal.json"
        code, text = self.run_main(*self.arguments(output), "--limits", str(self.limits),
                                   "--calibration-output", str(calibration))
        self.assertEqual(code, 0, text)
        self.assertIn("Validated base, elbow, shoulder", text)
        self.assertEqual(json.loads(calibration.read_text(encoding="utf-8"))["status"], "validated")
        again, message = self.run_main(*self.arguments(output))
        self.assertEqual(again, 2)
        self.assertIn("Not written", message)

    def test_two_logs_with_the_same_name_are_refused(self):
        other = self.folder / "elsewhere"
        other.mkdir()
        copy = other / self.logs[0].name
        copy.write_text(self.logs[0].read_text(encoding="utf-8"), encoding="utf-8")
        output = self.folder / "m.csv"
        code, text = self.run_main("--log", str(self.logs[0]), "--log", str(copy),
                                   "--readings", str(self.readings), "--output", str(output))
        self.assertEqual(code, 2)
        self.assertIn("same name", text)
        self.assertFalse(output.exists())

    def test_a_refusal_writes_nothing(self):
        output = self.folder / "m.csv"
        code, text = self.run_main("--log", str(self.logs[0]), "--readings", str(self.readings),
                                   "--output", str(output))
        self.assertEqual(code, 2)
        self.assertIn("was not passed", text)
        self.assertFalse(output.exists())

    def test_the_fit_refusing_is_reported_not_raised(self):
        output, calibration = self.folder / "m.csv", self.folder / "cal.json"
        self.limits.write_text(json.dumps({joint: {"soft_min_deg": -20, "soft_max_deg": 20}
                                           for joint in PER_DEGREE}), encoding="utf-8")
        code, text = self.run_main(*self.arguments(output), "--limits", str(self.limits),
                                   "--calibration-output", str(calibration))
        self.assertEqual(code, 3)
        self.assertIn("Fit refused", text)
        self.assertTrue(output.exists(), "the CSV is still kept")


if __name__ == "__main__":
    unittest.main()
