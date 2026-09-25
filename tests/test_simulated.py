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


if __name__ == "__main__":
    unittest.main()
