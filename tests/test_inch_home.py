"""scorbot.inch_home: the home search on its own, against a fake switch. No USB, no robot.

Positions are degrees along the approach direction: moving by a positive step
goes toward the switch's near edge, the way the vendor's homing approaches it.
"""

import contextlib
import io
import unittest

from scorbot import MotionStopped, ScorbotError, inch_home
from scorbot.inch_home import InchHomeError, inch_to_edge

try:
    from tests.sim_support import SimulatedRobotCase
except ImportError:      # discovered with -s tests: the folder itself is on sys.path
    from sim_support import SimulatedRobotCase


class FakeSwitch:
    """A switch that reads on over [low, high] on the approach axis; counts every move."""

    def __init__(self, start, low=0.0, high=2.0, always=None):
        self.x, self.low, self.high, self.always = start, low, high, always
        self.moves, self.clock = [], 0.0

    def read_on(self):
        return self.always if self.always is not None else self.low <= self.x <= self.high

    def move(self, delta):
        self.moves.append(delta)
        self.x += delta

    def now(self):
        return self.clock

    def run(self, **overrides):
        settings = dict(coarse_deg=1.0, fine_deg=0.25, cap_deg=60.0, deadline_s=1e9,
                        now=self.now)
        settings.update(overrides)
        return inch_to_edge(self.read_on, self.move, **settings)


