"""scorbot/policy_arm.py: PolicyArm on the simulated controller (no USB, no arm)."""

from dataclasses import replace
from importlib.util import find_spec
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from scorbot import SimulatedScorbot
from scorbot.policy_arm import LEAD_MARGIN_DEG, MAX_REUSE_AGE_S, STEP_MAX_DEG, PolicyArm
from scorbot.robot import ScorbotError


@unittest.skipUnless(find_spec("ruckig"), "needs the planning extra")
class PolicyArmTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        robot = SimulatedScorbot(log_path=Path(tmp.name) / "e.jsonl", robot_id="t")
        self.arm = PolicyArm(robot, travel_cap_deg=2.0)
        self.arm.connect(operator_at_stop=True, jaws_empty=True, wrist_clear=True)     # the simulator; the confirmations are this test's
        self.addCleanup(self.arm.close)

    def test_starts_at_home(self):
        self.assertTrue(self.arm.is_connected)
        self.assertEqual(self.arm.observe(), {"base": 0.0, "shoulder": 0.0, "elbow": 0.0})

    def test_a_far_goal_moves_one_clamped_step(self):
        self.assertEqual(self.arm.act({"base": 90.0})["base"], STEP_MAX_DEG)

    def test_goal_is_held_inside_the_travel_cap(self):
        for _ in range(10):
            target = self.arm.act({"base": 90.0})
        self.assertLessEqual(target["base"], 2.0)

    def test_speed_is_the_operators_percent_and_can_change_live(self):
        from scorbot.streaming import VENDOR_MAX_SPEED_COUNTS_S
        speed = lambda: self.arm._stream.core._input.max_velocity[0]    # noqa: E731
        self.assertEqual(self.arm.speed_pct, 25)
        self.assertAlmostEqual(speed(), VENDOR_MAX_SPEED_COUNTS_S * 0.25)
        self.arm.set_speed(100)
        self.assertEqual(self.arm.speed_pct, 100)
        self.assertAlmostEqual(speed(), VENDOR_MAX_SPEED_COUNTS_S)
        for bad in (0, 101, -5, float("nan"), True, "50"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.arm.set_speed(bad)
        self.assertEqual(self.arm.speed_pct, 100)

    def test_bad_values_and_unknown_motors_hold(self):
        target = self.arm.act({"base": float("nan"), "shoulder": "x", "wrist": 1.0, "elbow": True})
        self.assertEqual(target, {"base": 0.0, "shoulder": 0.0, "elbow": 0.0})

    def test_unavailable_feedback_latches_the_session(self):
        self.arm.robot.sim.inject("stale_feedback")
        with self.assertRaises(ScorbotError):
            self.arm.observe()
        self.assertIsNotNone(self.arm.robot._fault)
        try:
            self.arm.close()     # a faulted stream reports its fault here; the fault is what this test checks
        except Exception:
            pass

    def slow_replies(self, delay_s=0.024, base_counts=None):
        """Each controller read waits ``delay_s`` (one stream period) like a real fresh reply; returns the
        read count. ``base_counts`` (a list) overrides the measured base, one value per read."""
        real, reads = self.arm.robot._motion_state, []

        def read(**kw):
            time.sleep(delay_s)
            state = real(**kw)
            if base_counts:
                state = replace(state, encoder_counts={**state.encoder_counts, "base": base_counts.pop(0) % 65535})    # one's-complement wrap
            reads.append(state)
            return state
        patcher = mock.patch.object(self.arm.robot, "_motion_state", side_effect=read)
        patcher.start()
        self.addCleanup(patcher.stop)
        return reads

    def test_observe_then_act_waits_for_one_reply_not_two(self):
        reads = self.slow_replies()
        for _ in range(3):
            self.arm.observe()
            self.arm.act({"base": 90.0})
        self.assertEqual(len(reads), 3)

    def test_act_reads_fresh_when_nothing_was_observed_since_the_last_act(self):
        reads = self.slow_replies()
        self.arm.observe()
        self.arm.act({"base": 90.0})
        self.arm.act({"base": 90.0})
        self.assertEqual(len(reads), 2)

    def test_act_uses_the_newest_observation(self):
        per = self.arm._per_degree["base"]
        reads = self.slow_replies(base_counts=[self.arm._home["base"], self.arm._home["base"] - round(1.5 * per)])
        self.arm.observe()
        newest = self.arm.observe()["base"]
        target = self.arm.act({"base": 90.0})["base"]
        self.assertEqual(len(reads), 2)
        self.assertLessEqual(target, newest + LEAD_MARGIN_DEG + 1e-9)

    def test_a_stale_reading_never_widens_the_lead_clamp(self):
        # observe sees home, act (holding) uses it; the arm then reads 1.5 deg back. The next act must
        # clamp against that fresh reading (target about 0), not the stale home one (which allows 0.5).
        per = self.arm._per_degree["base"]
        home = self.arm._home["base"]
        reads = self.slow_replies(base_counts=[home, home - round(1.5 * per)])
        self.arm.observe()
        self.arm.act({"base": 0.0})
        target = self.arm.act({"base": 90.0})["base"]
        self.assertEqual(len(reads), 2)
        fresh = (-round(1.5 * per)) / per
        self.assertLessEqual(target, fresh + LEAD_MARGIN_DEG + 1e-9)

    def test_act_reads_fresh_when_the_observation_is_older_than_the_reuse_bound(self):
        # A slow policy (VLA inference) between observe and act: past the bound act reads again.
        reads = self.slow_replies(delay_s=0)
        clock = [1000.0]
        with mock.patch("scorbot.policy_arm.monotonic", side_effect=lambda: clock[0]):
            self.arm.observe()
            clock[0] += MAX_REUSE_AGE_S + 0.001
            self.arm.act({"base": 90.0})
            self.assertEqual(len(reads), 2)
            self.arm.observe()
            clock[0] += MAX_REUSE_AGE_S - 0.001      # just inside the bound: reused
            self.arm.act({"base": 90.0})
            self.assertEqual(len(reads), 3)

    def test_act_on_a_reused_reading_still_refuses_a_session_faulted_since(self):
        self.arm.observe()
        self.arm.robot._fault = "faulted between observe and act (test)"
        with self.assertRaises(ScorbotError):
            self.arm.act({"base": 1.0})
        try:
            self.arm.close()     # a faulted stream reports its fault here; the refusal is what this test checks
        except Exception:
            pass

    def test_close_is_safe_twice_and_act_after_close_fails(self):
        self.arm.close()
        self.arm.close()
        self.assertFalse(self.arm.is_connected)
        with self.assertRaises(ConnectionError):
            self.arm.act({"base": 1.0})


WRIST = ("wrist_motor_1", "wrist_motor_2")
JOINTS5 = ("base", "shoulder", "elbow", "pitch", "roll")


@unittest.skipUnless(find_spec("ruckig"), "needs the planning extra")
class PolicyArmWristTests(unittest.TestCase):
    """``wrist=True``: pitch and roll from the two coupled wrist motors (source model, not measured)."""

    def arm(self, **kw):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        robot = SimulatedScorbot(log_path=Path(tmp.name) / "e.jsonl", robot_id="t")
        arm = PolicyArm(robot, **{"travel_cap_deg": 2.0, "wrist": True, **kw})
        arm.connect(operator_at_stop=True, jaws_empty=True, wrist_clear=True)     # the simulator
        self.addCleanup(arm.close)
        return arm

    @staticmethod
    def wrist_target(arm):
        return {m: arm._stream.core._target[m] for m in WRIST}

    def settle(self, arm, goal, calls=40):
        for _ in range(calls):
            target = arm.act(goal)
            time.sleep(0.03)
        return target

    def test_off_by_default_the_wrist_is_not_streamed(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        arm = PolicyArm(SimulatedScorbot(log_path=Path(tmp.name) / "e.jsonl", robot_id="t"))
        arm.connect(operator_at_stop=True, jaws_empty=True, wrist_clear=True)     # the simulator
        self.addCleanup(arm.close)
        self.assertEqual(arm._stream.core.motors, ("base", "shoulder", "elbow"))
        self.assertEqual(arm.act({"pitch": 1.0, "roll": 1.0}), {"base": 0.0, "shoulder": 0.0, "elbow": 0.0})

    def test_starts_at_home_with_pitch_and_roll(self):
        arm = self.arm()
        self.assertEqual(arm.observe(), dict.fromkeys(JOINTS5, 0.0))
        self.assertEqual(arm._stream.core.motors, ("base", "shoulder", "elbow") + WRIST)

    def test_pitch_turns_the_wrist_motors_apart_and_roll_together(self):
        # Source model signs: +pitch (lifting) is wrist motor 1 down, motor 2 up; +roll is both up.
        arm = self.arm()
        self.assertEqual(arm.act({"pitch": 90.0})["pitch"], STEP_MAX_DEG)
        m1, m2 = self.wrist_target(arm).values()
        self.assertLess(m1, 0)
        self.assertEqual(m2, -m1)
        self.assertAlmostEqual(m2, STEP_MAX_DEG * 27.9, delta=1)
        arm = self.arm()
        self.assertEqual(arm.act({"roll": 90.0})["roll"], STEP_MAX_DEG)
        m1, m2 = self.wrist_target(arm).values()
        self.assertGreater(m1, 0)
        self.assertEqual(m1, m2)

    def test_the_measured_wrist_comes_back_as_the_commanded_pitch_and_roll(self):
        arm = self.arm()
        self.settle(arm, {"pitch": 1.0, "roll": -1.5})
        seen = arm.observe()
        self.assertAlmostEqual(seen["pitch"], 1.0, delta=0.08)     # a count is 0.036 degree
        self.assertAlmostEqual(seen["roll"], -1.5, delta=0.08)
        self.assertEqual((seen["base"], seen["shoulder"], seen["elbow"]), (0.0, 0.0, 0.0))

    def test_an_arm_only_action_never_moves_the_wrist_motors(self):
        arm = self.arm()
        self.settle(arm, {"shoulder": 2.0, "elbow": -2.0}, calls=15)
        self.assertEqual(set(self.wrist_target(arm).values()), {0.0})
        self.assertEqual({step[3:] for step in arm.robot.sim.stream_sent}, {(0, 0)})
        seen = arm.observe()
        self.assertEqual((seen["pitch"], seen["roll"]), (0.0, 0.0))

    def test_pitch_and_roll_at_the_cap_together_stay_inside_every_limit(self):
        arm = self.arm()
        for goal in ({"pitch": 9.0, "roll": 9.0}, {"pitch": -9.0, "roll": 9.0}, {"pitch": -9.0, "roll": -9.0}):
            target = self.settle(arm, goal, calls=12)
            self.assertEqual((abs(target["pitch"]), abs(target["roll"])), (2.0, 2.0))
            seen = arm.observe()
            self.assertLessEqual(max(abs(seen["pitch"]), abs(seen["roll"])), 2.0 + 0.08)
        self.assertIsNone(arm.robot._fault)

    def test_the_wrist_never_leads_the_measured_pitch_and_roll_past_the_margin(self):
        from scorbot.policy_arm import LEAD_MARGIN_DEG
        arm = self.arm()
        arm.robot.sim.stream_stuck = True
        for _ in range(8):
            target = arm.act({"pitch": 90.0, "roll": 90.0})
        self.assertEqual((target["pitch"], target["roll"]), (LEAD_MARGIN_DEG, LEAD_MARGIN_DEG))
        time.sleep(0.2)
        self.assertIsNone(arm.robot._fault)     # one motor carries both leads; the stream's limit allows it

    def test_the_speed_step_shares_one_wrist_motor_between_pitch_and_roll(self):
        from scorbot import policy_arm
        from scorbot.streaming import VENDOR_MAX_SPEED_COUNTS_S
        now = [100.0]
        with mock.patch.object(policy_arm, "monotonic", lambda: now[0]):
            arm = self.arm(travel_cap_deg=30.0, step_limit="speed")
            now[0] += 0.05
            arm.act({"pitch": 90.0, "roll": 90.0})
        budget = VENDOR_MAX_SPEED_COUNTS_S * 0.25 * 0.05
        self.assertLessEqual(max(abs(v) for v in self.wrist_target(arm).values()), budget + 1)
        self.assertGreater(max(abs(v) for v in self.wrist_target(arm).values()), 0.9 * budget)


if __name__ == "__main__":
    unittest.main()
