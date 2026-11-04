"""Property-based tests (Hypothesis) for encoder / packet arithmetic.

Encoder counts are 16-bit little-endian values plus a separate sign byte
(128 = positive, 127 = negative). Negative counts are ones'-complement-like:
``signed = raw - 65535``, so the counter modulus is 65535, not 65536.

Properties that hold only on a restricted domain are restricted to it. Inputs
outside the domain that expose real defects are pinned by explicit
``test_known_bug_*`` tests marked ``expectedFailure``; they document the bug and
turn into "unexpected success" (a loud failure) once product code is fixed. None is
open now: the two found so far (``suma`` and ``resta`` past one full turn) were fixed
on 2026-10-06 and their tests flipped.
No USB is ever opened.
"""

import importlib.util
import threading
import unittest
from unittest.mock import patch

from openScorbot import motion_profile
from scorbot import Scorbot
from scorbot import packet as packet_module
from scorbot.calibration import signed_count_delta
from scorbot.packet import TrackedInputEndpoint
from scorbot.session.analysis import csv_safe
from scorbot.simulated import encode_packet
from scorbot.state import COUNT_MAX, COUNT_MIN, COUNT_ZERO, ENCODER_OFFSETS, JOINTS, decode_state

HAVE_HYPOTHESIS = importlib.util.find_spec("hypothesis") is not None
HAVE_USB = importlib.util.find_spec("usb") is not None

MAX_COUNT = 65535


def _count(raw, sign):
    """The controller count of a low word and sign word, raw24 - 0x7FFFFF (docs/requirements/count-frame.md)."""
    return raw + 1 if sign in (128, "0000") else raw - 65535


def _count_pair(count):
    """Inverse of _count for -65535..65536, as (low word, legacy sign text)."""
    return (count - 1, "0000") if count >= 1 else (count + 65535, "ffff")


def _low(count):
    """The low word a reply carries for a controller count (raw24 = count + 0x7FFFFF)."""
    return (count + COUNT_ZERO) & 0xFFFF


def _legacy(name):
    return Scorbot()._legacy(name)


if HAVE_HYPOTHESIS:
    from hypothesis import assume, given, settings, strategies as st

    # 100 examples each, except the large-count profile: it builds a step list of up to
    # 70,000 entries per example, so 20 keep the boundaries at a fifth of the cost.
    # The known-bug tests below use fixed inputs and do not depend on it.
    PROFILE = settings(deadline=None, max_examples=100)
    SLOW = settings(deadline=None, max_examples=20)

    raw16 = st.integers(0, 65535)
    signed_counts = st.integers(-MAX_COUNT, MAX_COUNT)   # the legacy jog pair's range
    counts24 = st.integers(COUNT_MIN, COUNT_MAX)          # what the controller reports
    steps = st.integers(0, 20)  # legacy speed is 1..20 counts per iteration
    signs = st.sampled_from(["0000", "ffff"])

    _mp = _legacy("motion_profile")  # pure Python, no USB import
    JOINT_NAMES = sorted(_mp.COUNTS_PER_DEGREE)
    ORDERS = sorted(_mp.ORDER_TO_JOINT)


