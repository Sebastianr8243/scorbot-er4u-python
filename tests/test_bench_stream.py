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
                   controller=None):
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
        with mock.patch.object(sys, "argv", argv), \
                mock.patch.object(bench_stream, "SimulatedScorbot", Arm), \
                mock.patch.object(bench_stream, "run_checks", return_value=[
                    Check("USB", False, "mocked: a test never reaches the arm")]) \
                as self.checks, \
                mock.patch("builtins.input", side_effect=answers.splitlines()), \
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
        # not a fault here: the run completes and the verdict says NO.
        with mock.patch.object(SimulatedController, "__init__",
                               _with(SimulatedController.__init__, stream_stuck=True)):
            self.assertEqual(self.run_script(TO_STREAM + "STREAM\n" + AFTER), 0)
        result = next(r for r in self.rows() if r["type"] == "after_stream")["result"]
        self.assertFalse(result["reached_target"])
        self.assertFalse(result["returned_home"])
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

    def test_limits_are_refused_before_preflight_or_any_connection(self):
        for extra, delta in (((), "1.5"), ((), "0"), ((), "0.001"), (("--hold-s", "9"), "1")):
            with self.subTest(extra=extra, delta=delta):
                with self.assertRaises(SystemExit) as caught:
                    self.run_script("", *extra, delta=delta, simulate=False)
                self.assertEqual(caught.exception.code, 2)
                self.assertFalse(self.output.exists())
                self.assertEqual(self.made, [])
                self.checks.assert_not_called()

    def test_wrist_and_gripper_are_not_offered(self):
        with self.assertRaises(SystemExit):
            self.run_script("", motor="wrist_motor_1")
        self.assertEqual(self.made, [])


def _with(original, **attributes):
    def init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        for name, value in attributes.items():
            setattr(self, name, value)
    return init


if __name__ == "__main__":
    unittest.main()
