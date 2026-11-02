"""scorbot/policy_arm.py: the opt-in speed step limit, and proof the default clamp is unchanged.

No USB, no arm. The PolicyArm tests run on the simulated controller and need the planning extra.
"""

from importlib.util import find_spec
import itertools
import math
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scorbot import SimulatedScorbot, policy_arm
from scorbot.policy_arm import LEAD_MARGIN_DEG, MOTORS, STEP_MAX_DEG, PolicyArm, clamp, speed_step_deg
from scorbot.streaming import DEFAULT_HOLD_TIMEOUT_S, VENDOR_MAX_SPEED_COUNTS_S

PER_DEGREE = {"base": 142, "shoulder": 115, "elbow": -113}     # what PolicyArm reads from preview_jog in the sim


def clamp_before(delta_deg, target_deg, measured_deg, *, cap_deg, step_max_deg=STEP_MAX_DEG,
                 lead_margin_deg=LEAD_MARGIN_DEG):
    """policy_arm.clamp exactly as it was at 906b265 (PR #70), the oracle for the default path."""
    def finite(v):
        return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)

    out, clamped = dict(target_deg), []
    for motor, asked in (delta_deg or {}).items():
        if motor in MOTORS and not finite(target_deg.get(motor)):
            raise ValueError(f"last target for {motor} is not a finite number: {target_deg.get(motor)!r}")
        if motor not in MOTORS or not all(finite(v) for v in (asked, measured_deg.get(motor), cap_deg)) \
                or cap_deg < 0:
            clamped.append(motor)
            continue
        base = target_deg[motor]
        step = max(-step_max_deg, min(step_max_deg, asked))
        wanted = max(-cap_deg, min(cap_deg, base + step))
        lo, hi = measured_deg[motor] - lead_margin_deg, measured_deg[motor] + lead_margin_deg
        wanted = max(lo, min(hi, wanted))
        lo = max(base - step_max_deg, min(-cap_deg, base))
        hi = min(base + step_max_deg, max(cap_deg, base))
        wanted = max(lo, min(hi, wanted))
        if abs(wanted - (target_deg[motor] + asked)) > 1e-9:
            clamped.append(motor)
        out[motor] = wanted
    return out, clamped


class DefaultClampUnchangedTests(unittest.TestCase):
    """Passes before and after the change on purpose: it guards the default path, it is not a new feature."""

    def test_the_default_clamp_matches_the_old_one_over_a_sweep(self):
        values = (-9.0, -2.5, -2.0, -1.1, -0.5, -0.2, 0.0, 0.3, 0.5, 1.0, 1.9, 2.0, 3.0, math.nan, math.inf)
        caps = (0.0, 0.2, 2.0, 30.0, math.nan, -1.0)
        count = 0
        for asked, last, measured, cap in itertools.product(values, values[:-2], values, caps):
            delta, target = {"base": asked, "wrist": 1.0}, {"base": last, "shoulder": 0.5, "elbow": -0.5}
            seen = {"base": measured, "shoulder": 0.0, "elbow": 0.0}
            self.assertEqual(clamp(delta, target, seen, cap_deg=cap), clamp_before(delta, target, seen, cap_deg=cap),
                             (asked, last, measured, cap))
            count += 1
        self.assertGreater(count, 15000)

    def test_default_steps_and_margins_are_unchanged(self):
        self.assertEqual((STEP_MAX_DEG, LEAD_MARGIN_DEG, policy_arm.LEAD_LIMIT_DEG), (0.5, 1.5, 2.0))


class SpeedStepTests(unittest.TestCase):
    def test_the_step_is_the_vendor_speed_over_the_elapsed_time(self):
        step = speed_step_deg(25, 0.02, PER_DEGREE)
        for motor in MOTORS:
            self.assertAlmostEqual(step[motor], VENDOR_MAX_SPEED_COUNTS_S * 0.25 * 0.02 / abs(PER_DEGREE[motor]))

    def test_the_step_scales_with_speed_and_time(self):
        slow, fast, longer = (speed_step_deg(p, t, PER_DEGREE)["base"] for p, t in ((10, 0.02), (40, 0.02), (10, 0.08)))
        self.assertAlmostEqual(fast, 4 * slow)
        self.assertAlmostEqual(longer, 4 * slow)

    def test_a_negative_scale_still_gives_a_positive_step(self):
        self.assertGreater(speed_step_deg(50, 0.02, PER_DEGREE)["elbow"], 0)

    def test_the_step_never_passes_the_lead_margin(self):
        for pct, dt in itertools.product((1, 25, 50, 100), (0.0, 0.01, 0.1, 0.5, 3.0, 1e9)):
            for motor, step in speed_step_deg(pct, dt, PER_DEGREE).items():
                self.assertLessEqual(step, LEAD_MARGIN_DEG, (pct, dt, motor))
                self.assertGreaterEqual(step, 0.0)

    def test_the_time_counts_only_up_to_the_hold_timeout(self):
        self.assertEqual(speed_step_deg(1, 60.0, PER_DEGREE), speed_step_deg(1, DEFAULT_HOLD_TIMEOUT_S, PER_DEGREE))

    def test_no_time_or_bad_time_is_no_step(self):
        for dt in (0.0, -1.0, math.nan):
            self.assertEqual(speed_step_deg(100, dt, PER_DEGREE), dict.fromkeys(MOTORS, 0.0), dt)

    def test_never_faster_than_the_vendor_speed_in_counts(self):
        for pct, dt in itertools.product((1, 25, 50, 100), (0.005, 0.024, 0.1)):
            for motor, step in speed_step_deg(pct, dt, PER_DEGREE).items():
                self.assertLessEqual(step * abs(PER_DEGREE[motor]), VENDOR_MAX_SPEED_COUNTS_S * pct / 100 * dt + 1e-9)


