"""Property-based tests (Hypothesis) for encoder / packet arithmetic.

Encoder counts are 16-bit little-endian values plus a separate sign byte
(128 = positive, 127 = negative). Negative counts are ones'-complement-like:
``signed = raw - 65535``, so the counter modulus is 65535, not 65536.

Properties that hold only on a restricted domain are restricted to it. Inputs
outside the domain that expose real defects are pinned by explicit
``test_known_bug_*`` tests marked ``expectedFailure``; they document the bug and
turn into "unexpected success" (a loud failure) once product code is fixed.
No USB is ever opened.
"""

import importlib.util
import threading
import unittest
from unittest.mock import patch

HAVE_HYPOTHESIS = importlib.util.find_spec("hypothesis") is not None
HAVE_USB = importlib.util.find_spec("usb") is not None

from scorbot import Scorbot
from scorbot import packet as packet_module
from scorbot.calibration import signed_count_delta
from scorbot.packet import TrackedInputEndpoint
from scorbot.simulated import encode_packet
from scorbot.state import ENCODER_OFFSETS, JOINTS, decode_state

MAX_COUNT = 65535


def _signed(raw, sign):
    """Signed convention shared by scorbot.state (sign is a byte or legacy text)."""
    return raw if sign in (128, "0000") else raw - MAX_COUNT


def _pair(signed):
    """Inverse of _signed as (raw, legacy sign text)."""
    return (signed, "0000") if signed >= 0 else (signed + MAX_COUNT, "ffff")


def _legacy(name):
    return Scorbot()._legacy(name)


if HAVE_HYPOTHESIS:
    from hypothesis import assume, given, settings, strategies as st

    PROFILE = settings(deadline=None, max_examples=300)
    SLOW = settings(deadline=None, max_examples=100)

    raw16 = st.integers(0, 65535)
    signed_counts = st.integers(-MAX_COUNT, MAX_COUNT)
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
    def test_suma_stays_in_range_and_moves_by_step(self, raw, sign, step):
        out = self.lib.suma([raw, sign], 1, 20, 100, step=step)
        self.assertIn(out[0], range(0, 65536))
        self.assertIn(out[1], ("0000", "ffff"))
        self.assertEqual(len(self.lib.detrans(out[0])), 4)
        # Same distance as the calibration helper (mod-65535 arithmetic).
        self.assertEqual(signed_count_delta(out[0], raw), step)

    @PROFILE
    @given(raw16, signs, steps)
    def test_resta_stays_in_range_and_moves_by_step(self, raw, sign, step):
        out = self.lib.resta([raw, sign], 1, 20, 100, step=step)
        self.assertIn(out[0], range(0, 65536))
        self.assertIn(out[1], ("0000", "ffff"))
        self.assertEqual(len(self.lib.detrans(out[0])), 4)
        self.assertEqual(signed_count_delta(out[0], raw), -step)

    @PROFILE
    @given(signed_counts, steps)
    def test_suma_signed_convention_within_representable_range(self, s, step):
        assume(s + step <= MAX_COUNT)
        raw, sign = _pair(s)
        out = self.lib.suma([raw, sign], 1, 20, 100, step=step)
        self.assertEqual(_signed(out[0], out[1]), s + step)

    @PROFILE
    @given(signed_counts, steps)
    def test_resta_signed_convention_within_representable_range(self, s, step):
        assume(s - step >= -MAX_COUNT)
        raw, sign = _pair(s)
        out = self.lib.resta([raw, sign], 1, 20, 100, step=step)
        self.assertEqual(_signed(out[0], out[1]), s - step)

    @PROFILE
    @given(raw16, signs, steps)
    def test_suma_then_resta_returns_to_start_modulo_wrap(self, raw, sign, step):
        mid = self.lib.suma([raw, sign], 1, 20, 100, step=step)
        back = self.lib.resta(list(mid), 1, 20, 100, step=step)
        self.assertEqual(signed_count_delta(back[0], raw), 0)

    @PROFILE
    @given(raw16, signs, st.integers(1, 20), st.integers(13, 200), st.integers(1, 300))
    def test_speed_profile_increments_are_int_and_bounded(self, raw, sign, vel, ite, cont):
        inc = self.lib.incremento(cont, vel, ite)
        self.assertIs(type(inc), int)
        self.assertIn(inc, range(0, vel + 1))
        for fn in (self.lib.suma, self.lib.resta):
            out = fn([raw, sign], cont, vel, ite)
            self.assertIn(out[0], range(0, 65536))

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
            self.assertEqual(raw[4 * i:4 * i + 2], c.to_bytes(2, "little"))
            self.assertEqual(raw[4 * i + 2:4 * i + 4], b"\x00\x00" if s == 128 else b"\xff\xff")


