"""Render the MuJoCo twin's front and wrist cameras to PNG files. No USB, no arm.

    pip install mujoco pillow
    python examples/mujoco_twin_view.py --out twin_frames --pose 20 30 -40 -30 0

The pose is arm_chain degrees (base, shoulder, elbow, wrist_pitch, wrist_roll); the arm is
driven there and allowed to settle before the frames are taken. UNVALIDATED geometry.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import mujoco
from PIL import Image

from scorbot import mujoco_twin


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--pose", type=float, nargs=5, default=(20, 30, -40, -30, 0))
    args = ap.parse_args(argv)
    model, data = mujoco_twin.load()
    data.ctrl[:] = [math.radians(v) for v in args.pose]
    for _ in range(1500):
        mujoco.mj_step(model, data)
    args.out.mkdir(parents=True, exist_ok=True)
    with mujoco.Renderer(model, 480, 640) as renderer:
        for cam in mujoco_twin.CAMERAS:
            renderer.update_scene(data, camera=cam)
            path = args.out / f"{cam}.png"
            Image.fromarray(renderer.render()).save(path)
            print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
