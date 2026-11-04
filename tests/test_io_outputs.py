"""Digital and analog outputs, the vendor's way (docs/protocol/LEDGER.md PL68, PL70), through the simulator.

``SetDigitalOutput(n, on, 1)`` at rest sends ``47`` then ``44`` with the whole output word in bytes 8-11;
``SetAnalogOutput(n, value)`` sends ``41`` with the value in bytes 6-7 and n in bytes 8-11. Bytes from
the lab DLL on the desk (2026-11-02); what the lines do on the arm is unverified.
"""

import json
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from scorbot import ScorbotError, vendor_home as vh, vendor_io
from scorbot.vendor_home_source import VendorHomeSource

try:
    from tests.sim_support import SimulatedRobotCase
except ImportError:      # discovered with -s tests
    from sim_support import SimulatedRobotCase


OUTPUTS = json.loads((Path(__file__).parent / "fixtures" / "dll_lab_jaw.json").read_text(encoding="utf-8"))["outputs"]


def fields(packet):
    return (packet[4], packet[5], int.from_bytes(packet[6:8], "little"),
            int.from_bytes(packet[8:12], "little"))


class OutputTests(SimulatedRobotCase):

    def test_digital_output_sends_clear_then_the_whole_word(self):
        robot = self.robot()
        robot.set_digital_output(1, True)
        self.assertEqual([fields(p) for p in robot.sim.vendor_packets], [(0x47, 0, 0, 0), (0x44, 0, 0, 0x02)])
        robot.set_digital_output(3, True)
        self.assertEqual(fields(robot.sim.vendor_packets[-1]), (0x44, 0, 0, 0x0A))
        robot.set_digital_output(1, False)
        self.assertEqual(fields(robot.sim.vendor_packets[-1]), (0x44, 0, 0, 0x08))
        self.assertEqual(robot.digital_outputs, 0x08)

    def test_output_31_fits_the_word(self):
        robot = self.robot()
        robot.set_digital_output(31, True)
        self.assertEqual(fields(robot.sim.vendor_packets[-1])[3], 0x8000_0000)
        self.assertEqual(robot.digital_outputs, 0x8000_0000)

    def test_analog_output_sends_value_and_number(self):
        robot = self.robot()
        robot.set_analog_output(1, 100)
        self.assertEqual([fields(p) for p in robot.sim.vendor_packets], [(0x41, 0, 100, 1)])

    def test_bad_numbers_send_nothing(self):
        robot = self.robot()
        sent = list(robot.sim.commands)
        for call in (lambda: robot.set_digital_output(-1, True), lambda: robot.set_digital_output(32, True),
                     lambda: robot.set_analog_output(2, 0), lambda: robot.set_analog_output(0, 256),
                     lambda: robot.set_analog_output(0, -1)):
            with self.assertRaises(ValueError):
                call()
        self.assertEqual(robot.sim.commands, sent)
        self.assertEqual(robot.digital_outputs, 0)

    def test_the_setpoints_hold_the_arm_where_it_is(self):
        robot = self.robot()
        robot.jog_joint("base", 1.0)
        before = robot.get_state().encoder_counts
        robot.set_digital_output(0, True)
        slots = {packet[12:36] for packet in robot.sim.vendor_packets}
        self.assertEqual(len(slots), 1, "every message holds the same setpoints")
        self.assertNotEqual(slots, {bytes(24)}, "the setpoints are the measured counts, not zero")
        self.assertEqual(robot.get_state().encoder_counts, before)

    def test_outputs_work_with_the_motors_off(self):
        robot = self.robot()
        robot.disable()
        robot.set_digital_output(2, True)
        self.assertEqual(robot.digital_outputs, 0x04)

    def test_refused_when_not_connected(self):
        robot = self.robot()
        robot.disconnect()
        with self.assertRaises(ScorbotError):
            robot.set_digital_output(0, True)
        self.assertEqual(robot.digital_outputs, 0)

    def test_logged(self):
        robot = self.robot()
        robot.set_digital_output(4, True)
        robot.set_analog_output(0, 7)
        rows = [r for r in self.rows() if r["event"] in ("digital_output", "analog_output")]
        self.assertEqual([(r["event"], r.get("number"), r.get("on", r.get("value"))) for r in rows],
                         [("digital_output", 4, True), ("analog_output", 0, 7)])
        self.assertEqual(rows[0]["word"], 0x10)

    def test_waits_for_the_arm_at_rest_and_sends_nothing_if_it_is_moving(self):
        robot = self.robot()
        packets = list(robot.sim.vendor_packets)
        with mock.patch.object(robot, "_settled_after_stop", side_effect=ScorbotError("moving")) as settle:
            with self.assertRaises(ScorbotError):
                robot.set_digital_output(0, True)
        settle.assert_called_once()
        self.assertEqual(robot.sim.vendor_packets, packets)
        self.assertEqual(robot.digital_outputs, 0)

    def test_two_writes_at_once_keep_both_bits(self):
        robot = self.robot()
        real_io, release = robot._io, threading.Event()

        def slow_io(messages):
            if not release.is_set():
                release.wait(5)
            real_io(messages)

        with mock.patch.object(robot, "_io", side_effect=slow_io):
            first = threading.Thread(target=robot.set_digital_output, args=(0, True))
            first.start()
            time.sleep(0.2)
            second = threading.Thread(target=robot.set_digital_output, args=(1, True))
            second.start()
            time.sleep(0.2)
            release.set()
            first.join(10)
            second.join(10)
        self.assertEqual(robot.digital_outputs, 0x03)
        self.assertEqual(fields(robot.sim.vendor_packets[-1])[3], 0x03)



