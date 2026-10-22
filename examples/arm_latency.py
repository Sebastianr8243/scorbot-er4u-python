"""Time the arm calls through the server: how many actions per second can a policy get?

Needs tools/usbc_server running and homed. Moves the arm straight up and back down along z, so give it
`max(--sizes)` mm of free space above where it stands, and keep a hand near the red stop. It asks you to
type "go" first. Run it once per speed (20, 50, 100) and compare.

    python examples/arm_latency.py --speed 20

Times where(), then pairs of move_by(+dz) / move_by(-dz) for each size, then the gripper. Writes one row per
call to logs/arm-latency-<time>.jsonl and prints the median and worst time per kind and the calls per second.
"""

import argparse
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

from scorbot.bridge import Arm, ArmError

SIZES_MM = (2, 5, 10, 20)


def timed(kind, call, clock=time.monotonic):
    started = clock()
    call()
    return {"kind": kind, "seconds": round(clock() - started, 4)}


def run(arm, log, speed=20, pairs=5, sizes=SIZES_MM, reads=20, clock=time.monotonic):
    """Every timed call, in order; each row goes to log as it finishes. Ends where it started."""
    rows = []

    def record(row):
        log(row)
        rows.append(row)

    arm.speed(speed)
    for _ in range(reads):
        record(timed("where", arm.where, clock))
    for size in sizes:
        for _ in range(pairs):
            record(timed(f"move_by_{size}mm", lambda size=size: arm.move_by(dz=size), clock))
            record(timed(f"move_by_{size}mm", lambda size=size: arm.move_by(dz=-size), clock))
    for _ in range(pairs):
        record(timed("gripper", lambda: arm.gripper(open=False), clock))
        record(timed("gripper", lambda: arm.gripper(open=True), clock))
    return rows


def summary(rows):
    """{kind: {n, median_s, worst_s, per_second}}; per_second is 1 / median."""
    result = {}
    for kind in dict.fromkeys(row["kind"] for row in rows):
        seconds = [row["seconds"] for row in rows if row["kind"] == kind]
        median = statistics.median(seconds)
        result[kind] = {"n": len(seconds), "median_s": median, "worst_s": max(seconds),
                        "per_second": round(1 / median, 2) if median > 0 else None}
    return result


def main(argv=None, read=input):
    parser = argparse.ArgumentParser(description="Time arm calls through the arm server")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--speed", type=int, default=20, help="percent of top speed, 1 to 100")
    parser.add_argument("--pairs", type=int, default=5, help="up-and-down pairs per step size")
    parser.add_argument("--sizes", type=int, nargs="+", default=list(SIZES_MM), help="step sizes in mm")
    args = parser.parse_args(argv)
    if read(f"The arm will move up and down by up to {max(args.sizes)} mm at speed {args.speed}. "
            "Space clear and a hand on the red stop? Type go: ").strip().lower() != "go":
        print("not started")
        return 1
    path = Path("logs") / f"arm-latency-{datetime.now():%Y%m%d-%H%M%S}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Arm(port=args.port) as arm, open(path, "x", encoding="utf-8") as handle:
            def log(row):
                handle.write(json.dumps({**row, "speed": args.speed}, allow_nan=False) + "\n")
                handle.flush()
            rows = run(arm, log, args.speed, args.pairs, tuple(args.sizes))
    except ArmError as error:
        print(f"stopped: {error}")
        return 1
    for kind, stats in summary(rows).items():
        print(f"{kind:>14}  n={stats['n']:<3} median {stats['median_s']:.3f} s  worst {stats['worst_s']:.3f} s  "
              f"{stats['per_second']} per second")
    print(f"log: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
