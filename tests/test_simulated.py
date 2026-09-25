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

    def idle(self, **extra):
        return run_script("examples/record_raw_state.py", "--output", self.logs / "idle-01.jsonl",
                          *LABELS, "--pose-note", "desk rehearsal", "--seconds", "1",
                          "--hz", "2", "--simulate", "--acknowledge-connect-handshake")

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


if __name__ == "__main__":
    unittest.main()
