"""Pure builder for the 64-byte OUT packets the legacy ``openScorbot/`` code sends.

Executable form of ``docs/PROTOCOL.md`` sections 2 (OUT layout), 4 (handshake),
6 (motion messages) and 8 (encoder number format). Every packet the legacy
code writes can be built here from named fields instead of hex strings, and
``tests/test_transport_codec.py`` proves the bytes identical to the legacy
functions. That is equality with the legacy code, not with the controller:
the layout, the command values and their meaning are unverified against the
Intelitek software until the phase B capture comparison.

Standard library only. This module never imports ``usb``, ``openScorbot`` or
``numpy``, never sleeps and holds no state. It is not wired into ``Scorbot``,
the simulator or any script.

Layout of an OUT packet (offsets 0-based)::

    0       sequence byte, 1..255
    1-3     header, zero except in the handshake's get_msg2(84)
    4-11    command bytes (libhex tables), zero padded
    12-35   encoder region: six joints x (2-byte count LE + 2-byte sign word)
    36-63   zero padding

Signs use the IN sign byte that ``scorbot.state.decode_state`` reports
(``encoder_sign_bytes``): 128 is written as the sign word ``00 00`` and 127 as
``FF FF`` (``libdef.get_signo``). The legacy code keeps the word as the text
``'0000'``/``'ffff'``; this module keeps the byte, so a state read from the
controller can be echoed without translation.

Inputs outside the domain where the legacy code is correct raise
``ValueError``; legacy bugs are not reproduced (for example a count step above
65535, which ``libdef.suma``/``resta`` double-overflow).
"""

from collections.abc import Mapping
from types import MappingProxyType

from ..state import JOINTS

MSG_LEN = 64
"""OUT packet length in bytes (legacy ``MSG_LEN`` = 128 hex characters)."""
MAX_SEQUENCE = 255
"""Largest sequence byte; ``next_sequence`` wraps 255 -> 1 (legacy ``MAX_COUNT`` - 1)."""
MAX_COUNT = 65535
"""Largest encoder count. Counts are unsigned 16-bit with modulus 65535, not 65536."""
COUNT_MODULUS = 65535

SIGN_POSITIVE = 128
"""IN sign byte written as the OUT sign word ``00 00``."""
SIGN_NEGATIVE = 127
"""IN sign byte written as the OUT sign word ``FF FF``."""
_SIGN_WORDS = {SIGN_POSITIVE: b"\x00\x00", SIGN_NEGATIVE: b"\xff\xff"}

HEADER = b"\x00\x00\x00"
"""Bytes 1-3 of every legacy packet except the handshake's ``get_msg2(84)``."""
COMMAND_OFFSET = 4
REGION_OFFSET = 12
FIELD_LEN = 4
REGION_LEN = FIELD_LEN * len(JOINTS)


def _frozen(table):
    return MappingProxyType({index: bytes.fromhex(text) for index, text in table.items()})


# Command tables, keyed by the legacy 1-based index. Values are the bytes that
# start at offset 4. Where the legacy template carries trailing zeros
# (mov_comm(3..5)) they are kept, so each entry reads like its libhex string.

MOV_COMM = _frozen({
    1: "0D",
    2: "47",
    3: "4F3F530000000000",
    4: "7320000000000000",
    5: "4220000000000000",
    6: "",  # '{seq}000000': the prefix only; get_msg1/motorson/get_scorbotoff follow it
})
"""``libhex.mov_comm``: idle/setpoint carrier, move open, move close steps, bare prefix."""

GET_MSG1 = _frozen({1: "5A", 2: "4FFF54", 3: "73FF", 4: "42FF"})
"""``libhex.get_msg1``: handshake pkt1 (sequence n sends entry n); 4 and 2 also end ``scorbotoff``."""

