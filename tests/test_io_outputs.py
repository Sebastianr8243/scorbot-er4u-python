"""Digital and analog outputs, the vendor's way (docs/protocol/LEDGER.md PL68, PL70), through the simulator.

``SetDigitalOutput(n, on, 1)`` at rest sends ``47`` then ``44`` with the whole output word in bytes 8-11;
``SetAnalogOutput(n, value)`` sends ``41`` with the value in bytes 6-7 and n in bytes 8-11. Bytes from
the lab DLL on the desk (2026-11-02); what the lines do on the arm is unverified.
"""

import unittest

from scorbot import ScorbotError

try:
    from tests.sim_support import SimulatedRobotCase
except ImportError:      # discovered with -s tests
    from sim_support import SimulatedRobotCase


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


if __name__ == "__main__":
    unittest.main()

