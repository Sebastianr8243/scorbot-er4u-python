import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
MOTION_ORDERS = set(range(4, 16)) | {18}


def ready_robot(**kwargs):
    from scorbot.simulated import SimulatedScorbot
    robot = SimulatedScorbot(**kwargs).connect()
    robot.enable()
    robot.home(start_position_confirmed=True)
    return robot


class EncoderTests(unittest.TestCase):
    def test_packet_round_trips_through_the_real_decoder(self):
        from scorbot.simulated import encode_packet
        from scorbot.state import JOINTS, decode_state
        for value in (0, 1, -1, 142, -142, -65535, 65535):
            counts = {name: 0 for name in JOINTS}
            counts["base"] = value
            state = decode_state(encode_packet(counts, switch_bits=5), connected=True,
                                 enabled=False, homed=False, fault=None)
            self.assertEqual(state.signed_encoder_counts["base"], value)
            self.assertEqual(state.home_switch_bits, 5)

    def test_out_of_range_counts_are_rejected(self):
        from scorbot.simulated import encode_packet
        for value in (65536, -65536):
            with self.assertRaises(ValueError):
                encode_packet({"base": value})


class SimulatedRobotTests(unittest.TestCase):
    def test_full_cycle_moves_by_exactly_the_planned_counts(self):
        robot = ready_robot()
        try:
            before = robot.get_state()
            plan = robot.preview_jog("base", 1.0,
                                     starting_signed_counts=before.signed_encoder_counts)
            after = robot.jog_joint("base", 1.0)
            self.assertTrue(after.simulated)
            self.assertEqual(after.signed_encoder_counts["base"],
                             before.signed_encoder_counts["base"]
                             + plan["motor_count_deltas"]["base"])
            self.assertGreater(after.packet_index, before.packet_index)
            self.assertEqual(abs(plan["motor_count_deltas"]["base"]), 142)
        finally:
            robot.disconnect()

    def test_home_resets_counts_to_home_counts(self):
        from scorbot.simulated import SimulatedController
        controller = SimulatedController(start_counts={"base": 900, "elbow": -300})
        robot = ready_robot(controller=controller)
        try:
            self.assertEqual(robot.get_state().signed_encoder_counts["base"], 0)
            self.assertEqual(robot.get_state().signed_encoder_counts["elbow"], 0)
        finally:
            robot.disconnect()

    def test_facade_gates_are_the_real_ones(self):
        from scorbot import ScorbotError
        from scorbot.simulated import SimulatedScorbot
        robot = SimulatedScorbot().connect()
        try:
            with self.assertRaises(ScorbotError):
                robot.jog_joint("base", 1.0)  # not enabled / homed
            robot.enable()
            robot.home(start_position_confirmed=True)
            queued = len(robot.sim.commands)
            with self.assertRaises(ScorbotError):
                robot.jog_joint("wrist_pitch", 1.0)
            self.assertEqual(len(robot.sim.commands), queued)
            with self.assertRaises(ValueError):
                robot.jog_joint("base", 6.0)
        finally:
            robot.disconnect()

    def assert_latched(self, robot):
        from scorbot import ScorbotError
        with self.assertRaises(ScorbotError) as caught:
            robot.enable()
        self.assertIn("faulted", str(caught.exception).lower())
        with self.assertRaises(ScorbotError):
            robot.home(start_position_confirmed=True)
        with self.assertRaises((ScorbotError, ValueError)):
            robot.jog_joint("base", 1.0)

    def test_every_fault_kind_latches_the_session(self):
        from scorbot import ScorbotError
        from scorbot.simulated import FAULT_KINDS
        for kind in FAULT_KINDS:
            with self.subTest(kind=kind):
                robot = ready_robot(command_timeout=0.3)
                try:
                    robot.sim.inject(kind)
                    with self.assertRaises(ScorbotError):
                        robot.jog_joint("base", 1.0)
                    self.assert_latched(robot)
                finally:
                    robot.disconnect()

    def test_stale_feedback_queues_no_motion(self):
        from scorbot import ScorbotError
        robot = ready_robot()
        try:
            queued = len(robot.sim.commands)
            robot.sim.inject("stale_feedback")
            with self.assertRaises(ScorbotError):
                robot.jog_joint("base", 1.0)
            new_orders = {payload[0] for payload in robot.sim.commands[queued:]}
            self.assertFalse(new_orders & MOTION_ORDERS, new_orders)
        finally:
            robot.disconnect()

    def test_count_overflow_is_a_controller_error_not_a_wrap(self):
        from scorbot import ScorbotError
        from scorbot.simulated import SimulatedController
        controller = SimulatedController(home_counts={"base": 65500})
        robot = ready_robot(controller=controller)
        try:
            with self.assertRaises(ScorbotError):
                robot.jog_joint("base", 1.0)
            self.assertEqual(controller.counts["base"], 65500)
            self.assert_latched(robot)
        finally:
            robot.disconnect()

    def test_connection_lifecycle_matches_the_real_facade(self):
        from scorbot import ScorbotError
        from scorbot.simulated import SimulatedScorbot
        robot = SimulatedScorbot().connect()
        with self.assertRaises(ScorbotError):
            robot.connect()  # already connected
        thread = robot._command_thread
        robot.disconnect()
        self.assertFalse(thread.is_alive())
        with self.assertRaises(ScorbotError):
            robot.get_state()  # not connected
        robot.connect()  # a clean reconnect works, as on hardware
        robot.disconnect()

    def test_event_log_rows_are_marked_simulated(self):
        from scorbot.simulated import SimulatedScorbot
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "events.jsonl"
            with SimulatedScorbot(log_path=log) as robot:
                robot.enable()
            rows = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertTrue(rows)
        self.assertTrue(all(row["simulated"] is True for row in rows))

    def test_import_loads_no_usb_and_leaves_sys_path_alone(self):
        code = ("import sys; before = list(sys.path); import scorbot, scorbot.simulated;"
                "print('usb' in sys.modules, sys.path == before)")
        result = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT,
                                capture_output=True, text=True)
        self.assertEqual(result.stdout.split(), ["False", "True"], result.stderr)

    def test_robot_state_keeps_positional_construction(self):
        from scorbot.state import RobotState
        state = RobotState("t", {}, {}, 0, True, False, False, None)
        self.assertFalse(state.simulated)


