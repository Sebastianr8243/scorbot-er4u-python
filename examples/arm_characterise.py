"""Measure the arm through the arm server before trusting its numbers: frame, scale, repeatability, a 3x3 grid.

Needs tools/usbc_server running (docs/lab/RUN_THE_ARM.md), a printed mm grid mat under the arm and a pointer in
the gripper. Starts wherever the arm is; keep about --grid-mm of free space around it. Every step uses move_by
(relative, at most 50 mm), so nothing here needs the box. At each prompt read the mat and type the number, or
press Enter to skip; the mat is the check that is neither the DLL nor this code.

    python examples/arm_characterise.py [--grid-mm 40] [--repeats 10] [--step-mm 20]

Writes calibration/arm_report-<time>.json (every reading with encoder, joint and xyz arrays) and grows
calibration/arm.json's box to the points reached, which is what lets move_to run.
"""

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path

from scorbot.bridge import BOX, XYZ_UNITS_PER_MM, Arm, ArmError, widen_box



def scale(before, after, axis, step_mm):
    """DLL xyz units per mm along one axis, and the largest off-axis drift in mm."""
    index = "xyz".index(axis)
    moved = [a - b for a, b in zip(after.xyz[:3], before.xyz[:3])]
    off = max(abs(value) for i, value in enumerate(moved) if i != index) / XYZ_UNITS_PER_MM
    return moved[index] / step_mm, off


def spread_mm(points):
    """Largest distance of any point from the mean point (the repeatability spread)."""
    centre = [sum(p[i] for p in points) / len(points) for i in range(3)]
    return max(math.dist(p, centre) for p in points)


def grid_offsets(size):
    return [(dx, dy) for dy in (-size, 0, size) for dx in (-size, 0, size)]


def ask(prompt, read=input):
    answer = read(f"{prompt} (mm, Enter to skip): ").strip()
    try:
        return float(answer) if answer else None
    except ValueError:
        return None


def summarise(report):
    """What the mat answers say. The DLL's own readings cannot show the scale (they are in its units); the mat can.
    suggested_units_per_mm is the DLL units that one real mm is worth, for bridge.XYZ_UNITS_PER_MM."""
    step = report["step_mm"]
    factor = {axis: f["mat_mm"] / step for axis, f in report["frame"].items() if f.get("mat_mm")}
    units = {axis: report["frame"][axis]["units_per_mm"] / factor[axis] for axis in factor}
    repeats = [abs(r["mat_mm"]) for r in report["repeatability"]["readings"] if r.get("mat_mm") is not None]
    grid = [abs(g["mat_error_mm"]) for g in report["grid"] if g.get("mat_error_mm") is not None]
    return {"mat_mm_per_commanded_mm": factor, "suggested_units_per_mm": units,
            "worst_repeat_mat_mm": max(repeats, default=None), "worst_grid_mat_mm": max(grid, default=None)}


def reading(position, **extra):
    return {"x": position.x, "y": position.y, "z": position.z, "encoder": position.encoder,
            "joint": position.joint, "xyz": position.xyz, **extra}


def characterise(arm, grid_mm=40.0, repeats=10, step_mm=20.0, read=input):
    report = {"when": datetime.now().isoformat(timespec="seconds"), "xyz_units_per_mm_assumed": XYZ_UNITS_PER_MM,
              "step_mm": step_mm}
    start = arm.where()
    report["start"] = reading(start)
    reached = [(start.x, start.y, start.z)]

    frame = {}
    for axis in "xyz":
        moved = arm.move_by(**{f"d{axis}": step_mm})
        units, off = scale(start, moved, axis, step_mm)
        mat = ask(f"{axis} +{step_mm:g} mm: how far did the tip move on the mat", read)
        frame[axis] = {"units_per_mm": units, "off_axis_mm": off, "mat_mm": mat, "after": reading(moved)}
        reached.append((moved.x, moved.y, moved.z))
        arm.move_by(**{f"d{axis}": -step_mm})
        print(f"{axis}: {units:.1f} DLL units per mm (assumed {XYZ_UNITS_PER_MM}), off-axis {off:.2f} mm, mat {mat}")
    report["frame"] = frame

    points, mats = [], []
    for i in range(repeats):
        side = 15.0 if i % 2 == 0 else -15.0      # come back from alternating sides
        arm.move_by(dx=side)
        back = arm.move_by(dx=-side)
        points.append((back.x, back.y, back.z))
        mats.append(reading(back, mat_mm=ask(f"repeat {i + 1}/{repeats}: offset from the first mark", read)))
    report["repeatability"] = {"spread_mm": spread_mm(points), "readings": mats}
    print(f"repeatability (DLL-reported): {report['repeatability']['spread_mm']:.3f} mm")

    grid = []
    for dx, dy in grid_offsets(grid_mm):
        if dx or dy:
            at = arm.move_by(dx=dx, dy=dy)
        else:
            at = arm.where()
        error = math.dist((at.x - start.x, at.y - start.y), (dx, dy))
        grid.append(reading(at, commanded_mm=[dx, dy], dll_error_mm=error,
                            mat_error_mm=ask(f"grid ({dx:+g}, {dy:+g}): mat error", read)))
        reached.append((at.x, at.y, at.z))
        if dx or dy:
            arm.move_by(dx=-dx, dy=-dy)
    report["grid"] = grid
    print(f"grid: worst DLL-reported error {max(g['dll_error_mm'] for g in grid):.2f} mm")
    return report, reached


def main(argv=None):
    parser = argparse.ArgumentParser(description="Measure the arm's frame, scale, repeatability and grid accuracy")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--calibration-dir", default="calibration", help="folder for arm.json and the report (one per arm)")
    parser.add_argument("--grid-mm", type=float, default=40.0)
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--step-mm", type=float, default=20.0)
    parser.add_argument("--summarise", metavar="REPORT", help="print what an earlier report's mat answers say, then exit")
    args = parser.parse_args(argv)
    if args.summarise:
        print(json.dumps(summarise(json.loads(Path(args.summarise).read_text(encoding="utf-8"))), indent=2))
        return 0
    if not 0 < args.grid_mm <= 50 or not 0 < args.step_mm <= 50:
        parser.error("--grid-mm and --step-mm must be above 0 and at most 50 (the server's step limit)")
    try:
        with Arm(port=args.port) as arm:
            report, reached = characterise(arm, args.grid_mm, args.repeats, args.step_mm)
    except ArmError as error:
        print(f"stopped: {error}")
        return 1
    box = widen_box(Path(args.calibration_dir) / BOX.name, reached, measured=True)
    path = Path(args.calibration_dir) / f"arm_report-{datetime.now():%Y%m%d-%H%M%S}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8") as handle:       # evidence is never overwritten
        json.dump(report, handle, indent=2, allow_nan=False)
    print(f"report: {path}   box now {box}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
