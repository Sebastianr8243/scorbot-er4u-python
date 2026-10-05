"""scorbot.streaming.StreamCore: the streaming logic on its own, no USB, no robot.

Each test names the requirement it covers (R1-R12 in
docs/specs/2026-10-04-streaming-driver-requirements.md).
"""

import importlib.util
import random
import unittest

HAS_RUCKIG = importlib.util.find_spec("ruckig") is not None
PERIOD = 0.024
CAP = {"base": 1400.0, "shoulder": 1100.0, "elbow": 1100.0}
LEAD = {"base": 300.0, "shoulder": 300.0, "elbow": 300.0}
ZERO = {"base": 0, "shoulder": 0, "elbow": 0}


def make(**kwargs):
    from scorbot.streaming import StreamCore
    settings = {"travel_cap": CAP, "lead_limit": LEAD, "period_s": PERIOD}
    settings.update(kwargs)
    return StreamCore(dict(settings.pop("start", ZERO)), **settings)


class Arm:
    """A perfect follower by default; ``follow`` < 1 lags, ``stuck`` does not move."""

    def __init__(self, follow=1.0, stuck=False):
        self.counts = dict(ZERO)
        self.follow, self.stuck = follow, stuck

    def apply(self, commanded):
        if not self.stuck:
            for motor, value in commanded.items():
                self.counts[motor] += round((value - self.counts[motor]) * self.follow)


def run(core, arm, steps, start=0, on_step=None):
    """Drive ``steps`` periods; return the Step records."""
    records = []
    for index in range(start, start + steps):
        now = index * PERIOD
        if on_step:
            on_step(index, now)
        step = core.step(arm.counts, now)
        records.append(step)
        if step.action == "send":
            arm.apply(step.commanded)
        if step.action in ("stop", "end"):
            break
    return records


def keep_alive(core, target):
    return lambda index, now: core.set_target(target, now)


