"""TargetFollower: bounded steps toward LeRobot-style targets, on the simulator."""

from importlib.util import find_spec
import time
import unittest
from unittest import mock

from scorbot.simulated import SimulatedScorbot


def target(base=0.0, shoulder=0.0, elbow=0.0, wrist=0.0):
    return {"base": float(base), "shoulder": float(shoulder), "elbow": float(elbow),
            "wrist_motor_1": float(wrist), "wrist_motor_2": 0.0}


def pin_the_cap(case):
    """Hold the follower's cap at 10 degrees for one test case.

    These tests exercise the cap mechanism, not its production value
    (limits.TRAVEL_CAP_DEG, lifted from 10 to 180 on 2026-10-06).
    """
    for name in ("scorbot.follow.TRAVEL_CAP_DEG", "scorbot.lab.session.TRAVEL_CAP_DEG"):
        patcher = mock.patch(name, 10.0)      # both, so the two stay the same limit
        patcher.start()
        case.addCleanup(patcher.stop)


class FollowTests(unittest.TestCase):
    def setUp(self):
        pin_the_cap(self)
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


@unittest.skipUnless(find_spec("ruckig"), "ruckig not installed (pip install .[planning])")
class StreamFollowTests(unittest.TestCase):
    """The same follower interface on the streaming driver: a policy can call it at any rate."""

    def setUp(self):
        from scorbot.follow import StreamFollower
        from scorbot.simulated import SimulatedController
        self.robot = SimulatedScorbot(controller=SimulatedController(step_delay_s=0.001)).connect()
        self.robot.enable()
        self.robot.home(start_position_confirmed=True)
        # The stream's own cap, held at 10 degrees: the test exercises the
        # cap mechanism, not the production value (180 since 2026-10-06).
        self.follower = StreamFollower(self.robot, self.robot.get_state().encoder_counts,
                                       travel_cap_deg=10.0)
        self.addCleanup(self.shutdown)

    def shutdown(self):
        from scorbot import ScorbotError
        for end in (self.follower.close, self.robot.disconnect):
            try:
                end()
            except ScorbotError:
                pass               # a faulted session reports it again on the way out
        self.follower = self.robot = None

    def run_policy(self, policy, seconds=4.0, rate_hz=30.0):
        """Observe, ask the policy for an action, send it; as a policy loop would."""
        observation = self.follower.observe()
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            action = policy(observation)
            if action is None:
                break
            self.follower.step_toward(action)
            time.sleep(1.0 / rate_hz)
            observation = self.follower.observe()
        return observation

    def test_a_policy_loop_brings_three_motors_to_a_goal_at_once(self):
        goal = target(base=400, shoulder=-250, elbow=180)

        def policy(observation):
            near = all(abs(observation[m] - goal[m]) <= 1 for m in ("base", "shoulder", "elbow"))
            return None if near else goal

        final = self.run_policy(policy)
        for motor in ("base", "shoulder", "elbow"):       # the policy stops within a count
            self.assertAlmostEqual(final[motor], goal[motor], delta=1, msg=motor)
        self.assertEqual((final["wrist_motor_1"], final["wrist_motor_2"]), (0.0, 0.0))
        self.assertIsNone(self.robot._fault)

    def test_a_target_that_changes_every_step_is_followed_without_stopping(self):
        # What a learned policy does: a slightly different target on every call.
        steps = iter(range(1, 61))

        def policy(_observation):
            step = next(steps, None)
            return None if step is None else target(base=5 * step)

        self.run_policy(policy, rate_hz=50.0)
        final = self.run_policy(lambda obs: None if abs(obs["base"] - 300) <= 1
                                else target(base=300))
        self.assertAlmostEqual(final["base"], 300.0, delta=1)
        self.assertIsNone(self.robot._fault)

    def test_the_command_is_echoed_back_like_the_step_follower_does(self):
        commanded = self.follower.step_toward(target(base=120, elbow=-60))
        self.assertEqual((commanded["base"], commanded["shoulder"], commanded["elbow"]),
                         (120.0, 0.0, -60.0))
        self.assertEqual(set(commanded), set(target()))

    def test_wrist_targets_and_targets_past_the_cap_are_refused(self):
        from scorbot.follow import FollowRefused
        with self.assertRaisesRegex(FollowRefused, "wrist"):
            self.follower.step_toward(target(wrist=200))
        with self.assertRaisesRegex(FollowRefused, "travel cap"):
            self.follower.step_toward(target(base=5000))       # the pinned 10 degrees is 1420 counts
        self.assertIsNone(self.robot._fault, "a refused target is not a fault")
        self.follower.step_toward(target(base=50))             # and the stream carries on

    def test_closing_ends_the_stream_and_leaves_the_session_usable(self):
        self.follower.step_toward(target(base=60))
        self.follower.close()
        self.follower.close()                                   # idempotent
        self.assertIsNone(self.robot._stream)
        self.robot.jog_joint("base", 1.0)

    def test_a_faulted_stream_refuses_further_actions(self):
        from scorbot.follow import FollowRefused
        self.robot.sim.stream_error_word = 40
        deadline = time.monotonic() + 3
        with self.assertRaises(FollowRefused):
            while time.monotonic() < deadline:
                self.follower.step_toward(target(base=100))
                time.sleep(0.01)
        self.assertIsNotNone(self.robot._fault)


if __name__ == "__main__":
    unittest.main()
