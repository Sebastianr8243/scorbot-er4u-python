"""Coupled joint moves on the facade: the pre-home move and the park, against the simulator.

The simulator obeys jogs exactly and has no stops or coupling of its own, so these tests check
the plumbing and the gates, and that the motors asked for are the ones the vendor's joint
formulas call for. Whether the wrist signs are right on the arm is for the first supervised
trial (docs/lab/SESSION_PROCESS.md). No USB.
"""

import unittest
from unittest.mock import patch

from scorbot import MotionStopped, ScorbotError, joint_move, source_model

try:
    from tests.sim_support import SimulatedRobotCase
except ImportError:      # discovered with -s tests: the folder itself is on sys.path
    from sim_support import SimulatedRobotCase

HOME = source_model.HOME_ANGLES
MOTORS = source_model.MOTORS
JOG_ORDERS = set(range(4, 14))
ALLOWED_ORDERS = set(range(4, 12))          # base, shoulder, elbow and wrist pitch; never roll


class ProportionalJogsTests(unittest.TestCase):
    def test_a_long_move_is_cut_into_steps_that_keep_the_motors_in_proportion(self):
        errors = {"base": 0.0, "shoulder": 2400.0, "elbow": -2400.0, "wrist_pitch": -590.0}
        limits = {"base": 150, "shoulder": 120, "elbow": 120, "wrist_pitch": 80}
        jogs = dict(joint_move.proportional_jogs(errors, limits))
        # The shoulder is the limiting motor (120 of 2400); the others follow at the same pace.
        self.assertEqual(jogs["shoulder"], 120)
        self.assertEqual(jogs["elbow"], -120)
        self.assertAlmostEqual(jogs["wrist_pitch"] / jogs["shoulder"], -590 / 2400, delta=0.01)
        self.assertNotIn("base", jogs)

    def test_a_small_error_is_done_in_one_step_and_dust_is_skipped(self):
        errors = {"base": 0.0, "shoulder": 70.0, "elbow": -70.0, "wrist_pitch": 5.0}
        limits = {"base": 150, "shoulder": 120, "elbow": 120, "wrist_pitch": 80}
        jogs = dict(joint_move.proportional_jogs(errors, limits))
        self.assertEqual(jogs, {"shoulder": 70, "elbow": -70})

    def test_nothing_to_do_gives_nothing(self):
        errors = dict.fromkeys(("base", "shoulder", "elbow", "wrist_pitch"), 3.0)
        self.assertEqual(joint_move.proportional_jogs(errors, {k: 100 for k in errors}), [])


