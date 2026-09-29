"""Measured controller data; angles are intentionally not inferred from counts."""

from dataclasses import dataclass
from datetime import datetime, timezone


JOINTS = ("base", "shoulder", "elbow", "wrist_motor_1", "wrist_motor_2", "gripper")
ENCODER_OFFSETS = (19, 24, 29, 34, 39, 44)
ERROR_OFFSETS = (22, 27, 32, 37, 42, 47)
# The last field decoded is the gripper error pair at bytes 47-48.
PACKET_MIN_LENGTH = ERROR_OFFSETS[-1] + 2
# Legacy home-switch bits in packet byte 5 (libdef.get_switch); polarity unverified.
HOME_SWITCH_BITS = {"base": 1, "shoulder": 2, "elbow": 4, "wrist_pitch": 8, "wrist_roll": 16}


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
    )
