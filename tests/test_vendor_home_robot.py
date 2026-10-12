"""Scorbot.vendor_home through the simulator: the gate, the fault paths, the stop, the evidence rows.

The simulator runs ``scorbot.vendor_home`` against the arm model (``scorbot/vendor_home_model.py``),
packet by packet, behind the real facade, so the same gates, fault latch and event log run as in
the lab. It says nothing about the real controller.
"""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from scorbot import ScorbotError, vendor_home as vh
from scorbot.packet import PacketTrace
from scorbot.robot import MotionStopped
from scorbot.simulated import SimulatedScorbot
from scorbot.vendor_home_model import Plant
from scorbot.vendor_home_source import VENDOR_HOME_ORDER, VendorHomeSource

CONFIRMED = dict(operator_at_stop=True, jaws_empty=True, wrist_clear=True)


class Case(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.log = Path(self.folder.name) / "run.jsonl"

    def robot(self, *, enable=True, **kwargs):
        robot = SimulatedScorbot(log_path=self.log, **kwargs).connect()
        self.addCleanup(self.close, robot)
        if enable:
            robot.enable()
        return robot

    @staticmethod
    def close(robot):
        try:
            robot.disconnect()
        except Exception:
            pass

    def events(self, name=None):
        rows = [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines() if line.strip()]
        return [r for r in rows if name is None or r["event"] == name]

    @staticmethod
    def ordered(robot):
        return [c[0] for c in robot.sim.commands]

    def assert_latched(self, robot):
        with self.assertRaises(ScorbotError) as caught:
            robot.enable()
        self.assertIn("faulted", str(caught.exception).lower())
        with self.assertRaises(ScorbotError):
            robot.vendor_home(**CONFIRMED)
        with self.assertRaises((ScorbotError, ValueError)):
            robot.jog_joint("base", 1.0)


class GateTests(Case):
    def test_each_confirmation_is_needed_and_nothing_is_queued_without_it(self):
        robot = self.robot()
        before = list(robot.sim.commands)
        for missing in CONFIRMED:
            with self.subTest(missing=missing):
                with self.assertRaises(ValueError):
                    robot.vendor_home(**{**CONFIRMED, missing: False})
        with self.assertRaises(ValueError):
            robot.vendor_home()
        self.assertEqual(robot.sim.commands, before)

    def test_motors_must_be_enabled(self):
        robot = self.robot(enable=False)
        before = list(robot.sim.commands)
        with self.assertRaises(ScorbotError):
            robot.vendor_home(**CONFIRMED)
        self.assertEqual(robot.sim.commands, before)

    def test_not_connected_is_refused(self):
        with self.assertRaises(ScorbotError):
            SimulatedScorbot().vendor_home(**CONFIRMED)

    def test_unknown_switch_bits_are_refused_before_anything_is_sent(self):
        robot = self.robot()
        robot.sim.switch_bits = 0x40
        before = list(robot.sim.commands)
        with self.assertRaises(ScorbotError):
            robot.vendor_home(**CONFIRMED)
        self.assertEqual(robot.sim.commands, before)
        self.assertEqual(len(self.events("home_refused")), 1)

    def test_a_stream_blocks_it(self):
        robot = self.robot()
        robot._stream = object()
        with self.assertRaises(ScorbotError):
            robot.vendor_home(**CONFIRMED)
        robot._stream = None

    def test_a_faulted_session_refuses_it(self):
        robot = self.robot()
        robot._latch_fault("test fault")
        before = list(robot.sim.commands)
        with self.assertRaises(ScorbotError):
            robot.vendor_home(**CONFIRMED)
        self.assertEqual(robot.sim.commands, before)

    def test_an_old_stop_request_refuses_the_home_once_and_sends_nothing(self):
        robot = self.robot()
        robot.request_stop()
        before = list(robot.sim.commands)
        with self.assertRaises(MotionStopped) as caught:
            robot.vendor_home(**CONFIRMED)
        self.assertFalse(caught.exception.started)
        self.assertEqual(robot.sim.commands, before)
        robot.vendor_home(**CONFIRMED)                               # the second try runs
        self.assertTrue(robot._homed)


class RunTests(Case):
    def test_a_home_records_the_session_home_and_leaves_the_arm_motors_enabled(self):
        robot = self.robot()
        robot.vendor_home(**CONFIRMED)
        self.assertTrue(robot._homed)
        self.assertTrue(robot._enabled)
        self.assertTrue(robot.sim.motors_on)
        self.assertEqual(set(robot._home_counts.values()), {0})
        self.assertIn(VENDOR_HOME_ORDER, self.ordered(robot))
        start, complete = self.events("home_start"), self.events("home_complete")
        self.assertEqual((start[0]["method"], start[0]["wrist_homed"], start[0]["gripper_homed"]), ("vendor", True, True))
        self.assertEqual(complete[0]["axes"], ["shoulder", "elbow", "wrist_m2", "wrist_m1", "base", "gripper"])
        self.assertTrue(all(row["simulated"] for row in start + complete))

    def test_the_whole_command_stream_is_the_vendors(self):
        robot = self.robot()
        robot.vendor_home(**CONFIRMED)
        packets = robot.sim.vendor_packets
        fixture = json.loads((Path(__file__).parent / "fixtures" / "vendor_home_trace04.json").read_text(encoding="utf-8"))
        items = [[p[4], p[5], int.from_bytes(p[6:8], "little"), int.from_bytes(p[8:12], "little", signed=True)]
                 for p in packets if p[4] != vh.NOP]
        collapsed = [item for i, item in enumerate(items) if i == 0 or item != items[i - 1]]
        self.assertEqual(collapsed, [list(s) for s in fixture["steps"]])

    def test_it_waits_long_enough_for_a_whole_home(self):
        robot = self.robot()
        with mock.patch.object(robot, "_command", wraps=robot._command) as command:
            robot.vendor_home(**CONFIRMED)
        self.assertGreater(command.call_args.kwargs["timeout"], 800)

    def test_the_controller_version_reaches_the_home(self):
        robot = self.robot()
        robot.vendor_home(controller_version=9, **CONFIRMED)
        self.assertEqual(self.events("home_start")[0]["controller_version"], 9)
        robot2 = self.robot()
        robot2.vendor_home(**CONFIRMED)
        self.assertNotEqual(len(robot.sim.vendor_packets), len(robot2.sim.vendor_packets))

    def test_the_packet_trace_is_recorded_under_its_own_label(self):
        robot = self.robot()
        robot._trace = PacketTrace()
        robot.vendor_home(**CONFIRMED)
        rows = self.events("motion_trace")
        self.assertEqual([r["joint"] for r in rows], ["vendor_home"])
        self.assertEqual(rows[0]["kept"], "last")

    def test_a_calibration_that_rejects_the_home_latches_a_fault_and_switches_off(self):
        robot = self.robot()

        def refuse(_count):
            raise ValueError("home count is off")
        robot._calibration = SimpleNamespace(joints={"base": SimpleNamespace(encoder="base", validate_home=refuse)})
        with self.assertRaises(ScorbotError):
            robot.vendor_home(**CONFIRMED)
        self.assertFalse(robot._homed)
        self.assertIn("Home verification failed", robot._fault)
        self.assertIn(16, self.ordered(robot)[self.ordered(robot).index(VENDOR_HOME_ORDER):])

    def test_after_the_home_each_jointcan_be_jogged_a_degree_each_way(self):
        # The first thing the lab does after a home. Every joint now sits on the legacy 0/65535 seam.
        robot = self.robot()
        robot.vendor_home(**CONFIRMED)
        for joint in ("base", "shoulder", "elbow"):
            with self.subTest(joint=joint):
                start = robot.get_state().encoder_counts[joint]
                robot.jog_joint(joint, -1.0, speed=2)
                moved = robot.get_state().encoder_counts[joint]
                robot.jog_joint(joint, 1.0, speed=2)
                back = robot.get_state().encoder_counts[joint]
                self.assertNotEqual(moved, start)
                self.assertLessEqual(abs(back - start), 2)
        self.assertIsNone(robot._fault)


class FailureTests(Case):
    def fail(self, *, hook=None, plant=None, **kwargs):
        robot = self.robot(**kwargs)
        robot.sim.vendor_hook = hook
        robot.sim.vendor_plant = plant
        with self.assertRaises(ScorbotError) as caught:
            robot.vendor_home(**CONFIRMED)
        return robot, caught.exception

    def check_failure(self, robot, error, code, text):
        self.assertIn(f"error {code}", str(error))
        self.assertIn(text, str(error))
        self.assertIn("physical stop", str(error))
        self.assertFalse(robot._homed)
        self.assertIsNone(robot._home_counts)
        self.assertFalse(robot.sim.motors_on)                        # switched off
        self.assertIn(16, self.ordered(robot)[self.ordered(robot).index(VENDOR_HOME_ORDER):])
        row = self.events("home_failed")[-1]
        self.assertEqual((row["method"], row["code"]), ("vendor", code))
        self.assertEqual(robot._fault, str(error))
        self.assert_latched(robot)

    def assert_stop_sent(self, robot):
        tail = [bytes(p) for p in robot.sim.vendor_packets if p[4] != vh.NOP][-8:]
        self.assertEqual([p[4] for p in tail], [vh.CLEAR] + [vh.MOVE] * 6 + [vh.MODE])
        self.assertEqual([p[5] for p in tail[1:7]], [1 << n for n in range(6)])
        self.assertTrue(all(p[8:12] == bytes(4) for p in tail[1:7]))

    def test_an_emergency_input_stops_and_latches(self):
        robot, error = self.fail(hook=lambda n, p: setattr(p, "emergency", n > 200))
        self.check_failure(robot, error, vh.ERR_EMERGENCY, "emergency")
        self.assert_stop_sent(robot)

    def test_an_impact_during_a_search_stops_and_latches(self):
        robot, error = self.fail(hook=lambda n, p: setattr(p, "impact", 0x02 if 60 < n < 70 else 0))
        self.check_failure(robot, error, vh.ERR_IMPACT, "shoulder")
        self.assert_stop_sent(robot)

    def test_a_wrong_drive_sign_is_caught_within_a_few_degrees(self):
        plant = Plant(gain=-0.1)
        robot, error = self.fail(plant=plant)
        self.check_failure(robot, error, vh.ERR_DIRECTION, "moves against its drive")
        self.assertLess(max(abs(x) for x in plant.phys), 600)

    def test_a_jammed_wrist_motor_is_a_stall(self):
        robot, error = self.fail(plant=Plant(frozen={vh.WRIST_M1}))
        self.check_failure(robot, error, vh.ERR_STALL, "wrist_m1")

    def test_a_stop_request_that_lands_after_the_last_message_still_latches_and_switches_off(self):
        # The controller's counters were already zeroed by 48, so the session cannot go on as "not homed".
        robot = self.robot()
        original = robot._command

        def command(*args, **kwargs):
            result = original(*args, **kwargs)
            robot.request_stop()
            return result
        with mock.patch.object(robot, "_command", command):
            with self.assertRaises(ScorbotError):
                robot.vendor_home(**CONFIRMED)
        self.assert_latched(robot)
        self.assertIn(16, self.ordered(robot)[self.ordered(robot).index(VENDOR_HOME_ORDER):])
        self.assertFalse(robot._homed)

    def test_a_log_write_that_fails_after_the_home_still_switches_off_and_latches(self):
        robot = self.robot()
        robot._trace = PacketTrace()
        with mock.patch.object(robot, "_record_home_trace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                robot.vendor_home(**CONFIRMED)
        self.assert_latched(robot)
        self.assertIn(16, self.ordered(robot)[self.ordered(robot).index(VENDOR_HOME_ORDER):])

    def test_every_home_failed_row_carries_the_lag(self):
        late = self.robot()
        original = late._command

        def command(*args, **kwargs):
            result = original(*args, **kwargs)
            late.request_stop()
            return result
        with mock.patch.object(late, "_command", command):
            with self.assertRaises(ScorbotError):
                late.vendor_home(**CONFIRMED)
        self.assertIn("lag_max", self.events("home_failed")[-1])
        refused = self.robot()

        def refuse(_count):
            raise ValueError("home count is off")
        refused._calibration = SimpleNamespace(joints={"base": SimpleNamespace(encoder="base", validate_home=refuse)})
        with self.assertRaises(ScorbotError):
            refused.vendor_home(**CONFIRMED)
        self.assertIn("lag_max", self.events("home_failed")[-1])

    def test_the_failure_text_does_not_claim_the_motors_are_off(self):
        # The motors-off messages may themselves have failed to get through; only the LED shows it.
        robot, error = self.fail(plant=Plant(frozen={vh.WRIST_M1}))
        self.assertNotIn("were switched off", robot._fault)
        self.assertIn("MOTORS LED", robot._fault)

    def test_the_measured_lag_is_in_the_home_rows(self):
        robot = self.robot()
        robot.vendor_home(**CONFIRMED)
        complete = self.events("home_complete")[0]
        self.assertIn("lag_first", complete)
        self.assertIn("lag_max", complete)
        failing = self.robot()
        failing.sim.vendor_plant = Plant(frozen={vh.WRIST_M1})
        with self.assertRaises(ScorbotError):
            failing.vendor_home(**CONFIRMED)
        self.assertIn("lag_max", self.events("home_failed")[-1])

    def test_a_stop_request_during_the_home_stops_it_and_latches(self):
        robot = self.robot()
        robot.sim.vendor_hook = lambda n, p: robot.request_stop() if n == 250 else None
        with self.assertRaises(ScorbotError) as caught:
            robot.vendor_home(**CONFIRMED)
        self.check_failure(robot, caught.exception, vh.ERR_STOPPED, "stop was requested")
        self.assert_stop_sent(robot)
        self.assertFalse(robot._stop_event.is_set())                 # the stop that ended it is spent
        self.assertLess(len(robot.sim.vendor_packets), 300)

    def test_a_home_that_never_finds_a_switch_ends_in_an_error_not_a_hang(self):
        robot, error = self.fail(plant=Plant(bands={}))
        self.assertTrue(any(f"error {code}" in str(error)
                            for code in (vh.ERR_TIMEOUT, vh.ERR_TRAVEL, vh.ERR_STALL, vh.ERR_NOT_FOUND)))
        self.assertFalse(robot._homed)

    def test_the_failure_message_names_the_axis(self):
        robot, error = self.fail(plant=Plant(frozen={vh.WRIST_M1}))
        self.assertIn("wrist_m1", str(error))
        self.assertEqual(self.events("home_failed")[-1]["axis"], vh.WRIST_M1)

    def test_a_source_that_dies_is_reported_as_unexpected(self):
        robot = self.robot()
        with mock.patch.object(VendorHomeSource, "_step", side_effect=RuntimeError("boom")):
            with self.assertRaises(ScorbotError) as caught:
                robot.vendor_home(**CONFIRMED)
        self.assertIn("unexpectedly", str(caught.exception))
        self.assertFalse(robot.sim.motors_on)


class ModuleHygieneTests(unittest.TestCase):
    def test_the_new_modules_are_in_the_motion_fingerprint(self):
        from scorbot import provenance
        for name in ("scorbot/vendor_home.py", "scorbot/vendor_home_source.py"):
            self.assertIn(name, provenance._SOURCE_FILES)

    def test_the_gate_text_says_what_the_arm_will_do(self):
        doc = SimulatedScorbot.vendor_home.__doc__
        for word in ("close the gripper", "37 degrees", "unverified", "not an emergency stop"):
            self.assertIn(word.lower(), doc.lower().replace("**", ""))


if __name__ == "__main__":
    unittest.main()
