"""Offline kinematics check: nominal DH model versus legacy libdef.cIn.

Never opens USB and never moves the arm. The geometry is UNVALIDATED.

    python examples/kinematics_check.py --joints 0 90 -90 0 0
    python examples/kinematics_check.py --xyz 300 0 200 --pitch -90
"""

import argparse
import importlib.util

import numpy as np

from scorbot import Scorbot
from scorbot import kinematics as kin


BANNER = "=== OFFLINE, UNVALIDATED GEOMETRY: nominal legacy conf.py values, not measured ==="


def _fmt(values):
    return "[" + ", ".join(f"{float(v):9.3f}" for v in values) + "]"


def _legacy_cin():
    if importlib.util.find_spec("usb") is None:
        return None  # legacy libdef imports PyUSB at module level
    return Scorbot()._legacy("libdef").cIn


def show_joints(q, cin):
    pose = kin.forward(q)
    pitch, roll = kin.tool_pitch_roll(q)
    wrist = kin.wrist_center(q)
    print(f"FK joints (deg)        {_fmt(q)}")
    print(f"   tool xyz (mm)       {_fmt(pose[:3, 3])}")
    print(f"   tool pitch/roll     {_fmt((pitch, roll))}")
    print(f"   wrist center (mm)   {_fmt(wrist)}")
    print("   pose:")
    for row in pose:
        print("     " + _fmt(row))
    if cin is not None:
        legacy = cin(*wrist)
        print("   legacy cIn(wrist center) vs model joints 1-3:")
        print(f"     cIn   {_fmt(legacy)}")
        print(f"     model {_fmt(q[:3])}")
        print(f"     diff  {_fmt(np.asarray(legacy, dtype=float) - q[:3])}")


def show_xyz(x, y, z, pitch, roll, cin):
    print(f"IK target xyz (mm) {_fmt((x, y, z))}  pitch {pitch:g}  roll {roll:g}")
    for elbow in ("up", "down"):
        try:
            q = kin.inverse(x, y, z, pitch, roll, elbow=elbow)
        except ValueError as exc:
            print(f"   elbow {elbow:4}: unreachable ({exc})")
            continue
        print(f"   elbow {elbow:4}: joints {_fmt(q)}  wrist center {_fmt(kin.wrist_center(q))}")
    if cin is not None:
        # moveXYZ passes the typed xyz straight to cIn, which treats it as the
        # wrist-pitch-axis point (no tool length, no pitch, no a1 offset).
        print(f"   legacy cIn(xyz) as moveXYZ uses it: {_fmt(cin(x, y, z))}"
              "  ([-1,-1,-1] means unreachable)")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--joints", type=float, nargs=5,
                        metavar=("BASE", "SHOULDER", "ELBOW", "PITCH", "ROLL"),
                        help="joint angles in degrees (default: assumed HOME angRef)")
    parser.add_argument("--xyz", type=float, nargs=3, metavar=("X", "Y", "Z"),
                        help="tool position in mm for inverse kinematics")
    parser.add_argument("--pitch", type=float, default=-90.0,
                        help="tool pitch in degrees above horizontal (default -90, down)")
    parser.add_argument("--roll", type=float, default=0.0, help="tool roll in degrees")
    args = parser.parse_args(argv)

    print(BANNER)
    cin = _legacy_cin()
    if cin is None:
        print("(PyUSB not installed: legacy cIn comparison skipped)")
    if args.joints is not None or args.xyz is None:
        show_joints(np.array(args.joints or [0, 90, -90, 0, 0], dtype=float), cin)
    if args.xyz is not None:
        show_xyz(*args.xyz, args.pitch, args.roll, cin)
    print(BANNER)


if __name__ == "__main__":
    main()
