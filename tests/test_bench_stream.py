"""examples/bench_stream.py: the supervised first streaming trial, rehearsed
through the simulator. No USB, no hardware."""

import contextlib
from importlib.util import find_spec
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from examples import bench_stream
from scorbot import ScorbotError
from scorbot.preflight import Check
from scorbot.simulated import SimulatedController, SimulatedScorbot

HAS_RUCKIG = find_spec("ruckig") is not None
TO_STREAM = "n\ng\n" "HOME\n" "y\ng\n" "home looked normal\nHOME_OK\n"
AFTER = "y\ng\n" "toward door\nnone\nno sounds\nnone\n" "n\ng\n"


class BenchStreamTests(unittest.TestCase):
    def run_script(self, answers, *extra, motor="base", delta="1", simulate=True,
                   controller=None, drift=None):
        made = self.made = []

        class Arm(SimulatedScorbot):
            def __init__(self, **kwargs):
                kwargs["controller"] = SimulatedController(step_delay_s=0.001,
                                                           **(controller or {}))
                super().__init__(**kwargs)
                made.append(self)


        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.output = Path(folder.name) / "stream.jsonl"
        argv = ["bench_stream.py", "--output", str(self.output), "--robot-id", "rehearsal-arm",
                "--arm-label", "arm-plate", "--controller-label", "controller-plate",
                "--driver", "none (simulated)", "--operator", "tester",
                "--start-pose-note", "desk rehearsal", "--motor", motor, "--delta", delta,
                "--hold-s", "0.2", "--acknowledge-supervised-motion", *extra]
        if simulate:
            argv.append("--simulate")
        self.printed = io.StringIO()
        replies = iter(answers.splitlines())

        def answer(question):
            if drift and "Type STREAM" in question:      # the arm sags while the operator reads
                for name, counts in drift.items():
                    made[0].sim.counts[name] += counts
            return next(replies)

        with mock.patch.object(sys, "argv", argv), \
                mock.patch.object(bench_stream, "SimulatedScorbot", Arm), \
                mock.patch.object(bench_stream, "run_checks", return_value=[
                    Check("USB", False, "mocked: a test never reaches the arm")]) \
                as self.checks, \
                mock.patch("builtins.input", side_effect=answer), \
                contextlib.redirect_stdout(self.printed), \
                contextlib.redirect_stderr(self.printed):
            return bench_stream.main()

    def rows(self):
        return [json.loads(line) for line in self.output.read_text(encoding="utf-8").splitlines()]

    def events(self):
        log = self.output.with_name(self.output.stem + ".controller.jsonl")
        return [json.loads(line)["event"] for line in log.read_text(encoding="utf-8").splitlines()]

    @unittest.skipUnless(HAS_RUCKIG, "ruckig not installed (pip install .[planning])")
    def test_one_motor_goes_out_one_degree_and_comes_back(self):
        self.assertEqual(self.run_script(TO_STREAM + "STREAM\n" + AFTER), 0)
        rows = self.rows()
        kinds = [row["type"] for row in rows]
        for kind in ("session", "home_complete", "stream_plan", "before_stream", "after_stream",
                     "operator_observation", "disabled"):
            self.assertIn(kind, kinds)
        session, plan = rows[0], next(r for r in rows if r["type"] == "stream_plan")
        self.assertEqual((session["data_source"], session["motor"]), ("simulated", "base"))
        self.assertTrue(session["motion_source_sha256"])
        self.assertEqual(plan["target_counts_from_home"], 142)
        self.assertEqual(plan["travel_cap_deg"], 2.0)
        result = next(r for r in rows if r["type"] == "after_stream")["result"]
        self.assertTrue(result["reached_target"])
        self.assertTrue(result["returned_home"])
        self.assertEqual((result["passed"], result["problems"]), (True, []))
        self.assertIn("Trial result: PASSED", self.printed.getvalue())
        self.assertEqual(result["furthest_counts_from_home"], 142)
        self.assertEqual(result["other_motors_max_counts"], 0)
        self.assertGreaterEqual(result["step_gap_ms"]["min"], 23.0)
        self.assertLessEqual(result["max_lead_counts"], 2 * 142)
        self.assertEqual([r["step"] for r in rows if r["type"] == "led_observation"],
                         ["after_connect", "after_enable", "after_stream", "after_disable"])
        events = self.events()
        self.assertIn("stream_complete", events)
        self.assertNotIn("stream_fault", events)
        out = self.printed.getvalue()
        self.assertIn("SIMULATED", out)
        self.assertIn("NOT an emergency stop", out)
        self.assertIn("Reached the target: yes", out)
        # one motor only: the others were never asked to leave home
        self.assertTrue(all(step[1:] == (0, 0) for step in self.made[0].sim.stream_sent))
        self.checks.assert_not_called()

    @unittest.skipUnless(HAS_RUCKIG, "ruckig not installed (pip install .[planning])")
    def test_a_negative_delta_goes_the_way_a_jog_would(self):
        self.assertEqual(self.run_script(TO_STREAM + "STREAM\n" + AFTER, delta="-0.5",
                                         motor="shoulder"), 0)
        plan = next(r for r in self.rows() if r["type"] == "stream_plan")
        jog = SimulatedScorbot().preview_jog("shoulder", -0.5)["motor_count_deltas"]["shoulder"]
        self.assertEqual(plan["target_counts_from_home"], jog)
        result = next(r for r in self.rows() if r["type"] == "after_stream")["result"]
        self.assertEqual(result["furthest_counts_from_home"], jog)

    def test_declining_at_the_stream_prompt_sends_no_stream(self):
        self.assertEqual(self.run_script(TO_STREAM + "no\n"), bench_stream.EXIT_DECLINED)
        self.assertEqual(self.rows()[-1]["type"], "operator_declined")
        self.assertNotIn("stream_start", self.events())
        self.assertFalse(any(command[0] == 21 for command in self.made[0].sim.commands))

    @unittest.skipUnless(HAS_RUCKIG, "ruckig not installed (pip install .[planning])")
    def test_an_arm_that_does_not_move_is_reported_plainly(self):
        # One degree is inside the two degree lead limit, so a stalled motor is
        # not an SDK fault. The script still finishes its prompts and disables,
        # but the trial has failed and the exit code says so.
        with mock.patch.object(SimulatedController, "__init__",
                               _with(SimulatedController.__init__, stream_stuck=True)):
            self.assertEqual(self.run_script(TO_STREAM + "STREAM\n" + AFTER),
                             bench_stream.EXIT_TRIAL_FAILED)
        rows = self.rows()
        result = next(r for r in rows if r["type"] == "after_stream")["result"]
        self.assertFalse(result["reached_target"])
        self.assertFalse(result["returned_home"])
        self.assertFalse(result["passed"])
        self.assertIn("did not reach the target", result["problems"])
        self.assertIn("disabled", [row["type"] for row in rows])
        self.assertIn("Trial result: FAILED", self.printed.getvalue())
        self.assertEqual(result["max_lead_counts"], 142)
        out = self.printed.getvalue()
        self.assertIn("Reached the target: NO", out)
        self.assertIn("!!! The arm did not follow the stream", out)
        self.assertTrue(all(abs(step[0]) <= 142 for step in self.made[0].sim.stream_sent))

    @unittest.skipUnless(HAS_RUCKIG, "ruckig not installed (pip install .[planning])")
    def test_a_stream_fault_fails_the_run_and_keeps_the_steps(self):
        with mock.patch.object(SimulatedController, "__init__",
                               _with(SimulatedController.__init__, stream_error_word=40)):
            with self.assertRaises(ScorbotError):
                self.run_script(TO_STREAM + "STREAM\n")
        rows = self.rows()
        self.assertEqual(rows[-1]["type"], "session_failed")
        failed = next(r for r in rows if r["type"] == "stream_failed")
        self.assertGreaterEqual(failed["result"]["steps"], 1)
        self.assertFalse(failed["result"]["reached_target"])
        self.assertIn("stream_fault", self.events())
        self.assertIn("physical stop", self.printed.getvalue())
        self.assertEqual(self.made[0].sim.stream_sent, [])

    @unittest.skipUnless(HAS_RUCKIG, "ruckig not installed (pip install .[planning])")
    def test_an_arm_that_is_not_at_home_is_refused_before_any_stream(self):
        for motor in ("base", "elbow"):          # the motor to move, and one that should not
            with self.subTest(drifted=motor):
                with self.assertRaises(ScorbotError) as caught:
                    self.run_script(TO_STREAM + "STREAM\n", drift={motor: 30})
                self.assertIn("not at home", str(caught.exception))
                self.assertEqual(self.rows()[-1]["type"], "session_failed")
                self.assertNotIn("stream_start", self.events())
                self.assertFalse(any(c[0] == 21 for c in self.made[0].sim.commands))

    def test_a_target_too_small_to_tell_from_standing_still_is_refused(self):
        # Four counts is inside the "arrived" tolerance: a motor that never
        # moved would have been reported as reached and returned.
        with self.assertRaises(SystemExit) as caught:
            self.run_script("", delta="0.03")
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("too small", self.printed.getvalue())
        self.assertEqual(self.made, [])

    def test_limits_are_refused_before_preflight_or_any_connection(self):
        for extra, delta in (((), "1.5"), ((), "0"), ((), "0.001"), (("--hold-s", "9"), "1")):
            with self.subTest(extra=extra, delta=delta):
                with self.assertRaises(SystemExit) as caught:
                    self.run_script("", *extra, delta=delta, simulate=False)
                self.assertEqual(caught.exception.code, 2)
                self.assertFalse(self.output.exists())
                self.assertEqual(self.made, [])
                self.checks.assert_not_called()

    def test_a_missing_planner_is_reported_before_preflight_or_any_connection(self):
        # Streaming needs Ruckig. Finding that out after homing would leave the
        # arm enabled and homed for nothing.
        for simulate in (True, False):
            with self.subTest(simulate=simulate),                     mock.patch.object(bench_stream, "find_spec", return_value=None):
                with self.assertRaises(SystemExit) as caught:
                    self.run_script("", simulate=simulate)
                self.assertEqual(caught.exception.code, 2)
                self.assertIn("--extra planning", self.printed.getvalue())
                self.assertFalse(self.output.exists())
                self.assertEqual(self.made, [])
                self.checks.assert_not_called()

    def test_wrist_and_gripper_are_not_offered(self):
        with self.assertRaises(SystemExit):
            self.run_script("", motor="wrist_motor_1")
        self.assertEqual(self.made, [])


