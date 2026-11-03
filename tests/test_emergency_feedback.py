"""Emergency feedback locks motion on desk fakes only. Physical-arm behavior is unverified."""

from dataclasses import replace
import time
import unittest
from unittest import mock

from scorbot import MotionStopped, ScorbotError
from scorbot.lab.dry_link import DryScorbot
from scorbot.simulated import encode_packet
from scorbot.state import decode_state
from tests.sim_support import SimulatedRobotCase


class EmergencyDecodeTests(unittest.TestCase):
    def test_emergency_is_byte_two_bit_zero(self):
        for status in (0x08, 0x09):
            with self.subTest(status=status):
                packet = bytearray(encode_packet({}))
                packet[2] = status
                state = decode_state(bytes(packet), connected=True, enabled=True, homed=True, fault=None)
                self.assertEqual(state.emergency, bool(status & 1))


class MotionEmergencyTests(SimulatedRobotCase):
    def test_emergency_feedback_refuses_a_jog_and_latches_with_motors_off(self):
        robot = self.robot()
        snapshot = robot.sim.snapshot

        def emergency_snapshot(**kwargs):
            sample = snapshot(**kwargs)
            packet = bytearray(sample.data)
            packet[2] |= 1
            return replace(sample, data=bytes(packet))

        with mock.patch.object(robot.sim, "snapshot", side_effect=emergency_snapshot):
            with self.assertRaisesRegex(ScorbotError, "emergency"):
                robot.jog_joint("base", 1.0)
        deadline = time.monotonic() + 2
        while robot.sim.motors_on and time.monotonic() < deadline:
            time.sleep(0.001)
        self.assertFalse(robot.sim.motors_on)
        self.assertIsNone(robot._enabled)
        self.assertFalse(robot._homed)
        self.assertIsNone(robot._home_counts)
        self.assertIn("emergency", robot._fault)
        self.assertNotIn(5, [c[0] for c in robot.sim.commands])
        with self.assertRaises(ScorbotError):
            robot.enable()

    def test_a_single_emergency_reply_during_a_real_worker_jog_stops_and_latches(self):
        # DryScorbot injects fake endpoints; connect and the legacy workers are unchanged.
        robot = DryScorbot()
        with robot:
            robot.enable()
            # The fake plant starts at vendor zero; this test isolates the worker's emergency path.
            robot._home_counts = robot.get_state().encoder_counts
            robot._homed = robot._gripper_homed = True
            original = robot.dry_link.plant.reply
            pulsed = []

            def reply(data):
                packet = bytearray(original(data))
                target = int.from_bytes(data[12:16], "little", signed=True)
                if not pulsed and data[4] == 0x0D and target > 5:
                    packet[2] |= 1
                    pulsed.append(len(robot.dry_link.sent))
                return bytes(packet)

            with mock.patch.object(robot.dry_link.plant, "reply", side_effect=reply):
                with mock.patch.object(robot._commands, "put", wraps=robot._commands.put) as queued:
                    with self.assertRaisesRegex(ScorbotError, "emergency") as caught:
                        robot.jog_joint("base", 1.0)
                    self.assertNotIsInstance(caught.exception, MotionStopped)
                    self.assertIn(mock.call([16, 1, 1]), queued.call_args_list)
            self.assertEqual(len(pulsed), 1)
            tail = robot.dry_link.sent[pulsed[0]:]
            stop = next(i for i, packet in enumerate(tail) if packet[4] == 0x47)
            self.assertLessEqual(stop, 3, "the existing stop must follow the emergency pulse promptly")
            self.assertEqual(tail[stop + 1][4], 0x4F)
            self.assertIsNone(robot._enabled)
            self.assertFalse(robot._homed)
            self.assertIsNone(robot._home_counts)
            self.assertIn("emergency", robot._fault)
            with self.assertRaises(ScorbotError):
                robot.enable()