class CoupledMoveTests(SimulatedRobotCase):
    HOMED = False

    def angles(self, robot):
        counts = robot.get_state().signed_encoder_counts
        return source_model.angles_from_counts({m: counts[m] for m in MOTORS})

    def assert_only(self, robot, joint, degrees, *, tolerance=1.0):
        after = self.angles(robot)
        for name in ("base", "shoulder", "elbow", "pitch", "roll"):
            expected = HOME[name] + (degrees if name == joint else 0.0)
            self.assertAlmostEqual(after[name], expected, delta=tolerance, msg=name)

    def test_a_shoulder_move_keeps_the_elbow_and_the_wrist_pitch_where_they_were(self):
        robot = self.robot()
        robot.pre_home_jog("shoulder", 2.0, operator_at_stop=True)
        self.assert_only(robot, "shoulder", 2.0)

    def test_an_elbow_move_keeps_the_wrist_pitch_where_it_was(self):
        robot = self.robot()
        robot.pre_home_jog("elbow", -1.5, operator_at_stop=True)
        self.assert_only(robot, "elbow", -1.5)

    def test_a_base_move_moves_only_the_base(self):
        robot = self.robot()
        before = robot.get_state().signed_encoder_counts
        robot.pre_home_jog("base", 2.0, operator_at_stop=True)
        after = robot.get_state().signed_encoder_counts
        self.assertGreater(abs(after["base"] - before["base"]), 200)
        for motor in ("shoulder", "elbow", "wrist_motor_1", "wrist_motor_2"):
            self.assertEqual(after[motor], before[motor], motor)

    def test_a_sequence_of_small_moves_does_not_drift(self):
        robot = self.robot()
        for _ in range(12):
            robot.pre_home_jog("shoulder", 0.5, operator_at_stop=True)
        for _ in range(6):
            robot.pre_home_jog("elbow", 1.0, operator_at_stop=True)
        after = self.angles(robot)
        self.assertAlmostEqual(after["shoulder"], HOME["shoulder"] + 6.0, delta=1.0)
        self.assertAlmostEqual(after["elbow"], HOME["elbow"] + 6.0, delta=1.0)
        self.assertAlmostEqual(after["pitch"], HOME["pitch"], delta=1.5)

    def test_only_wrist_pitch_ever_moves_and_never_the_roll_or_a_legacy_home(self):
        robot = self.robot()
        before = len(robot.sim.commands)
        robot.pre_home_jog("shoulder", 2.0, operator_at_stop=True)
        robot.pre_home_jog("elbow", 2.0, operator_at_stop=True)
        orders = {c[0] for c in robot.sim.commands[before:] if c}
        self.assertTrue(orders & {10, 11}, "the wrist pitch followed")
        self.assertTrue(orders <= ALLOWED_ORDERS, orders)        # no 12 or 13 (roll), no 18

    def test_the_public_jog_still_refuses_the_wrist_and_the_private_one_refuses_roll(self):
        robot = self.robot()
        queued = list(robot.sim.commands)
        for joint in ("wrist_pitch", "wrist_roll", "wrist_motor_1"):
            with self.subTest(joint=joint), self.assertRaises((ScorbotError, ValueError)):
                robot.jog_joint(joint, 1.0)
        with self.assertRaises(ScorbotError):
            robot._jog_joint("wrist_roll", 1.0, 6, homing=True, wrist_ok=True)
        with self.assertRaises(ScorbotError):
            robot._jog_joint("wrist_pitch", 1.0, 6, homing=True)         # wrist_ok not given
        self.assertEqual(robot.sim.commands, queued)

    def test_the_gates_queue_nothing(self):
        robot = self.robot()
        queued = list(robot.sim.commands)
        bad = (
            ({"joint": "shoulder", "degrees": 1.0}, ValueError),              # no operator word
            ({"joint": "wrist_pitch", "degrees": 1.0, "operator_at_stop": True}, ValueError),
            ({"joint": "gripper", "degrees": 1.0, "operator_at_stop": True}, ValueError),
            ({"joint": "shoulder", "degrees": 0, "operator_at_stop": True}, ValueError),
            ({"joint": "shoulder", "degrees": 2.5, "operator_at_stop": True}, ValueError),
            ({"joint": "shoulder", "degrees": float("nan"), "operator_at_stop": True}, ValueError),
            ({"joint": "shoulder", "degrees": True, "operator_at_stop": True}, ValueError),
            ({"joint": "shoulder", "degrees": 1.0, "operator_at_stop": True, "speed": 0},
             ValueError),
        )
        for kwargs, error in bad:
            with self.subTest(kwargs=kwargs), self.assertRaises(error):
                robot.pre_home_jog(**kwargs)
        self.assertEqual(robot.sim.commands, queued)
        self.assertIsNone(robot._fault)

    def test_it_needs_enabled_motors_and_refuses_a_homed_arm(self):
        robot = self.robot()
        robot.disable()
        with self.assertRaises(ScorbotError):
            robot.pre_home_jog("base", 1.0, operator_at_stop=True)
        robot.enable()
        robot.home(start_position_confirmed=True)
        with self.assertRaisesRegex(ScorbotError, "homed"):
            robot.pre_home_jog("base", 1.0, operator_at_stop=True)

    def test_each_joint_has_a_cumulative_travel_cap_and_the_other_way_is_open(self):
        robot = self.robot()
        for _ in range(30):
            robot.pre_home_jog("base", 2.0, operator_at_stop=True)       # 60 degrees
        with self.assertRaisesRegex(ValueError, "cap"):
            robot.pre_home_jog("base", 1.0, operator_at_stop=True)
        robot.pre_home_jog("base", -2.0, operator_at_stop=True)          # back toward the start
        robot.pre_home_jog("shoulder", 1.0, operator_at_stop=True)       # another joint is open
        robot.disable()
        robot.enable()
        robot.pre_home_jog("base", 1.0, operator_at_stop=True)           # disable resets the cap

    def test_a_stop_request_ends_it_without_a_fault(self):
        robot = self.robot()
        robot.request_stop()
        with self.assertRaises(MotionStopped):
            robot.pre_home_jog("shoulder", 2.0, operator_at_stop=True)
        self.assertIsNone(robot._fault)
        robot.pre_home_jog("shoulder", 1.0, operator_at_stop=True)       # the next one runs

    def test_a_joint_that_does_not_follow_faults_before_the_next_jog(self):
        robot = self.robot()
        with patch.object(robot, "_command") as command:
            with self.assertRaisesRegex(ScorbotError, "did not follow"):
                robot.pre_home_jog("shoulder", 2.0, operator_at_stop=True)
        jogs = [c for c in command.call_args_list if c.args[0][0] in JOG_ORDERS]
        self.assertEqual(len(jogs), 1)
        self.assertIsNotNone(robot._fault)
        self.assertIn("pre_home_failed", self.events())

    def test_a_controller_error_latches_and_names_the_joint(self):
        robot = self.robot()
        robot.sim.inject("controller_error")
        with self.assertRaises(ScorbotError):
            robot.pre_home_jog("elbow", 2.0, operator_at_stop=True)
        self.assertIsNotNone(robot._fault)
        row = [r for r in self.rows() if r.get("event") == "pre_home_failed"][0]
        self.assertEqual(row["joint"], "elbow")

    def test_it_records_where_it_started_and_where_it_ended(self):
        robot = self.robot()
        robot.pre_home_jog("shoulder", 1.0, operator_at_stop=True)
        events = self.events()
        self.assertIn("pre_home_start", events)
        self.assertIn("pre_home_complete", events)


