"""Opt-in controller impact modelling through packets only; no USB."""

import unittest

from scorbot import vendor_home as vh
from scorbot.state import ERROR_OFFSETS, IMPACT_DETECT_COUNTS, JOINTS
from tests.vendor_home_plant import Plant, run

THRESHOLDS = (70, 70, 70, 70, 70, 300)


def errors(packet):
    return tuple(int.from_bytes(packet[o:o + 2], "little", signed=True) for o in ERROR_OFFSETS)


class ImpactModelTests(unittest.TestCase):
    def test_default_keeps_error_words_and_impact_zero_with_a_large_gap(self):
        plant = Plant()
        plant.mode[1] = "S"
        packet = plant.exchange(vh.encode_out(vh.Out(vh.NOP, setpoints=(0, 10000, 0, 0, 0, 0)), 1))
        self.assertEqual(packet[59], 0)
        self.assertEqual(errors(packet), (0,) * 6)

    def test_thresholds_match_the_controller_parameters(self):
        self.assertEqual(THRESHOLDS, tuple(IMPACT_DETECT_COUNTS[name] for name in JOINTS))

    def test_shoulder_error_precedes_follow_and_uses_the_offset_and_strict_threshold(self):
        for gap in (153, 70, -153, -70):
            with self.subTest(gap=gap):
                plant = Plant(impact_detect=THRESHOLDS)
                plant.phys[1], plant.off[1] = 500, -200
                packet = plant.exchange(vh.encode_out(
                    vh.Out(vh.MODE, 0x02, vh.MODE_S, setpoints=(0, 300 + gap, 0, 0, 0, 0)), 1))
                self.assertEqual(packet[27:29], gap.to_bytes(2, "little", signed=True))
                self.assertEqual(errors(packet), (0, gap, 0, 0, 0, 0))
                self.assertEqual(packet[59], 0x02 if abs(gap) > 70 else 0)
                self.assertEqual(plant.phys[1], 500 + gap)
                settled = plant.exchange(vh.encode_out(
                    vh.Out(vh.NOP, setpoints=(0, 300 + gap, 0, 0, 0, 0)), 2))
                self.assertEqual(errors(settled), (0,) * 6)
                self.assertEqual(settled[59], 0)

    def test_each_motor_bit_clamping_and_manual_impact(self):
        for motor in range(6):
            for gap in (40000, -40000):
                with self.subTest(motor=motor, gap=gap):
                    plant = Plant(impact_detect=THRESHOLDS, frozen=(motor,))
                    plant.mode[motor] = "S"
                    plant.impact = 0x80
                    targets = [plant.reported(n) for n in range(6)]
                    targets[motor] += gap
                    packet = plant.exchange(vh.encode_out(vh.Out(vh.NOP, setpoints=tuple(targets)), 1))
                    expected = [0] * 6
                    expected[motor] = max(-32768, min(32767, gap))
                    self.assertEqual(errors(packet), tuple(expected))
                    self.assertEqual(packet[59], 0x80 | (1 << motor))

    def test_non_s_modes_do_not_model_errors_or_impact(self):
        plant = Plant(impact_detect=THRESHOLDS)
        plant.mode = ["T", "H", "P", "T", "H", "P"]
        packet = plant.exchange(vh.encode_out(vh.Out(vh.NOP, setpoints=(10000,) * 6), 1))
        self.assertEqual(errors(packet), (0,) * 6)
        self.assertEqual(packet[59], 0)

    def test_shoulder_disturbance_during_elbow_search_reproduces_lab_failure(self):
        plant = Plant(impact_detect=THRESHOLDS)
        injected = False

        def disturb(n, p):
            nonlocal injected
            if not injected and p.mode[1] == "S" and p.drive[2] != 0:
                p.phys[1] += 153
                p.frozen.add(1)  # Keep the disturbance through the type-14 reversal's extra messages.
                injected = True

        with self.assertRaises(vh.HomeError) as caught:
            run(plant, on_cycle=disturb)
        self.assertTrue(injected)
        self.assertEqual(caught.exception.code, vh.ERR_IMPACT)
        text = str(caught.exception)
        self.assertIn("flag 0x02 (shoulder)", text)
        self.assertIn("while searching elbow", text)
        self.assertIn("error words [0, -153, 0, 0, 0, 0]", text)
