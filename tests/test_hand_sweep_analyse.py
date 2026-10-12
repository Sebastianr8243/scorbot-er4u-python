"""scripts/hand_sweep_report.py:analyse pinned: every refusal and its order, each warning and its boundary, the
unwrapped counts, the switch bands and the roll-axis note, before the function is split into helpers.

Synthetic recordings only; no real data and no USB.
"""

import unittest

from scripts import hand_sweep_report as hs

NAMES = ("base", "shoulder", "elbow", "wrist_motor_1", "wrist_motor_2", "gripper")


def state(counts=None, bits=0, **extra):
    counts = counts or {}
    return {"encoder_counts": {name: counts.get(name, 0) for name in NAMES}, "home_switch_bits": bits, **extra}


def rows(*states, source="real"):
    return [{"type": "session", "data_source": source}] + [{"type": "sample", "state": s} for s in states]


def walk(joint, values, bits=None):
    return rows(*[state({joint: v}, (bits or {}).get(i, 0)) for i, v in enumerate(values)])


def refusal(recording, **kwargs):
    with_error = None
    try:
        hs.analyse(recording, **kwargs)
    except ValueError as exc:
        with_error = str(exc)
    return with_error


class Refusals(unittest.TestCase):
    def test_simulated_by_the_session_row_or_by_a_sample(self):
        message = "this recording is simulated; a hand sweep needs the real arm"
        self.assertEqual(refusal(rows(state(), state(), source="simulated")), message)
        self.assertEqual(refusal(rows(state(), state(simulated=True))), message)

    def test_simulated_is_refused_before_the_sample_count_is_looked_at(self):
        self.assertEqual(refusal(rows(state(), source="simulated")),
                         "this recording is simulated; a hand sweep needs the real arm")

    def test_too_few_samples_counts_only_samples_with_a_state_dict(self):
        self.assertEqual(refusal(rows(state())), "need at least 2 samples, found 1")
        recording = rows(state(), state()) + [{"type": "note"}, {"type": "sample", "state": None},
                                              {"type": "sample", "state": "x"}]
        self.assertEqual(hs.analyse(recording).samples, 2)
        self.assertEqual(refusal([{"type": "session", "data_source": "real"}]), "need at least 2 samples, found 0")

    def test_unknown_angle_joint_is_refused_after_the_sample_count(self):
        self.assertEqual(refusal(rows(state(), state()), angles={"foo": (0, 1)}),
                         "unknown joint 'foo'; use one of base, shoulder, elbow, wrist_motor_1, wrist_motor_2, gripper")
        self.assertEqual(refusal(rows(state()), angles={"foo": (0, 1)}), "need at least 2 samples, found 1")

    def test_equal_angles_are_refused_for_that_joint_only(self):
        self.assertEqual(refusal(walk("base", [0, 10]), angles={"base": (5, 5)}), "base: the two angles must differ")
        self.assertIsNone(refusal(walk("base", [0, 10]), angles={"base": (5, 6)}))


class Counts(unittest.TestCase):
    def test_golden_report_for_one_joint_and_one_switch(self):
        report = hs.analyse(walk("base", [0, 10, 20, 30, 20], {1: 1, 2: 1}))
        self.assertEqual(report.samples, 5)
        self.assertEqual(report.joints["base"], hs.JointSweep(0, 30, 20, 10, None))
        for name in NAMES[1:]:
            self.assertEqual(report.joints[name], hs.JointSweep(0, 0, 0, 0, None))
        self.assertEqual(list(report.joints), ["base", "shoulder", "elbow", "wrist_motor_1", "wrist_motor_2", "gripper"])
        self.assertEqual(report.bands, [hs.Band("base", "base", (0, 10), (20, 30), 2, 2)])
        self.assertEqual((report.warnings, report.notes), ([], []))

    def test_counts_unwrap_across_the_zero_seam_and_down(self):
        report = hs.analyse(walk("elbow", [65530, 65534, 3, 8]))
        self.assertEqual(report.joints["elbow"], hs.JointSweep(0, 13, 13, 5, None))
        report = hs.analyse(walk("elbow", [5, 2, 65533]))
        self.assertEqual(report.joints["elbow"], hs.JointSweep(-7, 0, -7, 4, None))

    def test_counts_per_degree_is_end_over_the_angle_change_and_keeps_its_sign(self):
        report = hs.analyse(walk("shoulder", [0, 50, 100]), angles={"shoulder": (10, 20)})
        self.assertEqual(report.joints["shoulder"].counts_per_degree, 10.0)
        report = hs.analyse(walk("shoulder", [0, 50, 100]), angles={"shoulder": (20, 10)})
        self.assertEqual(report.joints["shoulder"].counts_per_degree, -10.0)
        self.assertIsNone(report.joints["base"].counts_per_degree)


