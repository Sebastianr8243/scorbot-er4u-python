"""Teach pendant reply byte 3 decode tests (LEDGER.md PL107).

Tests mode codes, named keys pressed and released, in and out of Home,
Abort pressed matching packet.PENDANT_ABORT, undefined keys, and range checks.
"""

from dataclasses import FrozenInstanceError
import unittest

from scorbot.packet import PENDANT_ABORT
from scorbot.pendant import KEY_NAMES, MODES, PendantInfo, decode_pendant


class PendantDecodeTests(unittest.TestCase):
    def test_all_eight_modes(self):
        expected_modes = [
            (0x40, "In Teach mode not in Home", False),
            (0xC0, "In Teach mode in Home", True),
            (0x7E, "Auto not in Home", False),
            (0xFE, "Auto in Home", True),
            (0x47, "Not Dead Man not in Home", False),
            (0x4F, "Not Dead Man not in Home", False),
            (0xF8, "Not Dead Man in Home", True),
            (0xF9, "Not Dead Man in Home", True),
        ]
        self.assertEqual(len(expected_modes), 8)
        self.assertEqual(len(MODES), 8)
        for code, expected_text, expected_in_home in expected_modes:
            info = decode_pendant(code)
            self.assertEqual(
                info,
                PendantInfo(
                    code=code,
                    mode=expected_text,
                    key=None,
                    key_name=None,
                    pressed=None,
                    in_home=expected_in_home,
                ),
                f"mode code {code:#04x}",
            )

    def test_every_named_key_pressed_and_released_in_and_out_of_home(self):
        self.assertEqual(len(KEY_NAMES), 25)
        for key_num, key_name in KEY_NAMES.items():
            # 1. Pressed, not in Home (bit 6 = 0, bit 7 = 0)
            code_pressed_out = key_num
            info = decode_pendant(code_pressed_out)
            self.assertEqual(
                info,
                PendantInfo(
                    code=code_pressed_out,
                    mode=None,
                    key=key_num,
                    key_name=key_name,
                    pressed=True,
                    in_home=False,
                ),
                f"key {key_name} pressed not in Home ({code_pressed_out:#04x})",
            )

            # 2. Released, not in Home (bit 6 = 1, bit 7 = 0)
            code_released_out = key_num | 0x40
            info = decode_pendant(code_released_out)
            self.assertEqual(
                info,
                PendantInfo(
                    code=code_released_out,
                    mode=None,
                    key=key_num,
                    key_name=key_name,
                    pressed=False,
                    in_home=False,
                ),
                f"key {key_name} released not in Home ({code_released_out:#04x})",
            )

            # 3. Pressed, in Home (bit 6 = 0, bit 7 = 1)
            code_pressed_in = key_num | 0x80
            info = decode_pendant(code_pressed_in)
            self.assertEqual(
                info,
                PendantInfo(
                    code=code_pressed_in,
                    mode=None,
                    key=key_num,
                    key_name=key_name,
                    pressed=True,
                    in_home=True,
                ),
                f"key {key_name} pressed in Home ({code_pressed_in:#04x})",
            )

            # 4. Released, in Home (bit 6 = 1, bit 7 = 1)
            code_released_in = key_num | 0xC0
            info = decode_pendant(code_released_in)
            self.assertEqual(
                info,
                PendantInfo(
                    code=code_released_in,
                    mode=None,
                    key=key_num,
                    key_name=key_name,
                    pressed=False,
                    in_home=True,
                ),
                f"key {key_name} released in Home ({code_released_in:#04x})",
            )

    def test_pendant_abort_decodes_as_abort_pressed(self):
        self.assertEqual(PENDANT_ABORT, (0x21, 0xA1))

        # 0x21: Abort pressed, not in Home
        info_21 = decode_pendant(0x21)
        self.assertEqual(
            info_21,
            PendantInfo(
                code=0x21,
                mode=None,
                key=33,
                key_name="Abort",
                pressed=True,
                in_home=False,
            ),
        )

        # 0xA1: Abort pressed, in Home
        info_a1 = decode_pendant(0xA1)
        self.assertEqual(
            info_a1,
            PendantInfo(
                code=0xA1,
                mode=None,
                key=33,
                key_name="Abort",
                pressed=True,
                in_home=True,
            ),
        )

    def test_undefined_keys(self):
        undefined_cases = [
            # (code, expected_key, expected_pressed, expected_in_home)
            (0x00, 0, True, False),   # key 0, pressed, not in home
            (0x48, 8, False, False),  # key 8, released, not in home (8 | 0x40)
            (0x8E, 14, True, True),   # key 14, pressed, in home (14 | 0x80)
            (0xD0, 16, False, True),  # key 16, released, in home (16 | 0xC0)
            (0xBF, 63, True, True),   # key 63, pressed, in home (63 | 0x80)
        ]
        for code, expected_key, expected_pressed, expected_in_home in undefined_cases:
            info = decode_pendant(code)
            self.assertEqual(
                info,
                PendantInfo(
                    code=code,
                    mode=None,
                    key=expected_key,
                    key_name="Undefined",
                    pressed=expected_pressed,
                    in_home=expected_in_home,
                ),
                f"undefined key code {code:#04x}",
            )

    def test_out_of_range_values_raise_value_error(self):
        bad_values = [-1, -100, 256, 300, 1000, True, False, "42", None, 3.14]
        for val in bad_values:
            with self.assertRaises(ValueError, msg=f"expected ValueError for {val!r}"):
                decode_pendant(val)  # type: ignore[arg-type]

    def test_pendant_info_is_frozen(self):
        info = decode_pendant(0x21)
        with self.assertRaises(FrozenInstanceError):
            info.code = 0  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