GET_MSG2 = _frozen({
    1: "000800", 2: "B0AD01", 3: "B0710B", 4: "E02E00", 5: "000000",
    6: "E80300", 7: "000000", 8: "010000", 9: "460000", 10: "00F000",
    11: "000800", 12: "C0D401", 13: "804F12", 14: "E02E00", 15: "000000",
    16: "007D00", 17: "000000", 18: "010000", 19: "460000", 20: "00F000",
    21: "000800", 22: "C0D401", 23: "804F12", 24: "E02E00", 25: "000000",
    26: "E80300", 27: "000000", 28: "010000", 29: "460000", 30: "00F000",
    31: "000800", 32: "C0D401", 33: "804F12", 34: "E02E00", 35: "000000",
    36: "E80300", 37: "000000", 38: "010000", 39: "460000", 40: "00F000",
    41: "000800", 42: "C0D401", 43: "804F12", 44: "E02E00", 45: "000000",
    46: "E80300", 47: "000000", 48: "010000", 49: "460000", 50: "001800",
    51: "140000", 52: "A08601", 53: "00350C", 54: "102700", 55: "F40100",
    56: "007D00", 57: "000000", 58: "010000", 59: "2C0100", 60: "00F000",
    61: "000800", 62: "983A00", 63: "102700", 64: "E80300", 65: "000000",
    66: "E80300", 67: "000000", 68: "010000", 69: "40420F", 70: "00F000",
    71: "000800", 72: "983A00", 73: "102700", 74: "E80300", 75: "000000",
    76: "E80300", 77: "000000", 78: "010000", 79: "40420F",
})
"""``libhex.get_msg2(1..79)``: the 3 data bytes of the handshake pkt2 table.

Entries 80-84 of the legacy table are message formats, built here by
``pkt2_mode_command`` (80), ``PKT2_SETUP_1``/``PKT2_SETUP_2`` (81, 82),
``pkt2_table_command`` (83 + data) and ``PKT2_END_HEADER``/``PKT2_END_COMMAND`` (84).
"""

MOTORS_ON = _frozen({1: "0D", 2: "47", 3: "0D", 4: "4FFF53", 5: "42FF01", 6: "7320", 7: "4220"})
"""``libhex.motorson``: handshake pkt3 tail and ``libcomm.motors_on``, each with encoder region."""

MOTORS_OFF = _frozen({
    1: "0D", 2: "47", 3: "73FF", 4: "42FF", 5: "4FFF53", 6: "73FF", 7: "7320", 8: "4220",
    9: "64", 10: "64", 11: "64", 12: "61", 13: "61", 14: "61",
    15: "64", 16: "64", 17: "64", 18: "61", 19: "61", 20: "61",
    21: "64", 22: "64", 23: "64", 24: "61", 25: "61", 26: "61",
})
"""``libhex.motorsoff``: ``libcomm.motors_off`` (26 messages, each with encoder region)."""

SCORBOT_OFF = _frozen({
    1: "47", 2: "4FFF53", 3: "7320", 4: "4220", 5: "64", 6: "64", 7: "64",
    8: "61", 9: "61", 10: "61", 11: "61",
})
"""``libhex.get_scorbotoff``: ``libcomm.scorbotoff`` shutdown (each with encoder region)."""

CLAMP = _frozen({1: "4F2053", 2: "4C2000", 3: "422001", 4: "7320", 5: "4220"})
"""``libhex.clamp``: gripper sequence. The SDK never sends it (gripper is disabled)."""

IDLE = MOV_COMM[1]
"""``mov_comm(1)`` (``0D``): idle sync, jog steps, settle loops, handshake waits."""
MOVE_OPEN = MOV_COMM[2]
"""``mov_comm(2)`` (``47``): ``libdef.openMov`` and the start of each homing axis."""
MOVE_CLOSE = (MOV_COMM[3], MOV_COMM[4], MOV_COMM[5])
"""``mov_comm(3..5)``: the three ``libdef.closeMov`` messages, in order."""

