"""Measured controller data; angles are intentionally not inferred from counts."""

from dataclasses import dataclass, field
from datetime import datetime, timezone


JOINTS = ("base", "shoulder", "elbow", "wrist_motor_1", "wrist_motor_2", "gripper")
ENCODER_OFFSETS = (19, 24, 29, 34, 39, 44)
ERROR_OFFSETS = (22, 27, 32, 37, 42, 47)
# The last field decoded is the gripper error pair at bytes 47-48.
PACKET_MIN_LENGTH = ERROR_OFFSETS[-1] + 2
# Legacy home-switch bits in packet byte 5 (libdef.get_switch). A pressed switch sets its
# bit and this order matches the arm (ULPGC thesis pp. 55-56, docs/protocol/ULPGC_THESIS_FINDINGS.md).
HOME_SWITCH_BITS = {"base": 1, "shoulder": 2, "elbow": 4, "wrist_pitch": 8, "wrist_roll": 16}
# The controller's own collision flag: IN byte 59, one bit per motor (0x01 motor 1, 0x18 motors
# 4 and 5, 0x20 gripper; bits 6 and 7 are the external motors 7 and 8). It is nonzero when an
# axis hits its mechanical limit; ULPGC thesis p. 57. The legacy code never reads it, and this
# SDK only records it for now: nothing latches a fault on it until it has been seen on the arm.
COLLISION_OFFSET = 59
# The controller's inputs: byte 6 is the digital input word, bytes 7-10 are four analog inputs, one byte
# each. The vendor DLL's GetDigitalInputs and GetAnalogInput return these, with no command sent
# (docs/protocol/LEDGER.md PL65, PL69). What the lines read on the arm is unverified.
DIGITAL_INPUT_OFFSET = 6
ANALOG_INPUT_OFFSETS = (7, 8, 9, 10)
COLLISION_MOTORS = JOINTS + ("motor7", "motor8")
# Byte 3 is the teach pendant's byte. The vendor DLL keeps a teach-mode flag (``IsTeachMode``) that its pendant
# handler (0x1002b56c) updates from every reply's byte 3 (docs/protocol/LEDGER.md PL64): these codes set it on
# or off, 0 leaves it, and any other code (a key) sets it on unless the emergency bit (byte 2 bit 0) is set.
# Checked against the lab DLL on the pipe fake (tests/test_teach_mode.py); unverified on the arm.
PENDANT_OFFSET = 3
TEACH_ON_CODES = (0x40, 0x4F, 0xC0, 0xF9)
TEACH_OFF_CODES = (0x47, 0x7E, 0xF8, 0xFE)


def collision_motors(packet: bytes) -> list[str]:
    """The motors whose bit is set in the collision flag; empty for a packet too short to hold it."""
    if len(packet) <= COLLISION_OFFSET:
        return []
    flag = packet[COLLISION_OFFSET]
    return [name for bit, name in enumerate(COLLISION_MOTORS) if flag & (1 << bit)]


def teach_mode_after(teach: bool, packet: bytes) -> bool:
    """The DLL's teach-mode flag after one more reply; a reply too short to hold byte 3 keeps it."""
    if len(packet) <= PENDANT_OFFSET or packet[PENDANT_OFFSET] == 0:
        return teach
    code = packet[PENDANT_OFFSET]
    if code in TEACH_ON_CODES:
        return True
    if code in TEACH_OFF_CODES:
        return False
    return teach or not packet[2] & 1


def switch_summary(bits: int) -> str:
    """One line for the operator: which home switches read set, before any homing.

    A set bit is read as pressed (``HOME_SWITCH_BITS``; polarity from the vendor DLL,
    unverified on the arm). Bits this SDK does not know are flagged.
    """
    parts = [f"{name.replace('wrist_', 'wrist ')} {'ON' if bits & bit else 'off'}"
             for name, bit in HOME_SWITCH_BITS.items()]
    line = ("Home switches now (a set bit is read as pressed; polarity unverified): "
            + ", ".join(parts))
    unknown = bits & ~sum(HOME_SWITCH_BITS.values())
    if unknown:
        line += f". UNEXPECTED BITS {unknown:#x}: do not home."
    return line


@dataclass(frozen=True)
class RobotState:
    """One decoded controller response.

    ``signed_encoder_counts`` applies the sign byte for display and planning; it
    jumps by 65536 at the 0/65535 seam. Take differences between samples from
    ``encoder_counts`` with ``calibration.signed_count_delta``.
    """

    timestamp_utc: str
    encoder_counts: dict[str, int]
    controller_error_counts: dict[str, int]
    home_switch_bits: int
    connected: bool
    enabled: bool | None
    homed: bool
    fault: str | None
    packet_index: int | None = None
    host_monotonic_ns: int | None = None
    encoder_sign_bytes: dict[str, int] | None = None
    signed_encoder_counts: dict[str, int] | None = None
    simulated: bool = False
    collision_bits: int = 0       # IN byte 59, see ``collision_motors``
    digital_inputs: int = 0       # IN byte 6, bit N = input N
    analog_inputs: list[int] = field(default_factory=lambda: [0, 0, 0, 0])   # IN bytes 7-10
    teach_mode: bool = False      # the pendant is in teach mode (IN byte 3 over time, ``teach_mode_after``)


def decode_state(packet: bytes, *, connected: bool, enabled: bool | None,
                 homed: bool, fault: str | None, packet_index: int | None = None,
                 host_monotonic_ns: int | None = None, teach_mode: bool = False) -> RobotState:
    if len(packet) < PACKET_MIN_LENGTH:
        raise ValueError(f"Controller packet is too short: {len(packet)} bytes")
    counts = {
        name: int.from_bytes(packet[offset:offset + 2], "little")
        for name, offset in zip(JOINTS, ENCODER_OFFSETS)
    }
    signs = {name: packet[offset + 2] for name, offset in zip(JOINTS, ENCODER_OFFSETS)}
    for name, sign in signs.items():
        if sign not in (127, 128):
            raise ValueError(f"Invalid encoder sign byte for {name}: {sign}")
    signed = {
        name: counts[name] if signs[name] == 128 else counts[name] - 65535
        for name in JOINTS
    }
    errors = {
        name: int.from_bytes(packet[offset:offset + 2], "little")
        for name, offset in zip(JOINTS, ERROR_OFFSETS)
    }
    return RobotState(
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
        encoder_counts=counts,
        controller_error_counts=errors,
        home_switch_bits=packet[5],
        connected=connected,
        enabled=enabled,
        homed=homed,
        fault=fault,
        packet_index=packet_index,
        host_monotonic_ns=host_monotonic_ns,
        encoder_sign_bytes=signs,
        signed_encoder_counts=signed,
        collision_bits=packet[COLLISION_OFFSET] if len(packet) > COLLISION_OFFSET else 0,
        digital_inputs=packet[DIGITAL_INPUT_OFFSET],
        analog_inputs=[packet[offset] for offset in ANALOG_INPUT_OFFSETS],
        teach_mode=teach_mode,
    )