@unittest.skipUnless(HAVE_HYPOTHESIS, "hypothesis is not installed")
@unittest.skipUnless(HAVE_USB, "pyusb is required by the legacy modules")
class LegacyKnownBugs(unittest.TestCase):
    """Real defects found by property search. Product code is untouched."""

    @classmethod
    def setUpClass(cls):
        cls.lib = _legacy("libdef")

    @unittest.expectedFailure
    def test_known_bug_suma_double_overflow(self):
        # Counterexample: suma([65535, '0000'], step=65536). The wrap subtracts
        # 65535 once, leaving 65536: still > 65535, so detrans() emits 5 hex
        # digits and the 4-byte position field of the USB message is
        # misaligned. Legacy speeds are <= 20 so it needs an unvalidated step.
        out = self.lib.suma([65535, "0000"], 1, 20, 100, step=65536)
        self.assertLessEqual(out[0], 65535)
        self.assertEqual(len(self.lib.detrans(out[0])), 4)

    @unittest.expectedFailure
    def test_known_bug_resta_double_underflow(self):
        # Counterexample: resta([0, '0000'], step=65536). 65535 + (-65536) = -1,
        # still negative, so detrans() formats "-001" and the message carries
        # garbage instead of a position.
        out = self.lib.resta([0, "0000"], 1, 20, 100, step=65536)
        self.assertGreaterEqual(out[0], 0)
        self.assertEqual(len(self.lib.detrans(out[0])), 4)

    def test_legacy_settle_check_is_not_wrap_aware(self):
        # Documents openScorbot/libcomm.py:86/135/185/266, which loop on
        #   abs(dato_in[0] - media[i]) > 20
        # That check sits inside a USB loop, so it is restated here rather than
        # run; this test cannot notice a fix in libcomm, only record the gap.
        # Target raw 3 and measured raw 65533 are 5 counts apart (mod 65535),
        # but abs() gives 65530 > 20: a move that crosses the 0/65535 seam never
        # settles and aborts with a spurious motion error.
        target, measured = 3, 65533
        self.assertTrue(abs(target - measured) > 20)
        self.assertFalse(abs(signed_count_delta(target, measured)) > 20)

    def test_signed_counts_are_not_linear_across_the_seam(self):
        # By design, not a bug: the sign byte gives +/-65535 over a counter that
        # wraps at 65535, so raw 65535/sign 128 (+65535) and raw 65534/sign 127
        # (-1) are one count apart but differ by 65536 as signed values. Take
        # differences from encoder_counts with signed_count_delta instead.
        a = decode_state(encode_packet({"base": 65535}), connected=True, enabled=None,
                         homed=False, fault=None).signed_encoder_counts["base"]
        b = decode_state(encode_packet({"base": -1}), connected=True, enabled=None,
                         homed=False, fault=None).signed_encoder_counts["base"]
        self.assertEqual(a - b, 65536)
        self.assertEqual(signed_count_delta(65535, 65534), 1)


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
    @given(st.lists(signed_counts, min_size=6, max_size=6), st.integers(0, 255))
    def test_encode_decode_round_trip(self, counts, switches):
        signed = dict(zip(JOINTS, counts))
        state = decode_state(encode_packet(signed, switches), connected=True,
                             enabled=False, homed=False, fault=None)
        self.assertEqual(state.signed_encoder_counts, signed)
        self.assertEqual(state.home_switch_bits, switches)
        for name, value in signed.items():
            raw = state.encoder_counts[name]
            self.assertIn(raw, range(0, 65536))
            self.assertEqual(state.encoder_sign_bytes[name], 128 if value >= 0 else 127)
            self.assertEqual(raw, value if value >= 0 else value + MAX_COUNT)

    @PROFILE
    @given(raw16, st.sampled_from([127, 128]))
    def test_decode_then_encode_preserves_raw_modulo_wrap(self, raw, sign):
        packet = bytearray(64)
        for offset in ENCODER_OFFSETS:
            packet[offset:offset + 2] = raw.to_bytes(2, "little")
            packet[offset + 2] = sign
        state = decode_state(bytes(packet), connected=True, enabled=None,
                             homed=False, fault=None)
        signed = state.signed_encoder_counts
        self.assertEqual(_signed(raw, sign), signed["base"])
        # raw 0/sign 127 is -65535 (representable); re-encoding must agree.
        again = decode_state(encode_packet(signed), connected=True, enabled=None,
                             homed=False, fault=None)
        self.assertEqual(again.signed_encoder_counts, signed)

    @PROFILE
    @given(st.integers(0, 255).filter(lambda b: b not in (127, 128)),
           st.sampled_from(JOINTS))
    def test_decode_rejects_any_other_sign_byte(self, bad, joint):
        packet = bytearray(encode_packet({}, 0))
        packet[ENCODER_OFFSETS[JOINTS.index(joint)] + 2] = bad
        with self.assertRaisesRegex(ValueError, "sign byte"):
            decode_state(bytes(packet), connected=True, enabled=None,
                         homed=False, fault=None)

    @PROFILE
    @given(st.binary(max_size=48))
    def test_decode_rejects_short_packets(self, data):
        with self.assertRaises(ValueError):
            decode_state(data, connected=True, enabled=None, homed=False, fault=None)

    @PROFILE
    @given(st.integers(MAX_COUNT + 1, 10 ** 6) | st.integers(-10 ** 6, -MAX_COUNT - 1))
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
        self.assertEqual((b + d) % MAX_COUNT, a % MAX_COUNT)

    @PROFILE
    @given(raw16, st.integers(-32766, 32766))
    def test_inverse_of_wrapping_addition(self, origin, delta):
        value = (origin + delta) % MAX_COUNT
        self.assertEqual(signed_count_delta(value, origin), delta)

    @PROFILE
    @given(raw16)
    def test_zero_and_negative_zero_are_identical(self, origin):
        self.assertEqual(signed_count_delta(origin, origin), 0)
        self.assertEqual(signed_count_delta(65535, 0), 0)
        self.assertEqual(signed_count_delta(0, 65535), 0)

    @PROFILE
    @given(st.integers(0, 4000), st.integers(0, 40))
    def test_small_moves_across_the_seam_are_small_deltas(self, offset, step):
        origin = MAX_COUNT - offset
        self.assertEqual(signed_count_delta((origin + step) % MAX_COUNT, origin), step)
        self.assertEqual(signed_count_delta((origin - step) % MAX_COUNT, origin), -step)

    @PROFILE
    @given(raw16, st.sampled_from([32767, 32768]))
    def test_half_range_is_ambiguous(self, origin, half):
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            signed_count_delta((origin + half) % MAX_COUNT, origin)

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
    @given(signed_counts, signed_counts)
    def test_consistent_with_state_signed_convention(self, s1, s2):
        r1, _ = _pair(s1)
        r2, _ = _pair(s2)
        d = self._delta(r1, r2)
        assume(d is not None)
        # Raw counts are the signed counts modulo 65535 always...
        self.assertEqual((d - (s1 - s2)) % MAX_COUNT, 0)
        # ...and equal them exactly when the true distance is short.
        if abs(s1 - s2) <= 32766:
            self.assertEqual(d, s1 - s2)


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
        for n, data in enumerate(packets, start=1):
            ep.read(buffer, 0.1)
            buffer[:] = bytes(64)  # legacy code reuses/mutates the buffer
            try:
                sample = ep.snapshot(timeout=0.0, max_age=3600)
            except TimeoutError:
                self.assertLess(len(data), 49)  # latest packet is short: no stale fallback
                continue
            self.assertGreaterEqual(len(data), 49)
            self.assertEqual(sample.data, data)  # a private copy
            self.assertEqual(sample.index, n)
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
        ep.read(bytearray(64))  # short: must not satisfy the waiter
        ep.read(bytearray(64))
        thread.join(3)
        self.assertEqual(result["s"].index, 3)


if __name__ == "__main__":
    unittest.main()
