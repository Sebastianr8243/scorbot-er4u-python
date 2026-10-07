"""Golden tests: scorbot.transport.codec builds the legacy OUT packets byte for byte.

The codec is compared with the legacy ``openScorbot`` functions (``libhex``
tables, ``libdef.fill_msg``/``f_byte``/``countByte1``/``detrans``/``get_encoder``/
``suma``/``resta``/``builder``/``getStruct``/``set_msg``, ``libsync`` handshake,
``libcomm`` motor on/off and shutdown). Packets the legacy code would write are
captured with fake endpoint objects; no USB device is ever opened and nothing
here can move the arm.

Equality with the legacy code is not equality with the controller: nothing in
this file is verified against a capture (phase B of the USB upgrade).
"""

import ast
import importlib.util
import queue
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from scorbot.state import ENCODER_OFFSETS, JOINTS
from scorbot.transport import codec

HAVE_HYPOTHESIS = importlib.util.find_spec("hypothesis") is not None
HAVE_USB = importlib.util.find_spec("usb") is not None
ROOT = Path(__file__).resolve().parents[1]

SIGN_TEXT = {128: "0000", 127: "ffff"}
TEXT_SIGN = {"0000": 128, "ffff": 127}
# libdef.builder: suma for these orders, resta for every other order.
SUMA_ORDERS = (5, 6, 9, 14)
# Joints whose 4-byte fields libdef.getStruct replaces, per order.
ORDER_JOINTS = {
    4: ("base",), 5: ("base",),
    6: ("shoulder",), 7: ("shoulder",),
    8: ("elbow",), 9: ("elbow",),
    10: ("wrist_motor_1", "wrist_motor_2"), 11: ("wrist_motor_1", "wrist_motor_2"),
    12: ("wrist_motor_1", "wrist_motor_2"), 13: ("wrist_motor_1", "wrist_motor_2"),
    14: ("gripper",), 15: ("gripper",),
    20: ("base", "shoulder", "elbow"),
}
# move_wrist: (motor 1 suma?, motor 2 suma?) per order.
WRIST_DIRECTIONS = {10: (False, True), 11: (True, False), 12: (True, True), 13: (False, False)}


def _legacy(name):
    from scorbot import Scorbot

    return Scorbot()._legacy(name)


def _legacy_table(function):
    """Every entry of a libhex table, walking indices from 1 until it ends."""
    entries = {}
    index = 1
    while (value := function(index)) != "Invalid request":
        entries[index] = value
        index += 1
    return entries


def _response(states):
    """A static controller response carrying ``states`` (joint -> (count, sign))."""
    buffer = bytearray(64)
    buffer[1] = 13  # libsync.send_wait stops waiting on this byte
    for name, offset in zip(JOINTS, ENCODER_OFFSETS):
        count, sign = states[name]
        buffer[offset:offset + 2] = count.to_bytes(2, "little")
        buffer[offset + 2] = sign
    return buffer


class _FakeOut:
    def __init__(self):
        self.packets = []

    def write(self, data, timeout):
        self.packets.append(bytes(data))
        return len(data)


class _FakeIn:
    def read(self, buffer, timeout):
        # The legacy code reads into its shared buffer; leaving it unchanged
        # replays the same response, so libdef.get_media stays fixed when the
        # smoothed vector equals the buffer counts.
        return len(buffer)


def _states(seed):
    """Deterministic joint states that include seam values."""
    seams = (0, 1, 65534, 65535, 32767, 32768)
    return {
        name: (seams[(seed + i) % len(seams)], (127, 128)[(seed + i) % 2])
        for i, name in enumerate(JOINTS)
    }


