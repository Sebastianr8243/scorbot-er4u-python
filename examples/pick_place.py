"""Pick a block at A, place it at B, and back again, through the arm server; record each run.

Needs tools/usbc_server running and four taught points (drive the arm there with `python -m scorbot.bridge by ...`,
then `python -m scorbot.bridge teach NAME`): above_a, at_a, above_b, at_b. Odd runs carry A to B, even runs B to A,
so the block ends where it started. After each run type y/n for placed and the placement error read off the mat.

    python examples/pick_place.py --runs 10

Writes logs/pick-place-<time>.jsonl (one row per run) and prints the success rate and mean error. This scripted
pick is the baseline any later camera or VLA work has to beat.
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from scorbot.bridge import Arm, ArmError

POINTS = ("above_a", "at_a", "above_b", "at_b")


def carry(arm, source, target):
    """One pick at `source` and place at `target` ("a" or "b"), from above to above."""
    arm.go(f"above_{source}")
    arm.gripper(open=True)
    arm.go(f"at_{source}")
    arm.gripper(open=False)
    arm.go(f"above_{source}")
    arm.go(f"above_{target}")
    arm.go(f"at_{target}")
    arm.gripper(open=True)
    arm.go(f"above_{target}")


def summary(rows):
    placed = [row for row in rows if row["placed"]]
    errors = [row["error_mm"] for row in placed if row["error_mm"] is not None]
    return {"runs": len(rows), "placed": len(placed),
            "success_rate": len(placed) / len(rows) if rows else 0.0,
            "mean_error_mm": sum(errors) / len(errors) if errors else None}


def run(arm, runs, log, read=input):
    rows = []
    for index in range(runs):
        source, target = ("a", "b") if index % 2 == 0 else ("b", "a")
        started = time.monotonic()
        try:
            carry(arm, source, target)
            failure = None
        except ArmError as error:
            failure = str(error)
        seconds = time.monotonic() - started
        placed = failure is None and read(f"run {index + 1}: placed? (y/n): ").strip().lower().startswith("y")
        answer = read("placement error on the mat (mm, Enter to skip): ").strip() if placed else ""
        try:
            error_mm = float(answer) if answer else None
        except ValueError:
            error_mm = None
        row = {"run": index + 1, "from": source, "to": target, "placed": placed, "error_mm": error_mm,
               "seconds": round(seconds, 2), "failure": failure}
        log(row)
        rows.append(row)
        if failure:
            print(f"run {index + 1} stopped: {failure}")
            break
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description="Scripted pick-and-place between two taught points")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--runs", type=int, default=10)
    args = parser.parse_args(argv)
    path = Path("logs") / f"pick-place-{datetime.now():%Y%m%d-%H%M%S}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Arm(port=args.port) as arm, open(path, "x", encoding="utf-8") as handle:
            def log(row):
                handle.write(json.dumps(row, allow_nan=False) + "\n")
                handle.flush()
            rows = run(arm, args.runs, log)
    except ArmError as error:
        print(f"stopped: {error}")
        return 1
    result = summary(rows)
    print(f"{result['placed']}/{result['runs']} placed, mean error {result['mean_error_mm']} mm   log: {path}")
    return 0 if rows and result["placed"] == result["runs"] else 1


if __name__ == "__main__":
    sys.exit(main())
