"""Streaming part B (issue #171): ST07, ST08, ST09 and ST10 of docs/requirements/streaming.md.

Written before the code. ST03 and ST16 are in ``test_stream_built_rows.py`` (#172). The simulator and the core
only: nothing here opens USB or moves an arm.
"""

from importlib.util import find_spec
import math
import unittest

try:
    from tests.sim_support import SimulatedRobotCase
except ImportError:      # discovered with -s tests: the folder itself is on sys.path
    from sim_support import SimulatedRobotCase

HAS_RUCKIG = find_spec("ruckig") is not None
PERIOD = 0.024
STREAM = ("base", "shoulder", "elbow")


def core(**kwargs):
    from scorbot.streaming import StreamCore
    wide = {m: 20000.0 for m in STREAM}
    settings = {"travel_cap": wide, "lead_limit": {m: 1000.0 for m in STREAM}, "period_s": PERIOD}
    settings.update(kwargs)
    return StreamCore({m: 0 for m in STREAM}, **settings)


def drive(stream_core, steps, *, target, dt=PERIOD, keep_sending=True, measured=None):
    """Step a core against a perfect follower; returns the Step records."""
    counts = dict(measured or {m: 0 for m in STREAM})
    records = []
    for index in range(steps):
        now = index * dt
        if keep_sending:
            stream_core.set_target(target, now)
        step = stream_core.step(counts, now)
        records.append(step)
        if step.action == "send":
            counts = dict(step.commanded)
        if step.action in ("stop", "end"):
            break
    return records


# ---- ST07, ST08, ST10 through Scorbot.start_stream on the simulator ----

@unittest.skipUnless(HAS_RUCKIG, "ruckig not installed (pip install .[planning])")
class StreamSettingsTests(SimulatedRobotCase):
    CONTROLLER_DEFAULTS = {"step_delay_s": 0.001}

    def refused(self, robot, **settings):
        with self.assertRaises(ValueError):
            robot.start_stream(**settings)
        self.assertIsNone(robot._stream)
        self.assertTrue(robot._commands.empty(), "a refused start queues nothing")

    def accepted(self, robot, **settings):
        stream = robot.start_stream(**settings)
        try:
            return stream.core
        finally:
            stream.close()

    def test_st07_one_setpoint_per_period_0_024_by_default_0_01_to_0_05_accepted(self):
        robot = self.robot()
        self.assertEqual(self.accepted(robot).period_s, 0.024)
        for period in (0.01, 0.05):
            self.assertEqual(self.accepted(robot, period_s=period).period_s, period)
        for period in (0.0099, 0.0501, 0, -0.024, math.nan):
            with self.subTest(period=period):
                self.refused(robot, period_s=period)

    def test_st08_hold_timeout_0_5_by_default_0_05_to_2_accepted(self):
        robot = self.robot()
        self.assertEqual(self.accepted(robot).hold_timeout_s, 0.5)
        for timeout in (0.05, 2.0):
            self.assertEqual(self.accepted(robot, hold_timeout_s=timeout).hold_timeout_s, timeout)
        for timeout in (0.049, 2.01, math.inf):
            with self.subTest(timeout=timeout):
                self.refused(robot, hold_timeout_s=timeout)

    def test_st10_lead_limit_2_degrees_by_default_at_most_max_jog_deg(self):
        from scorbot import limits
        robot = self.robot()
        per_degree = {m: abs(robot.preview_jog(m, 1.0)["motor_count_deltas"][m]) for m in STREAM}
        self.assertEqual(self.accepted(robot).lead_limit, {m: 2.0 * per_degree[m] for m in STREAM})
        self.assertEqual(self.accepted(robot, lead_limit_deg=limits.MAX_JOG_DEG).lead_limit,
                         {m: limits.MAX_JOG_DEG * per_degree[m] for m in STREAM})
        for lead in (limits.MAX_JOG_DEG + 0.01, 0, -1.0):
            with self.subTest(lead=lead):
                self.refused(robot, lead_limit_deg=lead)


# ---- ST07, ST08, ST09, ST10 on the core ----

