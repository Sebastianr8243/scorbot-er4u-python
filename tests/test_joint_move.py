"""scorbot.joint_move: coupled joint moves on their own, against the vendor's joint formulas.

No USB, no robot. The vectors come from the same source model the rest of the SDK uses
(``source_model``, the vendor DLL's counts-to-angles function), so these tests check the
arithmetic and the ledger, not the arm: whether the signs are right on the arm is for the
first supervised trial.
"""

import random
import unittest

from scorbot import joint_move, source_model
from scorbot.calibration import signed_count_delta

HOME = source_model.HOME_ANGLES
MOTORS = source_model.MOTORS


def angles_after(deltas):
    return source_model.angles_from_counts({m: deltas.get(m, 0.0) for m in MOTORS})


class VectorTests(unittest.TestCase):
    def test_moving_one_joint_by_its_vector_changes_only_that_joint(self):
        # The point of a joint move: every other joint angle (including the relative wrist
        # pitch) stays where it was. This is what the vendor's joint mode does.
        for joint in joint_move.JOINTS:
            for degrees in (-15.0, -1.0, 0.25, 1.0, 12.0):
                with self.subTest(joint=joint, degrees=degrees):
                    deltas = {m: c * degrees for m, c in joint_move.vector(joint).items()}
                    after = angles_after(deltas)
                    for name in ("base", "shoulder", "elbow", "pitch", "roll"):
                        expected = HOME[name] + (degrees if name == joint else 0.0)
                        self.assertAlmostEqual(after[name], expected, delta=0.35, msg=name)

    def test_the_shoulder_and_elbow_vectors_carry_the_coupled_motors(self):
        shoulder, elbow, base = (joint_move.vector(j) for j in ("shoulder", "elbow", "base"))
        # From the vendor trace (docs/protocol/VENDOR_COUPLING_TRACE.md): the elbow follows the
        # shoulder one for one, opposite; the wrist pair moves as a pure pitch, 0.246 of that.
        self.assertAlmostEqual(shoulder["elbow"], -shoulder["shoulder"], delta=0.5)
        self.assertAlmostEqual(abs(shoulder["wrist_motor_1"]) / abs(shoulder["shoulder"]),
                               0.246, delta=0.005)
        self.assertAlmostEqual(shoulder["wrist_motor_1"], -shoulder["wrist_motor_2"], delta=0.2)
        self.assertEqual(elbow["shoulder"], 0.0)
        self.assertAlmostEqual(elbow["wrist_motor_1"], -elbow["wrist_motor_2"], delta=0.2)
        self.assertEqual({m for m, c in base.items() if c}, {"base"})

    def test_the_vectors_match_the_numbers_in_the_coupling_trace(self):
        # docs/protocol/VENDOR_COUPLING_TRACE.md: moving the shoulder by Ds counts holding every
        # other joint angle is (shoulder Ds, elbow -Ds, m1 -0.246 Ds, m2 +0.246 Ds); the elbow
        # joint moves its motor and the same wrist pair. Counts per degree here. A flipped sign
        # in any of them would double a swing instead of cancelling it.
        expected = {
            "base": {"base": -141.8},
            "shoulder": {"shoulder": 113.5, "elbow": -113.5,
                         "wrist_motor_1": -27.85, "wrist_motor_2": 27.85},
            "elbow": {"elbow": -113.5, "wrist_motor_1": -27.85, "wrist_motor_2": 27.85},
        }
        for joint, numbers in expected.items():
            vec = joint_move.vector(joint)
            for motor in MOTORS:
                self.assertAlmostEqual(vec[motor], numbers.get(motor, 0.0), delta=0.3,
                                       msg=(joint, motor))

    def test_the_wrist_is_always_a_pure_pitch_never_a_roll(self):
        for joint in joint_move.JOINTS:
            vec = joint_move.vector(joint)
            self.assertAlmostEqual(vec["wrist_motor_1"] + vec["wrist_motor_2"], 0.0, delta=0.2,
                                   msg=joint)

    def test_an_unknown_joint_is_refused(self):
        for bad in ("wrist_pitch", "pitch", "gripper", "", None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                joint_move.vector(bad)


class FakeArm:
    """Encoder counts that obey jog requests with an error of up to 20 counts, like the
    legacy jog (it ends once within 20 counts of its target)."""

    def __init__(self, seed=0, start=None):
        self.rng = random.Random(seed)
        self.counts = {m: 40000 for m in MOTORS}           # away from the 0/65535 seam
        self.counts.update(start or {})
        self.jogs = []

    def jog(self, motor, counts):
        self.jogs.append((motor, counts))
        landed = counts + self.rng.randint(-19, 19) if abs(counts) > 40 else counts
        if motor == "wrist_pitch":
            self.counts["wrist_motor_1"] += landed
            self.counts["wrist_motor_2"] -= landed
        else:
            self.counts[motor] += landed


def run_move(arm, target, joint, degrees, *, primary):
    for jog_motor, counts in target.next_jogs(arm.counts, primary=primary):
        arm.jog(jog_motor, counts)


class SingleMotorTargetTests(unittest.TestCase):
    """coupled=False: a move asks for the requested motor only (the vendor's joint jog)."""

    def test_a_single_motor_move_owes_nothing_to_the_other_motors(self):
        arm = FakeArm()
        target = joint_move.CoupledTarget(arm.counts, coupled=False)
        target.add("shoulder", 2.0)
        errors = target.errors(arm.counts)
        self.assertAlmostEqual(errors["shoulder"], 2.0 * joint_move.vector("shoulder")["shoulder"])
        for motor in MOTORS:
            if motor != "shoulder":
                self.assertEqual(errors[motor], 0.0, motor)
        self.assertEqual([m for m, _ in target.next_jogs(arm.counts, primary="shoulder",
                                                         final=True)], ["shoulder"])

    def test_a_motor_that_drifts_is_ignored_unless_it_was_asked_for(self):
        arm = FakeArm()
        arm.counts["wrist_motor_1"] += 300
        arm.counts["wrist_motor_2"] -= 300
        single = joint_move.CoupledTarget(dict.fromkeys(MOTORS, 0), coupled=False)
        single.add("base", 1.0)
        self.assertNotIn("wrist_pitch",
                         dict(single.next_jogs(arm.counts, primary="base", final=True)))
        coupled = joint_move.CoupledTarget(dict.fromkeys(MOTORS, 0))
        coupled.add("base", 1.0)
        self.assertIn("wrist_pitch", dict(coupled.next_jogs(arm.counts, primary="base",
                                                            final=True)))

    def test_every_motor_asked_for_over_a_run_stays_tracked(self):
        arm = FakeArm()
        target = joint_move.CoupledTarget(arm.counts, coupled=False)
        target.add("shoulder", 1.0)
        target.add("elbow", 1.0)
        errors = target.errors(arm.counts)
        self.assertNotEqual(errors["shoulder"], 0.0)
        self.assertNotEqual(errors["elbow"], 0.0)
        self.assertEqual(errors["base"], 0.0)


class LedgerTests(unittest.TestCase):
    def test_the_target_is_cumulative_and_measured_from_the_run_start(self):
        arm = FakeArm()
        target = joint_move.CoupledTarget(arm.counts)
        target.add("shoulder", 1.0)
        target.add("shoulder", 1.0)
        vec = joint_move.vector("shoulder")
        for motor in MOTORS:
            self.assertAlmostEqual(target.errors(arm.counts)[motor], 2.0 * vec[motor], places=6)

    def test_the_primary_motor_is_jogged_down_to_the_settle_band_and_not_below(self):
        arm = FakeArm()
        target = joint_move.CoupledTarget(arm.counts)
        target.add("base", 0.25)                           # about 35 counts
        jogs = target.next_jogs(arm.counts, primary="base")
        self.assertEqual([m for m, _ in jogs], ["base"])
        target = joint_move.CoupledTarget(arm.counts)
        target.add("base", 0.1)                            # about 14 counts: under the band
        self.assertEqual(target.next_jogs(arm.counts, primary="base"), [])

    def test_coupled_motors_wait_for_a_worthwhile_jog_and_the_end_flushes_them(self):
        arm = FakeArm()
        target = joint_move.CoupledTarget(arm.counts)
        target.add("shoulder", 0.5)                        # elbow owed ~57 counts, wrist ~14
        jogs = dict(target.next_jogs(arm.counts, primary="shoulder"))
        self.assertIn("shoulder", jogs)
        self.assertNotIn("elbow", jogs)
        self.assertNotIn("wrist_pitch", jogs)
        arm.jog("shoulder", jogs["shoulder"])
        final = dict(target.next_jogs(arm.counts, primary=None, final=True))
        self.assertIn("elbow", final)                      # 28 counts owed, over the final band
        target.add("shoulder", 4.0)
        big = dict(target.next_jogs(arm.counts, primary="shoulder"))
        self.assertTrue({"shoulder", "elbow", "wrist_pitch"} <= set(big))

    def test_the_wrist_is_asked_for_as_one_pitch_amount_with_the_motors_opposite(self):
        arm = FakeArm()
        target = joint_move.CoupledTarget(arm.counts)
        target.add("shoulder", 5.0)
        jogs = dict(target.next_jogs(arm.counts, primary="shoulder"))
        vec = joint_move.vector("shoulder")
        self.assertAlmostEqual(jogs["wrist_pitch"], 5.0 * vec["wrist_motor_1"], delta=1.5)

    def test_many_fine_steps_do_not_drift_from_the_cumulative_target(self):
        # The failure a per-call reference would have: 24 quarter degree steps of the
        # shoulder, each landing up to 19 counts off, with the coupled motors owed in
        # lumps. The error against the cumulative target must stay inside the bands.
        for seed in range(20):
            arm = FakeArm(seed)
            target = joint_move.CoupledTarget(arm.counts)
            for _ in range(24):
                target.add("shoulder", 0.25)
                run_move(arm, target, "shoulder", 0.25, primary="shoulder")
            errors = target.errors(arm.counts)
            self.assertLess(abs(errors["shoulder"]), 40, (seed, errors))
            self.assertLess(abs(errors["elbow"]), joint_move.COUPLED_MIN_COUNTS + 40,
                            (seed, errors))
            for jog_motor, counts in target.next_jogs(arm.counts, primary=None, final=True):
                arm.jog(jog_motor, counts)
            errors = target.errors(arm.counts)
            self.assertLess(abs(errors["elbow"]), 60, (seed, errors))
            self.assertLess(abs(errors["wrist_motor_1"]), 60, (seed, errors))

    def test_counts_are_compared_across_the_counter_seam(self):
        arm = FakeArm(start={"base": 5})                   # just above the seam
        target = joint_move.CoupledTarget(arm.counts)
        target.add("base", 1.0)
        moved = round(joint_move.vector("base")["base"])   # about 142 counts, base counts fall
        arm.counts["base"] = (5 + moved) % 65535           # wraps below 0 to near 65535
        self.assertGreater(arm.counts["base"], 60000)
        self.assertLess(abs(target.errors(arm.counts)["base"]), 5)
        self.assertEqual(signed_count_delta(arm.counts["base"], 5), moved)

    def test_bad_input_is_refused(self):
        target = joint_move.CoupledTarget(FakeArm().counts)
        for joint, degrees in (("wrist_pitch", 1.0), ("shoulder", float("nan")),
                               ("shoulder", True), ("shoulder", "1")):
            with self.subTest(joint=joint, degrees=degrees), self.assertRaises(ValueError):
                target.add(joint, degrees)
        with self.assertRaises(ValueError):
            joint_move.CoupledTarget({"base": 1})          # missing motors


class ConstantsTests(unittest.TestCase):
    def test_a_wrist_step_is_progress_checked_and_stays_under_the_jog_ceiling(self):
        # A wrist jog cannot be cut short by a stop (the legacy wrist loop takes no stop event),
        # so it is small; and it is large enough to be checked for progress.
        step = joint_move.STEP_LIMIT_COUNTS["wrist_pitch"]
        self.assertGreaterEqual(step, joint_move.PROGRESS_CHECK_COUNTS)
        self.assertLessEqual(step, 60)
        self.assertLessEqual(step / 33.8, 2.0)                  # legacy pitch scale, ceiling 2

    def test_the_thresholds_sit_around_the_legacy_settle_band(self):
        # The legacy jog ends within 20 counts of its target (libcomm settle loop).
        self.assertGreaterEqual(joint_move.PRIMARY_MIN_COUNTS, 20)
        self.assertGreater(joint_move.COUPLED_MIN_COUNTS, joint_move.FINAL_MIN_COUNTS)
        self.assertGreater(joint_move.FINAL_MIN_COUNTS, joint_move.PRIMARY_MIN_COUNTS)
        self.assertLessEqual(joint_move.SLICE_DEG, 5.0)         # the SDK's jog ceiling


if __name__ == "__main__":
    unittest.main()