class InchToEdgeTests(unittest.TestCase):
    def assert_at_the_near_edge(self, fake):
        # The near edge is where the approach direction first turns the switch on:
        # inside the first fine step above it.
        self.assertTrue(fake.read_on())
        self.assertGreaterEqual(fake.x, fake.low - 1e-9)
        self.assertLess(fake.x, fake.low + 0.25 + 1e-9)

    def test_starting_before_the_switch_walks_up_to_its_near_edge(self):
        fake = FakeSwitch(start=-10.3)
        net = fake.run()
        self.assert_at_the_near_edge(fake)
        self.assertAlmostEqual(net, fake.x - (-10.3), places=6)

    def test_starting_on_the_switch_backs_off_it_and_comes_back_to_the_near_edge(self):
        fake = FakeSwitch(start=1.4)
        fake.run()
        self.assert_at_the_near_edge(fake)

    def test_starting_past_the_switch_searches_the_other_way_then_crosses_to_its_near_edge(self):
        fake = FakeSwitch(start=12.0)
        fake.run()
        self.assert_at_the_near_edge(fake)

    def test_the_search_goes_back_to_the_start_before_trying_the_other_way(self):
        fake = FakeSwitch(start=12.0)
        fake.run(cap_deg=15.0)
        # 15 degrees toward the switch found nothing (it is 10 behind), back to
        # 12.0, then the other way: the walk must have passed through the start.
        positions, x = [], 12.0
        for delta in fake.moves:
            x += delta
            positions.append(round(x, 6))
        self.assertIn(12.0, positions)

    def test_no_move_is_larger_than_the_coarse_step_and_none_is_zero(self):
        for start in (-20.0, 1.0, 15.0):
            fake = FakeSwitch(start=start)
            fake.run()
            self.assertTrue(all(0 < abs(m) <= 1.0 + 1e-9 for m in fake.moves), start)

    def test_the_arm_never_goes_farther_than_the_cap_from_where_it_started(self):
        fake = FakeSwitch(start=0.0, low=500.0, high=502.0)        # nowhere near
        with self.assertRaises(InchHomeError):
            fake.run(cap_deg=20.0)
        x, farthest = 0.0, 0.0
        for delta in fake.moves:
            x += delta
            farthest = max(farthest, abs(x))
        self.assertLessEqual(farthest, 20.0 + 1e-9)

    def test_a_switch_that_is_never_found_either_way_is_an_error_not_a_hang(self):
        fake = FakeSwitch(start=0.0, always=False)
        with self.assertRaisesRegex(InchHomeError, "not found"):
            fake.run(cap_deg=10.0)

    def test_a_switch_stuck_on_is_an_error_not_a_hang(self):
        fake = FakeSwitch(start=0.0, always=True)
        with self.assertRaisesRegex(InchHomeError, "never turns off"):
            fake.run(cap_deg=10.0)

    def test_the_deadline_ends_the_search(self):
        fake = FakeSwitch(start=-50.0)

        def move(delta):
            fake.move(delta)
            fake.clock += 5.0

        with self.assertRaisesRegex(InchHomeError, "timed out"):
            inch_to_edge(fake.read_on, move, coarse_deg=1.0, fine_deg=0.25, cap_deg=60.0,
                         deadline_s=20.0, now=fake.now)
        self.assertLess(len(fake.moves), 10)

    def test_a_stuck_on_switch_is_given_up_on_within_the_edge_cap_not_the_search_cap(self):
        fake = FakeSwitch(start=0.0, always=True)
        with self.assertRaisesRegex(InchHomeError, "never turns off"):
            fake.run(cap_deg=60.0)
        self.assertLessEqual(len(fake.moves), 30)               # 6 degrees in quarter steps

    def test_a_switch_that_does_not_come_back_on_is_given_up_on_within_the_edge_cap(self):
        fake = FakeSwitch(start=0.0)
        reads = iter([True] + [False] * 1000)                    # on once, then never again
        with self.assertRaisesRegex(InchHomeError, "not found again"):
            inch_to_edge(lambda: next(reads), fake.move, coarse_deg=1.0, fine_deg=0.25,
                         cap_deg=60.0, deadline_s=1e9, now=fake.now)
        self.assertLessEqual(len(fake.moves), 30)

    def test_bad_settings_are_refused(self):
        fake = FakeSwitch(start=0.0)
        for bad in ({"coarse_deg": 0}, {"fine_deg": -1}, {"cap_deg": 0}, {"coarse_deg": 0.1},
                    {"fine_deg": float("nan")}, {"edge_cap_deg": 0}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                fake.run(**bad)


class ConstantsTests(unittest.TestCase):
    def test_the_vendor_values_are_what_the_trace_says(self):
        # docs/protocol/VENDOR_HOMING_TRACE.md summary table: from disassembly, unverified.
        self.assertEqual(inch_home.ORDER, ("shoulder", "elbow", "base"))
        self.assertEqual(inch_home.APPROACH_COUNTS, {"shoulder": 1, "elbow": -1, "base": -1})
        self.assertEqual(inch_home.OFFSET_COUNTS, {"shoulder": -190, "elbow": 45, "base": 0})

    def test_the_steps_stay_under_the_jog_ceiling_and_the_search_is_per_joint(self):
        self.assertLessEqual(inch_home.COARSE_DEG, 5.0)           # the SDK's jog ceiling
        self.assertLess(inch_home.FINE_DEG, inch_home.COARSE_DEG)
        caps = inch_home.DEFAULT_SEARCH_DEG
        self.assertEqual(set(caps), set(inch_home.ORDER))
        self.assertTrue(all(0 < v <= 180 for v in caps.values()))
        # Shoulder and elbow stay within a few tens of degrees by default: a long sweep with
        # the elbow and wrist motors held swings the elbow joint into its stop (see the
        # comment on DEFAULT_SEARCH_DEG).
        self.assertLessEqual(caps["shoulder"], 40.0)
        self.assertLessEqual(caps["elbow"], 40.0)
        self.assertLessEqual(inch_home.EDGE_CAP_DEG, 10.0)


JOG_ORDERS = set(range(4, 14))


class HomeInchTests(SimulatedRobotCase):
    """``Scorbot.home_inch`` against the simulator's modeled switches (not measured).

    The simulator puts each switch at ``home_counts - home_offset`` (shoulder +190,
    elbow -45, base 0), 200 counts wide. The near edge from the approach side is
    then shoulder +90, elbow +55, base +100 counts, and home is the edge plus the
    vendor offset: shoulder -100, elbow +100, base +100.
    """

    HOMED = False
    EXPECTED = {"shoulder": -100, "elbow": 100, "base": 100}

    def inch_robot(self, **start):
        from scorbot.simulated import SimulatorProfile
        profile = SimulatorProfile(model_homing=True, fail_home_motor=start.pop("fail", None))
        robot = self.robot(profile=profile, start_counts=start)
        with robot.sim._lock:
            robot.sim.switch_bits = robot.sim._pressed_switches()
        return robot

    def signed(self, robot):
        return robot.get_state().signed_encoder_counts

    def test_homes_from_before_the_switches_and_records_the_vendor_home(self):
        robot = self.inch_robot(shoulder=-3000, elbow=3000, base=3000)
        robot.home_inch(operator_at_stop=True)
        self.assertTrue(robot._homed)
        now = self.signed(robot)
        for joint, expected in self.EXPECTED.items():
            self.assertAlmostEqual(now[joint], expected, delta=60, msg=joint)
        self.assertEqual((now["wrist_motor_1"], now["wrist_motor_2"]), (0, 0))
        self.assertIsNotNone(robot._home_counts)
        self.assertIn("home_complete", self.events())
        robot.jog_joint("base", 1.0)                       # an ordinary jog works afterwards

    def test_homes_from_past_the_switch_by_searching_the_other_way(self):
        robot = self.inch_robot(shoulder=3000, elbow=-3000, base=-3000)
        robot.home_inch(operator_at_stop=True)
        now = self.signed(robot)
        for joint, expected in self.EXPECTED.items():
            self.assertAlmostEqual(now[joint], expected, delta=60, msg=joint)

    def test_homes_from_on_the_switch(self):
        robot = self.inch_robot(shoulder=190, elbow=-45, base=0)
        robot.home_inch(operator_at_stop=True)
        now = self.signed(robot)
        for joint, expected in self.EXPECTED.items():
            self.assertAlmostEqual(now[joint], expected, delta=60, msg=joint)

    def test_it_sends_only_motors_on_and_legacy_jogs(self):
        robot = self.inch_robot(shoulder=-3000, elbow=3000, base=3000)
        before = len(robot.sim.commands)
        robot.home_inch(operator_at_stop=True)
        orders = {c[0] for c in robot.sim.commands[before:] if c}
        # Only the base, shoulder and elbow jogs (4 to 9): no wrist jog, no order 18, no
        # stream, no gripper.
        self.assertTrue(orders <= set(range(4, 10)), orders)

    def test_it_refuses_without_the_operators_word_and_queues_nothing(self):
        robot = self.inch_robot(shoulder=-3000)
        queued = list(robot.sim.commands)
        for kwargs in ({}, {"operator_at_stop": False}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                robot.home_inch(**kwargs)
        self.assertEqual(robot.sim.commands, queued)
        self.assertIsNone(robot._fault)

    def test_it_refuses_unknown_switch_bits_like_the_legacy_home(self):
        robot = self.inch_robot()
        with robot.sim._lock:
            robot.sim.switch_bits = 64
        queued = list(robot.sim.commands)
        with self.assertRaises(ScorbotError):
            robot.home_inch(operator_at_stop=True)
        self.assertEqual(robot.sim.commands, queued)

    def test_it_needs_the_motors_enabled(self):
        robot = self.inch_robot()
        robot.disable()
        with self.assertRaises(ScorbotError):
            robot.home_inch(operator_at_stop=True)

    def test_a_switch_that_is_never_found_latches_a_fault_and_leaves_no_home(self):
        # Near their switches so the shoulder and base succeed inside the 10 degree cap
        # and the elbow, whose switch never turns on, is the one that fails.
        robot = self.inch_robot(shoulder=-500, elbow=500, base=500, fail="elbow")
        with self.assertRaises(ScorbotError) as caught:
            robot.home_inch(operator_at_stop=True, max_search_deg=10.0)
        self.assertIn("elbow", str(caught.exception))
        self.assertIsNotNone(robot._fault)
        self.assertFalse(robot._homed)
        self.assertIsNone(robot._home_counts)
        with self.assertRaises(ScorbotError):
            robot.jog_joint("base", 1.0)                   # a fault rejects further motion
        self.assertIn("home_failed", self.events())

    def test_a_controller_error_during_the_search_latches_a_fault(self):
        robot = self.inch_robot(shoulder=-3000, elbow=3000, base=3000)
        robot.sim.inject("controller_error")
        with self.assertRaises(ScorbotError) as caught:
            robot.home_inch(operator_at_stop=True)
        # The terminal line must say which joint, which jog and how far it had gone:
        # the bare "error code" alone cannot tell us what happened on the arm.
        self.assertRegex(str(caught.exception),
                         r"Home search failed on the shoulder on jog 1 \(\+0\.0 degrees .* before it\)")
        self.assertIn("error code", str(caught.exception))
        self.assertIsNotNone(robot._fault)
        self.assertFalse(robot._homed)
        failed = [row for row in self.rows() if row.get("event") == "home_failed"]
        self.assertEqual(failed[0]["joint"], "shoulder")
        self.assertIn("jog 1", failed[0]["error"])

    def test_a_jog_failure_later_in_the_search_reports_how_far_it_had_come(self):
        # Break the third jog of the shoulder: two 1 degree steps are behind it.
        from unittest.mock import patch
        robot = self.inch_robot(shoulder=-3000, elbow=3000, base=3000)
        original, jogs = robot._command, [0]

        def command(payload, **kwargs):
            if payload[0] in JOG_ORDERS:
                jogs[0] += 1
                if jogs[0] == 3:
                    raise ScorbotError("Legacy controller returned error code 1")
            return original(payload, **kwargs)

        with patch.object(robot, "_command", command):
            with self.assertRaisesRegex(ScorbotError,
                                        r"shoulder on jog 3 \(\+2\.0 degrees toward its switch "
                                        r"before it\): Legacy controller returned error code 1"):
                robot.home_inch(operator_at_stop=True)

    def test_a_stop_request_ends_the_search_without_a_fault_and_without_a_home(self):
        robot = self.inch_robot(shoulder=-3000, elbow=3000, base=3000)
        robot.request_stop()
        with self.assertRaises(MotionStopped):
            robot.home_inch(operator_at_stop=True)
        self.assertIsNone(robot._fault)
        self.assertFalse(robot._homed)
        self.assertIsNone(robot._home_counts)

    def test_the_search_cap_is_bounded(self):
        robot = self.inch_robot()
        for bad in (0, -1, 181, float("nan"), True, {"shoulder": 10.0},
                    {"shoulder": 10.0, "elbow": 10.0, "base": 0}):
            with self.subTest(cap=bad), self.assertRaises(ValueError):
                robot.home_inch(operator_at_stop=True, max_search_deg=bad)

    def test_a_far_shoulder_is_searched_only_when_the_caller_raises_the_cap(self):
        # About 150 degrees below home. The simulator has no elbow coupling, so this only
        # shows the mechanism; on the arm a sweep that long is the thing the default avoids.
        robot = self.inch_robot(shoulder=-17000, elbow=3000, base=3000)
        with self.assertRaisesRegex(ScorbotError, "shoulder"):
            robot.home_inch(operator_at_stop=True)             # the default cap refuses
        robot = self.inch_robot(shoulder=-17000, elbow=3000, base=3000)
        robot.home_inch(operator_at_stop=True,
                        max_search_deg={"shoulder": 160.0, "elbow": 60.0, "base": 100.0})
        self.assertAlmostEqual(self.signed(robot)["shoulder"], self.EXPECTED["shoulder"], delta=60)

    def test_each_joint_has_its_own_search_cap(self):
        robot = self.inch_robot(shoulder=-3000, elbow=3000, base=3000)
        with self.assertRaisesRegex(ScorbotError, "shoulder"):
            robot.home_inch(operator_at_stop=True,
                            max_search_deg={"shoulder": 10.0, "elbow": 60.0, "base": 100.0})

    def test_one_false_switch_reading_cannot_make_a_wrong_home(self):
        # A single reading flipped at any point of the search, from on the switch (the
        # unsafe case: a false off ends the back-off early and the home lands too high).
        import dataclasses
        for flipped in range(1, 40):
            with self.subTest(flipped=flipped):
                robot = self.inch_robot(shoulder=190, elbow=-45, base=0)
                original, calls = robot._motion_state, [0]

                def state(*args, original=original, calls=calls, flipped=flipped, **kwargs):
                    seen = original(*args, **kwargs)
                    calls[0] += 1
                    if calls[0] == flipped:
                        seen = dataclasses.replace(seen, home_switch_bits=seen.home_switch_bits ^ 7)
                    return seen

                robot._motion_state = state
                robot.home_inch(operator_at_stop=True)
                now = self.signed(robot)
                for joint, expected in self.EXPECTED.items():
                    self.assertAlmostEqual(now[joint], expected, delta=60, msg=(flipped, joint))

    def test_it_refuses_a_jog_ceiling_too_small_for_its_offset_move(self):
        robot = self.inch_robot(shoulder=-3000)
        robot.max_jog_degrees = 1.5
        queued = list(robot.sim.commands)
        with self.assertRaises(ValueError):
            robot.home_inch(operator_at_stop=True)
        self.assertEqual(robot.sim.commands, queued)

    def test_an_interrupt_between_commands_latches_a_fault_and_switches_the_motors_off(self):
        robot = self.inch_robot(shoulder=-3000, elbow=3000, base=3000)
        original, calls = robot._motion_state, [0]

        def state(*args, **kwargs):
            calls[0] += 1
            if calls[0] == 8:
                raise KeyboardInterrupt
            return original(*args, **kwargs)

        robot._motion_state = state
        before = len(robot.sim.commands)
        with self.assertRaises(KeyboardInterrupt):
            robot.home_inch(operator_at_stop=True)
        self.assertIsNotNone(robot._fault)
        self.assertFalse(robot._homed)
        self.assertIn(16, [c[0] for c in robot.sim.commands[before:] if c])   # motors off

    def test_the_bench_script_homes_by_inching_with_the_same_typed_words(self):
        from unittest.mock import Mock, patch
        try:
            from examples import bench_joint
        except ImportError:
            import bench_joint
        robot = self.inch_robot(shoulder=-3000, elbow=3000, base=3000)
        rows = []
        prompts = []

        def prompt(text, word):
            prompts.append(word)
            return word

        out = io.StringIO()
        with patch.object(bench_joint, "observe_leds"), patch("builtins.input", return_value="ok"),                 contextlib.redirect_stdout(out):
            state = bench_joint.connect_and_home(
                robot, lambda kind, **fields: rows.append((kind, fields)), Mock(), prompt,
                Mock(), "declined", inch=True)
        # What each home switch reads before HOME: all three off here (each joint starts
        # 3000 counts from its modeled switch).
        self.assertRegex(out.getvalue(),
                         r"Home switches now .*base off, shoulder off, elbow off")
        self.assertEqual(prompts, ["HOME", "HOME_OK"])          # the same words as the legacy home
        self.assertTrue(robot._homed)
        self.assertEqual(state.signed_encoder_counts["base"], self.signed(robot)["base"])
        self.assertEqual([f.get("method") for k, f in rows if k == "home_complete"], ["inch"])

    def test_a_joint_that_does_not_move_faults_before_it_is_driven_on(self):
        # A jog that is accepted but moves nothing (a hard stop, an unpowered motor):
        # the next step must not be sent.
        from unittest.mock import patch
        robot = self.inch_robot(shoulder=-3000, elbow=3000, base=3000)
        with patch.object(robot, "_command") as command:
            with self.assertRaisesRegex(ScorbotError, "did not follow"):
                robot.home_inch(operator_at_stop=True)
        jogs = [c for c in command.call_args_list if c.args[0][0] in JOG_ORDERS]
        self.assertEqual(len(jogs), 1)
        self.assertIsNotNone(robot._fault)
        self.assertFalse(robot._homed)

    def test_the_log_says_the_wrist_was_not_homed(self):
        robot = self.inch_robot(shoulder=-3000, elbow=3000, base=3000)
        robot.home_inch(operator_at_stop=True)
        rows = {row["event"]: row for row in self.rows() if "event" in row}
        for event in ("home_start", "home_complete"):
            self.assertIs(rows[event]["wrist_homed"], False, event)

    def stop_after_jog(self, robot, number):
        """Make a stop request arrive just as jog ``number`` (1-based) is closing."""
        original, seen = robot._command, [0]

        def command(payload, **kwargs):
            original(payload, **kwargs)
            if payload[0] in JOG_ORDERS:
                seen[0] += 1
                if seen[0] == number:
                    robot._stop_event.set()

        robot._command = command

    def test_a_stop_that_arrives_as_a_step_ends_is_not_lost(self):
        robot = self.inch_robot(shoulder=-3000, elbow=3000, base=3000)
        self.stop_after_jog(robot, 1)
        with self.assertRaises(MotionStopped):
            robot.home_inch(operator_at_stop=True)
        self.assertEqual(len([c for c in robot.sim.commands if c and c[0] in JOG_ORDERS]), 1)
        self.assertIsNone(robot._fault)
        self.assertFalse(robot._homed)
        self.assertIsNone(robot._home_counts)

    def test_a_stop_that_arrives_with_the_very_last_jog_still_means_no_home(self):
        counting = self.inch_robot(shoulder=-3000, elbow=3000, base=3000)
        counting.home_inch(operator_at_stop=True)
        total = len([c for c in counting.sim.commands if c and c[0] in JOG_ORDERS])
        robot = self.inch_robot(shoulder=-3000, elbow=3000, base=3000)
        self.stop_after_jog(robot, total)
        with self.assertRaises(MotionStopped):
            robot.home_inch(operator_at_stop=True)
        self.assertFalse(robot._homed)
        self.assertIsNone(robot._fault)
        self.assertFalse(robot._stop_event.is_set(), "a spent stop does not refuse the next jog")

    def test_the_switch_summary_names_each_switch_and_flags_unknown_bits(self):
        try:
            from examples import bench_joint
        except ImportError:
            import bench_joint
        self.assertEqual(
            bench_joint.switch_summary(0b00101),
            "Home switches now (a set bit is read as pressed; polarity unverified): "
            "base ON, shoulder off, elbow ON, wrist pitch off, wrist roll off")
        self.assertIn("UNEXPECTED BITS 0x20", bench_joint.switch_summary(0b100000))
        self.assertIn("do not home", bench_joint.switch_summary(0b100000))

    def test_it_is_in_the_motion_fingerprint(self):
        from scorbot import provenance
        self.assertIn("scorbot/inch_home.py", provenance._SOURCE_FILES)


if __name__ == "__main__":
    unittest.main()
