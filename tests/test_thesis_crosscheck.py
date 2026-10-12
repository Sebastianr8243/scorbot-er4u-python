"""The vendor-home port against what the ULPGC thesis captured of SCORBASE on the real controller.

Source: Miranda Betancor, ULPGC 2017 (``references/ULPGC-2017-Miranda-Scorbot-ER4u-USB-driver.pdf``, local
only), pp. 58 to 60. The author used SCORBASE's "Poner Eje A Cero" on the external motors 7 and 8 and
captured every non-idle OUT message (Captures 7, 8 and 9):

    4F 40 54    mode T for mask 0x40 (motor 7)
    48 40 00    set position of mask 0x40
    47 00 00    clear
    4F 40 53    mode S for mask 0x40
    73 20 00    control off, gripper group
    42 20 00    motors, gripper group

and a table of the buffer offsets that hold each motor's position: 12, 16, 20, ..., 40 for motors 1 to 8.
It is the only real-controller evidence for the ``48`` set-position command; the port's command bytes come
from the vendor DLL, so agreement with a capture that never went through the DLL is a second source for the
shape of a zeroing (the controller's acceptance of the arm axes themselves is still unverified).
"""

import unittest

from scorbot import vendor_home as vh
from tests.vendor_home_plant import Plant, commands, run

THESIS_ZEROING = [
    (0x4F, 0x40, 0x54),
    (0x48, 0x40, 0x00),
    (0x47, 0x00, 0x00),
    (0x4F, 0x40, 0x53),
    (0x73, 0x20, 0x00),
    (0x42, 0x20, 0x00),
]
THESIS_POSITION_OFFSETS = {1: 12, 2: 16, 3: 20, 4: 24, 5: 28, 6: 32, 7: 36, 8: 40}


class ThesisCrosscheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.outs, cls.result = run(Plant())
        cls.mine = commands(cls.outs)

    def test_the_thesis_commands_encode_to_the_same_header_bytes_as_ours(self):
        for cmd, mask, arg in THESIS_ZEROING:
            if cmd == 0x4F:
                packet = vh.encode_out(vh.Out(cmd, mask, arg), 9)
                self.assertEqual((packet[4], packet[5], packet[6]), (cmd, mask, arg))
            else:
                packet = vh.encode_out(vh.Out(cmd, mask), 9)
                self.assertEqual((packet[4], packet[5], packet[6], packet[7]), (cmd, mask, 0, 0))

    def test_the_setpoint_slot_for_each_motor_is_where_the_thesis_found_it(self):
        for motor, offset in THESIS_POSITION_OFFSETS.items():
            setpoints = tuple(7 if n == motor - 1 else 0 for n in range(6))
            if motor > 6:
                continue                                      # the port drives the six arm motors only
            packet = vh.encode_out(vh.Out(vh.NOP, setpoints=setpoints), 1)
            self.assertEqual(int.from_bytes(packet[offset:offset + 4], "little", signed=True), 7, motor)

    def test_every_zeroing_of_the_gripper_ends_the_way_scorbases_does(self):
        # 48 <mask>, 47, 4F <mask> 53, 73 20, 42 20: the thesis's captures 7 to 9 minus the leading mode T.
        mask = 1 << vh.GRIPPER
        thesis_tail = [list(c) for c in THESIS_ZEROING[1:]]
        want = [[0x48, mask, 0, 0], [0x47, 0, 0, 0], [0x4F, mask, 0x53, 0], [0x73, 0x20, 0, 0], [0x42, 0x20, 0, 0]]
        self.assertEqual([[c[0], (mask if c[0] in (0x48, 0x4F) else c[1]), c[2], 0] for c in thesis_tail], want)
        index = max(i for i, c in enumerate(self.mine) if c[0] == vh.SET_POSITION and c[1] == mask)
        self.assertEqual(self.mine[index:index + 5], want)

    def test_every_arm_motor_is_put_in_mode_t_before_it_is_zeroed_like_the_thesis_capture(self):
        # Capture 7 sends 4F <mask> 54 and then 48 <mask>. In the port's first zeroing group (the ER-4u
        # pre-pass of shoulder and elbow) each zeroed motor has had its mode T just before.
        first = next(i for i, c in enumerate(self.mine) if c[0] == vh.SET_POSITION)
        modes_t = {c[1] for c in self.mine[max(0, first - 8):first] if c[0] == vh.MODE and c[2] == vh.MODE_T}
        zeroed = []
        for c in self.mine[first:]:
            if c[0] != vh.SET_POSITION:
                break
            zeroed.append(c[1])
        self.assertTrue(zeroed)
        self.assertTrue(set(zeroed) <= modes_t, (zeroed, modes_t))


if __name__ == "__main__":
    unittest.main()
