"""Desk-only ladder and first-fault aborts through existing SDK gates."""

import argparse
import contextlib
import dataclasses
import io
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scorbot import SimulatedScorbot, vendor_model, vendor_move
from scorbot.calibration import signed_count_delta
from scorbot.simulated import SimulatedController
from scorbot.vendor_home_source import VendorHomeSource
from scripts import bring_up


def options(**overrides):
    return argparse.Namespace(**dict({"simulate": True, "transport": "auto", "logs": "unused",
                                     "online": False, "joint": "base",
                                     "stream": False, "stream_seconds": 1, "adaptive_pacing": False,
                                     "latency_ms": 0, "speed_pct": 25, "velocity_jog": False}, **overrides))


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

    def test_degree_moves_use_vendor_targets_and_return_to_start(self):
        fixture = json.loads((Path(__file__).parent / "fixtures" / "dll_lab_move_joint.json").read_text(encoding="utf-8"))
        self.assertIn(1, [move["speed_percent"] for move in fixture["moves"]])
        start = vendor_move.home_joints()
        # Vendor joint signs and motor names differ from preview_jog, including the coupled wrist motors.
        deltas = {"base": (-141, 0, 0, 0, 0), "shoulder": (0, -113, 113, 27, -27),
                  "elbow": (0, 0, 113, 27, -27)}
        for joint in deltas:
            with self.subTest(joint=joint):
                robot = SimulatedScorbot()
                target = list(start)
                target[vendor_model.JOINTS.index(joint)] += math.radians(1)
                target = tuple(target)
                with mock.patch.object(robot, "move_joint_vendor", wraps=robot.move_joint_vendor) as move, \
                        mock.patch.object(robot, "jog_joint", side_effect=AssertionError("legacy jog")) as jog, \
                        mock.patch.object(robot, "preview_jog", side_effect=AssertionError("legacy preview")), \
                        mock.patch.object(bring_up, "_check_move", wraps=bring_up._check_move) as check:
                    result, lines, rows = self.run_ladder(options(joint=joint), lambda robot=robot, **kw: robot)
                self.assertEqual(result, 0, lines)
                jog.assert_not_called()
                self.assertEqual(move.call_args_list, [mock.call(target, speed_percent=1),
                                                       mock.call(start, speed_percent=1)])
                expected = dict(zip(vendor_model.MOTORS, deltas[joint]))
                self.assertEqual(len(check.call_args_list), 3)  # out, back, then original encoder counts
                for call, sign in zip(check.call_args_list[:2], (1, -1)):
                    before, after, wanted = call.args
                    self.assertEqual(wanted, {motor: sign * value for motor, value in expected.items()})
                    self.assertEqual(set(wanted), set(before.encoder_counts) - {"gripper"})
                    for motor in before.encoder_counts:
                        self.assertEqual(signed_count_delta(after.encoder_counts[motor], before.encoder_counts[motor]),
                                         wanted.get(motor, 0))
                self.assertEqual(check.call_args_list[-1].args[2], {})
                plans = [row for row in rows if row["event"] == "step_motion"]
                self.assertEqual([row["step"] for row in plans], [f"{joint} +1 degree", f"{joint} back 1 degree"])
                actual = [row for row in rows if row["event"] == "vendor_joint_start"]
                self.assertEqual(len(actual), 2)
                for plan, sent, a, b in zip(plans, actual, (start, target), (target, start)):
                    self.assertEqual(plan["method"], "move_joint_vendor")
                    self.assertEqual(plan["speed_percent"], 1)
                    self.assertEqual(plan["start"], list(a))
                    self.assertEqual(plan["target"], list(b))
                    self.assertEqual(plan["total_ms"], vendor_move.duration_ms(a, b, speed_percent=1))
                    self.assertEqual(plan["total_ms"], sent["total_ms"])

    def test_degree_moves_start_at_the_current_vendor_target(self):
        robot = SimulatedScorbot()
        home, move = robot.vendor_home, robot.move_joint_vendor
        start = tuple(value + math.radians(0.3) for value in vendor_move.home_joints())

        def offset_home(**kwargs):
            home(**kwargs)
            move(start, speed_percent=1)

        with mock.patch.object(robot, "vendor_home", side_effect=offset_home), \
                mock.patch.object(robot, "move_joint_vendor", wraps=move) as commanded:
            result, lines, _ = self.run_ladder(factory=lambda **kw: robot)
        self.assertEqual(result, 0, lines)
        target = (start[0] + math.radians(1), *start[1:])
        self.assertEqual(commanded.call_args_list, [mock.call(target, speed_percent=1),
                                                   mock.call(start, speed_percent=1)])

    def test_stop_during_a_vendor_degree_move_fails_the_step(self):
        for step in ("base +1 degree", "base back 1 degree"):
            with self.subTest(step=step):
                robot = SimulatedScorbot()
                move, send = robot.move_joint_vendor, VendorHomeSource._send
                active = {"step": None, "packets": 0}

                def moving(*args, active=active, move=move, **kwargs):
                    active["step"] = "base +1 degree" if active["step"] is None else "base back 1 degree"
                    active["packets"] = 0
                    return move(*args, **kwargs)

                def stop(out, sequence, send=send, active=active, step=step, robot=robot):
                    packet = send(out, sequence)
                    if active["step"] == step:
                        active["packets"] += 1
                        if active["packets"] == 5:
                            robot.request_stop()
                    return packet

                with mock.patch.object(robot, "move_joint_vendor", side_effect=moving) as commanded, \
                        mock.patch.object(VendorHomeSource, "_send", new=staticmethod(stop)):
                    result, lines, rows = self.run_ladder(options(velocity_jog=True, stream=True),
                                                         lambda robot=robot, **kw: robot)
                self.assertEqual(result, 1, lines)
                (failure,) = [row for row in rows if row["event"] == "step_fail"]
                self.assertEqual(failure["step"], step)
                self.assertIn("stop request", failure["error"])
                self.assertEqual(commanded.call_count, 1 if step == "base +1 degree" else 2)
                self.assertTrue(any(row["event"] == "vendor_joint_stopped" for row in rows))
                self.assertFalse(any(row["event"] in ("velocity_jog_start", "stream_start") for row in rows))
                self.assertIsNotNone(robot._fault)
                self.assertIsNone(robot._device)
                self.assertFalse(robot.sim.motors_on)
                self.assertFalse(rows[-1]["passed"])

    def test_degree_move_rejects_unexpected_motor_counts(self):
        robot = SimulatedScorbot()
        move = robot.move_joint_vendor

        def drift(*args, **kwargs):
            result = move(*args, **kwargs)
            robot.sim.counts["wrist_motor_2"] += 55
            return result

        with mock.patch.object(robot, "move_joint_vendor", side_effect=drift) as commanded:
            result, lines, rows = self.run_ladder(factory=lambda **kw: robot)
        self.assertEqual(result, 1, lines)
        commanded.assert_called_once()
        (failure,) = [row for row in rows if row["event"] == "step_fail"]
        self.assertEqual(failure["step"], "base +1 degree")
        self.assertIn("Unexpected wrist_motor_2 count change", failure["error"])

    def test_degree_return_still_checks_the_original_encoder_counts(self):
        robot = SimulatedScorbot()
        move = robot.move_joint_vendor

        def drift(*args, **kwargs):
            result = move(*args, **kwargs)
            # Each 15-count gripper drift is tolerated; together they exceed the 20-count origin check.
            robot.sim.counts["gripper"] += 15
            return result

        with mock.patch.object(robot, "move_joint_vendor", side_effect=drift), \
                mock.patch.object(bring_up, "_check_move", wraps=bring_up._check_move) as checked:
            result, lines, rows = self.run_ladder(factory=lambda **kw: robot)
        self.assertEqual(result, 1, lines)
        self.assertEqual(checked.call_count, 3)
        (failure,) = [row for row in rows if row["event"] == "step_fail"]
        self.assertEqual(failure["step"], "base back 1 degree")
        self.assertIn("Unexpected gripper count change: 30", failure["error"])

    def test_the_velocity_jog_runs_one_step_per_axis_out_and_back(self):
        robot = SimulatedScorbot()
        result, lines, rows = self.run_ladder(options(velocity_jog=True), lambda **kw: robot)
        self.assertEqual(result, 0, lines)
        passed = [row["step"] for row in rows if row["event"] == "step_pass"]
        self.assertEqual(passed, ["probe", "enable motors", "Home", "base +1 degree", "base back 1 degree",
                                  "base velocity jog", "shoulder velocity jog", "elbow velocity jog",
                                  "disable motors"])
        speed = bring_up.VELOCITY_JOG_SPEED
        self.assertLessEqual(speed, 5)                                   # a low speed for a first trial
        starts = [(row["joint"], row["speed"]) for row in rows if row["event"] == "velocity_jog_start"]
        self.assertEqual(starts, [(axis, sign * speed) for axis in ("base", "shoulder", "elbow") for sign in (1, -1)])
        ends = [row for row in rows if row["event"] == "velocity_jog_complete"]
        self.assertEqual(len(ends), 6)

    def test_each_velocity_jog_waits_for_an_ok_and_declining_skips_it(self):
        robot = SimulatedScorbot()
        ask = mock.Mock(side_effect=("EMPTY", "CLEAR", "HOME", "JOG", "no", "JOG"))
        result, lines, rows = self.run_ladder(options(simulate=False, online=True, velocity_jog=True),
                                             lambda **kw: robot, ask)
        self.assertEqual(result, 0, lines)
        self.assertEqual(ask.call_count, 6)
        self.assertEqual([row["step"] for row in rows if row["event"] == "step_skipped"], ["shoulder velocity jog"])
        jogged = {row["joint"] for row in rows if row["event"] == "velocity_jog_start"}
        self.assertEqual(jogged, {"base", "elbow"})
        self.assertTrue(any("SKIPPED shoulder velocity jog" in line for line in lines))

    def test_a_velocity_jog_that_ends_by_itself_is_a_fail(self):
        robot = SimulatedScorbot()

        class EndedAtTheWindow:
            reason = "limit"

            def renew(self):
                pass

            def stop(self):
                return robot._motion_state()

        with mock.patch.object(robot, "start_velocity_jog", return_value=EndedAtTheWindow()):
            result, lines, rows = self.run_ladder(options(velocity_jog=True), lambda **kw: robot)
        self.assertEqual(result, 1, lines)
        (fail,) = [row for row in rows if row["event"] == "step_fail"]
        self.assertEqual(fail["step"], "base velocity jog")
        self.assertIn("limit", fail["error"])

    def test_without_the_flag_there_is_no_velocity_jog(self):
        result, _, rows = self.run_ladder(options(), lambda **kw: SimulatedScorbot())
        self.assertEqual(result, 0)
        self.assertFalse(any(row["event"] == "velocity_jog_start" for row in rows))
        self.assertFalse(bring_up.parse_args(["--simulate", "--logs", "x"]).velocity_jog)
        self.assertTrue(bring_up.parse_args(["--simulate", "--logs", "x", "--velocity-jog"]).velocity_jog)

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
            if not seen["stream"] or robot._stream is None:
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
        clock = {"ms": 0}
        fake_time = mock.Mock(
            monotonic=lambda: clock["ms"] / 1000,
            sleep=lambda seconds: clock.__setitem__("ms", clock["ms"] + round(seconds * 1000)))
        counts = dict.fromkeys(bring_up.JOINTS, 0)
        robot = mock.MagicMock(_home_counts=counts)
        robot._motion_state.return_value = mock.Mock(
            encoder_counts=counts, fault=None, collision_bits=0, home_switch_bits=0)
        robot.preview_jog.return_value = {"motor_count_deltas": {"base": 142}}
        stream = robot.start_stream.return_value.__enter__.return_value

        # Exercise the whole wait without shortening a real follower's return trajectory.
        with mock.patch.object(bring_up, "time", fake_time), \
                mock.patch.object(bring_up.limits, "STABLE_COUNTS", -1):
            bring_up._sine(robot, "base", 1, False, 0.25)
        back = {m: 0 for m in ("base", "shoulder", "elbow")}
        waits = round(bring_up.SETTLE_SECONDS / 0.05)
        self.assertEqual(clock["ms"], 1000 + round(bring_up.SETTLE_SECONDS * 1000))
        self.assertEqual(stream.set_target.call_args_list[-(waits + 1):],
                         [mock.call(back)] * (waits + 1))     # once, then every settle poll

    def test_a_return_55_counts_below_zero_is_rejected_across_the_seam(self):
        counts = dict.fromkeys(bring_up.JOINTS, 0)
        before = mock.Mock(encoder_counts=counts)
        after = mock.Mock(encoder_counts={**counts, "base": 65481})   # 55 below, mod 65536
        with self.assertRaisesRegex(bring_up.ScorbotError, "Unexpected base count change: -55; abort"):
            bring_up._check_move(before, after, {})

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

    def test_vendor_typed_empty_clear_home(self):
        ask = mock.Mock(side_effect=("EMPTY", "CLEAR", "HOME"))
        result, lines, rows = self.run_ladder(options(simulate=False, online=True),
                                             SimulatedScorbot, ask)
        self.assertEqual(result, 0, lines)
        self.assertEqual(ask.call_count, 3)
        complete = next(row for row in rows if row["event"] == "home_complete")
        self.assertEqual(complete["method"], "vendor")

    def test_the_legacy_home_flag_is_gone(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            bring_up.parse_args(["--logs", "x", "--legacy-home"])

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
