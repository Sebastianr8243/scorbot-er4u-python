"""Teach mode from reply byte 3, decoded as the lab DLL's ``IsTeachMode`` does, and the stream refusal.

The DLL keeps a flag that the pendant handler (``0x1002b56c``) updates from every reply's byte 3
(``docs/protocol/LEDGER.md`` PL64). ``DLL_RUN`` is what ``IsTeachMode`` returned on the pipe fake
(lab ``USBC.dll`` sha256 6118465d..., 2026-11-02): byte 3 and byte 2 were set, then it was read.
Refusing to stream in teach mode is Stack 2's own rule; the DLL refuses nothing (PL64).
"""

import dataclasses
from contextlib import contextmanager
from importlib.util import find_spec
import time
import unittest
from unittest import mock

from scorbot import ScorbotError, StreamRefused
from scorbot.packet import TrackedInputEndpoint
from scorbot.state import ENCODER_OFFSETS, decode_state, teach_mode_after

try:
    from tests.sim_support import SimulatedRobotCase
except ImportError:
    from sim_support import SimulatedRobotCase

# (byte 3, byte 2, IsTeachMode after), in run order; the flag starts off.
DLL_RUN = [
    (0x00, 0x08, 0), (0x47, 0x08, 0), (0x40, 0x08, 1), (0x00, 0x08, 1), (0x47, 0x08, 0),
    (0x4F, 0x08, 1), (0x7E, 0x08, 0), (0xC0, 0x08, 1), (0xF8, 0x08, 0), (0xF9, 0x08, 1),
    (0xFE, 0x08, 0), (0x09, 0x08, 1), (0x47, 0x08, 0), (0x21, 0x08, 1), (0x47, 0x08, 0),
    (0xA1, 0x08, 1), (0x47, 0x08, 0), (0x09, 0x09, 0), (0x47, 0x09, 0), (0x40, 0x09, 1),
    (0x09, 0x09, 1), (0x47, 0x08, 0),
]


def reply(byte3=0, byte2=0x08):
    packet = bytearray(64)
    for offset in ENCODER_OFFSETS:
        packet[offset + 2] = 128
    packet[2], packet[3] = byte2, byte3
    return bytes(packet)


@contextmanager
def teach_after_setpoints(robot):
    """Flip the simulated reply after three sends; mirror the real endpoint's teach flag."""
    from scorbot.simulated import encode_packet
    snapshot = robot.sim.snapshot

    def packet(*args, **kwargs):
        data = bytearray(encode_packet(*args, **kwargs))
        data[3] = 0x40 if len(robot.sim.stream_sent) >= 3 else 0x47
        return bytes(data)

    def sample(**kwargs):
        result = snapshot(**kwargs)
        return dataclasses.replace(result, teach_mode=teach_mode_after(False, result.data))

    with mock.patch("scorbot.simulated.encode_packet", side_effect=packet), \
            mock.patch.object(robot.sim, "snapshot", side_effect=sample):
        yield


def wait_for_stop(stream):
    deadline = time.monotonic() + 3
    while stream.core.state != "stopped" and time.monotonic() < deadline:
        time.sleep(0.002)
    return stream.core.state == "stopped"


class DecodeTests(unittest.TestCase):
    def test_the_flag_follows_the_dll_run(self):
        teach = False
        for byte3, byte2, expected in DLL_RUN:
            teach = teach_mode_after(teach, reply(byte3, byte2))
            self.assertEqual(teach, bool(expected), f"byte 3 {byte3:#04x}, byte 2 {byte2:#04x}")

    def test_a_short_reply_keeps_the_flag(self):
        self.assertTrue(teach_mode_after(True, b"\x00\x00"))
        self.assertFalse(teach_mode_after(False, b"\x00\x00"))

    def test_robot_state_carries_it(self):
        self.assertFalse(decode_state(reply(), connected=True, enabled=True, homed=True, fault=None).teach_mode)
        self.assertTrue(decode_state(reply(), connected=True, enabled=True, homed=True, fault=None,
                                     teach_mode=True).teach_mode)

    def test_every_reply_through_the_endpoint_updates_the_snapshot(self):
        replies = iter([reply(0x40), reply(0x00), reply(0x47)])

        class Endpoint:
            def read(self, buffer, timeout=None):
                data = next(replies)
                buffer[:len(data)] = data
                return len(data)

        endpoint = TrackedInputEndpoint(Endpoint())
        seen = []
        for _ in range(3):
            endpoint.read(bytearray(64))
            seen.append(endpoint.snapshot().teach_mode)
        self.assertEqual(seen, [True, True, False])


