"""Measured controller data; angles are intentionally not inferred from counts."""

from dataclasses import dataclass
from datetime import datetime, timezone


JOINTS = ("base", "shoulder", "elbow", "wrist_motor_1", "wrist_motor_2", "gripper")
ENCODER_OFFSETS = (19, 24, 29, 34, 39, 44)
ERROR_OFFSETS = (22, 27, 32, 37, 42, 47)


@dataclass(frozen=True)
class RobotState:
    timestamp_utc: str
    encoder_counts: dict[str, int]
    controller_error_counts: dict[str, int]
    home_switch_bits: int
    connected: bool
    enabled: bool | None
    homed: bool
    fault: str | None


def decode_state(packet: bytes, *, connected: bool, enabled: bool | None,
                 homed: bool, fault: str | None) -> RobotState:
    if len(packet) < 49:
        raise ValueError(f"Controller packet is too short: {len(packet)} bytes")
    counts = {
        name: int.from_bytes(packet[offset:offset + 2], "little")
        for name, offset in zip(JOINTS, ENCODER_OFFSETS)
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
    )