class ParkTests(SimulatedRobotCase):
    def angles(self, robot):
        counts = robot.get_state().signed_encoder_counts
        home = robot._home_counts
        return source_model.angles_from_counts(
            {m: counts[m] - home[m] for m in MOTORS})

    def moved_robot(self):
        robot = self.robot()                                   # enabled and homed
        for joint, degrees in (("base", 5.0), ("base", 5.0), ("elbow", -4.0), ("elbow", -4.0),
                               ("shoulder", -5.0), ("shoulder", -5.0)):
            robot.jog_joint(joint, degrees)
        return robot

    def test_park_brings_every_motor_back_to_the_home_counts(self):
        robot = self.moved_robot()
        counts = robot.get_state().signed_encoder_counts
        self.assertGreater(abs(counts["base"] - robot._home_counts["base"]), 1000)
        before = len(robot.sim.commands)
        result = robot.park_at_home(operator_at_stop=True)
        self.assertTrue(result["parked"], result)
        after = robot.get_state().signed_encoder_counts
        for motor in MOTORS:
            self.assertLessEqual(abs(after[motor] - robot._home_counts[motor]), 45, motor)
        self.assertIn("parked", self.events())
        orders = {c[0] for c in robot.sim.commands[before:] if c}
        self.assertTrue(orders <= ALLOWED_ORDERS, orders)

    def test_park_at_home_when_already_there_does_nothing(self):
        robot = self.robot()
        before = len(robot.sim.commands)
        result = robot.park_at_home(operator_at_stop=True)
        self.assertTrue(result["parked"])
        self.assertEqual(robot.sim.commands[before:], [])

    def test_park_needs_a_home_the_operator_and_enabled_motors(self):
        robot = self.robot()
        queued = list(robot.sim.commands)
        with self.assertRaises(ValueError):
            robot.park_at_home()
        robot.disable()
        with self.assertRaises(ScorbotError):
            robot.park_at_home(operator_at_stop=True)                   # not enabled, no home
        self.assertEqual([c for c in robot.sim.commands[len(queued):] if c and c[0] != 16], [])

    def test_a_stop_request_ends_the_park_without_a_fault_and_says_it_is_not_parked(self):
        robot = self.moved_robot()
        robot.request_stop()
        with self.assertRaises(MotionStopped):
            robot.park_at_home(operator_at_stop=True)
        self.assertIsNone(robot._fault)
        self.assertTrue(robot._homed)

    def test_a_joint_that_does_not_follow_faults_the_park(self):
        robot = self.moved_robot()
        with patch.object(robot, "_command"):
            with self.assertRaisesRegex(ScorbotError, "did not follow"):
                robot.park_at_home(operator_at_stop=True)
        self.assertIsNotNone(robot._fault)


if __name__ == "__main__":
    unittest.main()