class SummaryTests(unittest.TestCase):
    """The verdict, from step records alone."""

    @staticmethod
    def step(base, commanded=None, action="send", lead=0, shoulder=0, elbow=0, t=0):
        measured = {"base": base, "shoulder": shoulder, "elbow": elbow}
        return {"host_monotonic_ns": t, "action": action, "measured": measured,
                "commanded": {**measured, "base": base if commanded is None else commanded},
                "lead": {"base": lead, "shoulder": 0, "elbow": 0}}

    def good(self):
        return [self.step(0, 40), self.step(40, 100), self.step(100, 142), self.step(142),
                self.step(142, 80), self.step(80, 0), self.step(0, action="end")]

    def test_a_clean_out_and_back_passes(self):
        result = bench_stream.summarize(self.good(), "base", 142)
        self.assertEqual((result["passed"], result["problems"]), (True, []))

    def test_another_motor_moving_fails_the_trial(self):
        steps = self.good()
        steps[3] = self.step(142, elbow=30)
        result = bench_stream.summarize(steps, "base", 142)
        self.assertFalse(result["passed"])
        self.assertEqual(result["other_motors_max_counts"], 30)
        self.assertIn("another motor moved 30 counts", result["problems"])

    def test_travel_well_past_the_target_fails_the_trial(self):
        steps = self.good()
        steps[3] = self.step(190)
        result = bench_stream.summarize(steps, "base", 142)
        self.assertEqual(result["overshoot_counts"], 48)
        self.assertIn("went 48 counts past the target", result["problems"])

    def test_the_lead_counts_the_setpoint_just_sent_not_only_the_one_before(self):
        # StreamCore's own "lead" is measured before the new setpoint is made.
        steps = self.good()
        self.assertEqual(max(abs(s["lead"]["base"]) for s in steps), 0)
        self.assertEqual(bench_stream.summarize(steps, "base", 142)["max_lead_counts"], 80)

    def test_no_steps_at_all_is_a_failed_trial(self):
        result = bench_stream.summarize([], "base", 142)
        self.assertFalse(result["passed"])
        self.assertEqual(result["problems"], ["the stream took no steps"])


def _with(original, **attributes):
    def init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        for name, value in attributes.items():
            setattr(self, name, value)
    return init


if __name__ == "__main__":
    unittest.main()
