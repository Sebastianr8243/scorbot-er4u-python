"""Property-based tests: rules that must hold for every input, checked with Hypothesis."""

import unittest

from hypothesis import given, settings, strategies as st

from scorbot.calibration import signed_count_delta
from scorbot.session.analysis import csv_safe
from scorbot.simulated import MAX_COUNT, encode_packet
from scorbot.state import JOINTS, decode_state

counts = st.integers(min_value=-MAX_COUNT, max_value=MAX_COUNT)
unsigned = st.integers(min_value=0, max_value=MAX_COUNT)


class PacketProperties(unittest.TestCase):
    @given(st.fixed_dictionaries({name: counts for name in JOINTS}),
           st.integers(min_value=0, max_value=255))
    def test_encode_then_decode_returns_every_count_and_switch_byte(self, values, switches):
        state = decode_state(encode_packet(values, switch_bits=switches), connected=True,
                             enabled=False, homed=False, fault=None)
        self.assertEqual(state.signed_encoder_counts, values)
        self.assertEqual(state.home_switch_bits, switches)


class CountDeltaProperties(unittest.TestCase):
    """signed_count_delta is the arithmetic both the lab review and compare rely on."""

    @given(unsigned, unsigned)
    def test_result_is_the_short_way_round_the_65535_ring(self, value, origin):
        try:
            delta = signed_count_delta(value, origin)
        except ValueError:
            self.assertIn((value - origin) % MAX_COUNT, (32767, 32768))
            return
        self.assertEqual((origin + delta) % MAX_COUNT, value % MAX_COUNT)
        self.assertLess(abs(delta), 32768)

    @given(unsigned, unsigned)
    def test_swapping_arguments_negates_the_delta(self, value, origin):
        try:
            forward = signed_count_delta(value, origin)
            backward = signed_count_delta(origin, value)
        except ValueError:
            return  # the ambiguous half-range case has no direction to negate
        self.assertEqual(forward, -backward)

    @given(unsigned, st.integers(min_value=-32766, max_value=32766))
    def test_a_small_move_is_recovered_exactly_even_across_the_wrap(self, origin, move):
        value = (origin + move) % MAX_COUNT
        self.assertEqual(signed_count_delta(value, origin), move)


class IncrementProperties(unittest.TestCase):
    @given(st.integers(min_value=1, max_value=2000), st.integers(min_value=1, max_value=20))
    @settings(max_examples=200)
    def test_increments_sum_to_the_target_and_respect_the_speed(self, total, speed):
        import importlib
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "openScorbot"))
        try:
            profile = importlib.import_module("motion_profile")
        finally:
            sys.path.pop(0)
        steps = profile.increments_for_counts(total, speed)
        self.assertEqual(sum(steps), total)
        self.assertTrue(all(0 < step <= speed for step in steps))


class CsvSafetyProperties(unittest.TestCase):
    @given(st.one_of(st.integers(), st.floats(allow_nan=False), st.none(), st.booleans()))
    def test_non_text_values_are_never_changed(self, value):
        self.assertEqual(csv_safe(value), value)

    @given(st.text())
    def test_text_never_starts_with_a_formula_character(self, text):
        safe = csv_safe(text)
        self.assertFalse(safe.startswith(("=", "+", "-", "@")))
        self.assertTrue(safe.endswith(text))


if __name__ == "__main__":
    unittest.main()
