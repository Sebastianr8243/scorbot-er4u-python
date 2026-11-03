"""Teleoperator logic that needs no lerobot, so the normal test run covers it.

Both teleoperators hold their own absolute target (degrees from home) and move it with
``scorbot.policy_arm.clamp``: at most ``step_deg`` per call and never past ``cap_deg`` from home.
The measured position is not known to a teleoperator, so the lead band is left to ``PolicyArm``
on the robot side (``clamp`` is called with measured = target, which never binds).
"""

from __future__ import annotations

import math

from scorbot.policy_arm import DEFAULT_TRAVEL_CAP_DEG, JOINTS, MOTORS, STEP_MAX_DEG, clamp

from .robot import ACTION_KEYS

GRIPPER = "gripper"         # 0 closed .. 100 open; ScorbotRobot thresholds it at 50
GRIPPER_KEYS = {0: 0.0, 2: 100.0}       # keyboard_ee's "gripper": 0 left ctrl (close), 2 right ctrl (open), 1 stay
KEY_AXES = {"delta_x": "base", "delta_y": "shoulder", "delta_z": "elbow"}     # keyboard_ee: left/right x, up/down y, shifts z
# base,shoulder,elbow[,pitch,roll[,gripper]] waypoints (degrees, gripper 0..100); ends at home. keyboard_ee has
# no wrist keys: they hold.
DEFAULT_SCRIPT = "1,0,0,0,0;1,1,0,1,0;0,1,-1,1,-1;0,0,0,0,0"


def home() -> dict[str, float]:
    return dict.fromkeys((*JOINTS, GRIPPER), 0.0)      # the vendor Home closes the jaw


def check_limits(step_deg: float, cap_deg: float) -> None:
    for name, value, top in (("step_deg", step_deg, STEP_MAX_DEG), ("travel_cap_deg", cap_deg, DEFAULT_TRAVEL_CAP_DEG)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= top:
            raise ValueError(f"{name} must be above 0 and at most {top:g}, got {value}")


def to_action(target: dict) -> dict[str, float]:
    return {m + ".pos": float(target[m]) for m in (*JOINTS, GRIPPER)}


def step_toward(target: dict, goal: dict, step_deg: float, cap_deg: float) -> dict[str, float]:
    """The next target: ``goal`` (absolute degrees) approached by at most ``step_deg`` per motor; the gripper
    goes to its goal at once (it is binary). Pure."""
    delta = {m: goal[m] - target[m] for m in JOINTS if m in goal}
    new, _ = clamp(delta, target, target, cap_deg=cap_deg, step_max_deg=step_deg, lead_margin_deg=math.inf)
    return {**new, GRIPPER: goal.get(GRIPPER, target[GRIPPER])}


def keyboard_to_target(raw: dict, target: dict, step_deg: float, cap_deg: float) -> dict[str, float]:
    """LeRobot's keyboard_ee action (delta_x/y/z in -1..1, gripper 0/1/2) to the next absolute target. Gripper 0
    closes, 2 opens, anything else holds. Other keys are ignored."""
    delta = {}
    for key, motor in KEY_AXES.items():
        value = raw.get(key, 0)
        delta[motor] = float(value) * step_deg if isinstance(value, (int, float)) and not isinstance(value, bool) \
            else math.nan       # clamp holds a motor with a bad value
    new, _ = clamp(delta, target, target, cap_deg=cap_deg, step_max_deg=step_deg, lead_margin_deg=math.inf)
    key = raw.get(GRIPPER)
    if isinstance(key, int) and not isinstance(key, bool) and key in GRIPPER_KEYS:
        new[GRIPPER] = GRIPPER_KEYS[key]
    return new


def parse_script(text: str, cap_deg: float = DEFAULT_TRAVEL_CAP_DEG) -> list[dict]:
    """'base,shoulder,elbow[,pitch,roll[,gripper]];...' absolute degrees (gripper 0..100) to a list of waypoints
    (pitch, roll and gripper 0 when left out). ValueError on anything malformed."""
    waypoints = []
    for piece in text.split(";"):
        parts = piece.split(",")
        if len(parts) not in (len(MOTORS), len(JOINTS), len(JOINTS) + 1):
            raise ValueError(f"each waypoint needs {len(MOTORS)} numbers (base,shoulder,elbow), {len(JOINTS)} "
                             f"(and pitch,roll) or {len(JOINTS) + 1} (and gripper), got {piece.strip()!r}")
        try:
            values = [float(part) for part in parts]
        except ValueError:
            raise ValueError(f"not a number in waypoint {piece.strip()!r}") from None
        joints, gripper = values[:len(JOINTS)], values[len(JOINTS):]
        if not all(math.isfinite(v) and abs(v) <= cap_deg for v in joints):
            raise ValueError(f"every joint must be finite and within {cap_deg:g} degrees of home, got {piece.strip()!r}")
        if not all(0 <= v <= 100 for v in gripper):
            raise ValueError(f"the gripper must be from 0 to 100, got {piece.strip()!r}")
        waypoints.append({**home(), **dict(zip(JOINTS, joints)), **dict(zip((GRIPPER,), gripper))})
    return waypoints


def script_walk(waypoints: list[dict], step_deg: float, cap_deg: float):
    """Endless targets: walk to each waypoint at step_deg per call, then on to the next, and around again."""
    target, index = home(), 0
    while True:
        target = step_toward(target, waypoints[index], step_deg, cap_deg)
        if all(abs(target[m] - waypoints[index][m]) < 1e-9 for m in JOINTS):
            index = (index + 1) % len(waypoints)
        yield dict(target)


__all__ = ["ACTION_KEYS", "DEFAULT_SCRIPT", "GRIPPER", "GRIPPER_KEYS", "KEY_AXES", "check_limits", "home", "keyboard_to_target", "parse_script",
           "script_walk", "step_toward", "to_action"]
