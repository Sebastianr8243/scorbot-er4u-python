"""Pure step planning for multi-step moves: no robot, no I/O."""

import dataclasses
import unittest

from scorbot.lab.moves import (MAX_MARKS, RETURN_ORDER, MarkedPosition, Move, apply,
                               plan_moves)

ZERO = {"base": 0.0, "shoulder": 0.0, "elbow": 0.0}


class PlanMovesTests(unittest.TestCase):
    def test_order_is_elbow_shoulder_base(self):
        self.assertEqual(RETURN_ORDER, ("elbow", "shoulder", "base"))
        moves = plan_moves({"base": -2.0, "shoulder": 1.0, "elbow": -1.0}, ZERO)
        self.assertEqual([m.label for m in moves],
                         ["ELBOW +1", "SHOULDER -1", "BASE +1", "BASE +1"])

    def test_whole_steps_then_one_half_step(self):
        self.assertEqual([m.delta_deg for m in plan_moves({"base": 0.0}, {"base": 2.5})],
                         [1.0, 1.0, 0.5])
        self.assertEqual([m.label for m in plan_moves({"elbow": 0.5}, {"elbow": -1.0})],
                         ["ELBOW -1", "ELBOW -0.5"])

    def test_already_there_is_empty(self):
        self.assertEqual(plan_moves({"base": 1.0}, {"base": 1.0}), [])
        self.assertEqual(plan_moves({}, {}), [])

    def test_missing_joint_counts_as_zero(self):
        self.assertEqual(plan_moves({"base": 1.0}, {}), [Move("base", -1.0)])

    def test_float_noise_is_tolerated(self):
        self.assertEqual(plan_moves({"base": 0.1 + 0.2 - 0.3 + 1.0}, {}), [Move("base", -1.0)])

    def test_rejects_bad_input(self):
        for current, target in (({"base": 0.3}, {}), ({"wrist_pitch": 1.0}, {}),
                                ({}, {"gripper": 1.0}), ({"base": float("nan")}, {}),
                                ({"base": float("inf")}, {})):
            with self.subTest(current=current, target=target):
                with self.assertRaises(ValueError):
                    plan_moves(current, target)


class MoveTests(unittest.TestCase):
    def test_labels_match_the_typed_confirmation_format(self):
        self.assertEqual(Move("base", -1.0).label, "BASE -1")
        self.assertEqual(Move("elbow", 0.5).label, "ELBOW +0.5")

    def test_apply_returns_a_new_dict(self):
        travel = dict(ZERO)
        new = apply(travel, Move("base", -1.0))
        self.assertEqual(new["base"], -1.0)
        self.assertEqual(travel["base"], 0.0)
        with self.assertRaises(ValueError):
            apply(travel, Move("gripper", 1.0))

    def test_marked_position_is_frozen(self):
        mark = MarkedPosition("P1", dict(ZERO), {"base": 5})
        with self.assertRaises(dataclasses.FrozenInstanceError):
            mark.name = "P2"
        self.assertEqual(MAX_MARKS, 9)


if __name__ == "__main__":
    unittest.main()