@unittest.skipUnless(HAS_RUCKIG, "ruckig not installed (pip install .[planning])")
class StreamCoreTests(unittest.TestCase):
    def test_r1_reaches_a_target_and_holds_it(self):
        core, arm = make(), Arm()
        target = {"base": 142, "shoulder": -60, "elbow": 30}
        records = run(core, arm, 200, on_step=keep_alive(core, target))
        self.assertEqual(records[-1].commanded, target)
        self.assertEqual(arm.counts, target)
        self.assertEqual({r.action for r in records}, {"send"})

    def test_r1_a_new_target_mid_move_bends_the_motion_without_stopping(self):
        core, arm = make(), Arm()
        first, second = {"base": 600}, {"base": -300}
        records = run(core, arm, 12, on_step=keep_alive(core, first))
        moving = records[-1].commanded["base"]
        self.assertGreater(moving, 0)
        self.assertLess(moving, 600)
        records += run(core, arm, 300, start=12, on_step=keep_alive(core, second))
        positions = [r.commanded["base"] for r in records]
        self.assertEqual(positions[-1], -300)
        # it keeps moving through the change: no repeated position right after it
        self.assertNotEqual(positions[12], positions[13])
        self.assertGreater(max(positions), moving - 1)     # coasts on a little, then turns
        self.assertLess(max(positions), 600)

    def test_r2_every_step_respects_speed_acceleration_and_jerk(self):
        from scorbot.streaming import prior_limits
        limits = prior_limits()
        rng = random.Random(5)
        core, arm = make(), Arm()
        target = dict(ZERO)

        def retarget(index, now):
            if index % 9 == 0:
                for motor in target:
                    target[motor] = rng.randint(-1000, 1000)
            core.set_target(target, now)

        records = run(core, arm, 600, on_step=retarget)
        for motor in ZERO:
            positions = [r.commanded[motor] for r in records]
            velocity = [(b - a) / PERIOD for a, b in zip(positions, positions[1:])]
            acceleration = [(b - a) / PERIOD for a, b in zip(velocity, velocity[1:])]
            # one count of rounding per sample is allowed on top of each limit
            self.assertLessEqual(max(map(abs, velocity)), limits.max_velocity + 1 / PERIOD)
            self.assertLessEqual(max(map(abs, acceleration)),
                                 limits.max_acceleration + 2 / PERIOD ** 2)
            jerk = [(b - a) / PERIOD for a, b in zip(acceleration, acceleration[1:])]
            self.assertLessEqual(max(map(abs, jerk)), limits.max_jerk + 4 / PERIOD ** 3)

    def test_r3_r6_an_arm_that_does_not_follow_stops_the_stream_with_a_fault(self):
        core, arm = make(), Arm(stuck=True)
        records = run(core, arm, 400, on_step=lambda i, now: core.state == "tracking"
                      and core.set_target({"base": 1000}, now))
        last = records[-1]
        self.assertEqual(last.action, "stop")
        self.assertEqual(core.state, "faulted")
        self.assertIn("not following", core.fault)
        # nothing that was sent led the arm by more than the limit, not even by a step
        sent = [r for r in records if r.action == "send"]
        self.assertTrue(sent)
        self.assertLessEqual(max(abs(r.commanded["base"] - r.measured["base"]) for r in sent),
                             LEAD["base"])
        self.assertLessEqual(max(abs(r.lead["base"]) for r in records), LEAD["base"])
        self.assertEqual(core.step(arm.counts, 99.0).action, "stop")

    def test_r4_target_outside_the_travel_cap_is_refused_and_changes_nothing(self):
        from scorbot.streaming import StreamRefused
        core, arm = make(), Arm()
        run(core, arm, 5, on_step=keep_alive(core, {"base": 100}))
        with self.assertRaises(StreamRefused):
            core.set_target({"base": 1401}, 1.0)
        with self.assertRaises(StreamRefused):
            core.set_target({"base": 50, "shoulder": -1101}, 1.0)
        records = run(core, arm, 200, start=5, on_step=keep_alive(core, {"base": 100}))
        self.assertEqual(records[-1].commanded, {"base": 100, "shoulder": 0, "elbow": 0})

    def test_r4_bad_targets_are_refused(self):
        from scorbot.streaming import StreamRefused
        core = make()
        for bad in ({}, {"wrist_pitch": 1}, {"base": float("nan")}, {"base": True},
                    {"base": "1"}, [1, 2, 3]):
            with self.subTest(bad=bad), self.assertRaises(StreamRefused):
                core.set_target(bad, 0.0)

    def test_r5_stop_takes_effect_on_the_next_step(self):
        core, arm = make(), Arm()
        run(core, arm, 10, on_step=keep_alive(core, {"base": 800}))
        core.request_stop()
        step = core.step(arm.counts, 10 * PERIOD)
        self.assertEqual(step.action, "stop")
        self.assertEqual(core.state, "stopped")
        self.assertIsNone(core.fault)
        self.assertEqual(core.step(arm.counts, 11 * PERIOD).action, "stop")

    def test_r6_controller_error_word_and_emergency_bit_fault(self):
        for kwargs, text in (({"error_counts": {"base": 40}}, "error word"),
                             ({"emergency": True}, "emergency")):
            with self.subTest(kwargs=kwargs):
                core = make()
                self.assertEqual(core.step(ZERO, 0.0, error_counts={"base": 39}).action, "send")
                step = core.step(ZERO, PERIOD, **kwargs)
                self.assertEqual(step.action, "stop")
                self.assertIn(text, core.fault)

    def test_r6_a_fault_reported_from_outside_stops_the_stream(self):
        core = make()
        core.fail("stale reply")
        self.assertEqual(core.step(ZERO, 0.0).action, "stop")
        self.assertEqual(core.fault, "stale reply")

    def test_r7_no_fresh_target_slows_to_rest_and_holds(self):
        core, arm = make(hold_timeout_s=0.1), Arm()
        run(core, arm, 8, on_step=keep_alive(core, {"base": 1200}))     # 0.19 s of targets
        records = run(core, arm, 200, start=8)                          # then silence
        self.assertEqual(core.state, "holding")
        final = records[-1].commanded["base"]
        self.assertLess(final, 1200)
        self.assertEqual({r.commanded["base"] for r in records[-20:]}, {final})
        self.assertEqual({r.action for r in records}, {"send"})         # it keeps holding
        # a new target resumes
        resumed = run(core, arm, 300, start=208, on_step=keep_alive(core, {"base": 1200}))
        self.assertEqual(resumed[-1].commanded["base"], 1200)
        self.assertEqual(core.state, "tracking")

    def test_r8_a_full_controller_queue_makes_the_stream_wait(self):
        core = make(queue_limit=4)
        core.set_target({"base": 500}, 0.0)
        before = core.step(ZERO, 0.0, queued=3)
        self.assertEqual(before.action, "send")
        waiting = core.step(ZERO, PERIOD, queued=4)
        self.assertEqual(waiting.action, "wait")
        self.assertEqual(waiting.commanded, before.commanded)
        self.assertEqual(core.step(ZERO, 2 * PERIOD, queued=1).action, "send")

    def test_r11_a_lagging_arm_is_tracked_with_a_bounded_error(self):
        core, arm = make(), Arm(follow=0.5)
        records = run(core, arm, 400, on_step=keep_alive(core, {"base": 1000}))
        self.assertEqual(core.state, "tracking")
        worst = max(abs(r.lead["base"]) for r in records)
        self.assertGreater(worst, 5)
        self.assertLess(worst, LEAD["base"])
        self.assertLessEqual(abs(arm.counts["base"] - 1000), 1)

    def test_finish_slows_to_rest_then_ends_and_takes_no_more_targets(self):
        from scorbot.streaming import StreamRefused
        core, arm = make(), Arm()
        run(core, arm, 10, on_step=keep_alive(core, {"base": 900}))
        core.finish()
        records = run(core, arm, 300, start=10)
        self.assertEqual(records[-1].action, "end")
        self.assertEqual(core.state, "ended")
        self.assertEqual(records[-2].commanded, records[-1].commanded)
        self.assertEqual(arm.counts, records[-1].commanded)
        with self.assertRaises(StreamRefused):
            core.set_target({"base": 0}, 99.0)

    def test_settings_are_validated(self):
        from scorbot.streaming import StreamLimits, StreamRefused, prior_limits
        for kwargs in ({"period_s": 0}, {"hold_timeout_s": -1},
                       {"travel_cap": {"base": 1.0}}, {"lead_limit": {**LEAD, "base": 0}},
                       {"start": {"base": 0.5, "shoulder": 0, "elbow": 0}},
                       {"start": {"base": 2000, "shoulder": 0, "elbow": 0}}):
            with self.subTest(kwargs=kwargs), self.assertRaises(StreamRefused):
                make(**kwargs)
        with self.assertRaises(StreamRefused):
            StreamLimits(0, 1, 1)
        for fraction in (0, 1.5, True):
            with self.assertRaises(StreamRefused):
                prior_limits(fraction)
        limits = prior_limits(0.25)
        self.assertAlmostEqual(limits.max_velocity, 1625.0)
        self.assertAlmostEqual(limits.max_acceleration, 1625.0 / 0.285)


if __name__ == "__main__":
    unittest.main()