class PerMotorStepClampTests(unittest.TestCase):
    ZERO = dict.fromkeys(MOTORS, 0.0)

    def test_each_motor_gets_its_own_step(self):
        new, clamped = clamp({"base": 5.0, "shoulder": -5.0, "elbow": 0.1}, self.ZERO, self.ZERO, cap_deg=30.0,
                             step_max_deg={"base": 1.2, "shoulder": 0.8, "elbow": 0.3})
        self.assertEqual(new, {"base": 1.2, "shoulder": -0.8, "elbow": 0.1})
        self.assertEqual(clamped, ["base", "shoulder"])

    def test_a_mapping_of_the_old_step_matches_the_scalar(self):
        for asked, last, measured in itertools.product((-3.0, -0.2, 0.0, 0.4, 3.0), (-2.0, 0.0, 1.0), (-1.2, 0.0, 1.1)):
            delta, target, seen = {"base": asked}, {**self.ZERO, "base": last}, {**self.ZERO, "base": measured}
            self.assertEqual(clamp(delta, target, seen, cap_deg=2.0, step_max_deg=dict.fromkeys(MOTORS, STEP_MAX_DEG)),
                             clamp(delta, target, seen, cap_deg=2.0))

    def test_the_lead_correction_never_beats_a_speed_step(self):
        steps = {"base": 1.2, "shoulder": 1.2, "elbow": 1.2}
        for t, m, asked in itertools.product((-9.0, -1.0, 0.0, 2.0, 9.0), (-12.0, -1.1, 0.0, 1.8, 12.0), (-5.0, 0.0, 5.0)):
            new, _ = clamp({"base": asked}, {**self.ZERO, "base": t}, {**self.ZERO, "base": m}, cap_deg=10.0,
                           step_max_deg=steps)
            self.assertLessEqual(abs(new["base"] - t), 1.2 + 1e-9, (t, m, asked))

    def test_a_zero_step_holds_the_motor(self):
        new, clamped = clamp({"base": 1.0}, self.ZERO, self.ZERO, cap_deg=2.0, step_max_deg=dict.fromkeys(MOTORS, 0.0))
        self.assertEqual((new["base"], clamped), (0.0, ["base"]))

    def test_a_bad_step_is_refused(self):
        for bad in ({"base": -0.1, "shoulder": 1, "elbow": 1}, {"base": math.nan, "shoulder": 1, "elbow": 1},
                    {"shoulder": 1, "elbow": 1}, {"base": True, "shoulder": 1, "elbow": 1}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                clamp({"base": 1.0}, self.ZERO, self.ZERO, cap_deg=2.0, step_max_deg=bad)


@unittest.skipUnless(find_spec("ruckig"), "needs the planning extra")
class PolicyArmStepLimitTests(unittest.TestCase):
    def arm(self, **kw):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        arm = PolicyArm(SimulatedScorbot(log_path=Path(tmp.name) / "e.jsonl", robot_id="t"), **kw)
        arm.connect()
        self.addCleanup(arm.close)
        return arm

    def test_the_default_is_the_fixed_step(self):
        arm = self.arm(travel_cap_deg=30.0)
        self.assertEqual(arm.step_limit, "fixed")
        with mock.patch.object(policy_arm, "clamp", wraps=policy_arm.clamp) as spy:
            self.assertEqual(arm.act({"base": 90.0})["base"], STEP_MAX_DEG)
        self.assertEqual(spy.call_args.kwargs.get("step_max_deg", STEP_MAX_DEG), STEP_MAX_DEG)

    def test_speed_mode_steps_by_the_vendor_speed_since_the_last_call(self):
        now = [100.0]
        with mock.patch.object(policy_arm, "monotonic", lambda: now[0]):
            arm = self.arm(travel_cap_deg=30.0, step_limit="speed")
            now[0] += 0.05
            first = arm.act({"base": 90.0})["base"]
        self.assertAlmostEqual(first, VENDOR_MAX_SPEED_COUNTS_S * 0.25 * 0.05 / abs(arm._per_degree["base"]))
        self.assertGreater(first, 0.0)

    def test_speed_mode_follows_a_live_speed_change(self):
        now = [100.0]
        with mock.patch.object(policy_arm, "monotonic", lambda: now[0]):
            arm = self.arm(travel_cap_deg=30.0, step_limit="speed", speed_pct=10)
            arm.set_speed(20)
            now[0] += 0.05
            first = arm.act({"base": 90.0})["base"]
        self.assertAlmostEqual(first, VENDOR_MAX_SPEED_COUNTS_S * 0.20 * 0.05 / abs(arm._per_degree["base"]))

    def test_an_unknown_step_limit_is_refused(self):
        for bad in ("Speed", "", None, 1, "vendor"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                PolicyArm(SimulatedScorbot(log_path=Path(tempfile.gettempdir()) / "unused.jsonl"), step_limit=bad)


if __name__ == "__main__":
    unittest.main()
