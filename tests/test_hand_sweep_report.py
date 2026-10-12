"""scripts/hand_sweep_report.py: read a motors-off recording of the arm moved by hand.

The recordings here are synthetic: a joint moves through a known list of counts and a home-switch bit
is set over a known band, and the report must give those numbers back. No real data, no USB.
"""

import json
from pathlib import Path
import tempfile
import unittest

from scripts import hand_sweep_report as hs

JOINTS = ("base", "shoulder", "elbow", "wrist_motor_1", "wrist_motor_2", "gripper")


def sample(index, counts, bits=0, *, enabled=False, simulated=False):
    counts = {name: counts.get(name, 0) % 65535 for name in JOINTS}
    return {"type": "sample", "index": index, "state": {
        "encoder_counts": counts, "home_switch_bits": bits, "enabled": enabled,
        "simulated": simulated, "host_monotonic_ns": index * 100_000_000}}


def sweep(joint, values, *, bit=None, band=None, **kwargs):
    """One joint walks through ``values`` (counts from its start); ``bit`` is set while value is in ``band``."""
    rows = [{"type": "session", "data_source": "simulated" if kwargs.get("simulated") else "real"}]
    for i, v in enumerate(values):
        on = bit if bit is not None and band[0] <= v < band[1] else 0
        rows.append(sample(i, {joint: v + OFFSETS.get(joint, 0)}, on, **kwargs))
    return rows


OFFSETS = {}


class JointTests(unittest.TestCase):
    def test_travel_range_and_end_points_are_relative_to_the_first_sample(self):
        report = hs.analyse(sweep("shoulder", list(range(0, 1001, 10))))
        s = report.joints["shoulder"]
        self.assertEqual((s.min, s.max, s.end), (0, 1000, 1000))
        self.assertEqual(s.max_step, 10)
        self.assertEqual(report.joints["base"].max - report.joints["base"].min, 0)

    def test_a_walk_across_the_zero_seam_is_unwrapped(self):
        OFFSETS["base"] = 65500
        try:
            report = hs.analyse(sweep("base", list(range(0, 201, 10))))
        finally:
            OFFSETS.clear()
        self.assertEqual(report.joints["base"].end, 200)

    def test_a_negative_walk_is_negative(self):
        report = hs.analyse(sweep("elbow", list(range(0, -501, -10))))
        self.assertEqual((report.joints["elbow"].min, report.joints["elbow"].end), (-500, -500))

    def test_counts_per_degree_from_the_two_angles_you_read(self):
        report = hs.analyse(sweep("shoulder", list(range(0, 1136, 5))), angles={"shoulder": (10.0, 20.0)})
        self.assertAlmostEqual(report.joints["shoulder"].counts_per_degree, 113.5, places=6)

    def test_angles_for_an_unknown_joint_are_refused(self):
        with self.assertRaises(ValueError):
            hs.analyse(sweep("shoulder", [0, 10]), angles={"nose": (0, 1)})


class SwitchBandTests(unittest.TestCase):
    def test_a_band_is_reported_as_two_edge_intervals(self):
        values = list(range(0, 1001, 10))
        report = hs.analyse(sweep("shoulder", values, bit=2, band=(300, 420)))
        (band,) = report.bands
        self.assertEqual((band.switch, band.joint), ("shoulder", "shoulder"))
        self.assertEqual(band.on_edge, (290, 300))     # off at 290, first on at 300
        self.assertEqual(band.off_edge, (410, 420))    # last on at 410, off at 420
        self.assertEqual(band.transitions, 2)

    def test_two_passes_over_the_switch_give_two_bands(self):
        values = list(range(0, 600, 10)) + list(range(600, -1, -10))
        report = hs.analyse(sweep("elbow", values, bit=4, band=(200, 300)))
        self.assertEqual(len(report.bands), 2)

    def test_a_switch_already_on_at_the_start_has_no_on_edge(self):
        report = hs.analyse(sweep("base", list(range(0, 300, 10)), bit=1, band=(0, 100)))
        (band,) = report.bands
        self.assertIsNone(band.on_edge)
        self.assertEqual(band.off_edge, (90, 100))

    def test_the_wrist_switches_are_reported_against_both_wrist_motors(self):
        rows = sweep("wrist_motor_1", list(range(0, 300, 10)), bit=8, band=(100, 200))
        report = hs.analyse(rows)
        self.assertEqual({b.joint for b in report.bands}, {"wrist_motor_1", "wrist_motor_2"})
        self.assertTrue(all(b.switch == "wrist_pitch" for b in report.bands))

    def test_unknown_switch_bits_are_flagged(self):
        rows = sweep("base", [0, 10, 20], bit=64, band=(0, 100))
        self.assertTrue(any("0x40" in w for w in hs.analyse(rows).warnings))


