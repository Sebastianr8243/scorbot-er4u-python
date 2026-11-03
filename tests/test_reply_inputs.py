"""The controller's digital and analog inputs in every reply (IN byte 6 and bytes 7-10).

The vendor DLL sends no command for ``GetDigitalInputs`` or ``GetAnalogInput``: its comm thread stores
reply byte 6 and bytes 7-10, and those functions return the stored values (docs/protocol/LEDGER.md PL65,
PL69). Synthetic packets only; what the lines read on the arm is unverified.
"""

import unittest

from scorbot.simulated import encode_packet
from scorbot.state import (ANALOG_INPUT_OFFSETS, DIGITAL_INPUT_OFFSET, PACKET_MIN_LENGTH, RobotState,
                           decode_state)


def decode(packet):
    return decode_state(packet, connected=True, enabled=True, homed=False, fault=None)


class ReplyInputTests(unittest.TestCase):

    def test_the_offsets_are_the_dlls(self):
        self.assertEqual(DIGITAL_INPUT_OFFSET, 6)
        self.assertEqual(ANALOG_INPUT_OFFSETS, (7, 8, 9, 10))

    def test_the_state_carries_the_inputs(self):
        packet = bytearray(encode_packet({}))
        packet[6] = 0b1010_0101
        packet[7:11] = bytes([0, 17, 128, 255])
        state = decode(bytes(packet))
        self.assertEqual(state.digital_inputs, 0xA5)
        self.assertEqual(state.analog_inputs, [0, 17, 128, 255])

    def test_the_inputs_do_not_change_the_other_fields(self):
        packet = bytearray(encode_packet({"base": 300}))
        plain = decode(bytes(packet))
        packet[6], packet[7] = 0xFF, 0xFF
        busy = decode(bytes(packet))
        self.assertEqual(busy.encoder_counts, plain.encoder_counts)
        self.assertEqual(busy.home_switch_bits, plain.home_switch_bits)

    def test_old_positional_construction_still_works(self):
        state = RobotState("t", {}, {}, 0, True, True, False, None)
        self.assertEqual(state.digital_inputs, 0)
        self.assertEqual(state.analog_inputs, [0, 0, 0, 0])

    def test_the_shortest_decodable_packet_still_holds_them(self):
        self.assertGreater(PACKET_MIN_LENGTH, ANALOG_INPUT_OFFSETS[-1])


if __name__ == "__main__":
    unittest.main()