LABELS = ["--robot-id", "rehearsal-arm", "--arm-label", "arm-plate",
          "--controller-label", "controller-plate", "--driver", "none (simulated)",
          "--operator", "tester"]
BENCH_TYPES = ["session", "connected", "home_complete", "home_observation",
               "motion_preview", "before_jog", "after_jog", "operator_observation",
               "disabled"]
BENCH_ANSWERS = "HOME\nhome looked normal\nHOME_OK\nMOVE\ntoward door ~1 deg\nnone\nno LEDs\nnone\n"


def run_script(path, *args, stdin=""):
    return subprocess.run([sys.executable, str(REPO_ROOT / path), *map(str, args)],
                          cwd=REPO_ROOT, input=stdin, capture_output=True, text=True,
                          timeout=120)


def sessions_in(folder):
    return sorted(p for p in Path(folder).iterdir() if (p / "session.mcap").exists())


class SimulatedG1RehearsalTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.logs = Path(self._tmp.name) / "rehearsal"

    def tearDown(self):
        self._tmp.cleanup()

    def idle(self, seconds="1", hz="2", name="idle-01.jsonl"):
        return run_script("examples/record_raw_state.py", "--output", self.logs / name,
                          *LABELS, "--pose-note", "desk rehearsal", "--seconds", seconds,
                          "--hz", hz, "--simulate", "--acknowledge-connect-handshake")

    def test_idle_sample_counts_match_at_the_rate_limits(self):
        from scorbot.session import load_session
        for seconds, hz, expected in (("1", "0.2", 1), ("1.05", "10", 11)):
            with self.subTest(seconds=seconds, hz=hz):
                name = f"idle-{hz}.jsonl"
                result = self.idle(seconds, hz, name)
                self.assertEqual(result.returncode, 0, result.stderr)
                rows = [json.loads(l) for l in (self.logs / name).read_text().splitlines()]
                self.assertEqual(sum(r["type"] == "sample" for r in rows), expected)
                session = load_session(self.logs / "sessions" / rows[0]["mcap_session"])
                self.assertEqual(sum(e["topic"] == "/robot/state" for e in session.events),
                                 expected)

    def bench(self, stdin=BENCH_ANSWERS, name="base-first-01.jsonl", *extra):
        return run_script("examples/bench_joint.py", "--output", self.logs / name, *LABELS,
                          "--start-pose-note", "desk rehearsal", "--joint", "base",
                          "--delta", "1", "--simulate", "--acknowledge-supervised-motion",
                          *extra, stdin=stdin)

    def test_full_g1_sequence_rehearses_offline(self):
        from scorbot.session import load_session
        idle = self.idle()
        self.assertEqual(idle.returncode, 0, idle.stderr)
        bench = self.bench()
        self.assertEqual(bench.returncode, 0, bench.stderr + bench.stdout)

        idle_rows = [json.loads(l) for l in (self.logs / "idle-01.jsonl").read_text().splitlines()]
        self.assertEqual(idle_rows[0]["data_source"], "simulated")
        self.assertEqual(sum(r["type"] == "sample" for r in idle_rows), 2)
        bench_rows = [json.loads(l) for l in
                      (self.logs / "base-first-01.jsonl").read_text().splitlines()]
        self.assertEqual([r["type"] for r in bench_rows], BENCH_TYPES)
        self.assertEqual(bench_rows[0]["data_source"], "simulated")

        review = run_script("scripts/review_lab_logs.py", "--idle", self.logs / "idle-01.jsonl",
                            "--bench", self.logs / "base-first-01.jsonl")
        self.assertEqual(review.returncode, 0, review.stdout + review.stderr)
        self.assertIn("SIMULATED", review.stdout)

        idle_session, bench_session = sessions_in(self.logs / "sessions")
        for path in (idle_session, bench_session):
            session = load_session(path)
            self.assertEqual(session.errors, [], [f.message for f in session.errors])
            self.assertEqual(session.metadata["data_source"], "simulated")
        idle_states = [e for e in load_session(idle_session).events
                       if e["topic"] == "/robot/state"]
        self.assertEqual(len(idle_states), 2)
        events = load_session(bench_session).events
        commands = {e["payload"]["kind"]: e["payload"]["command_id"]
                    for e in events if e["topic"] == "/robot/command"}
        self.assertEqual(set(commands), {"home", "jog_joint"})
        results = {e["payload"]["command_id"]: e["payload"]["status"]
                   for e in events if e["topic"] == "/robot/command_result"}
        self.assertEqual({results[cid] for cid in commands.values()}, {"completed"})

    def test_missing_nested_session_root_is_created(self):
        root = Path(self._tmp.name) / "new" / "nested sessions"
        result = self.bench(BENCH_ANSWERS, "b.jsonl", "--session-root", root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(sessions_in(root)), 1)

    def test_interrupted_run_leaves_a_loadable_faulted_record(self):
        from scorbot.session import load_session
        result = self.bench("HOME\nhome looked normal\n")  # stdin ends at HOME_OK prompt
        self.assertNotEqual(result.returncode, 0)
        rows = [json.loads(l) for l in (self.logs / "base-first-01.jsonl").read_text().splitlines()]
        self.assertEqual(rows[-1]["type"], "session_failed")
        [path] = sessions_in(self.logs / "sessions")
        session = load_session(path)
        self.assertEqual(session.errors, [], [f.message for f in session.errors])
        self.assertIn("/session/fault", {e["topic"] for e in session.events})


