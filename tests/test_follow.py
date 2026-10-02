"""TargetFollower: bounded steps toward LeRobot-style targets, on the simulator."""

import unittest

from scorbot.simulated import SimulatedScorbot


def target(base=0.0, shoulder=0.0, elbow=0.0, wrist=0.0):
    return {"base": float(base), "shoulder": float(shoulder), "elbow": float(elbow),
            "wrist_motor_1": float(wrist), "wrist_motor_2": 0.0}


class FollowTests(unittest.TestCase):
    def setUp(self):
        from scorbot.follow import TargetFollower
        self.robot = SimulatedScorbot().connect()
        self.robot.enable()
        self.robot.home(start_position_confirmed=True)
        home = self.robot.get_state().encoder_counts
        self.follower = TargetFollower(self.robot, home)
        self.base = self.follower.step_counts["base"]

    def tearDown(self):
        self.robot.disconnect()

    def test_one_step_toward_the_largest_error(self):
        commanded = self.follower.step_toward(target(base=-3 * self.base, shoulder=40))
        self.assertEqual(self.follower.observe()["base"], -self.base)
        self.assertEqual(commanded["base"], -self.base)
        self.assertEqual(commanded["shoulder"], 0.0)

    def test_still_within_half_a_step(self):
        commanded = self.follower.step_toward(target(base=self.base * 0.4))
        self.assertEqual(self.follower.observe()["base"], 0.0)
        self.assertEqual(commanded["base"], 0.0)

    def test_wrist_target_refused(self):
        from scorbot.follow import FollowRefused
        with self.assertRaisesRegex(FollowRefused, "wrist"):
            self.follower.step_toward(target(wrist=200))

    def test_cap_refused_before_queuing(self):
        from scorbot.follow import FollowRefused
        for _ in range(10):
            self.follower.step_toward(target(base=-20 * self.base))
        jogs_before = len(self.robot.sim.commands)
        with self.assertRaisesRegex(FollowRefused, "cap"):
            self.follower.step_toward(target(base=-20 * self.base))
        self.assertEqual(len(self.robot.sim.commands), jogs_before)

    def test_drift_refused(self):
        from scorbot.follow import FollowRefused
        self.follower.step_toward(target(base=-self.base))
        self.robot.sim.counts["base"] -= 500      # something moved the arm unprompted
        with self.assertRaisesRegex(FollowRefused, "drift"):
            self.follower.step_toward(target(base=-self.base))

    def test_limits_are_the_lab_limits(self):
        from scorbot import follow
        from scorbot.lab import session
        self.assertEqual((follow.TRAVEL_CAP_DEG, follow.DRIFT_COUNTS),
                         (session.TRAVEL_CAP_DEG, session.DRIFT_COUNTS))

    def test_follows_a_sequence_to_its_end(self):
        for goal in (target(base=-self.base), target(base=-2 * self.base),
                     target(base=-2 * self.base, elbow=self.follower.step_counts["elbow"])):
            self.follower.step_toward(goal)
        seen = self.follower.observe()
        self.assertEqual((seen["base"], seen["elbow"]),
                         (-2 * self.base, self.follower.step_counts["elbow"]))


if __name__ == "__main__":
    unittest.main()
