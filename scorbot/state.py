"""Measured controller data; angles are intentionally not inferred from counts."""

from dataclasses import dataclass
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
COLLISION_MOTORS = JOINTS + ("motor7", "motor8")


def collision_motors(packet: bytes) -> list[str]:
    """The motors whose bit is set in the collision flag; empty for a packet too short to hold it."""
    if len(packet) <= COLLISION_OFFSET:
        return []
    flag = packet[COLLISION_OFFSET]
    return [name for bit, name in enumerate(COLLISION_MOTORS) if flag & (1 << bit)]


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


def decode_state(packet: bytes, *, connected: bool, enabled: bool | None,
                 homed: bool, fault: str | None, packet_index: int | None = None,
                 host_monotonic_ns: int | None = None) -> RobotState:
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
    )