def roll_sweep(values, band, *, second=None):
    """A pure roll: both wrist motors walk through ``values`` together (``second`` overrides motor 2's walk)."""
    rows = [{"type": "session", "data_source": "real"}]
    for i, v in enumerate(values):
        w = values[i] if second is None else second[i]
        rows.append(sample(i, {"wrist_motor_1": v, "wrist_motor_2": w}, 16 if band[0] <= v < band[1] else 0))
    return rows


class RollPredictionTests(unittest.TestCase):
    """The port needs the wrist roll switch to stay on at least ~800 counts (tests/test_vendor_home_geometry.py)."""

    def notes(self, band):
        return " ".join(hs.analyse(roll_sweep(list(range(0, 1500, 10)), band)).notes)

    def test_the_roll_width_is_measured_on_both_wrist_motors_not_motor_1_alone(self):
        values = list(range(0, 1500, 10))
        # Motor 2 stays put: the roll coordinate (m1 + m2) / 2 only moved half as far, so a 900-count window
        # of motor 1 is a 450-count roll switch, and the report must not call it wide enough.
        text = " ".join(hs.analyse(roll_sweep(values, (300, 1200), second=[0] * len(values))).notes)
        self.assertNotIn("wide enough", text)
        self.assertIn("did not move together", text)

    def test_a_narrow_roll_switch_says_the_vendor_home_will_not_find_it_again(self):
        text = self.notes((300, 600))
        self.assertIn("roll", text)
        self.assertIn("800", text)
        self.assertIn("narrower", text)

    def test_a_wide_roll_switch_is_reported_as_wide_enough(self):
        text = self.notes((300, 1200))
        self.assertIn("wide enough", text)

    def test_a_roll_switch_whose_edges_were_not_both_crossed_is_not_judged(self):
        rows = roll_sweep(list(range(0, 600, 10)), (300, 10_000))                            # still on at the end
        text = " ".join(hs.analyse(rows).notes)
        self.assertIn("did not cross both", text)

    def test_other_switches_get_no_roll_note(self):
        rows = sweep("shoulder", list(range(0, 1000, 10)), bit=2, band=(300, 420))
        self.assertEqual(hs.analyse(rows).notes, [])

    def test_the_notes_are_printed_and_do_not_change_the_exit_code(self):
        rows = roll_sweep(list(range(0, 1500, 10)), (300, 600))
        self.assertIn("NOTE", hs.format_report(hs.analyse(rows)))


class SafetyOfTheDataTests(unittest.TestCase):
    def test_simulated_data_is_refused(self):
        with self.assertRaises(ValueError):
            hs.analyse(sweep("base", [0, 10], simulated=True))

    def test_motors_on_is_not_a_hand_sweep(self):
        report = hs.analyse(sweep("base", [0, 10, 20], enabled=True))
        self.assertTrue(any("motors" in w.lower() for w in report.warnings))

    def test_a_coarse_recording_says_how_coarse(self):
        report = hs.analyse(sweep("base", list(range(0, 3001, 300))))
        self.assertTrue(any("resolution" in w.lower() for w in report.warnings))

    def test_a_jump_past_half_the_counter_is_a_warning_not_a_crash(self):
        rows = sweep("base", [0, 10, 20])
        rows.insert(2, sample(99, {"base": 40000}))
        report = hs.analyse(rows)
        self.assertTrue(any("jump" in w.lower() for w in report.warnings))

    def test_too_few_samples_is_an_error(self):
        with self.assertRaises(ValueError):
            hs.analyse(sweep("base", [0]))


class CommandLineTests(unittest.TestCase):
    def test_main_reads_a_file_and_prints_each_joint_and_each_band(self):
        rows = sweep("shoulder", list(range(0, 1001, 10)), bit=2, band=(300, 420))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sweep.jsonl"
            path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            text = hs.format_report(hs.analyse(hs.read_rows(path)))
        self.assertIn("shoulder", text)
        self.assertIn("290", text)
        self.assertIn("420", text)


if __name__ == "__main__":
    unittest.main()