class CodecContractTests(unittest.TestCase):
    """Codec behaviour that needs no legacy code."""

    def test_constants(self):
        self.assertEqual(codec.MSG_LEN, 64)
        self.assertEqual(codec.MAX_SEQUENCE, 255)
        self.assertEqual(codec.JOINTS, JOINTS)
        self.assertEqual(codec.HEADER, b"\x00\x00\x00")

    def test_next_sequence_wraps_255_to_1(self):
        self.assertEqual(codec.next_sequence(0), 1)
        self.assertEqual(codec.next_sequence(1), 2)
        self.assertEqual(codec.next_sequence(254), 255)
        self.assertEqual(codec.next_sequence(255), 1)

    def test_next_sequence_rejects_out_of_range(self):
        for bad in (-1, 256, True, 1.0, "1", None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                codec.next_sequence(bad)

    def test_encode_count_layout(self):
        self.assertEqual(codec.encode_count(1, 128), b"\x01\x00\x00\x00")
        self.assertEqual(codec.encode_count(0x1234, 127), b"\x34\x12\xff\xff")
        self.assertEqual(codec.encode_count(65535, 128), b"\xff\xff\x00\x00")
        self.assertEqual(codec.encode_count(0, 127), b"\x00\x00\xff\xff")

    def test_encode_count_rejects_bad_input(self):
        for count, sign in ((-1, 128), (65536, 128), (True, 128), (1.0, 128),
                            (0, 0), (0, 129), (0, "0000"), (0, True)):
            with self.subTest(count=count, sign=sign), self.assertRaises(ValueError):
                codec.encode_count(count, sign)

    def test_step_count_seams(self):
        # Fixed seam cases: also checked against legacy suma/resta below.
        self.assertEqual(codec.step_count(65535, 127, 1), (1, 128))
        self.assertEqual(codec.step_count(65534, 127, 1), (65535, 127))
        self.assertEqual(codec.step_count(0, 128, -1), (65534, 127))
        self.assertEqual(codec.step_count(1, 128, -1), (0, 128))
        self.assertEqual(codec.step_count(0, 128, 65535), (65535, 128))
        self.assertEqual(codec.step_count(65535, 127, 65535), (65535, 128))
        self.assertEqual(codec.step_count(0, 128, -65535), (0, 127))
        self.assertEqual(codec.step_count(65535, 128, -65535), (0, 128))
        self.assertEqual(codec.step_count(100, 127, 0), (100, 127))

    def test_step_count_rejects_steps_the_legacy_double_overflows(self):
        for step in (65536, -65536, 10**6):
            with self.subTest(step=step), self.assertRaisesRegex(ValueError, "step"):
                codec.step_count(65535, 128, step)

    def test_step_count_rejects_bad_state(self):
        for count, sign, step in ((-1, 128, 1), (65536, 128, 1), (0, 0, 1),
                                  (0, 128, 1.5), (0, 128, True)):
            with self.subTest(count=count, sign=sign, step=step), self.assertRaises(ValueError):
                codec.step_count(count, sign, step)

    def test_build_out_layout(self):
        states = _states(0)
        packet = codec.build_out(7, codec.IDLE, states)
        self.assertEqual(len(packet), codec.MSG_LEN)
        self.assertEqual(packet[:5], b"\x07\x00\x00\x00\x0d")
        self.assertEqual(packet[5:12], bytes(7))
        self.assertEqual(packet[12:36], codec.encode_region(states))
        self.assertEqual(packet[36:], bytes(28))
        self.assertEqual(codec.build_out(255, codec.IDLE),
                         b"\xff\x00\x00\x00\x0d" + bytes(59))
        self.assertEqual(codec.build_out(3, b""), b"\x03" + bytes(63))

    def test_build_out_rejects_bad_sequence(self):
        for bad in (0, 256, -1, True, 1.0):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                codec.build_out(bad, codec.IDLE)

    def test_build_out_rejects_command_overlapping_region(self):
        with self.assertRaisesRegex(ValueError, "offset 12"):
            codec.build_out(1, bytes(9), _states(1))
        # Eight bytes end exactly at offset 12 (mov_comm(3)).
        codec.build_out(1, bytes(8), _states(1))
        with self.assertRaises(ValueError):
            codec.build_out(1, bytes(61))
        codec.build_out(1, bytes(60))

    def test_build_out_rejects_bad_joints(self):
        good = _states(2)
        missing = dict(good)
        del missing["gripper"]
        unknown = dict(good, wrist=(0, 128))
        bad_count = dict(good, base=(65536, 128))
        bad_sign = dict(good, base=(0, 0))
        not_pair = dict(good, base=5)
        for joints in (missing, unknown, bad_count, bad_sign, not_pair, {}):
            with self.subTest(joints=joints), self.assertRaises(ValueError):
                codec.build_out(1, codec.IDLE, joints)

    def test_build_out_rejects_bad_command_and_header(self):
        for command in ("0D", [13], None, 13):
            with self.subTest(command=command), self.assertRaises(ValueError):
                codec.build_out(1, command)
        for header in (b"", b"\x00\x00", b"\x00" * 4, "000"):
            with self.subTest(header=header), self.assertRaises(ValueError):
                codec.build_out(1, codec.IDLE, header=header)

    def test_pkt2_helpers_reject_unknown_values(self):
        for mode in (0, 0x0D, 256, -1, True):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                codec.pkt2_mode_command(mode)
        for index in (0, 80, -1, True):
            with self.subTest(index=index), self.assertRaises(ValueError):
                codec.pkt2_table_command(index)

    def test_tables_are_read_only(self):
        for table in (codec.MOV_COMM, codec.GET_MSG1, codec.GET_MSG2, codec.MOTORS_ON,
                      codec.MOTORS_OFF, codec.SCORBOT_OFF, codec.CLAMP):
            with self.assertRaises(TypeError):
                table[1] = b"\x00"


class ImportPurityTests(unittest.TestCase):
    def test_codec_source_imports_only_stdlib_and_state(self):
        tree = ast.parse((ROOT / "scorbot" / "transport" / "codec.py").read_text(encoding="utf-8"))
        allowed = set(sys.stdlib_module_names) | {"__future__"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertIn(alias.name.split(".")[0], allowed, alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    self.assertEqual((node.level, node.module), (2, "state"))
                else:
                    self.assertIn(node.module.split(".")[0], allowed, node.module)

    def test_import_loads_no_usb_numpy_or_legacy(self):
        code = (
            "import sys\n"
            "import scorbot.transport.codec as c\n"
            "print(c.__file__)\n"
            "bad = sorted(m for m in sys.modules if m.split('.')[0] in "
            "('usb', 'numpy', 'openScorbot', 'libdef', 'libhex', 'libsync', 'libcomm', 'conf'))\n"
            "print(bad)\n"
        )
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True,
                                text=True, check=True)
        path, loaded = result.stdout.strip().splitlines()
        self.assertTrue(Path(path).resolve().is_relative_to(ROOT), path)
        self.assertEqual(loaded, "[]")


class _LegacyMixin:
    """Loads the legacy modules once and holds comparisons shared by the classes."""

    @classmethod
    def setUpClass(cls):
        cls.hex = _legacy("libhex")
        cls.lib = _legacy("libdef")
        cls.sync = _legacy("libsync")
        cls.comm = _legacy("libcomm")
        cls.conf = _legacy("conf")
        # conf.readData re-reads data.json on every call; read the length once.
        cls.msg_len_hex = cls.conf.readData("general", "MSG_LEN")

    def legacy_packet(self, text):
        """What libdef.set_msg turns ``text`` into before writing it."""
        return bytes.fromhex(self.lib.fill_msg(text, self.msg_len_hex))

    def assert_builder_matches(self, start, order, states, dato, step):
        """libdef.builder + set_msg for one jog step versus build_out."""
        moving = ORDER_JOINTS[order][0]
        media = [states[name][0] for name in JOINTS]
        buffer = _response(states)
        _, cadena, _, legacy_state = self.lib.builder(
            start, [dato[0], SIGN_TEXT[dato[1]]], 0, 1, order, 0, media, buffer, step=step)
        out = _FakeOut()
        self.lib.set_msg(cadena, out, _FakeIn(), buffer, 0, 0)
        target = codec.step_count(*dato, step if order in SUMA_ORDERS else -step)
        self.assertEqual(target, (legacy_state[0], TEXT_SIGN[legacy_state[1]]))
        expected = codec.build_out(codec.next_sequence(start), codec.IDLE,
                                   dict(states, **{moving: target}))
        self.assertEqual(out.packets, [expected], (start, order, states, dato, step))

    def assert_struct_matches(self, order, states, targets):
        """libdef.getStruct region replacement (wrist pair, XYZ triple) versus build_out."""
        moving = ORDER_JOINTS[order]
        media = [states[name][0] for name in JOINTS]
        buffer = _response(states)
        signal_out = "".join(self.lib.detrans(targets[name][0]) + SIGN_TEXT[targets[name][1]]
                             for name in moving)
        text = self.lib.fill_msg(self.hex.mov_comm(1).format(self.lib.f_byte(9)), 24)
        text += self.lib.getStruct(order, signal_out, self.lib.get_encoder(buffer, media))
        self.assertEqual(codec.build_out(9, codec.IDLE, dict(states, **targets)),
                         self.legacy_packet(text), (order, states, targets))


@unittest.skipUnless(HAVE_USB, "pyusb is required by the legacy modules")
class LegacyGoldenTests(_LegacyMixin, unittest.TestCase):
    """Byte-for-byte equality with the legacy code over fixed and exhaustive cases."""

    def prefixed(self, seq, command_text):
        """The legacy ``mov_comm(6)`` prefix followed by a prefix-less table entry."""
        prefix = self.lib.fill_msg(self.hex.mov_comm(6).format(self.lib.f_byte(seq)), 8)
        return self.legacy_packet(prefix + command_text)

    def test_msg_len_matches_legacy_hex_length(self):
        self.assertEqual(codec.MSG_LEN * 2, self.conf.readData("general", "MSG_LEN"))
        self.assertEqual(codec.MAX_SEQUENCE + 1, self.conf.readData("general", "MAX_COUNT"))

    def test_next_sequence_matches_countByte1(self):
        for seq in range(0, 256):
            self.assertEqual(codec.next_sequence(seq), self.lib.countByte1(seq), seq)

    def test_codec_tables_cover_every_legacy_table_entry(self):
        pairs = {
            "mov_comm": (self.hex.mov_comm, codec.MOV_COMM),
            "get_msg1": (self.hex.get_msg1, codec.GET_MSG1),
            "motorson": (self.hex.motorson, codec.MOTORS_ON),
            "motorsoff": (self.hex.motorsoff, codec.MOTORS_OFF),
            "get_scorbotoff": (self.hex.get_scorbotoff, codec.SCORBOT_OFF),
            "clamp": (self.hex.clamp, codec.CLAMP),
        }
        for name, (function, table) in pairs.items():
            with self.subTest(table=name):
                self.assertEqual(set(_legacy_table(function)), set(table))
        # get_msg2: 1..79 are table data, 80..84 the formatted messages the
        # codec builds with pkt2_mode_command, PKT2_SETUP_*, pkt2_table_command
        # and PKT2_END_*.
        self.assertEqual(set(_legacy_table(self.hex.get_msg2)),
                         set(codec.GET_MSG2) | {80, 81, 82, 83, 84})

    def test_templates_with_sequence_field_for_every_sequence(self):
        tables = {
            "mov_comm": (self.hex.mov_comm, codec.MOV_COMM),
            "motorsoff": (self.hex.motorsoff, codec.MOTORS_OFF),
            "clamp": (self.hex.clamp, codec.CLAMP),
        }
        for name, (function, table) in tables.items():
            for index, text in _legacy_table(function).items():
                for seq in range(1, 256):
                    expected = self.legacy_packet(text.format(self.lib.f_byte(seq)))
                    self.assertEqual(codec.build_out(seq, table[index]), expected,
                                     (name, index, seq))

    def test_prefixless_templates_for_every_sequence(self):
        tables = {
            "get_msg1": (self.hex.get_msg1, codec.GET_MSG1),
            "motorson": (self.hex.motorson, codec.MOTORS_ON),
            "get_scorbotoff": (self.hex.get_scorbotoff, codec.SCORBOT_OFF),
        }
        for name, (function, table) in tables.items():
            for index, text in _legacy_table(function).items():
                for seq in range(1, 256):
                    self.assertEqual(codec.build_out(seq, table[index]),
                                     self.prefixed(seq, text), (name, index, seq))

    def test_pkt2_messages_for_every_sequence(self):
        b6, b7 = 1, 0
        addresses = {}
        for index in range(1, 80):
            b7 = self.sync.countByte7(b7)
            b6 = self.sync.countByte6(b7, b6)
            addresses[index] = (b6, b7)
        for index in range(1, 80):
            self.assertEqual(codec.pkt2_table_address(index), addresses[index], index)
        f = self.lib.f_byte
        for seq in range(1, 256):
            for mode in (0x72, 0x64, 0x61):
                expected = self.legacy_packet(self.hex.get_msg2(80).format(f(seq), f(mode)))
                self.assertEqual(codec.build_out(seq, codec.pkt2_mode_command(mode)), expected)
            self.assertEqual(codec.build_out(seq, codec.PKT2_SETUP_1),
                             self.legacy_packet(self.hex.get_msg2(81).format(f(seq))))
            self.assertEqual(codec.build_out(seq, codec.PKT2_SETUP_2),
                             self.legacy_packet(self.hex.get_msg2(82).format(f(seq))))
            for index, (b6, b7) in addresses.items():
                text = self.hex.get_msg2(83).format(f(seq), f(b6), f(b7)) + self.hex.get_msg2(index)
                self.assertEqual(codec.build_out(seq, codec.pkt2_table_command(index)),
                                 self.legacy_packet(text), (seq, index))
            self.assertEqual(
                codec.build_out(seq, codec.PKT2_END_COMMAND, header=codec.PKT2_END_HEADER),
                self.legacy_packet(self.hex.get_msg2(84).format(f(seq))))

    def assert_count_matches_legacy(self, count, sign):
        buffer = _response({name: (count, sign) for name in JOINTS})
        field = self.lib.detrans(count) + self.lib.get_signo(ENCODER_OFFSETS[0] + 2, buffer)
        self.assertEqual(codec.encode_count(count, sign), bytes.fromhex(field), (count, sign))

    def test_encode_count_seams_match_legacy(self):
        for count in (0, 1, 255, 256, 32767, 32768, 65534, 65535):
            for sign in (127, 128):
                self.assert_count_matches_legacy(count, sign)

    def test_encode_count_matches_legacy_for_every_count(self):
        for count in range(0, 65536):
            sign = 128 if count % 2 else 127
            self.assertEqual(codec.encode_count(count, sign),
                             bytes.fromhex(self.lib.detrans(count) + SIGN_TEXT[sign]), count)

    def assert_step_matches_legacy(self, count, sign, step):
        state = [count, SIGN_TEXT[sign]]
        legacy = self.lib.suma(state, 1, 0, 0, step=step) if step >= 0 else \
            self.lib.resta(state, 1, 0, 0, step=-step)
        self.assertEqual(codec.step_count(count, sign, step), (legacy[0], TEXT_SIGN[legacy[1]]),
                         (count, sign, step))

    def test_step_count_seams_match_legacy(self):
        counts = (0, 1, 2, 32767, 32768, 65533, 65534, 65535)
        steps = (0, 1, 2, 20, 32767, 32768, 65534, 65535)
        for count in counts:
            for sign in (127, 128):
                for step in steps:
                    self.assert_step_matches_legacy(count, sign, step)
                    self.assert_step_matches_legacy(count, sign, -step)

    def test_step_beyond_domain_is_still_rejected_by_the_codec(self):
        # Until 2026-10-06 legacy suma left 65536 here (a double overflow) and
        # detrans, a pure function that is unchanged, turned that into a silently
        # wrong count. suma now wraps repeatedly: 65535 + 65536 is 1. A step of a
        # full turn is still outside the codec's domain (real steps are at most
        # about 100), so the codec refuses it instead of choosing a meaning.
        state = self.lib.suma([65535, "0000"], 1, 0, 0, step=65536)
        self.assertEqual(state, [1, "0000"])
        self.assertEqual(self.lib.detrans(state[0]), "0100")
        self.assertEqual(self.lib.detrans(131070), "ff1f")      # detrans itself is unchanged
        with self.assertRaises(ValueError):
            codec.step_count(65535, 128, 65536)

    def test_handshake_matches_msg_start(self):
        states = _states(3)
        buffer = _response(states)
        out = _FakeOut()
        with patch.object(self.sync, "WRITE", 0), patch.object(self.sync, "READ", 0):
            seq, media = self.sync.msg_start(out, _FakeIn(), buffer)
        expected = []
        sequence = 0

        def add(command, joints=None, header=codec.HEADER):
            nonlocal sequence
            sequence = codec.next_sequence(sequence)
            expected.append(codec.build_out(sequence, command, joints, header=header))

        for index in range(1, 5):
            add(codec.GET_MSG1[index])
        add(codec.IDLE)  # send_wait: buffer[1] is already 13
        for mode in (0x72, 0x72, 0x64, 0x64, 0x64, 0x61, 0x61, 0x61):
            add(codec.pkt2_mode_command(mode))
        add(codec.PKT2_SETUP_1)
        add(codec.PKT2_SETUP_2)
        for index in range(1, 80):
            add(codec.pkt2_table_command(index))
        add(codec.PKT2_END_COMMAND, header=codec.PKT2_END_HEADER)
        add(codec.IDLE)  # second send_wait
        for _ in range(40):
            add(codec.IDLE)
        add(codec.IDLE, states)
        for index in range(1, 8):
            add(codec.MOTORS_ON[index], states)
        self.assertEqual(len(out.packets), 144)
        self.assertEqual(out.packets, expected)
        self.assertEqual(seq, sequence)

    def media_queue(self, states):
        media = queue.Queue()
        media.put([states[name][0] for name in JOINTS])
        return media

    def test_motors_on_off_and_shutdown_match_libcomm(self):
        states = _states(4)
        cases = (
            (self.comm.motors_on, [(codec.MOTORS_ON[i], states) for i in range(1, 8)]),
            (self.comm.motors_off, [(codec.MOTORS_OFF[i], states) for i in range(1, 27)]),
            # scorbotoff: 11 table messages, libsync.send_wait (one idle message
            # without encoder region, buffer[1] is already 13), get_msg1(4), get_msg1(2).
            (self.comm.scorbotoff,
             [(codec.SCORBOT_OFF[i], states) for i in range(1, 12)]
             + [(codec.IDLE, None), (codec.GET_MSG1[4], states), (codec.GET_MSG1[2], states)]),
        )
        for start in (1, 200, 250):
            for function, commands in cases:
                out = _FakeOut()
                with patch.object(self.comm, "WRITE", 0), patch.object(self.comm, "READ", 0), \
                        patch.object(self.sync, "WRITE", 0), patch.object(self.sync, "READ", 0):
                    function(start, out, _FakeIn(), _response(states), self.media_queue(states))
                expected = []
                seq = start
                for command, joints in commands:
                    seq = codec.next_sequence(seq)
                    expected.append(codec.build_out(seq, command, joints))
                self.assertEqual(out.packets, expected, (function.__name__, start))

    def test_open_and_close_move_match_libdef(self):
        for seed in range(3):
            states = _states(seed)
            for order, moving in ORDER_JOINTS.items():
                if order == 20:
                    continue
                target = {name: codec.step_count(*states[name], 37 + seed) for name in moving}
                signal_out = "".join(self.lib.detrans(target[name][0]) + SIGN_TEXT[target[name][1]]
                                     for name in moving)
                media = [states[name][0] for name in JOINTS]
                for start in (1, 254, 255):
                    out = _FakeOut()
                    opened, _, _ = self.lib.openMov(start, list(media), out, _FakeIn(),
                                                    _response(states), 0, 0)
                    closed, _, _ = self.lib.closeMov(opened, list(media), order, signal_out, out,
                                                     _FakeIn(), _response(states), 0, 0)
                    moved = dict(states, **target)
                    seqs = [codec.next_sequence(start)]
                    for _ in range(3):
                        seqs.append(codec.next_sequence(seqs[-1]))
                    self.assertEqual((opened, closed), (seqs[0], seqs[-1]))
                    expected = [codec.build_out(seqs[0], codec.MOVE_OPEN, states)] + [
                        codec.build_out(s, command, moved)
                        for s, command in zip(seqs[1:], codec.MOVE_CLOSE)
                    ]
                    self.assertEqual(out.packets, expected, (seed, order, start))

    def test_builder_jog_step_seams(self):
        for seed in range(6):
            states = _states(seed)
            for order in (4, 5, 6, 7, 8, 9, 14, 15):
                for dato in ((0, 128), (65535, 127), (1, 127), (65534, 128)):
                    for step in (0, 1, 20, 65535):
                        self.assert_builder_matches(1 + seed * 50, order, states, dato, step)

    def test_wrist_and_xyz_regions_match_getStruct(self):
        for seed in range(6):
            states = _states(seed)
            for order, (up_1, up_2) in WRIST_DIRECTIONS.items():
                # The move_wrist arithmetic: one suma/resta per motor.
                pairs = []
                for name, up in (("wrist_motor_1", up_1), ("wrist_motor_2", up_2)):
                    count, sign = states[name]
                    legacy = (self.lib.suma if up else self.lib.resta)(
                        [count, SIGN_TEXT[sign]], 1, 0, 0, step=19)
                    pairs.append((name, (legacy[0], TEXT_SIGN[legacy[1]])))
                    self.assertEqual(codec.step_count(count, sign, 19 if up else -19), pairs[-1][1])
                self.assert_struct_matches(order, states, dict(pairs))
            self.assert_struct_matches(20, states, {"base": (5, 128), "shoulder": (65535, 127),
                                                    "elbow": (0, 128)})


if HAVE_HYPOTHESIS and HAVE_USB:
    from hypothesis import given, settings, strategies as st

    # The exhaustive and seam tests above carry most of the coverage; keep these modest.
    PROFILE = settings(deadline=None, max_examples=150)
    counts = st.integers(0, 65535)
    signs = st.sampled_from((127, 128))
    joint_states = st.fixed_dictionaries({name: st.tuples(counts, signs) for name in JOINTS})
    sequences = st.integers(1, 255)

    class LegacyGoldenProperties(_LegacyMixin, unittest.TestCase):
        @PROFILE
        @given(joint_states)
        def test_region_matches_get_encoder(self, states):
            media = [states[name][0] for name in JOINTS]
            self.assertEqual(codec.encode_region(states),
                             bytes.fromhex(self.lib.get_encoder(_response(states), media)))

        @PROFILE
        @given(counts, signs, st.integers(-65535, 65535))
        def test_step_count_matches_suma_resta(self, count, sign, step):
            state = [count, SIGN_TEXT[sign]]
            legacy = self.lib.suma(state, 1, 0, 0, step=step) if step >= 0 else \
                self.lib.resta(state, 1, 0, 0, step=-step)
            self.assertEqual(codec.step_count(count, sign, step),
                             (legacy[0], TEXT_SIGN[legacy[1]]))

        @PROFILE
        @given(counts, signs, st.integers(65536, 10**6), st.booleans())
        def test_step_count_rejects_steps_beyond_domain(self, count, sign, step, negative):
            with self.assertRaises(ValueError):
                codec.step_count(count, sign, -step if negative else step)

        @PROFILE
        @given(sequences, st.sampled_from((4, 5, 6, 7, 8, 9, 14, 15)), joint_states,
               st.tuples(counts, signs), st.integers(0, 65535))
        def test_builder_jog_step_matches_build_out(self, start, order, states, dato, step):
            self.assert_builder_matches(start, order, states, dato, step)

        @PROFILE
        @given(st.sampled_from((10, 11, 12, 13, 20)), joint_states, st.tuples(counts, signs),
               st.tuples(counts, signs), st.tuples(counts, signs))
        def test_multi_joint_regions_match_getStruct(self, order, states, t1, t2, t3):
            targets = dict(zip(ORDER_JOINTS[order], (t1, t2, t3)))
            self.assert_struct_matches(order, states, targets)


if __name__ == "__main__":
    unittest.main()