class ReviewLeftoverTests(unittest.TestCase):
    def test_worker_crash_does_not_wait_for_a_disable_nobody_will_answer(self):
        import time
        from scorbot import ScorbotError
        robot = ready_robot(command_timeout=5.0)
        try:
            robot.sim.inject("worker_crash")
            started = time.monotonic()
            with self.assertRaises(ScorbotError):
                robot.jog_joint("base", 1.0)
            self.assertLess(time.monotonic() - started, 1.0)
        finally:
            robot.disconnect()

    def test_late_answer_after_timeout_cannot_answer_a_later_command(self):
        import time
        from scorbot import ScorbotError
        robot = ready_robot(command_timeout=0.3)
        try:
            robot.sim.inject("late_answer")
            with self.assertRaises(ScorbotError):
                robot.jog_joint("base", 1.0)
            time.sleep(0.6)  # the stale answer has now arrived in the result queue
            with self.assertRaises(ScorbotError) as caught:
                robot.enable()
            self.assertIn("faulted", str(caught.exception).lower())
        finally:
            robot.disconnect()

    def test_failed_connect_keeps_the_real_error_and_logs_it(self):
        from scorbot import ScorbotError
        from scorbot.simulated import SimulatedController, SimulatedScorbot
        controller = SimulatedController()
        controller.inject("controller_error")  # the connect-time disable fails
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "events.jsonl"
            robot = SimulatedScorbot(controller=controller, log_path=log)
            with self.assertRaises(ScorbotError) as caught:
                robot.connect()
            self.assertIn("error code 3", str(caught.exception))
            events = [json.loads(line)["event"] for line in log.read_text().splitlines()]
        self.assertIn("connect_failed", events)


