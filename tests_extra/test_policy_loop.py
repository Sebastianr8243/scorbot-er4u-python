"""examples/policy_loop.py: the clamp on its own, then the supervised policy run rehearsed through
the simulator. No USB, no hardware."""

import contextlib
from importlib.util import find_spec
import io
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from examples import policy_loop
from scorbot.preflight import Check
from scorbot.simulated import SimulatedController, SimulatedScorbot

HAS_RUCKIG = find_spec("ruckig") is not None
ZERO = {"base": 0.0, "shoulder": 0.0, "elbow": 0.0}
TO_POLICY = "n\ng\n" "HOME\n" "y\ng\n" "home looked normal\nHOME_OK\n"
AFTER = "y\ng\n" "toward door\nnone\nno sounds\nnone\n" "n\ng\n"


class ClampTests(unittest.TestCase):
    def clamp(self, delta, target=ZERO, measured=ZERO, **kw):
        return policy_loop.clamp(delta, target, measured, cap_deg=kw.pop("cap_deg", 2.0), **kw)

    def test_a_delta_is_cut_to_the_step_limit(self):
        target, clamped = self.clamp({"base": 3.0})
        self.assertEqual(target["base"], policy_loop.STEP_MAX_DEG)
        self.assertEqual(clamped, ["base"])

    def test_a_small_delta_passes_unchanged(self):
        target, clamped = self.clamp({"base": 0.2, "elbow": -0.3})
        self.assertEqual((target["base"], target["elbow"], clamped), (0.2, -0.3, []))

    def test_the_target_never_passes_the_travel_cap(self):
        target = {"base": 1.9, "shoulder": 0.0, "elbow": 0.0}
        new, clamped = self.clamp({"base": 0.5}, target=target, measured=target)
        self.assertEqual(new["base"], 2.0)
        self.assertEqual(clamped, ["base"])

    def test_the_target_stays_near_the_measured_position(self):
        target = {"base": 1.0, "shoulder": 0.0, "elbow": 0.0}      # the arm lags far behind
        new, _ = self.clamp({"base": 0.5}, target=target, measured=ZERO)
        self.assertLessEqual(new["base"], policy_loop.LEAD_MARGIN_DEG)

    def test_bad_input_holds_the_motor(self):
        for bad in (math.nan, math.inf, "1", None, True):
            target, clamped = self.clamp({"base": bad})
            self.assertEqual(target, ZERO, bad)
            self.assertEqual(clamped, ["base"], bad)

    def test_unknown_motor_and_none_hold(self):
        self.assertEqual(self.clamp({"wrist_motor_1": 1.0}), (ZERO, ["wrist_motor_1"]))
        self.assertEqual(self.clamp(None), (ZERO, []))

    def test_the_reach_policy_walks_to_its_goal(self):
        policy, target = policy_loop.reach_policy({"base": 1.0}), dict(ZERO)
        for _ in range(5):
            target, _ = self.clamp(policy({"target_deg": target}), target=target, measured=target)
        self.assertEqual(target["base"], 1.0)

    def test_parse_reach_rejects_a_wrist_and_nonsense(self):
        self.assertEqual(policy_loop.parse_reach("base=1,shoulder=-0.5"),
                         {"base": 1.0, "shoulder": -0.5})
        for text in ("wrist_motor_1=1", "base", "base=nan"):
            with self.assertRaises(ValueError):
                policy_loop.parse_reach(text)


@unittest.skipUnless(HAS_RUCKIG, "streaming needs the planning extra")
class PolicyRunTests(unittest.TestCase):
    def run_script(self, answers, *extra, policy_args=("--policy", "reach:base=1")):
        made = self.made = []

        class Arm(SimulatedScorbot):
            def __init__(self, **kwargs):
                kwargs["controller"] = SimulatedController(step_delay_s=0.001)
                super().__init__(**kwargs)
                made.append(self)

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.output = self.folder / "policy.jsonl"
        argv = ["policy_loop.py", "--output", str(self.output), "--robot-id", "rehearsal-arm",
                "--arm-label", "arm-plate", "--controller-label", "controller-plate",
                "--driver", "none (simulated)", "--operator", "tester",
                "--start-pose-note", "desk rehearsal", "--steps", "8", "--rate-hz", "10",
                "--acknowledge-supervised-motion", "--simulate", *policy_args, *extra]
        replies = iter(answers.splitlines())
        self.printed = io.StringIO()
        with mock.patch.object(sys, "argv", argv), \
                mock.patch.object(policy_loop, "SimulatedScorbot", Arm), \
                mock.patch.object(policy_loop, "run_checks", return_value=[
                    Check("USB", False, "mocked: a test never reaches the arm")]), \
                mock.patch("builtins.input", side_effect=lambda *_: next(replies)), \
                contextlib.redirect_stdout(self.printed), contextlib.redirect_stderr(self.printed):
            return policy_loop.main()

    def rows(self):
        return [json.loads(line) for line in self.output.read_text(encoding="utf-8").splitlines()]

    def test_a_reach_run_moves_the_arm_and_brings_it_home(self):
        self.assertEqual(self.run_script(TO_POLICY + "POLICY\n" + AFTER), 0, self.printed.getvalue())
        rows = self.rows()
        steps = [r for r in rows if r.get("type") == "policy_step"]
        self.assertEqual(len(steps), 8)
        self.assertEqual(max(abs(s["target_deg"]["base"]) for s in steps), 1.0)
        self.assertTrue(all(s["target_deg"]["shoulder"] == 0 for s in steps))
        after = next(r for r in rows if r.get("type") == "after_policy")
        self.assertTrue(after["result"]["passed"], after["result"])
        self.assertIn("Run result: PASSED", self.printed.getvalue())

    def test_a_simulated_run_exports_to_a_dataset_that_reloads_identically(self):
        from scripts import export_policy_dataset as ex
        self.assertEqual(self.run_script(TO_POLICY + "POLICY\n" + AFTER), 0, self.printed.getvalue())
        out = self.folder / "dataset"
        meta, refused = ex.export([self.output], out)
        self.assertEqual((meta["frames"], meta["data_source"], refused), (8, "simulated", []))
        self.assertEqual(ex.verify(out, [self.output]), [])

    def test_a_wrong_typed_word_declines_before_any_stream(self):
        self.assertEqual(self.run_script(TO_POLICY + "nope\n"), 3)
        events = [r.get("type") for r in self.rows()]
        self.assertNotIn("policy_step", events)

    def test_a_policy_that_crashes_holds_the_arm_and_ends_the_run(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        bad = Path(scratch.name) / "bad_policy.py"
        bad.write_text("def policy(obs):\n    raise RuntimeError('boom')\n", encoding="utf-8")
        code = self.run_script(TO_POLICY + "POLICY\n" + AFTER, policy_args=("--policy-file", str(bad)))
        steps = [r for r in self.rows() if r.get("type") == "policy_step"]
        self.assertEqual(len(steps), policy_loop.MAX_POLICY_ERRORS)
        self.assertTrue(all(s["target_deg"]["base"] == 0 for s in steps))
        self.assertEqual(code, 1)        # ended early on repeated policy errors

    def test_a_goal_past_the_travel_cap_is_refused_up_front(self):
        with self.assertRaises(SystemExit):
            self.run_script("", policy_args=("--policy", "reach:base=5"))


if __name__ == "__main__":
    unittest.main()
