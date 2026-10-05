"""Where each link of the arm sits for given joint angles. UNVALIDATED, offline.

A picture of the arm needs one thing before any drawing: the position and
orientation of every link. This module is that chain and nothing else. It is
for viewers. It is **wired into no motion command**, and a pose it returns is
not evidence about the real arm: the dimensions are not measured, and how
motor counts become these joint angles on our arm is not calibrated.

Frames (x forward, y left, z up; millimetres, degrees):

    base_link --base (+z)--> turret_link --shoulder (-y)--> upper_arm_link
      --elbow (-y)--> forearm_link --wrist_pitch (-y)--> wrist_link
      --wrist_roll (+x)--> flange_link --(fixed)--> tool

* ``base_link`` is at the centre of the base's mounting face.
* At the zero pose the arm is stretched out level along +x and every link
  frame has the base's orientation, so each joint origin is a plain offset.
* Positive base is counter-clockwise seen from above. Positive shoulder,
  elbow and wrist pitch each lift what is beyond them. Positive roll is
  right-handed about the tool direction.

This is the layout of the community ER-4U CAD model (github.com/baijuch/sboter4u,
as described by github.com/talos-rit/scorbot_ros2), chosen so that model's
link meshes can be placed on these frames with fixed offsets. It is **not**
the convention of ``kinematics.py``: there wrist pitch is measured from 90
degrees below the forearm (``tests/test_arm_chain.py`` checks the two agree
once that is allowed for). Zero here is a drawing convention, not the pose
after homing.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from . import nominal

JOINTS = ("base", "shoulder", "elbow", "wrist_pitch", "wrist_roll")
LINKS = ("base_link", "turret_link", "upper_arm_link", "forearm_link", "wrist_link",
         "flange_link")
STATUS = "nominal, not measured on our arm"


@dataclass(frozen=True)
class ChainGeometry:
    """The offsets between joint axes, in millimetres."""

    base_to_turret_mm: float       # mounting face up to the turret frame
    turret_to_shoulder_mm: float   # turret frame up to the shoulder axis
    shoulder_forward_mm: float     # shoulder axis ahead of the base axis
    upper_arm_mm: float            # shoulder axis to elbow axis
    forearm_mm: float              # elbow axis to wrist pitch axis
    tool_length_mm: float          # wrist axes to the tool tip
    source: str
    status: str = STATUS


# The CAD model's own placements, so its meshes line up. Upper arm and forearm
# are the manual's 220 mm. The shoulder axis is 346 mm up and 29 mm forward,
# against the manual's 364 mm and the legacy code's 16 mm; see MANUAL below.
# The tool length is the vendor parameter file's gripper length.
MESH_MODEL = ChainGeometry(
    base_to_turret_mm=148.33, turret_to_shoulder_mm=197.66, shoulder_forward_mm=29.0,
    upper_arm_mm=nominal.UPPER_ARM_MM.value, forearm_mm=nominal.FOREARM_MM.value,
    tool_length_mm=nominal.VENDOR_GRIPPER_LENGTH_MM.value,
    source="joint placements of the community ER-4U SolidWorks model (baijuch/sboter4u, "
           "as derived in talos-rit/scorbot_ros2 scorbot_description); tool length from "
           "the vendor ROB_4u.INI")

# The manual's side view and the legacy forward offset. The manual gives one
# height (base bottom to shoulder axis); the split at the turret frame is kept
# from the CAD model and has no meaning of its own here.
MANUAL = ChainGeometry(
    base_to_turret_mm=148.33,
    turret_to_shoulder_mm=nominal.SHOULDER_AXIS_HEIGHT_MM.value - 148.33,
    shoulder_forward_mm=16.0,
    upper_arm_mm=nominal.UPPER_ARM_MM.value, forearm_mm=nominal.FOREARM_MM.value,
    tool_length_mm=nominal.VENDOR_GRIPPER_LENGTH_MM.value,
    source="ER-4u manual side view (364 mm, 220 mm, 220 mm); 16 mm shoulder offset from "
           "legacy openScorbot conf.py; tool length from the vendor ROB_4u.INI")


# The vendor's own numbers (ROB_4u.INI: base height 349, links 221, gripper
# 145) with the legacy 16 mm offset. With the vendor's count formula this
# reproduces the home position the USNA toolbox publishes to within 0.3 mm
# (tests/test_arm_view.py), which is evidence about the vendor's model of the
# arm, not about ours.
VENDOR_INI = ChainGeometry(
    base_to_turret_mm=148.33,
    turret_to_shoulder_mm=nominal.VENDOR_BASE_HEIGHT_MM.value - 148.33,
    shoulder_forward_mm=16.0,
    upper_arm_mm=nominal.VENDOR_UPPER_ARM_MM.value, forearm_mm=nominal.VENDOR_FOREARM_MM.value,
    tool_length_mm=nominal.VENDOR_GRIPPER_LENGTH_MM.value,
    source="vendor ROB_4u.INI (349, 221, 221, 145 mm); 16 mm shoulder offset from legacy "
           "openScorbot conf.py")


def _translate(x: float, y: float, z: float) -> np.ndarray:
    pose = np.eye(4)
    pose[:3, 3] = (x, y, z)
    return pose


def _rotate(axis: tuple[int, int, int], degrees: float) -> np.ndarray:
    """Right-handed rotation about a signed coordinate axis."""
    angle = math.radians(degrees) * sum(axis)
    c, s = math.cos(angle), math.sin(angle)
    i = next(index for index, value in enumerate(axis) if value)
    j, k = (i + 1) % 3, (i + 2) % 3
    pose = np.eye(4)
    pose[j, j], pose[j, k], pose[k, j], pose[k, k] = c, -s, s, c
    return pose


def _angles(q) -> list[float]:
    try:
        values = [float(value) for value in q]
    except (TypeError, ValueError):
        raise ValueError("Expected five finite joint angles in degrees") from None
    if len(values) != len(JOINTS) or not all(math.isfinite(value) for value in values):
        raise ValueError("Expected five finite joint angles in degrees")
    return values


def link_poses(q, geometry: ChainGeometry = MESH_MODEL) -> dict[str, np.ndarray]:
    """4x4 pose of every link, and of the tool tip, in the base frame.

    ``q`` is (base, shoulder, elbow, wrist_pitch, wrist_roll) in degrees, in
    this module's convention. UNVALIDATED.
    """
    base, shoulder, elbow, pitch, roll = _angles(q)
    g = geometry
    poses = {"base_link": np.eye(4)}
    poses["turret_link"] = (poses["base_link"] @ _translate(0, 0, g.base_to_turret_mm)
                            @ _rotate((0, 0, 1), base))
    poses["upper_arm_link"] = (poses["turret_link"]
                               @ _translate(g.shoulder_forward_mm, 0, g.turret_to_shoulder_mm)
                               @ _rotate((0, -1, 0), shoulder))
    poses["forearm_link"] = (poses["upper_arm_link"] @ _translate(g.upper_arm_mm, 0, 0)
                             @ _rotate((0, -1, 0), elbow))
    poses["wrist_link"] = (poses["forearm_link"] @ _translate(g.forearm_mm, 0, 0)
                           @ _rotate((0, -1, 0), pitch))
    poses["flange_link"] = poses["wrist_link"] @ _rotate((1, 0, 0), roll)
    poses["tool"] = poses["flange_link"] @ _translate(g.tool_length_mm, 0, 0)
    return poses


def skeleton(q, geometry: ChainGeometry = MESH_MODEL) -> list[tuple[float, float, float]]:
    """The arm as a line: base, turret, shoulder, elbow, wrist, tool tip (mm)."""
    poses = link_poses(q, geometry)
    return [(float(poses[name][0, 3]), float(poses[name][1, 3]), float(poses[name][2, 3]))
            for name in ("base_link", "turret_link", "upper_arm_link", "forearm_link",
                         "wrist_link", "tool")]
