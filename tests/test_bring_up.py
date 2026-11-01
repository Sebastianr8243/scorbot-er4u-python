"""Desk-only ladder and first-fault aborts through existing SDK gates."""

import argparse
import contextlib
import dataclasses
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scorbot import SimulatedScorbot
from scorbot.simulated import SimulatedController
from scripts import bring_up


def options(**overrides):
    return argparse.Namespace(**dict({"simulate": True, "transport": "auto", "logs": "unused",
                                     "online": False, "joint": "base", "legacy_home": False,
                                     "stream": False, "stream_seconds": 1, "adaptive_pacing": False,
                                     "latency_ms": 0, "speed_pct": 25}, **overrides))


class BringUpTests(unittest.TestCase):
    def run_ladder(self, args=None, factory=None, ask=None):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "ladder.jsonl"
        lines = []
        with path.open("x", encoding="utf-8") as handle:
            result = bring_up.run(args or options(), handle, robot_factory=factory,
                                  ask=ask or mock.Mock(side_effect=AssertionError("unexpected prompt")),
                                  emit=lines.append)
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        return result, lines, rows

    def test_full_vendor_ladder_and_stream_keep_simulated_labels_and_log_first(self):
        robot = SimulatedScorbot()
        with mock.patch.object(robot, "start_stream", wraps=robot.start_stream) as stream:
            result, lines, rows = self.run_ladder(options(stream=True), lambda **kw: robot)
        self.assertEqual(result, 0, lines)
        self.assertTrue(all(line.startswith("SIMULATED ") for line in lines))
        self.assertTrue(all(row["label"] == "SIMULATED" and row["simulated"] for row in rows))
        self.assertEqual(rows[0]["event"], "ladder_start")
        passed = [row["step"] for row in rows if row["event"] == "step_pass"]
        self.assertEqual(passed, ["probe", "enable motors", "Home", "base +1 degree",
                                  "base back 1 degree", "1 degree sine stream", "disable motors"])
        self.assertTrue(any(row["event"] == "home_complete" and row["method"] == "vendor" for row in rows))
        stream.assert_called_once_with(adaptive_pacing=False, speed_fraction=0.25)
        self.assertFalse(robot.sim.motors_on)

    def test_the_stream_runs_at_the_operators_speed(self):
        robot = SimulatedScorbot()
        with mock.patch.object(robot, "start_stream", wraps=robot.start_stream) as stream:
            result, lines, _ = self.run_ladder(options(stream=True, speed_pct=100), lambda **kw: robot)
        self.assertEqual(result, 0, lines)
        stream.assert_called_once_with(adaptive_pacing=False, speed_fraction=1.0)

    def test_the_speed_flag_is_a_percent_from_1_to_100(self):
        for bad in ("0", "101", "nan", "fast"):
            with self.subTest(bad=bad), contextlib.redirect_stderr(io.StringIO()),                     self.assertRaises(SystemExit):
                bring_up.parse_args(["--simulate", "--logs", "x", "--speed-pct", bad])
        self.assertEqual(bring_up.parse_args(["--simulate", "--logs", "x"]).speed_pct, 25)

    def test_a_collision_flag_during_the_stream_aborts_the_ladder(self):
        robot = SimulatedScorbot()
        seen = {"stream": False, "reads": 0}
        start_stream, motion_state = robot.start_stream, robot._motion_state

        def streaming(*args, **kwargs):
            seen["stream"] = seen["stream"] or "adaptive_pacing" in kwargs   # only the ladder passes it
            return start_stream(*args, **kwargs)

        def state_with_flag(*args, **kwargs):
            state = motion_state(*args, **kwargs)
            if not seen["stream"]:
                return state
            seen["reads"] += 1      # the flag shows for the first few reads and clears before the final check
            return dataclasses.replace(state, collision_bits=1) if seen["reads"] <= 3 else state

        robot.start_stream, robot._motion_state = streaming, state_with_flag
        ask = mock.Mock(side_effect=("EMPTY", "CLEAR", "HOME"))
        result, lines, rows = self.run_ladder(options(simulate=False, online=True, stream=True),
                                             lambda **kw: robot, ask)
        self.assertEqual(result, 1, lines)
        self.assertEqual([r["step"] for r in rows if r["event"] == "step_fail"], ["1 degree sine stream"])

    def test_the_settle_wait_keeps_refreshing_the_return_target(self):
        robot = SimulatedScorbot()
        targets = []
        start_stream = robot.start_stream

        class Spy:
            def __init__(self, stream):
                self.stream = stream

            def set_target(self, target):
                targets.append(dict(target))
                return self.stream.set_target(target)

        class Wrapper:
            def __init__(self, context):
                self.context = context

            def __enter__(self):
                return Spy(self.context.__enter__())

            def __exit__(self, *exc):
                return self.context.__exit__(*exc)

        robot.start_stream = lambda **kw: Wrapper(start_stream(**kw))
        # No reading counts as settled, so the wait runs its whole (shortened) time.
        with mock.patch.object(bring_up, "SETTLE_SECONDS", 0.3), \
                mock.patch.object(bring_up.limits, "STABLE_COUNTS", -1):
            result, lines, rows = self.run_ladder(options(stream=True), lambda **kw: robot)
        self.assertEqual(result, 0, lines)
        back = targets[-1]
        self.assertGreaterEqual(targets.count(back), 4)     # sent once, then again while waiting

    def test_first_injected_fault_aborts_before_home_or_jog_and_latches(self):
        controller = SimulatedController()
        robot = SimulatedScorbot(controller=controller, command_timeout=0.5)
        enable = robot.enable

        def fail_enable():
            controller.inject("controller_error")
            enable()

        robot.enable = fail_enable
        result, lines, rows = self.run_ladder(factory=lambda **kw: robot)
        self.assertEqual(result, 1, lines)
        self.assertIsNotNone(robot._fault)
        self.assertEqual([r["step"] for r in rows if r["event"] == "step_fail"], ["enable motors"])
        self.assertFalse(any(r["event"] in ("home_start", "motion_start", "stream_start") for r in rows))
        self.assertFalse(any(c[0] in (18, 22, 21, 4, 5) for c in controller.commands))

    def test_real_mode_needs_online_before_factory(self):
        factory = mock.Mock(side_effect=AssertionError("real link must not open"))
        result, lines, rows = self.run_ladder(options(simulate=False, online=False), factory, mock.Mock())
        self.assertEqual(result, 1, lines)
        factory.assert_not_called()
        self.assertEqual(rows[-1]["passed"], False)

    def test_vendor_typed_empty_clear_home_and_legacy_selection(self):
        for legacy, answers in ((False, ("EMPTY", "CLEAR", "HOME")),
                                (True, ("HOME",))):
            ask = mock.Mock(side_effect=answers)
            result, lines, rows = self.run_ladder(options(simulate=False, online=True,
                                                         legacy_home=legacy), SimulatedScorbot, ask)
            self.assertEqual(result, 0, lines)
            self.assertEqual(ask.call_count, len(answers))
            method = "legacy" if legacy else "vendor"
            complete = next(row for row in rows if row["event"] == "home_complete")
            self.assertEqual(complete.get("method", "legacy"), method)

    def test_wrong_home_confirmation_aborts_without_home_commands(self):
        robot = SimulatedScorbot()
        result, lines, rows = self.run_ladder(options(simulate=False, online=True),
                                             lambda **kw: robot, mock.Mock(side_effect=("empty",)))
        self.assertEqual(result, 1, lines)
        self.assertFalse(any(c[0] in (18, 22, 4, 5, 21) for c in robot.sim.commands))
        self.assertFalse(rows[-1]["passed"])

    def test_cli_logs_exclusively_before_connection(self):
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(bring_up, "run", return_value=0) as run:
                self.assertEqual(bring_up.main(["--simulate", "--logs", folder]), 0)
            args, handle = run.call_args.args
            self.assertTrue(args.simulate)
            self.assertEqual(handle.mode, "x")
            self.assertEqual(len(list(Path(folder).glob("simulated-*.jsonl"))), 1)
        with mock.patch("pathlib.Path.open", side_effect=OSError("disk full")):
            with mock.patch.object(bring_up, "run") as run, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(bring_up.main(["--simulate", "--logs", tempfile.gettempdir()]), 1)
            run.assert_not_called()

    def test_log_failure_aborts_before_enable_and_closes_the_simulated_session(self):
        robot = SimulatedScorbot()
        with tempfile.TemporaryDirectory() as folder:
            with (Path(folder) / "fault.jsonl").open("x", encoding="utf-8") as handle:
                broken = mock.Mock(wraps=handle)
                broken.name = handle.name

                def write(line):
                    row = json.loads(line)
                    if row["event"] == "step_start" and row["step"] == "enable motors":
                        raise OSError("disk full")
                    return handle.write(line)

                broken.write.side_effect = write
                lines = []
                result = bring_up.run(options(), broken, robot_factory=lambda **kw: robot, emit=lines.append)
        self.assertEqual(result, 1, lines)
        self.assertIsNone(robot._device)
        self.assertIsNotNone(robot._fault)
        self.assertFalse(any(c[0] in (17, 18, 22, 4, 5, 21) for c in robot.sim.commands))