class QueuedOutputTests(unittest.TestCase):
    """``SetDigitalOutput(n, on, 0)``: wait for the queue, then ``44`` with no ``47`` (LEDGER PL102)."""

    def test_feedback_interrupts_never_send_the_output(self):
        for feedback, code in (({"emergency": True}, vh.ERR_EMERGENCY),
                               ({"pendant_abort": True}, vh.ERR_STOPPED)):
            with self.subTest(feedback=feedback):
                first = vh.Reply(counts=(0,) * 6, switches=0, now=0.0, echo=1)
                generator = vendor_io.send_queued(first, vendor_io.digital_messages(1, immediate=False))
                self.assertEqual(next(generator).cmd, vh.NOP)
                with self.assertRaises(vh.HomeError) as caught:
                    generator.send(vh.Reply(counts=first.counts, switches=0, now=0.1, echo=2, **feedback))
                self.assertEqual(caught.exception.code, code)

    def drive(self, messages, pace_s, echo=lambda n: n % 255 + 1, limit=10_000):
        """Answer every message after ``pace_s`` with echo ``echo(n)`` for reply n (1, 2, ...): the default moves
        forward on the 1..255 ring. Returns what was sent and the replies' elapsed time."""
        first = vh.Reply(counts=(5, -7, 9, 0, 3, 11), switches=0, now=100.0, echo=1)
        generator, sent, reply = vendor_io.send_queued(first, messages), [], first
        try:
            out = next(generator)
            while len(sent) < limit:
                sent.append(out)
                reply = vh.Reply(counts=first.counts, switches=0, now=reply.now + pace_s, echo=echo(len(sent)))
                out = generator.send(reply)
        except StopIteration:
            pass
        return sent, reply.now - first.now

    def test_matches_the_dlls_recorded_wait(self):
        # Our wait at the desk fake's pace (10.8 ms) is 4 x (10 replies + 10 to cover 100 ms) = 80 0D in 0.87 s.
        # The DLL sent 75 and 77 0D in 0.817 and 0.843 s: its 100 ms runs on a Windows timer, not on replies,
        # and the two recordings already differ by 2 0D and 26 ms. So ours is pinned exactly, and it may wait
        # longer than the DLL (never shorter) by at most one 0D per round plus one, and that many paces.
        for golden in OUTPUTS[1:]:
            number, on, immediate = golden["call"]
            self.assertEqual(immediate, 0)
            word = int.from_bytes(bytes.fromhex(golden["commands"][-1][14:22]), "little")   # bytes 8-11
            messages = vendor_io.digital_messages(word, immediate=False)
            pace = golden["seconds"] / (golden["nop_count"] + 1)        # the desk fake's pace
            sent, seconds = self.drive(messages, pace)
            with self.subTest(call=golden["call"]):
                packets = [vh.encode_out(o, 1) for o in sent]
                self.assertEqual([p[1:12].hex() for p in packets if p[4] != vh.NOP], golden["commands"])
                nops = sum(1 for p in packets if p[4] == vh.NOP)
                self.assertEqual(nops, 80, "four rounds of 10 replies, then 100 ms at this pace")
                slack = vendor_io.QUEUE_ROUNDS + 1
                self.assertTrue(0 <= nops - golden["nop_count"] <= slack, (nops, golden["nop_count"]))
                self.assertAlmostEqual(seconds, 81 * pace, places=9)
                self.assertTrue(0 <= seconds - golden["seconds"] <= slack * pace + 1e-9, (seconds, golden["seconds"]))
                self.assertEqual(len({p[12:44] for p in packets}), golden["setpoint_sets"])
                self.assertEqual(packets[-1][4], vendor_io.SET_DIGITAL)

    def test_stale_echo_never_drains_the_queue(self):
        messages = vendor_io.digital_messages(0x08, immediate=False)
        for name, echo in (("repeated", lambda n: 1), ("backward", lambda n: (1 - n) % 255 or 255),
                           ("missing", lambda n: -1)):
            with self.subTest(echo=name):
                sent, seconds = self.drive(messages, 0.1, echo=echo, limit=200)   # 20 s, past the 5 s deadline
                self.assertEqual({o.cmd for o in sent}, {vh.NOP})
        # Progress across the wrap counts: 254, 255, 1, 2 ... drains as usual.
        sent, _ = self.drive(messages, 0.0108, echo=lambda n: (n + 252) % 255 + 1)
        self.assertEqual(sent[-1].cmd, vendor_io.SET_DIGITAL)

    def test_advanced_is_a_small_forward_step_on_the_ring(self):
        self.assertTrue(vendor_io.advanced(255, 1))
        self.assertTrue(vendor_io.advanced(7, 7 + vh.MAX_LAG))
        for last, echo in ((1, 255), (7, 7), (7, 6), (7, 8 + vh.MAX_LAG), (-1, 3), (3, -1), (0, 1)):
            with self.subTest(last=last, echo=echo):
                self.assertFalse(vendor_io.advanced(last, echo))

    def test_stale_echo_fails_at_the_source_with_nothing_sent(self):
        now = [0.0]
        source = VendorHomeSource(start=lambda first: vendor_io.send_queued(
            first, vendor_io.digital_messages(0x08, immediate=False)),
            clock=lambda: now[0], total_seconds=vendor_io.TOTAL_SECONDS)
        stale = bytearray(64)
        stale[0] = 1                                            # every reply echoes message 1
        stale[19:49] = b"\xff\xff\x7f\x00\x00" * 6              # every count 0
        commands, step, sequence = [], ("send", None), 2
        while step[0] == "send" and len(commands) < 1000:
            step = source(bytes(stale), sequence)
            if step[0] == "send":
                commands.append(step[1][4])
            sequence, now[0] = sequence % 255 + 1, now[0] + 0.05
        self.assertEqual(step, ("stop",))
        self.assertIsInstance(source.error, vh.HomeError)
        self.assertNotIn(vendor_io.SET_DIGITAL, commands)

    def test_immediate_still_sends_clear_then_the_word(self):
        golden = OUTPUTS[0]
        self.assertEqual(golden["call"][2], 1)
        sent = list(vendor_io.send(vh.Reply(counts=(0,) * 6, switches=0, now=0.0, echo=1),
                                   vendor_io.digital_messages(0x02)))
        self.assertEqual([vh.encode_out(o, 1)[1:12].hex() for o in sent], golden["commands"])