class StreamRefusalTests(SimulatedRobotCase):
    def in_teach_mode(self, robot):
        real = robot._motion_state
        return mock.patch.object(robot, "_motion_state",
                                 side_effect=lambda **kw: dataclasses.replace(real(**kw), teach_mode=True))

    def test_start_stream_refuses_in_teach_mode_and_sends_nothing(self):
        robot = self.robot()
        before = len(robot.sim.commands)
        robot.request_stop()                      # a pending stop must survive the refusal
        rows_before = len(self.rows())
        with self.in_teach_mode(robot), self.assertRaisesRegex(ScorbotError, "teach mode"):
            robot.start_stream()
        self.assertEqual(len(robot.sim.commands), before)
        self.assertTrue(robot._commands.empty())
        self.assertIsNone(robot._stream)
        self.assertEqual([r["event"] for r in self.rows()[rows_before:]], ["stream_refused_teach_mode"])
        self.assertTrue(robot._stop_event.is_set())

    def test_start_stream_runs_when_teach_mode_is_off(self):
        robot = self.robot()
        robot.start_stream().close()
        self.assertIn("stream_start", self.events())

    @unittest.skipUnless(find_spec("ruckig"), "needs the planning extra")
    def test_teach_mode_mid_stream_stops_without_the_caller_and_refuses_restart(self):
        robot = self.robot()
        with teach_after_setpoints(robot):
            self.assertFalse(robot.get_state().teach_mode)
            stream = robot.start_stream()
            stream.set_target({"base": 1000})
            self.assertTrue(wait_for_stop(stream), "teach mode must stop without close() or stop()")
            self.assertEqual(len(robot.sim.stream_sent), 3)
            with self.assertRaises(StreamRefused):
                stream.set_target({"base": 0})
            final = stream.close()
            self.assertTrue(final.teach_mode)
            self.assertIsNone(robot._fault)
            self.assertEqual([r for r in self.rows() if r["event"] == "stream_stopped"][-1]["reason"],
                             "teach mode")
            before = list(robot.sim.commands)
            with self.assertRaisesRegex(ScorbotError, "teach mode"):
                robot.start_stream()
            self.assertEqual(robot.sim.commands, before)
            self.assertTrue(robot._commands.empty())


@unittest.skipUnless(find_spec("ruckig"), "needs the planning extra")
class PolicyArmTeachTests(unittest.TestCase):
    def test_teach_mode_mid_stream_stops_policy_actions(self):
        import tempfile
        from pathlib import Path
        from scorbot import SimulatedScorbot
        from scorbot.policy_arm import PolicyArm
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        robot = SimulatedScorbot(log_path=Path(tmp.name) / "e.jsonl", robot_id="t")
        arm = PolicyArm(robot)
        with teach_after_setpoints(robot):
            arm.connect(operator_at_stop=True, jaws_empty=True, wrist_clear=True)
            self.addCleanup(arm.close)
            arm.act({"base": 1.0})
            self.assertTrue(wait_for_stop(arm._stream))
            self.assertEqual(len(robot.sim.stream_sent), 3)
            with self.assertRaises(StreamRefused):
                arm.act({"base": 0.0})
            arm.close()
            self.assertIsNone(robot._fault)

    def test_connect_refuses_before_enabling_in_teach_mode(self):
        import tempfile
        from pathlib import Path
        from scorbot import SimulatedScorbot
        from scorbot.policy_arm import PolicyArm
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        robot = SimulatedScorbot(log_path=Path(tmp.name) / "e.jsonl", robot_id="t")
        real = robot.get_state
        arm = PolicyArm(robot)
        with mock.patch.object(robot, "get_state",
                               side_effect=lambda **kw: dataclasses.replace(real(**kw), teach_mode=True)), \
                mock.patch.object(robot, "enable") as enable, \
                self.assertRaisesRegex(ScorbotError, "teach mode"):
            arm.connect(operator_at_stop=True, jaws_empty=True, wrist_clear=True)
        enable.assert_not_called()
        self.assertFalse(arm.is_connected)
        self.assertIsNone(robot._device)

    def test_teach_mode_seen_after_enabling_refuses_before_homing(self):
        import tempfile
        from pathlib import Path
        from scorbot import SimulatedScorbot
        from scorbot.policy_arm import PolicyArm
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        robot = SimulatedScorbot(log_path=Path(tmp.name) / "e.jsonl", robot_id="t")
        real = robot.get_state

        def state(**kw):
            return dataclasses.replace(real(**kw), teach_mode=robot._enabled)   # the enable reply turns it on

        arm = PolicyArm(robot)
        with mock.patch.object(robot, "get_state", side_effect=state), \
                mock.patch.object(robot, "vendor_home") as home, \
                self.assertRaisesRegex(ScorbotError, "teach mode"):
            arm.connect(operator_at_stop=True, jaws_empty=True, wrist_clear=True)
        home.assert_not_called()
        self.assertIsNone(robot._device)


if __name__ == "__main__":
    unittest.main()