@unittest.skipUnless(HAVE_HYPOTHESIS, "hypothesis is not installed")
@unittest.skipUnless(HAVE_USB, "pyusb is required by the legacy modules")
class LegacyLibdefProperties(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = _legacy("libdef")

    @PROFILE
    @given(raw16)
    def test_transform_detrans_round_trip(self, n):
        text = self.lib.detrans(n)
        self.assertEqual(len(text), 4)
        # detrans emits the little-endian byte pair as hex.
        self.assertEqual(bytes.fromhex(text), n.to_bytes(2, "little"))
        self.assertEqual(self.lib.transform(list(bytes.fromhex(text))), n)

    @PROFILE
    @given(st.integers(0, 255), st.integers(0, 255))
    def test_transform_matches_little_endian(self, lo, hi):
        self.assertEqual(self.lib.transform([lo, hi]), lo + 256 * hi)

    @PROFILE
    @given(st.integers(1, 255))
    def test_count_byte_stays_in_1_to_255(self, b):
        nxt = self.lib.countByte1(b)
        self.assertIn(nxt, range(1, 256))
        self.assertEqual(nxt, b + 1 if b < 255 else 1)
        self.assertEqual(len(self.lib.f_byte(b)), 2)
        self.assertEqual(int(self.lib.f_byte(b), 16), b)

    @PROFILE
    @given(st.integers(0, 10 ** 6))
    def test_count_byte_never_yields_zero_or_256_for_any_input(self, b):
        self.assertIn(self.lib.countByte1(b), range(1, 256))

    def test_count_byte_full_cycle(self):
        seen, b = [], 1
        for _ in range(255):
            seen.append(b)
            b = self.lib.countByte1(b)
        self.assertEqual(sorted(seen), list(range(1, 256)))
        self.assertEqual(b, 1)

    @PROFILE
    @given(raw16)
    def test_get_error_folds_near_top_of_range(self, x):
        got = self.lib.getError(list(x.to_bytes(2, "little")), 0)
        if x < 65500:
            self.assertEqual(got, x)
        else:
            # 65535 is "negative zero": errors fold to a small magnitude.
            self.assertEqual(got, 65535 - x)
            self.assertLessEqual(got, 35)

    # -- suma / resta ---------------------------------------------------

    @PROFILE
    @given(raw16, signs, steps)
    def test_suma_moves_the_count_by_step(self, raw, sign, step):
        # On the '0000' side the low word runs past 65535 instead of wrapping (count-frame.md).
        out = self.lib.suma([raw, sign], 1, 20, 100, step=step)
        self.assertIn(out[1], ("0000", "ffff"))
        # The controller count moves by exactly the step, across zero too.
        self.assertEqual(_count(*out) - _count(raw, sign), step)

    @PROFILE
    @given(raw16, signs, steps)
    def test_resta_moves_the_count_by_step(self, raw, sign, step):
        out = self.lib.resta([raw, sign], 1, 20, 100, step=step)
        self.assertIn(out[1], ("0000", "ffff"))
        self.assertEqual(_count(*out) - _count(raw, sign), -step)

    @PROFILE
    @given(signed_counts, steps)
    def test_suma_signed_convention_within_representable_range(self, s, step):
        assume(s + step <= MAX_COUNT)
        raw, sign = _count_pair(s)
        out = self.lib.suma([raw, sign], 1, 20, 100, step=step)
        self.assertEqual(_count(out[0], out[1]), s + step)

    @PROFILE
    @given(signed_counts, steps)
    def test_resta_signed_convention_within_representable_range(self, s, step):
        assume(s - step >= -MAX_COUNT)
        raw, sign = _count_pair(s)
        out = self.lib.resta([raw, sign], 1, 20, 100, step=step)
        self.assertEqual(_count(out[0], out[1]), s - step)

    @PROFILE
    @given(raw16, signs, steps)
    def test_suma_then_resta_returns_to_start_modulo_wrap(self, raw, sign, step):
        mid = self.lib.suma([raw, sign], 1, 20, 100, step=step)
        back = self.lib.resta(list(mid), 1, 20, 100, step=step)
        self.assertEqual(_count(*back), _count(raw, sign))

    @PROFILE
    @given(raw16, signs, st.integers(1, 20), st.integers(13, 200), st.integers(1, 300))
    def test_speed_profile_increments_are_int_and_bounded(self, raw, sign, vel, ite, cont):
        inc = self.lib.incremento(cont, vel, ite)
        self.assertIs(type(inc), int)
        self.assertIn(inc, range(vel + 1))
        for fn, direction in ((self.lib.suma, 1), (self.lib.resta, -1)):
            out = fn([raw, sign], cont, vel, ite)
            self.assertEqual(_count(*out) - _count(raw, sign), direction * inc)

    @PROFILE
    @given(st.lists(raw16, min_size=6, max_size=6), st.lists(raw16, min_size=6, max_size=6))
    def test_get_media_stays_integer_and_snaps_on_jumps(self, readings, media):
        buf = bytearray(64)
        for offset, value in zip(self.lib.VEC_POS, readings):
            buf[offset:offset + 2] = value.to_bytes(2, "little")
        out = self.lib.get_media(buf, list(media))
        for value, old, new in zip(readings, media, out):
            self.assertIs(type(new), int)
            if abs(value - old) >= 1000:
                self.assertEqual(new, value)
            else:
                self.assertGreaterEqual(new, min(value, old))
                self.assertLessEqual(new, max(value, old))

    @PROFILE
    @given(st.integers(0, 255))
    def test_get_signo_only_accepts_127_and_128(self, byte):
        buf = [0, 0, byte]
        if byte == 128:
            self.assertEqual(self.lib.get_signo(2, buf), "0000")
        elif byte == 127:
            self.assertEqual(self.lib.get_signo(2, buf), "ffff")
        else:
            with self.assertRaises(ValueError):
                self.lib.get_signo(2, buf)

    @PROFILE
    @given(st.lists(raw16, min_size=6, max_size=6),
           st.lists(st.sampled_from([127, 128]), min_size=6, max_size=6))
    def test_get_encoder_builds_six_position_sign_fields(self, counts, sign_bytes):
        buf = bytearray(64)
        for offset, c, s in zip(self.lib.VEC_POS, counts, sign_bytes):
            buf[offset:offset + 2] = c.to_bytes(2, "little")
            buf[offset + 2] = s
        msg = self.lib.get_encoder(buf, list(counts))
        self.assertEqual(len(msg), 48)
        raw = bytes.fromhex(msg)
        for i, (c, s) in enumerate(zip(counts, sign_bytes)):
            # Each slot is the controller count as signed 32-bit (docs/requirements/count-frame.md).
            self.assertEqual(int.from_bytes(raw[4 * i:4 * i + 4], "little", signed=True), _count(c, s))


@unittest.skipUnless(HAVE_HYPOTHESIS, "hypothesis is not installed")
@unittest.skipUnless(HAVE_USB, "pyusb is required by the legacy modules")
class LegacyKnownBugs(unittest.TestCase):
    """Defects found by property search. Both were fixed on 2026-10-06 (see each test)."""

    @classmethod
    def setUpClass(cls):
        cls.lib = _legacy("libdef")

    @staticmethod
    def model(value, sign, step):
        """suma (step > 0) / resta (step < 0) since 2026-11-04: the low word moves by the step and only
        a crossing of zero changes the sign word, by 65536 (docs/requirements/count-frame.md)."""
        value += step
        if value > 65535 and sign == "ffff":
            return value - 65536, "0000"
        if value < 0 and sign == "0000":
            return value + 65536, "ffff"
        return value, sign

    def test_suma_and_resta_follow_the_model_for_every_step_the_arm_can_send(self):
        # Around both seams for every step size up to one full turn, on both sign words.
        for start in (0, 1, 20, 100, 32767, 65434, 65515, 65534, 65535):
            for sign in ("0000", "ffff"):
                for step in list(range(130)) + [1000, 32767, 32768, 65534, 65535]:
                    with self.subTest(start=start, sign=sign, step=step):
                        got = self.lib.suma([start, sign], 1, 20, 100, step=step)
                        self.assertEqual(tuple(got), self.model(start, sign, step))
                        self.assertEqual(_count(*got) - _count(start, sign), step)
                        got = self.lib.resta([start, sign], 1, 20, 100, step=step)
                        self.assertEqual(tuple(got), self.model(start, sign, -step))
                        self.assertEqual(_count(*got) - _count(start, sign), -step)

    def test_suma_double_overflow_is_fixed(self):
        # Counterexample (fixed 2026-10-06): suma([65535, '0000'], step=65536) left 65536 and
        # detrans() misaligned the field. Since 2026-11-04 the low word runs on (131071) and
        # countField writes the count, 131072, as a 4-byte slot.
        out = self.lib.suma([65535, "0000"], 1, 20, 100, step=65536)
        self.assertEqual(_count(*out), 131072)
        self.assertEqual(len(self.lib.countField(*out)), 8)

    def test_resta_double_underflow_is_fixed(self):
        # Counterexample (fixed 2026-10-06): resta([0, '0000'], step=65536) formatted "-001".
        out = self.lib.resta([0, "0000"], 1, 20, 100, step=65536)
        self.assertEqual(_count(*out), 1 - 65536)
        self.assertEqual(len(self.lib.countField(*out)), 8)

    def test_settle_check_is_wrap_aware(self):
        # openScorbot/libcomm.py and setHome.py settle on
        #   motion_profile.count_distance(target, measured) > 20
        # Target raw 3 and measured raw 65533 are 5 counts apart (mod 65535); a
        # plain abs() gave 65530 and the move never settled (backlog item 5,
        # fixed 2026-10-05; the jog itself is pinned in test_settle_seam.py).
        target, measured = 3, 65533
        self.assertTrue(abs(target - measured) > 20)
        self.assertFalse(motion_profile.count_distance(target, measured) > 20)

    def test_signed_counts_are_linear_across_every_seam(self):
        # The signed count is raw24 - 0x7FFFFF, so it is continuous through zero and past
        # +/-65535 (until 2026-11-04 the old decode jumped there; count-frame.md). The low
        # words still wrap every 65536: signed_count_delta takes short differences from them.
        for a, b in ((1, 0), (0, -1), (65537, 65536), (-65535, -65536)):
            got = [decode_state(encode_packet({"base": v}), connected=True, enabled=None,
                                homed=False, fault=None).signed_encoder_counts["base"] for v in (a, b)]
            self.assertEqual(got, [a, b])
            self.assertEqual(signed_count_delta(_low(a), _low(b)), a - b)
        self.assertEqual(signed_count_delta(_low(1), _low(0)), 1)
        self.assertEqual(signed_count_delta(_low(65537), _low(65536)), 1)


@unittest.skipUnless(HAVE_HYPOTHESIS, "hypothesis is not installed")
class MotionProfileProperties(unittest.TestCase):
    mp = _legacy("motion_profile") if HAVE_HYPOTHESIS else None

    @PROFILE
    @given(st.integers(1, 3000), st.integers(1, 20))
    def test_increments_sum_exactly_and_respect_speed(self, counts, speed):
        inc = self.mp.increments_for_counts(counts, speed)
        self.assertEqual(sum(inc), counts)
        self.assertTrue(all(type(v) is int and 0 < v <= speed for v in inc))

    @SLOW
    @given(st.integers(3000, 70000), st.integers(1, 20))
    def test_increments_for_large_counts(self, counts, speed):
        inc = self.mp.increments_for_counts(counts, speed)
        self.assertEqual(sum(inc), counts)
        self.assertTrue(all(0 < v <= speed for v in inc))

    def _counts(self, joint, deg):
        try:
            return self.mp.counts_for_degrees(joint, deg)
        except ValueError:
            return None

    @PROFILE
    @given(st.sampled_from(JOINT_NAMES if HAVE_HYPOTHESIS else [None]),
           st.floats(0, 720, allow_nan=False), st.floats(0, 720, allow_nan=False))
    def test_counts_for_degrees_monotone_and_sign_symmetric(self, joint, a, b):
        lo, hi = sorted((a, b))
        clo, chi = self._counts(joint, lo), self._counts(joint, hi)
        if clo is not None:
            self.assertIsNotNone(chi)
            self.assertLessEqual(clo, chi)
            self.assertEqual(self._counts(joint, -lo), clo)
        if chi is None:
            self.assertIsNone(clo)

    @PROFILE
    @given(st.sampled_from(ORDERS if HAVE_HYPOTHESIS else [None]),
           st.floats(0.05, 90, allow_nan=False), st.integers(1, 20))
    def test_plan_jog_delta_signs_match_directions(self, order, degrees, speed):
        assume(self._counts(self.mp.ORDER_TO_JOINT[order], degrees) is not None)
        plan = self.mp.plan_jog(order, degrees, speed)
        counts = plan["counts_per_motor"]
        directions = self.mp.MOTOR_DIRECTIONS[order]
        expected = {n: d * counts for n, d in zip(self.mp.MOTOR_NAMES, directions) if d}
        self.assertEqual(plan["motor_count_deltas"], expected)
        for name, delta in plan["motor_count_deltas"].items():
            direction = directions[self.mp.MOTOR_NAMES.index(name)]
            self.assertEqual(delta > 0, direction > 0)
            self.assertEqual(abs(delta), counts)
        self.assertEqual(sum(plan["increments"]), counts)


@unittest.skipUnless(HAVE_HYPOTHESIS, "hypothesis is not installed")
class StateCodecProperties(unittest.TestCase):
    @PROFILE
    @given(st.lists(counts24, min_size=6, max_size=6), st.integers(0, 255))
    def test_encode_decode_round_trip(self, counts, switches):
        signed = dict(zip(JOINTS, counts))
        state = decode_state(encode_packet(signed, switches), connected=True,
                             enabled=False, homed=False, fault=None)
        self.assertEqual(state.signed_encoder_counts, signed)
        self.assertEqual(state.home_switch_bits, switches)
        for name, value in signed.items():
            self.assertEqual(state.encoder_counts[name], value & 0xFFFF)   # the count's low word (CT13)
            self.assertEqual(state.encoder_sign_bytes[name], (value + COUNT_ZERO) >> 16)

    @PROFILE
    @given(raw16, st.integers(0, 255))
    def test_decode_then_encode_preserves_raw_modulo_wrap(self, raw, sign):
        packet = bytearray(64)
        for offset in ENCODER_OFFSETS:
            packet[offset:offset + 2] = raw.to_bytes(2, "little")
            packet[offset + 2] = sign
        state = decode_state(bytes(packet), connected=True, enabled=None,
                             homed=False, fault=None)
        signed = state.signed_encoder_counts
        self.assertEqual((sign << 16 | raw) - COUNT_ZERO, signed["base"])
        # every raw value is a count; re-encoding must agree.
        again = decode_state(encode_packet(signed), connected=True, enabled=None,
                             homed=False, fault=None)
        self.assertEqual(again.signed_encoder_counts, signed)

    @PROFILE
    @given(st.integers(0, 255), st.sampled_from(JOINTS))
    def test_decode_reads_any_sign_byte_as_the_top_byte(self, top, joint):
        # Until 2026-11-04 anything but 127/128 raised: counts past +/-65535 (count-frame.md).
        packet = bytearray(encode_packet({}, 0))
        offset = ENCODER_OFFSETS[JOINTS.index(joint)]
        packet[offset + 2] = top
        state = decode_state(bytes(packet), connected=True, enabled=None, homed=False, fault=None)
        low = int.from_bytes(packet[offset:offset + 2], "little")
        self.assertEqual(state.signed_encoder_counts[joint], (top << 16 | low) - COUNT_ZERO)

    @PROFILE
    @given(st.binary(max_size=48))
    def test_decode_rejects_short_packets(self, data):
        with self.assertRaises(ValueError):
            decode_state(data, connected=True, enabled=None, homed=False, fault=None)

    @PROFILE
    @given(st.integers(COUNT_MAX + 1, 10 ** 8) | st.integers(-10 ** 8, COUNT_MIN - 1))
    def test_encode_rejects_unrepresentable_counts(self, value):
        with self.assertRaises(ValueError):
            encode_packet({"base": value})


@unittest.skipUnless(HAVE_HYPOTHESIS, "hypothesis is not installed")
class SignedCountDeltaProperties(unittest.TestCase):
    def _delta(self, a, b):
        try:
            return signed_count_delta(a, b)
        except ValueError:
            return None

    @PROFILE
    @given(raw16, raw16)
    def test_antisymmetric_and_bounded(self, a, b):
        d, e = self._delta(a, b), self._delta(b, a)
        self.assertEqual(d is None, e is None)
        if d is not None:
            self.assertEqual(d, -e)
            self.assertLessEqual(abs(d), 32766)

    @PROFILE
    @given(raw16, raw16)
    def test_applying_delta_recovers_value_modulo_wrap(self, a, b):
        d = self._delta(a, b)
        assume(d is not None)
        self.assertEqual((b + d) % 65536, a)

    @PROFILE
    @given(raw16, st.integers(-32766, 32766))
    def test_inverse_of_wrapping_addition(self, origin, delta):
        value = (origin + delta) % 65536
        self.assertEqual(signed_count_delta(value, origin), delta)

    @PROFILE
    @given(raw16)
    def test_there_is_one_zero(self, origin):
        # Low word 65535 (count 0 under 127) and 0 (count 1 under 128) are one count apart;
        # the old 65535 modulus made them equal ("two zeros"), count-frame.md.
        self.assertEqual(signed_count_delta(origin, origin), 0)
        self.assertEqual(signed_count_delta(65535, 0), -1)
        self.assertEqual(signed_count_delta(0, 65535), 1)

    @PROFILE
    @given(st.integers(0, 4000), st.integers(0, 40))
    def test_small_moves_across_the_seam_are_small_deltas(self, offset, step):
        origin = 65535 - offset
        self.assertEqual(signed_count_delta((origin + step) % 65536, origin), step)
        self.assertEqual(signed_count_delta((origin - step) % 65536, origin), -step)

    @PROFILE
    @given(raw16, st.sampled_from([32767, 32768, 32769]))
    def test_half_range_is_ambiguous(self, origin, half):
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            signed_count_delta((origin + half) % 65536, origin)

    @PROFILE
    @given(st.integers(-10 ** 6, -1) | st.integers(65536, 10 ** 6), raw16)
    def test_out_of_range_and_non_int_inputs_rejected(self, bad, ok):
        for pair in ((bad, ok), (ok, bad)):
            with self.assertRaises(ValueError):
                signed_count_delta(*pair)
        for weird in (1.0, True, None, "1"):
            with self.assertRaises(ValueError):
                signed_count_delta(weird, ok)

    @PROFILE
    @given(counts24, counts24)
    def test_consistent_with_state_signed_convention(self, s1, s2):
        d = self._delta(_low(s1), _low(s2))
        assume(d is not None)
        # Low words are the controller counts modulo 65536 always...
        self.assertEqual((d - (s1 - s2)) % 65536, 0)
        # ...and equal them exactly when the true distance is short.
        if abs(s1 - s2) <= 32766:
            self.assertEqual(d, s1 - s2)


@unittest.skipUnless(HAVE_HYPOTHESIS, "hypothesis is not installed")
class CsvSafetyProperties(unittest.TestCase):
    """Exported text can never start a spreadsheet formula (session/analysis.py)."""

    @PROFILE
    @given(st.one_of(st.integers(), st.floats(allow_nan=False), st.none(), st.booleans()))
    def test_non_text_values_are_never_changed(self, value):
        self.assertEqual(csv_safe(value), value)

    @PROFILE
    @given(st.text())
    def test_text_never_starts_with_a_formula_character(self, text):
        safe = csv_safe(text)
        self.assertFalse(safe.startswith(("=", "+", "-", "@")))
        self.assertTrue(safe.endswith(text))


class _FakeEndpoint:
    """Endpoint whose read() copies scripted packets into the caller's buffer."""

    def __init__(self, script):
        self.script = list(script)

    def read(self, buffer, timeout=None):
        data = self.script.pop(0)
        buffer[:len(data)] = data
        return len(data)


@unittest.skipUnless(HAVE_HYPOTHESIS, "hypothesis is not installed")
class TrackedEndpointProperties(unittest.TestCase):
    @PROFILE
    @given(st.lists(st.binary(min_size=0, max_size=64), min_size=1, max_size=15))
    def test_snapshot_never_returns_short_or_stale_data(self, packets):
        ep = TrackedInputEndpoint(_FakeEndpoint(packets))
        buffer = bytearray(64)
        last_index = 0
        for data in packets:
            if len(data) != len(buffer):
                with self.assertRaises(ValueError):
                    ep.read(buffer, 0.1)
                with self.assertRaises(TimeoutError):
                    ep.snapshot(after_index=last_index, timeout=0.0, max_age=3600)
                continue
            ep.read(buffer, 0.1)
            buffer[:] = bytes(64)  # legacy code reuses/mutates the buffer
            sample = ep.snapshot(timeout=0.0, max_age=3600)
            self.assertEqual(sample.data, data)  # a private copy
            self.assertEqual(sample.index, last_index + 1)
            self.assertGreater(sample.index, last_index)
            last_index = sample.index
            with self.assertRaises(TimeoutError):
                ep.snapshot(after_index=sample.index, timeout=0.0, max_age=3600)

    @PROFILE
    @given(st.integers(0, 10 ** 10), st.integers(0, 10 ** 10))
    def test_snapshot_rejects_data_older_than_max_age(self, age_ns, max_age_ns):
        ep = TrackedInputEndpoint(_FakeEndpoint([bytes(64)]))
        with patch.object(packet_module.time, "monotonic_ns", return_value=1_000):
            ep.read(bytearray(64), 0.1)
        max_age = max_age_ns / 1e9
        with patch.object(packet_module.time, "monotonic_ns", return_value=1_000 + age_ns):
            if age_ns <= int(max_age * 1e9):
                self.assertEqual(ep.snapshot(timeout=0.0, max_age=max_age).index, 1)
            else:
                with self.assertRaises(TimeoutError):
                    ep.snapshot(timeout=0.0, max_age=max_age)

    def test_snapshot_waits_for_a_new_full_packet(self):
        ep = TrackedInputEndpoint(_FakeEndpoint([bytes(64), bytes(10), bytes(64)]))
        ep.read(bytearray(64))
        first = ep.snapshot(timeout=0.0, max_age=3600)
        result = {}

        def waiter():
            result["s"] = ep.snapshot(after_index=first.index, timeout=2.0, max_age=3600)

        thread = threading.Thread(target=waiter)
        thread.start()
        with self.assertRaises(ValueError):
            ep.read(bytearray(64))  # short: must not satisfy the waiter
        ep.read(bytearray(64))
        thread.join(3)
        self.assertEqual(result["s"].index, 2)


if __name__ == "__main__":
    unittest.main()