class QueuedOutputRobotTests(SimulatedRobotCase):

    def test_interrupt_during_wait_never_sends_the_output(self):
        for signal in ("stop", "pendant", "emergency", "fault", "cancel"):
            with self.subTest(signal=signal):
                robot = self.robot()

                def interrupt(count, plant, robot=robot, signal=signal):
                    if count == 3:
                        if signal == "stop":
                            robot.request_stop()
                        elif signal == "pendant":
                            robot._pendant_abort()
                        elif signal == "emergency":
                            robot.sim.stream_emergency = True
                        elif signal == "fault":
                            robot._latch_fault("injected feedback fault")
                        else:
                            robot._cancel_event.set()

                robot.sim.vendor_hook = interrupt
                with self.assertRaises(ScorbotError):
                    robot.set_digital_output(3, True, immediate=False)
                self.assertNotIn(0x44, [p[4] for p in robot.sim.vendor_packets])
                self.assertEqual(robot.digital_outputs, 0)
                self.assertIsNotNone(robot._fault)

    def test_a_pending_stop_refuses_the_queued_write_once(self):
        robot = self.robot()
        robot.request_stop()
        before = len(robot.sim.vendor_packets)
        with self.assertRaises(ScorbotError):
            robot.set_digital_output(3, True, immediate=False)
        self.assertEqual(robot.sim.vendor_packets[before:], [])
        self.assertEqual(robot.digital_outputs, 0)
        self.assertIsNone(robot._fault)
        robot.set_digital_output(3, True, immediate=False)       # the stop was used up
        self.assertEqual(fields(robot.sim.vendor_packets[-1]), (0x44, 0, 0, 0x08))

    def test_queued_write_sends_no_clear_and_holds_before_the_word(self):
        robot = self.robot()
        robot.set_digital_output(3, True, immediate=False)
        packets = robot.sim.vendor_packets
        self.assertNotIn(0x47, [p[4] for p in packets])
        self.assertEqual(fields(packets[-1]), (0x44, 0, 0, 0x08))
        self.assertGreaterEqual(sum(1 for p in packets if p[4] == 0x0D), 40)
        self.assertEqual(robot.digital_outputs, 0x08)
        rows = [r for r in self.rows() if r["event"] == "digital_output"]
        self.assertFalse(rows[-1]["immediate"])


if __name__ == "__main__":
    unittest.main()

