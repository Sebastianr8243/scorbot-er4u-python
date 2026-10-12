"""The controller's collision flag (IN offset 59, one bit per motor).

Layout from the ULPGC thesis (docs/protocol/ULPGC_THESIS_FINDINGS.md): 0x01 is motor 1,
0x18 is motors 4 and 5 (its Figures 51 and 52), 0x20 is the gripper. Synthetic packets only.
"""

import unittest

from scorbot.simulated import encode_packet
from scorbot.state import COLLISION_OFFSET, PACKET_MIN_LENGTH, collision_motors, decode_state


def packet_with(flag: int) -> bytes:
    packet = bytearray(encode_packet({}))
    packet[COLLISION_OFFSET] = flag
    return bytes(packet)


class CollisionMotorsTests(unittest.TestCase):
    def test_the_offset_is_the_one_in_the_thesis_figures(self):
        self.assertEqual(COLLISION_OFFSET, 59)

    def test_no_flag_means_no_motor(self):
        self.assertEqual(collision_motors(packet_with(0)), [])

    def test_each_bit_names_its_motor(self):
        names = ["base", "shoulder", "elbow", "wrist_motor_1", "wrist_motor_2", "gripper"]
        for bit, name in enumerate(names):
            with self.subTest(bit=bit):
                self.assertEqual(collision_motors(packet_with(1 << bit)), [name])

    def test_two_wrist_motors_together_as_in_the_thesis(self):
        self.assertEqual(collision_motors(packet_with(0x18)), ["wrist_motor_1", "wrist_motor_2"])

    def test_external_motors_seven_and_eight_are_named_not_dropped(self):
        self.assertEqual(collision_motors(packet_with(0x40)), ["motor7"])
        self.assertEqual(collision_motors(packet_with(0x80)), ["motor8"])

    def test_a_packet_too_short_to_hold_the_flag_reports_none(self):
        self.assertEqual(collision_motors(packet_with(0x02)[:COLLISION_OFFSET]), [])
        self.assertEqual(collision_motors(bytes(PACKET_MIN_LENGTH)), [])


class DecodedStateTests(unittest.TestCase):
    def decode(self, packet):
        return decode_state(packet, connected=True, enabled=True, homed=False, fault=None)

    def test_the_state_carries_the_flag(self):
        self.assertEqual(self.decode(packet_with(0x02)).collision_bits, 2)
        self.assertEqual(self.decode(packet_with(0)).collision_bits, 0)

    def test_a_short_packet_decodes_with_no_flag(self):
        self.assertEqual(self.decode(packet_with(0x02)[:PACKET_MIN_LENGTH]).collision_bits, 0)

    def test_old_positional_construction_still_works(self):
        from scorbot.state import RobotState
        state = RobotState("t", {}, {}, 0, True, True, False, None)
        self.assertEqual(state.collision_bits, 0)


if __name__ == "__main__":
    unittest.main()
