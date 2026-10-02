"""python -m scorbot.camera check: open a camera, show its settings and timing.

Opens no USB to the arm. With --record DIR, writes a camera-only session so the
stream file contract is exercised on the lab PC.
"""

from __future__ import annotations

import argparse
import sys
import time

from ..session import SessionWriter
from .recorder import CameraRecorder
from .source import FakeSource, OpenCVSource
from .stream import CameraStream, scan_stream


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m scorbot.camera")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="open a camera and report settings and timing")
    check.add_argument("--index", type=int, default=0)
    check.add_argument("--fake", action="store_true", help="synthetic frames, no camera")
    check.add_argument("--seconds", type=float, default=10.0)
    check.add_argument("--width", type=int, default=640)
    check.add_argument("--height", type=int, default=480)
    check.add_argument("--fps", type=float, default=30.0)
    check.add_argument("--record", metavar="DIR", help="write a camera-only session here")
    return parser


def _print_settings(settings: dict) -> None:
    print(f"backend: {settings.get('backend')}")
    requested = settings.get("requested", {})
    set_ok = settings.get("set_ok", {})
    read_back = settings.get("read_back", {})
    print(f"{'property':<14} {'requested':>10} {'set ok':>7} {'raw read-back':>14}")
    for key in sorted(set(requested) | set(read_back)):
        print(f"{key:<14} {str(requested.get(key, '-')):>10} "
              f"{str(set_ok.get(key, '-')):>7} {str(read_back.get(key, '-')):>14}")
    actual = settings.get("actual", {})
    print(f"actual frames: {actual.get('width')}x{actual.get('height')}, "
          f"measured fps {actual.get('measured_fps')}")
    print("Raw read-back values are backend flags, not proof a setting took effect.")


def _check(args) -> int:
    resolution = time.get_clock_info("monotonic").resolution
    print(f"clock: monotonic resolution {resolution * 1e3:.4f} ms")
    if resolution > 1e-3:
        print("WARNING: clock coarser than 1 ms; frame times will be coarse "
              "(use Python 3.13 on Windows).")
    source = (FakeSource(width=args.width, height=args.height, fps=args.fps, pace=True)
              if args.fake else OpenCVSource(args.index, width=args.width,
                                             height=args.height, fps=args.fps))
    source.open()
    settings = source.settings()
    _print_settings(settings)
    session = stream = None
    if args.record:
        session = SessionWriter.create(args.record,
                                       data_source="simulated" if args.fake else "real",
                                       robot_id="camera-check", task="camera check",
                                       camera_ids=["check"])
        stream = CameraStream.create(session, "check", settings)
    recorder = CameraRecorder(source, stream, fps=args.fps)
    recorder.start()
    deadline = time.monotonic() + args.seconds
    while time.monotonic() < deadline and not recorder.failure:
        time.sleep(0.2)
        for row in recorder.drain_health():
            print(f"health: {row['status']:<9} fps {row['measured_fps']} "
                  f"max gap {row['max_gap_ms']} ms dropped {row['dropped']}")
    status = recorder.stop()
    print(f"recorder: {status}; counts {recorder.counts}")
    if session is not None:
        session.close()
        index = scan_stream(session.path, "check")
        print(f"recorded {len(index.frames)} frames to {session.path}; "
              f"{len(index.errors)} errors, {len(index.warnings)} warnings")
        for finding in index.findings:
            print(f"  {finding.level}: {finding.message}")
        if index.errors:
            return 1
    return 1 if status.startswith("failed") else 0


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "check":
        return _check(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