class BenchRecorderIsSecondaryTests(unittest.TestCase):
    """The MCAP recorder must never cost the primary JSONL record or the operator flow."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.output = Path(self._tmp.name) / "base-first-01.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def run_bench(self, *patches, answers=None):
        import contextlib
        import io
        from unittest.mock import patch
        from examples import bench_joint
        argv = ["bench_joint.py", "--output", str(self.output), *LABELS,
                "--start-pose-note", "desk", "--joint", "base", "--delta", "1",
                "--simulate", "--acknowledge-supervised-motion"]
        answers = answers or BENCH_ANSWERS.splitlines()
        stdout = io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(sys, "argv", argv))
            stack.enter_context(patch("builtins.input", side_effect=answers))
            stack.enter_context(contextlib.redirect_stdout(stdout))
            for item in patches:
                stack.enter_context(item)
            try:
                code = bench_joint.main()
            except BaseException as exc:  # noqa: BLE001 - tests inspect it
                code = exc
        rows = [json.loads(l) for l in self.output.read_text().splitlines()]
        return code, rows, stdout.getvalue()

    def test_recorder_failure_after_the_jog_keeps_the_full_bench_record(self):
        from unittest.mock import patch
        from scorbot.session.record import SessionError, SessionWriter
        original = SessionWriter.log_command_result

        def failing(self, command_id, status, **kwargs):
            if command_id == "cmd-0002":  # the jog, right after the arm moved
                raise SessionError("disk vanished")
            return original(self, command_id, status, **kwargs)

        code, rows, out = self.run_bench(patch.object(SessionWriter, "log_command_result",
                                                      failing))
        self.assertEqual(code, 0, out)
        self.assertEqual([r["type"] for r in rows], BENCH_TYPES)
        self.assertIn("MCAP recording stopped", out)

    def test_interrupt_during_the_jog_marks_the_open_command_faulted_once(self):
        from unittest.mock import patch
        from scorbot.session import load_session
        from scorbot.simulated import SimulatedScorbot

        def interrupted(*_args, **_kwargs):
            raise KeyboardInterrupt()

        code, rows, out = self.run_bench(patch.object(SimulatedScorbot, "jog_joint",
                                                      interrupted))
        self.assertIsInstance(code, KeyboardInterrupt)
        self.assertEqual(rows[-1]["type"], "session_failed")
        self.assertIn("physical stop", out)
        [path] = sessions_in(self.output.parent / "sessions")
        results = [e["payload"] for e in load_session(path).events
                   if e["topic"] == "/robot/command_result"
                   and e["payload"]["command_id"] == "cmd-0002"]
        self.assertEqual([r["status"] for r in results], ["faulted"])

    def test_real_path_runs_through_the_jog_into_a_real_session(self):
        """The non --simulate branch, with a stand-in controller that reports real states."""
        import contextlib
        import dataclasses
        import io
        from unittest.mock import patch
        from examples import bench_joint
        from scorbot.preflight import Check
        from scorbot.session import load_session
        from scorbot.simulated import SimulatedScorbot

        class StandInRealController(SimulatedScorbot):
            def get_state(self, *, after_index=None):
                state = super().get_state(after_index=after_index)
                return dataclasses.replace(state, simulated=False)

        argv = ["bench_joint.py", "--output", str(self.output), *LABELS,
                "--start-pose-note", "desk", "--joint", "base", "--delta", "-1",
                "--acknowledge-supervised-motion"]
        with patch.object(sys, "argv", argv), \
                patch.object(bench_joint, "run_checks", return_value=[Check("USB", True, "mock")]), \
                patch.object(bench_joint, "Scorbot", StandInRealController), \
                patch("builtins.input", side_effect=BENCH_ANSWERS.splitlines()), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(bench_joint.main(), 0)
        rows = [json.loads(l) for l in self.output.read_text().splitlines()]
        self.assertEqual([r["type"] for r in rows], BENCH_TYPES)
        self.assertEqual(rows[0]["data_source"], "real")
        [path] = sessions_in(self.output.parent / "sessions")
        session = load_session(path)
        self.assertEqual(session.errors, [], [f.message for f in session.errors])
        self.assertEqual(session.metadata["data_source"], "real")
        self.assertEqual(sum(e["topic"] == "/robot/state" for e in session.events), 5)

    def test_review_repeats_the_simulated_label_after_the_report(self):
        logs = Path(self._tmp.name) / "rehearsal"
        idle = run_script("examples/record_raw_state.py", "--output", logs / "idle.jsonl",
                          *LABELS, "--pose-note", "desk", "--seconds", "1", "--hz", "2",
                          "--simulate", "--acknowledge-connect-handshake")
        self.assertEqual(idle.returncode, 0, idle.stderr)
        review = run_script("scripts/review_lab_logs.py", "--idle", logs / "idle.jsonl")
        self.assertIn("SIMULATED", review.stdout.strip().splitlines()[-1])


if __name__ == "__main__":
    unittest.main()
