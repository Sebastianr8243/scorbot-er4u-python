"""A live 3D picture of the arm in Rerun. UNVALIDATED geometry; simulator only.

    python -m scorbot.arm_view --simulate            # a simulated stream, live
    python -m scorbot.arm_view --pose 0 120 -95 -90 0   # one pose (degrees)

What is drawn is a model, not a measurement: the link sizes are nominal, and
motor counts become joint angles through the vendor's formula with the
vendor's default parameters (``vendor_model.py``), which nobody has checked on
our arm. A joint may be drawn bending the wrong way. The view says so on
screen. It reads state only and commands nothing.

Link meshes are optional. They are a community CAD model of the ER-4U whose
licence is unclear, so they are not in this repository: put the six STL files
in ``models/er4u_meshes/`` (see the README there) or point ``SCORBOT_MESH_DIR``
at them. Without them the arm is drawn as a line through its joints.

The Rerun viewer is the optional ``viz`` extra and is imported only when a
view is opened; ``scene`` and the helpers above it are pure.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import math
import os
from pathlib import Path
import sys
import time

import numpy as np

from . import arm_chain, source_model, vendor_model

MESH_DIR_ENV = "SCORBOT_MESH_DIR"
DEFAULT_MESH_DIR = Path(__file__).resolve().parents[1] / "models" / "er4u_meshes"
APPLICATION_ID = "scorbot_arm_view"
NOTICE = ("UNVALIDATED GEOMETRY. Nominal link sizes; counts become angles through the "
          "vendor's default formula, not a calibration of this arm. A joint may be drawn "
          "bending the wrong way. Not evidence about the real arm.")


@dataclass(frozen=True)
class LinkMesh:
    """One link's mesh file and where the mesh sits in that link's frame.

    The files were exported with their own origins and axes. These fixed
    offsets (metres, and URDF roll-pitch-yaw in radians) are the ones
    github.com/talos-rit/scorbot_ros2 derived for the same files.
    """

    filename: str
    xyz_m: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rpy: tuple[float, float, float] = (0.0, 0.0, 0.0)


_QUARTER = math.pi / 2
LINK_MESHES = {
    "base_link": LinkMesh("base_Link.STL", (-0.13902, -0.14222, -0.07809),
                          (-0.06299, 0.00400, 1.63421)),
    "turret_link": LinkMesh("shoulder_Link.STL", rpy=(0.0, 0.0, _QUARTER)),
    "upper_arm_link": LinkMesh("elbow_Link.STL", rpy=(0.0, 0.0, _QUARTER)),
    "forearm_link": LinkMesh("pitch_Link.STL", rpy=(0.0, 0.0, _QUARTER)),
    "wrist_link": LinkMesh("roll_Link.STL", rpy=(_QUARTER, -_QUARTER, -_QUARTER)),
    "flange_link": LinkMesh("gripper_Link.STL", rpy=(_QUARTER, -_QUARTER, -_QUARTER)),
}


def visual_offset(link: str) -> np.ndarray:
    """4x4 pose (metres) of a link's mesh in that link's frame."""
    mesh = LINK_MESHES[link]
    roll, pitch, yaw = mesh.rpy
    cr, sr, cp, sp, cy, sy = (math.cos(roll), math.sin(roll), math.cos(pitch),
                              math.sin(pitch), math.cos(yaw), math.sin(yaw))
    pose = np.eye(4)
    # URDF fixed-axis roll, pitch, yaw: Rz(yaw) Ry(pitch) Rx(roll).
    pose[:3, :3] = [[cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
                    [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
                    [-sp, cp * sr, cp * cr]]
    pose[:3, 3] = mesh.xyz_m
    return pose


def find_meshes(directory=None) -> dict[str, Path]:
    """The link meshes present in ``directory`` (default: env, then models/er4u_meshes).

    File names are matched without regard to case. A link with no file is
    simply absent from the result; the view draws it as a line.
    """
    folder = Path(directory or os.environ.get(MESH_DIR_ENV) or DEFAULT_MESH_DIR)
    if not folder.is_dir():
        return {}
    present = {path.name.lower(): path for path in folder.iterdir() if path.is_file()}
    return {link: present[mesh.filename.lower()] for link, mesh in LINK_MESHES.items()
            if mesh.filename.lower() in present}


def chain_angles_from_counts(counts) -> tuple[float, float, float, float, float]:
    """Motor counts to ``arm_chain`` joint angles in degrees. A PRIOR, not calibration.

    ``counts`` maps the SDK's motor names to signed counts. It uses the
    vendor's formula and default parameters, in which all counts zero is the
    vendor's nominal home pose, and the direction convention of the USNA
    toolboxes (positive shoulder, elbow and pitch lift). Whether our arm's
    home is that pose, and whether each sign is right, is unverified.
    """
    degrees = source_model.angles_from_counts(counts)
    return (degrees["base"], degrees["shoulder"], degrees["elbow"], degrees["pitch"],
            degrees["roll"])


@dataclass(frozen=True)
class Scene:
    """Everything to draw for one pose, in metres."""

    poses: dict[str, np.ndarray]
    skeleton: list[tuple[float, float, float]]


def scene(q, geometry: arm_chain.ChainGeometry = arm_chain.MESH_MODEL) -> Scene:
    """Link poses and the joint line for ``arm_chain`` angles ``q`` (degrees)."""
    poses = {}
    for name, pose in arm_chain.link_poses(q, geometry).items():
        metres = pose.copy()
        metres[:3, 3] /= 1000.0
        poses[name] = metres
    line = [(x / 1000.0, y / 1000.0, z / 1000.0) for x, y, z in arm_chain.skeleton(q, geometry)]
    return Scene(poses, line)


class RerunUnavailable(RuntimeError):
    """rerun-sdk is not installed."""


def require_rerun():
    try:
        import rerun
    except ImportError as exc:
        raise RerunUnavailable(
            'The 3D view needs the viz extra: pip install -e ".[viz]"') from exc
    return rerun


class ArmView:
    """Draws poses into one Rerun recording. The only Rerun call site here."""

    ARM_COLOR = (70, 130, 220)
    TARGET_COLOR = (240, 160, 40)

    def __init__(self, *, mesh_dir=None, save_path=None, simulated: bool = True,
                 geometry: arm_chain.ChainGeometry = arm_chain.MESH_MODEL,
                 recording=None, rerun=None):
        self._rr = rerun or require_rerun()
        self.geometry = geometry
        self.meshes = find_meshes(mesh_dir)
        if recording is None:
            recording = self._rr.RecordingStream(
                APPLICATION_ID, recording_id=f"arm-{time.time_ns():x}")
            if save_path is not None:
                recording.save(str(save_path))
            else:
                recording.spawn()
        self.recording = recording
        rr = self._rr
        self.recording.log("/", rr.ViewCoordinates.RIGHT_HAND_Z_UP, static=True)
        notice = ("# SIMULATED\n\n" if simulated else "# ") + NOTICE
        if not self.meshes:
            notice += ("\n\nNo link meshes found (models/er4u_meshes or "
                       f"{MESH_DIR_ENV}); the arm is drawn as a line.")
        self.recording.log("notice", rr.TextDocument(notice, media_type=rr.MediaType.MARKDOWN),
                           static=True)
        for link, path in self.meshes.items():
            offset = visual_offset(link)
            self.recording.log(f"arm/{link}/mesh", rr.Transform3D(
                translation=offset[:3, 3].tolist(), mat3x3=offset[:3, :3].tolist()), static=True)
            self.recording.log(f"arm/{link}/mesh", rr.Asset3D(
                contents=path.read_bytes(), media_type=rr.MediaType.STL), static=True)

    def show(self, q, *, time_s: float | None = None, target_q=None) -> None:
        """Draw the arm at ``q`` and, if given, a line for where it is told to go."""
        rr = self._rr
        if time_s is not None:
            self.recording.set_time("time", duration=float(time_s))
        drawn = scene(q, self.geometry)
        for link in arm_chain.LINKS:
            pose = drawn.poses[link]
            self.recording.log(f"arm/{link}", rr.Transform3D(
                translation=pose[:3, 3].tolist(), mat3x3=pose[:3, :3].tolist()))
        self.recording.log("skeleton", rr.LineStrips3D(
            [drawn.skeleton], colors=[self.ARM_COLOR], radii=[0.004]))
        if target_q is not None:
            self.recording.log("target", rr.LineStrips3D(
                [scene(target_q, self.geometry).skeleton], colors=[self.TARGET_COLOR],
                radii=[0.002]))
        for name, value in zip(arm_chain.JOINTS, q):
            self.recording.log(f"angles/{name}", rr.Scalars(float(value)))

    def close(self) -> None:
        self.recording.flush()


# -- command line -----------------------------------------------------------

DEMO_TARGETS = (                      # motor counts from home, well inside the joint limits
    {"base": 1200, "shoulder": 0, "elbow": 0},
    {"base": 1200, "shoulder": -900, "elbow": 700},
    {"base": -1200, "shoulder": -900, "elbow": -700},
    {"base": -1200, "shoulder": 300, "elbow": 0},     # the shoulder has little room above home
    {"base": 0, "shoulder": 0, "elbow": 0},
)


def run_simulated_demo(view: ArmView, seconds: float, *, period_s: float = 0.05) -> int:
    """Stream a simulated arm through a few targets and draw it. Returns frames drawn."""
    from . import SimulatedScorbot

    frames = 0
    robot = SimulatedScorbot().connect()
    try:
        robot.enable()
        robot.home(start_position_confirmed=True)
        zero = {name: 0 for name in vendor_model.MOTORS}
        started = time.monotonic()
        with robot.start_stream(speed_fraction=0.25) as stream:
            while (elapsed := time.monotonic() - started) < seconds:
                target = DEMO_TARGETS[int(elapsed / seconds * len(DEMO_TARGETS))
                                      % len(DEMO_TARGETS)]
                stream.set_target(target)
                counts = robot.get_state().signed_encoder_counts
                view.show(chain_angles_from_counts(counts), time_s=elapsed,
                          target_q=chain_angles_from_counts({**zero, **target}))
                frames += 1
                time.sleep(period_s)
    finally:
        robot.disconnect()
        view.close()
    return frames


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    what = parser.add_mutually_exclusive_group(required=True)
    what.add_argument("--simulate", action="store_true",
                      help="Draw a simulated arm following a stream (no USB, no robot)")
    what.add_argument("--pose", type=float, nargs=5,
                      metavar=("BASE", "SHOULDER", "ELBOW", "PITCH", "ROLL"),
                      help="Draw one pose, in degrees (0 0 0 0 0 is stretched out level)")
    parser.add_argument("--seconds", type=float, default=20.0,
                        help="How long the simulated demo runs (1 to 600)")
    parser.add_argument("--save", type=Path, default=None,
                        help="Write a .rrd recording instead of opening the viewer")
    parser.add_argument("--mesh-dir", type=Path, default=None,
                        help=f"Folder with the link STL files (default: {MESH_DIR_ENV}, "
                             "then models/er4u_meshes)")
    args = parser.parse_args(argv)
    if not math.isfinite(args.seconds) or not 1 <= args.seconds <= 600:
        parser.error("--seconds must be 1 through 600")
    if args.save is not None and args.save.exists():
        parser.error("--save file already exists; choose a new name")
    try:
        view = ArmView(mesh_dir=args.mesh_dir, save_path=args.save, simulated=args.simulate)
    except RerunUnavailable as exc:
        print(exc, file=sys.stderr)
        return 2
    print(("SIMULATED. " if args.simulate else "") + NOTICE)
    print(f"Link meshes: {len(view.meshes)} of {len(LINK_MESHES)} found"
          + ("" if view.meshes else " (drawing a line instead)"))
    if args.pose is not None:
        view.show(args.pose)
        view.close()
        return 0
    frames = run_simulated_demo(view, args.seconds)
    print(f"Drew {frames} frames.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
