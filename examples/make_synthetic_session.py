"""Write and replay a synthetic experiment session. No robot, camera, or USB needed.

The data is invented and marked data_source="synthetic". Use it to learn the
recording format, try the replay CLI, or open session.mcap in Foxglove or
Lichtblick.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import struct
import sys
import time
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scorbot.session.__main__ import main as replay_main  # noqa: E402
from scorbot.session.record import SessionWriter  # noqa: E402


def make_png(width: int, height: int, box: tuple[int, int, int, int]) -> bytes:
    """A grey image with a red rectangle, encoded as PNG with the standard library."""
    rows = []
    for y in range(height):
        row = bytearray([0])  # filter type 0
        for x in range(width):
            inside = box[0] <= x < box[2] and box[1] <= y < box[3]
            row += bytes((220, 40, 40) if inside else (90, 90, 90))
        rows.append(bytes(row))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b""))


def state(base: int) -> dict:
    return {"encoder_counts": {"base": base, "shoulder": 0, "elbow": 0,
                               "wrist_motor_1": 0, "wrist_motor_2": 0, "gripper": 0},
            "controller_error_counts": {}, "home_switch_bits": 0, "connected": True,
            "enabled": False, "homed": False, "fault": None,
            "host_monotonic_ns": time.monotonic_ns()}


def write_synthetic_session(root) -> Path:
    with SessionWriter.create(root, data_source="synthetic", robot_id="example-arm",
                              operator="example", task="synthetic walkthrough",
                              camera_ids=["cam0"]) as rec:
        rec.log_note("Synthetic example: invented values, no hardware involved")
        for _ in range(3):
            rec.log_state(state(1000))
            time.sleep(0.01)
        for frame, box in enumerate(((8, 8, 24, 20), (12, 8, 28, 20))):
            captured = time.monotonic_ns()
            rec.log_frame("cam0", frame, make_png(48, 32, box), format="png",
                          width=48, height=32, observed_monotonic_ns=captured,
                          camera_config_id="example-config", mount_id="example-mount")
            rec.log_detection("cam0", frame, "red_block", box, 0.9, "example-detector",
                              observed_monotonic_ns=captured)
        decision = rec.log_decision("jog_base_positive", reason="block right of centre")
        command_id = rec.log_command("jog_joint", {"joint": "base", "delta_counts": 50,
                                                   "decision_seq": decision})
        for base in (1010, 1030, 1050):
            rec.log_state(state(base))
            time.sleep(0.01)
        rec.log_command_result(command_id, "completed", completion_source="encoder_delta")
        rec.log_fault("Example fault: simulated stale-feedback warning")
        rec.log_note("End of synthetic example")
    return rec.path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("sessions"),
                        help="Folder to create the session in (default: sessions)")
    args = parser.parse_args()
    path = write_synthetic_session(args.root)
    print(f"Wrote {path}\n")
    return replay_main([str(path)])


if __name__ == "__main__":
    raise SystemExit(main())