class Warnings(unittest.TestCase):
    def test_a_step_of_exactly_the_jump_limit_is_not_a_jump(self):
        report = hs.analyse(walk("base", [0, 5000, 5010]))
        self.assertEqual(report.joints["base"].end, 5010)
        self.assertNotIn("jump", " ".join(report.warnings))

    def test_a_jump_is_skipped_counted_and_reported_once_for_all_joints(self):
        recording = rows(state({"base": 0, "elbow": 0}), state({"base": 5001, "elbow": 6000}),
                         state({"base": 5005, "elbow": 6010}))
        report = hs.analyse(recording)
        self.assertEqual(report.joints["base"].end, 4)
        self.assertEqual(report.joints["elbow"].end, 10)
        self.assertEqual(report.warnings, ["2 jump(s) over 5000 counts between samples were skipped: "
                                           "a sample was lost or a count glitched; read the neighbours"])

    def test_the_coarse_limit_is_a_step_over_fifty(self):
        self.assertEqual(hs.analyse(walk("base", [0, 50, 100])).warnings, [])
        self.assertEqual(hs.analyse(walk("base", [0, 51, 60])).warnings,
                         ["coarse resolution: the largest step between samples was 51 counts, so each switch edge "
                          "is only known to within that; move more slowly"])

    def test_the_coarse_step_is_the_largest_over_every_joint(self):
        recording = rows(state({"base": 0, "gripper": 0}), state({"base": 5, "gripper": 80}))
        self.assertIn("was 80 counts", hs.analyse(recording).warnings[0])

    def test_motors_enabled_in_any_one_sample(self):
        recording = rows(state(), state(enabled=True), state())
        self.assertEqual(hs.analyse(recording).warnings,
                         ["the motors were enabled during part of this recording: it is not a hand sweep "
                          "and moving the arm by hand with motors on fights the servo"])

    def test_unknown_switch_bits_are_ored_across_samples_and_known_bits_are_masked_off(self):
        recording = rows(state(bits=0x21), state(bits=0x40), state(bits=0x1F))
        self.assertEqual(hs.analyse(recording).warnings,
                         ["unexpected switch bits 0x60 were set; do not home until they are explained"])
        self.assertEqual(hs.analyse(rows(state(bits=0x1F), state())).warnings, [])

    def test_warnings_come_in_a_fixed_order(self):
        recording = rows(state({"base": 0}, bits=0x80), state({"base": 6000, "elbow": 70}, enabled=True))
        warnings = hs.analyse(recording).warnings
        self.assertEqual([w.split()[0] for w in warnings], ["1", "the", "coarse", "unexpected"])


class Bands(unittest.TestCase):
    def test_on_from_the_start_and_still_on_at_the_end_have_no_edges_and_no_transitions(self):
        report = hs.analyse(walk("base", [0, 10, 20], {0: 1, 1: 1, 2: 1}))
        self.assertEqual(report.bands, [hs.Band("base", "base", None, None, 0, 3)])

    def test_a_band_that_starts_on_has_only_an_off_edge(self):
        report = hs.analyse(walk("base", [0, 10, 20, 30], {0: 1, 1: 1}))
        self.assertEqual(report.bands, [hs.Band("base", "base", None, (10, 20), 1, 2)])

    def test_two_passes_give_two_bands_in_order(self):
        report = hs.analyse(walk("shoulder", [0, 1, 2, 3, 4, 5], {1: 2, 4: 2}))
        self.assertEqual([(b.on_edge, b.off_edge) for b in report.bands], [((0, 1), (1, 2)), ((3, 4), (4, 5))])

    def test_bands_follow_the_switch_table_order(self):
        report = hs.analyse(rows(state({}, 0), state({}, 0x0F), state({}, 0)))
        self.assertEqual([(b.switch, b.joint) for b in report.bands],
                         [("base", "base"), ("shoulder", "shoulder"), ("elbow", "elbow"),
                          ("wrist_pitch", "wrist_motor_1"), ("wrist_pitch", "wrist_motor_2")])

    def test_the_roll_switch_is_reported_on_both_motors_and_the_roll_axis_in_that_order(self):
        recording = rows(*[state({"wrist_motor_1": v, "wrist_motor_2": v + d}, 16 if 1 <= i <= 2 else 0)
                           for i, (v, d) in enumerate([(0, 0), (10, 2), (20, 4), (30, 6)])])
        report = hs.analyse(recording)
        self.assertEqual([b.joint for b in report.bands], ["wrist_motor_1", "wrist_motor_2", "wrist_roll_axis"])
        axis = report.bands[2]
        self.assertEqual((axis.on_edge, axis.off_edge), ((0.0, 11.0), (22.0, 33.0)))


class RollMotorsNote(unittest.TestCase):
    def roll(self, end_1, end_2):
        recording = rows(state({"wrist_motor_1": 0, "wrist_motor_2": 0}, 0),
                         state({"wrist_motor_1": end_1 // 2, "wrist_motor_2": end_2 // 2}, 16),
                         state({"wrist_motor_1": end_1, "wrist_motor_2": end_2}, 0))
        return " ".join(hs.analyse(recording).notes)

    def test_motors_that_ended_apart_by_more_than_a_fifth_are_noted(self):
        self.assertIn("the two wrist motors did not move together (motor 1 ended at 1000, motor 2 at 700): "
                      "this was not a pure roll, so the roll width above may be wrong", self.roll(1000, 700))

    def test_exactly_a_fifth_apart_is_not_noted_and_the_comparison_uses_the_larger_end(self):
        self.assertNotIn("did not move together", self.roll(1000, 800))
        self.assertNotIn("did not move together", self.roll(800, 1000))
        self.assertIn("did not move together", self.roll(790, 1000))

    def test_no_roll_switch_means_no_such_note(self):
        recording = rows(state({"wrist_motor_1": 0}), state({"wrist_motor_1": 1000}))
        self.assertEqual(hs.analyse(recording).notes, [])


if __name__ == "__main__":
    unittest.main()