PKT2_MODES = (0x72, 0x64, 0x61)
"""Values ``libsync.send_pkt2`` puts in ``get_msg2(80)``: 0x72 x2, 0x64 x3, 0x61 x3."""
PKT2_SETUP_1 = bytes.fromhex("5300080010")
"""``get_msg2(81)``."""
PKT2_SETUP_2 = bytes.fromhex("5301000000F0")
"""``get_msg2(82)``."""
PKT2_END_HEADER = b"\x00\x0c\x00"
"""``get_msg2(84)`` = ``'{0}000C000D'``: the only legacy packet whose header is not zero."""
PKT2_END_COMMAND = b"\x0d"
"""Byte 4 of ``get_msg2(84)``; build with ``build_out(seq, PKT2_END_COMMAND, header=PKT2_END_HEADER)``."""


def _require_int(name, value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an int in {low}..{high}, got {value!r}")
    return value


def _require_sign(sign):
    if type(sign) is not int or sign not in _SIGN_WORDS:
        raise ValueError(
            f"sign must be the IN sign byte {SIGN_POSITIVE} or {SIGN_NEGATIVE}, got {sign!r}")
    return sign


def next_sequence(sequence: int) -> int:
    """The sequence byte after ``sequence`` (legacy ``libdef.countByte1``).

    Counts 1..255 and wraps 255 -> 1; 0 is never produced. Accepts 0 as well
    as 1..255 because the legacy handshake (``libsync.msg_start``) starts its
    counter at 0 before the first packet; 0 itself is never sent.
    """
    _require_int("sequence", sequence, 0, MAX_SEQUENCE)
    return 1 if sequence == MAX_SEQUENCE else sequence + 1


def encode_count(count: int, sign: int) -> bytes:
    """One joint's 4-byte field: count little-endian, then the sign word.

    Legacy ``libdef.detrans(count)`` followed by ``get_signo`` (echo) or the
    ``suma``/``resta`` sign text (jog target). ``sign`` is the IN sign byte
    (128 -> ``00 00``, 127 -> ``FF FF``), not the legacy text.
    """
    _require_int("count", count, 0, MAX_COUNT)
    return count.to_bytes(2, "little") + _SIGN_WORDS[_require_sign(sign)]


def encode_region(joints: Mapping[str, tuple[int, int]]) -> bytes:
    """The 24-byte encoder region (offsets 12-35), joints in ``scorbot.state.JOINTS`` order.

    ``joints`` maps every joint name to ``(count, sign)``. Legacy
    ``libdef.get_encoder``, with ``getStruct`` replacing the moving joints'
    fields: here the caller simply passes the target for a moving joint.
    """
    if not isinstance(joints, Mapping):
        raise ValueError(f"joints must be a mapping of joint name to (count, sign), got {joints!r}")
    unknown = sorted(set(joints) - set(JOINTS), key=str)
    missing = [name for name in JOINTS if name not in joints]
    if unknown or missing:
        raise ValueError(f"joints must name exactly {JOINTS}; unknown {unknown}, missing {missing}")
    fields = []
    for name in JOINTS:
        state = joints[name]
        if not isinstance(state, tuple) or len(state) != 2:
            raise ValueError(f"{name} must be a (count, sign) tuple, got {state!r}")
        try:
            fields.append(encode_count(*state))
        except ValueError as exc:
            raise ValueError(f"{name}: {exc}") from None
    return b"".join(fields)


def step_count(count: int, sign: int, step: int) -> tuple[int, int]:
    """Move one joint target by ``step`` counts with the legacy wrap.

    Positive ``step`` is ``libdef.suma``: add; above 65535 subtract 65535 and
    the sign becomes 128. Negative ``step`` is ``libdef.resta`` by ``-step``:
    below 0 add 65535 and the sign becomes 127. Otherwise the sign is kept.
    Zero returns the input. Returns ``(count, sign)``.

    ``|step|`` must be at most 65535. Larger steps raise ``ValueError``; the
    legacy code would wrap only once and emit a count above 65535 (a five-digit
    ``detrans`` field that shifts the packet).
    """
    _require_int("count", count, 0, MAX_COUNT)
    _require_sign(sign)
    _require_int("step", step, -COUNT_MODULUS, COUNT_MODULUS)
    count += step
    if count > MAX_COUNT:
        return count - COUNT_MODULUS, SIGN_POSITIVE
    if count < 0:
        return count + COUNT_MODULUS, SIGN_NEGATIVE
    return count, sign


def _require_bytes(name, value, length=None):
    if not isinstance(value, (bytes, bytearray)):
        raise ValueError(f"{name} must be bytes, got {value!r}")
    if length is not None and len(value) != length:
        raise ValueError(f"{name} must be {length} bytes, got {len(value)}")
    return bytes(value)


def build_out(sequence: int, command: bytes = b"",
              joints: Mapping[str, tuple[int, int]] | None = None, *,
              header: bytes = HEADER) -> bytes:
    """A full 64-byte OUT packet.

    Byte 0 is ``sequence`` (1..255; legacy ``f_byte``), bytes 1-3 ``header``,
    ``command`` from offset 4, then, when ``joints`` is given, the encoder
    region from ``encode_region`` at offsets 12-35; the rest is zero (legacy
    ``fill_msg``). Packets the legacy code sends without an encoder region
    (handshake pkt1, pkt2, ``send_wait``, the first 40 of pkt3) leave
    ``joints`` as ``None``.

    The spec puts the header fixed at zero; ``header`` exists because the
    legacy ``get_msg2(84)`` sets byte 2 to 0x0C (``PKT2_END_HEADER``).
    """
    _require_int("sequence", sequence, 1, MAX_SEQUENCE)
    header = _require_bytes("header", header, len(HEADER))
    command = _require_bytes("command", command)
    end = COMMAND_OFFSET + len(command)
    limit = REGION_OFFSET if joints is not None else MSG_LEN
    if end > limit:
        where = "offset 12 (encoder region)" if joints is not None else f"offset {MSG_LEN}"
        raise ValueError(f"command of {len(command)} bytes runs past {where}")
    packet = bytearray(MSG_LEN)
    packet[0] = sequence
    packet[1:COMMAND_OFFSET] = header
    packet[COMMAND_OFFSET:end] = command
    if joints is not None:
        packet[REGION_OFFSET:REGION_OFFSET + REGION_LEN] = encode_region(joints)
    return bytes(packet)


def pkt2_mode_command(mode: int) -> bytes:
    """``get_msg2(80)`` = ``'{seq}000000{mode}'``: one mode byte at offset 4.

    Only the values the legacy ``send_pkt2`` sends are accepted (``PKT2_MODES``).
    """
    if type(mode) is not int or mode not in PKT2_MODES:
        raise ValueError(f"mode must be one of {[hex(m) for m in PKT2_MODES]}, got {mode!r}")
    return bytes((mode,))


def pkt2_table_address(index: int) -> tuple[int, int]:
    """The ``(b6, b7)`` bytes ``libsync.send_pkt2`` sends with table entry ``index``.

    Mirrors ``countByte7`` (cycles 1..7, 9, 10, 0, skipping 8) and
    ``countByte6`` (starts at 1, doubles each time b7 returns to 0) as a closed
    form of the legacy counters for ``index`` 1..79.
    """
    _require_int("index", index, 1, len(GET_MSG2))
    cycle = (1, 2, 3, 4, 5, 6, 7, 9, 10, 0)
    b7 = cycle[(index - 1) % len(cycle)]
    b6 = 1 << (index // len(cycle))
    return b6, b7


def pkt2_table_command(index: int) -> bytes:
    """``get_msg2(83)`` + ``get_msg2(index)``: ``53 b6 b7 00`` then 3 table bytes."""
    b6, b7 = pkt2_table_address(index)
    return bytes((0x53, b6, b7, 0x00)) + GET_MSG2[index]
