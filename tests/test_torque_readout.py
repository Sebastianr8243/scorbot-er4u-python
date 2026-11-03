"""The vendor's torque (PWM) readout, ``ShowTorque`` (docs/protocol/LEDGER.md PL74), through the simulator.

While ``ShowTorque`` is open the lab DLL sends ``57 <mask>`` in place of its idle ``0D``, with the same
setpoints, and takes the value only from a reply whose byte 1 echoes ``57``: signed bytes 11-14 minus 244
(comm thread ``0x1003ccc8``). Desk recording 2026-11-02: replies of 1000 + mask came back as 757 (axis 0),
760 (axis 2) and 819 (``'A'``). What the number means on the arm is unverified.
"""

import unittest

from scorbot import ScorbotError
from scorbot import vendor_home as vh
from scorbot import vendor_torque

try:
    from tests.sim_support import SimulatedRobotCase
except ImportError:      # discovered with -s tests
    from sim_support import SimulatedRobotCase


def head(packet):
    return packet[4:12].hex(" ")


class TorqueReadoutTests(SimulatedRobotCase):

    def robot(self, torque_raw=None, torque_lag=None):
        robot = super().robot()
        if torque_raw is not None:
            robot.sim.torque_raw = torque_raw
        if torque_lag is not None:
            robot.sim.torque_lag = torque_lag
        return robot

    def test_sends_the_dll_bytes_for_one_axis_and_for_all(self):
        robot = self.robot()
        for axis, expected in ((0, "57 01 00 00 00 00 00 00"), (2, "57 04 00 00 00 00 00 00"),
                               ("A", "57 3f 00 00 00 00 00 00")):
            robot.read_torque(axis)
            self.assertEqual({head(p) for p in robot.sim.vendor_packets}, {expected})

    def test_returns_bytes_11_to_14_minus_244(self):
        robot = self.robot(torque_raw=1001)
        self.assertEqual(robot.read_torque(0), 757)
        robot.sim.torque_raw = -10
        self.assertEqual(robot.read_torque(0), -254)

    def test_ignores_the_stale_first_reply_and_replies_to_other_commands(self):
        robot = self.robot(torque_raw=1063, torque_lag=3)
        self.assertEqual(robot.read_torque("A"), 819)
        self.assertEqual(len(robot.sim.vendor_packets), 4, "three replies echoed 0D, the fourth 57")

    def test_no_answer_is_an_error_without_motors_off(self):
        robot = self.robot(torque_lag=10_000)
        before = robot.get_state().encoder_counts
        with self.assertRaises(ScorbotError):
            robot.read_torque(0)
        self.assertTrue(robot.sim.motors_on)
        self.assertEqual(robot.get_state().encoder_counts, before)
        self.assertLessEqual(len(robot.sim.vendor_packets), 100)

    def test_the_setpoints_hold_the_arm_where_it_is(self):
        robot = self.robot(torque_lag=2)
        robot.jog_joint("base", 1.0)
        before = robot.get_state().encoder_counts
        robot.read_torque(0)
        slots = {packet[12:44] for packet in robot.sim.vendor_packets}
        self.assertEqual(len(slots), 1, "every message holds the same setpoints")
        self.assertNotEqual(slots, {bytes(32)}, "the setpoints are the measured counts, not zero")
        self.assertEqual(robot.get_state().encoder_counts, before)

    def test_bad_axis_sends_nothing(self):
        robot = self.robot()
        sent = list(robot.sim.commands)
        for axis in (-1, 6, "B", "&", 1.5, True):
            with self.assertRaises(ValueError):
                robot.read_torque(axis)
        self.assertEqual(robot.sim.commands, sent)

    def test_works_with_the_motors_off(self):
        robot = self.robot(torque_raw=300)
        robot.disable()
        self.assertEqual(robot.read_torque(1), 56)

    def test_refused_when_not_connected(self):
        robot = self.robot()
        robot.disconnect()
        with self.assertRaises(ScorbotError):
            robot.read_torque(0)

    def test_refused_while_a_stream_is_active(self):
        robot = self.robot()
        stream = robot.start_stream()
        self.addCleanup(stream.close)
        sent = list(robot.sim.commands)
        with self.assertRaisesRegex(ScorbotError, "stream"):
            robot.read_torque(0)
        self.assertEqual(robot.sim.commands, sent)

    def test_logged(self):
        robot = self.robot(torque_raw=1004)
        robot.read_torque(2)
        rows = [r for r in self.rows() if r["event"] == "torque_read"]
        self.assertEqual([(r["axis"], r["mask"], r["value"]) for r in rows], [(2, 4, 760)])


class GeneratorTests(unittest.TestCase):
    """``vendor_torque.read`` alone: which replies count as the answer."""

    def reply(self, command, result, lag=0):
        return vh.Reply(counts=(1, 2, 3, 4, 5, 6), switches=0, now=0.0, lag=lag, command=command, result=result)

    def test_only_a_57_echo_to_one_of_our_messages_is_the_answer(self):
        first = self.reply(0x57, 999)                 # a stale answer from before: never decoded
        run = vendor_torque.read(first, 0x01)
        out = next(run)
        self.assertEqual((out.cmd, out.mask, out.arg16, out.arg32, out.setpoints),
                         (0x57, 0x01, 0, 0, (1, 2, 3, 4, 5, 6)))
        self.assertEqual(run.send(self.reply(0x0D, 5000)).cmd, 0x57)        # another command's reply
        self.assertEqual(run.send(self.reply(0x57, 5000, lag=2)).cmd, 0x57)  # answers a message before ours
        with self.assertRaises(StopIteration) as done:
            run.send(self.reply(0x57, 300, lag=2))                        # third message: lag 2 is ours
        self.assertEqual(done.exception.value, 56)

    def test_gives_up_after_max_messages(self):
        run = vendor_torque.read(self.reply(0, 0), 0x3F)
        next(run)
        sent = 1
        with self.assertRaises(StopIteration) as done:
            while True:
                run.send(self.reply(0x0D, 0))
                sent += 1
        self.assertIsNone(done.exception.value)
        self.assertEqual(sent, vendor_torque.MAX_MESSAGES)

    def test_the_deadline_is_no_answer_and_takes_no_stop_path(self):
        from scorbot.vendor_home_model import Plant
        from scorbot.vendor_home_source import VendorHomeSource
        now = [0.0]
        source = VendorHomeSource(start=lambda first: vendor_torque.read(first, 0x01), clock=lambda: now[0],
                                  total_seconds=vendor_torque.TOTAL_SECONDS)
        plant = Plant()
        reply, sequence, sent = plant.exchange(vh.encode_out(vh.Out(vh.NOP), 1)), 2, []
        while True:
            step = source(reply, sequence)
            if step[0] != "send":
                break
            sent.append(step[1])
            reply = bytearray(plant.exchange(step[1]))
            reply[1] = 0x0D                      # a controller that never answers 57
            sequence = sequence % 255 + 1
            now[0] += 1.0                        # five messages reach the deadline
        self.assertEqual(step, ("end",))
        self.assertIsNone(source.error)
        self.assertFalse(source.ok)
        self.assertEqual({p[4] for p in sent}, {0x57}, "only 57 went out: no stop messages")
        self.assertLess(len(sent), vendor_torque.MAX_MESSAGES)


if __name__ == "__main__":
    unittest.main()