@unittest.skipUnless(HAS_RUCKIG, "ruckig not installed (pip install .[planning])")
class StreamCoreBTests(unittest.TestCase):
    def test_st07_a_slow_loop_is_not_caught_up(self):
        """Steps five periods apart plan exactly what steps one period apart plan: slower, never faster."""
        target = {"base": 3000, "shoulder": -2000, "elbow": 1000}
        paced = [r.commanded for r in drive(core(), 120, target=target)]
        slow = [r.commanded for r in drive(core(), 120, target=target, dt=5 * PERIOD)]
        self.assertEqual(slow, paced)

    def test_st08_holding_never_drifts_and_a_new_target_resumes_tracking(self):
        stream_core = core()
        counts = {m: 0 for m in STREAM}
        stream_core.set_target({"base": 6000}, 0.0)    # about 4 s away: the timeout must brake it
        held = []
        for index in range(200):                     # targets stop at once: 4.8 s, ten hold timeouts
            step = stream_core.step(counts, index * PERIOD)
            counts = dict(step.commanded)
            if index * PERIOD > 1.5:
                held.append(step.commanded)
        self.assertEqual(stream_core.state, "holding")
        self.assertEqual(len({tuple(sorted(c.items())) for c in held}), 1, "the held setpoint never moves")
        self.assertLess(held[0]["base"], 3000, "the hold timeout brakes the move well short of the target")
        start = 200 * PERIOD
        stream_core.set_target({"base": -300}, start)
        self.assertEqual(stream_core.state, "tracking")
        for index in range(300):
            stream_core.set_target({"base": -300}, start + index * PERIOD)
            step = stream_core.step(counts, start + index * PERIOD)
            counts = dict(step.commanded)
        self.assertEqual(counts["base"], -300)

    def test_st09_no_setpoint_steps_further_than_max_velocity_times_period(self):
        """The bound is strict: max_velocity x period_s counts, with no count of rounding on top."""
        from scorbot.streaming import prior_limits
        self.assertEqual(prior_limits().max_velocity * PERIOD, 39.0)      # the spec's 39 counts at 24 ms
        self.assertEqual(prior_limits(1.0).max_velocity * PERIOD, 156.0)  # and 156 at full speed
        target = {"base": 15000, "shoulder": -15000, "elbow": 9000}
        for fraction in (0.25, 0.3, 0.5, 0.77, 1.0):
            with self.subTest(speed_fraction=fraction):
                limit = prior_limits(fraction).max_velocity * PERIOD
                previous = {m: 0 for m in STREAM}
                worst = 0
                for record in drive(core(limits=prior_limits(fraction)), 400, target=target):
                    for m in STREAM:
                        worst = max(worst, abs(record.commanded[m] - previous[m]))
                    previous = record.commanded
                self.assertLessEqual(worst, limit + 1e-9)

    def test_st09_the_step_bound_never_breaks_the_acceleration_limit(self):
        """Clamping steps must not let the setpoint fall behind the plan and then catch up with a jolt.

        Same tolerance as ``test_r2``: two counts of rounding per acceleration sample. The target must still be
        reached exactly.
        """
        from scorbot.streaming import prior_limits
        for fraction in (0.25, 0.3, 0.77):
            with self.subTest(speed_fraction=fraction):
                limits = prior_limits(fraction)
                records = drive(core(limits=limits), 600, target={"base": 6000})
                positions = [r.commanded["base"] for r in records]
                velocity = [(b - a) / PERIOD for a, b in zip(positions, positions[1:])]
                acceleration = [(b - a) / PERIOD for a, b in zip(velocity, velocity[1:])]
                self.assertLessEqual(max(map(abs, acceleration)), limits.max_acceleration + 2 / PERIOD ** 2)
                self.assertEqual(positions[-1], 6000)

    def test_st09_a_speed_under_one_count_per_period_is_refused(self):
        """Whole counts per period: a speed that rounds to zero counts is refused up front, not raised mid-step."""
        from scorbot.streaming import StreamRefused, prior_limits
        slow = prior_limits(0.005)                       # 32.5 counts/s, 0.78 count per 24 ms
        with self.assertRaises(StreamRefused):
            core(limits=slow)
        stream_core = core()
        with self.assertRaises(StreamRefused):
            stream_core.set_speed(0.005)
        self.assertEqual(stream_core.state, "tracking")

    def slowed(self, *, finish):
        """Cruise at full speed, then set_speed(0.1) at step 60 (and finish at once if asked)."""
        from scorbot.streaming import prior_limits
        stream_core = core(limits=prior_limits(1.0), travel_cap={m: 60000.0 for m in STREAM})
        counts, positions, step = {m: 0 for m in STREAM}, [0], None
        for index in range(1000):
            now = index * PERIOD
            if index < 60 or not finish:
                stream_core.set_target({"base": 40000}, now)
            if index == 60:
                stream_core.set_speed(0.1)
                if finish:
                    stream_core.finish()
            step = stream_core.step(counts, now)
            if step.action == "send":
                counts = dict(step.commanded)
                positions.append(counts["base"])
            if step.action in ("stop", "end"):
                break
        return stream_core, step, positions

    def test_st09_slowing_down_mid_move_brakes_within_the_acceleration_limit(self):
        """A lower speed brakes smoothly from the old one: the step bound must not cut the speed in one period."""
        from scorbot.streaming import prior_limits
        positions = self.slowed(finish=False)[2]
        velocity = [(b - a) / PERIOD for a, b in zip(positions, positions[1:])]
        acceleration = [(b - a) / PERIOD for a, b in zip(velocity, velocity[1:])]
        self.assertLessEqual(max(map(abs, acceleration)), prior_limits(1.0).max_acceleration + 2 / PERIOD ** 2)
        self.assertEqual(positions[-1], 40000)

    def test_st09_finish_after_slowing_down_sends_the_resting_endpoint(self):
        """The stream ends only once the last sent setpoint is the planner's rounded resting point."""
        stream_core, step, positions = self.slowed(finish=True)
        self.assertEqual((step.action, stream_core.state), ("end", "ended"))
        self.assertLessEqual(abs(positions[-1] - positions[-2]), 1, "it ends at rest, not mid-move")

    def test_st10_a_setpoint_is_never_sent_past_the_lead_limit_and_the_stream_then_faults(self):
        lead = {m: 100.0 for m in STREAM}
        stream_core = core(lead_limit=lead)
        stuck = {m: 0 for m in STREAM}                   # the arm never moves
        sent = []
        for index in range(200):
            stream_core.set_target({"base": 2000}, index * PERIOD)
            step = stream_core.step(stuck, index * PERIOD)
            if step.action == "send":
                sent.append(step.commanded)
            else:
                break
        self.assertTrue(sent)
        self.assertLessEqual(max(abs(c["base"]) for c in sent), 100)
        self.assertEqual((step.action, stream_core.state), ("stop", "faulted"))
        self.assertIn("not following", stream_core.fault)


if __name__ == "__main__":
    unittest.main()
