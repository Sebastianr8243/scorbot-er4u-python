"""Teleoperator logic that needs no lerobot, so the normal test run covers it.

Both teleoperators hold their own absolute target (degrees from home) and move it with
``scorbot.policy_arm.clamp``: at most ``step_deg`` per call and never past ``cap_deg`` from home.
The measured position is not known to a teleoperator, so the lead band is left to ``PolicyArm``
on the robot side (``clamp`` is called with measured = target, which never binds).
"""

from __future__ import annotations

import math

from scorbot.policy_arm import DEFAULT_TRAVEL_CAP_DEG, MOTORS, STEP_MAX_DEG, clamp

from .robot import ACTION_KEYS

KEY_AXES = {"delta_x": "base", "delta_y": "shoulder", "delta_z": "elbow"}     # keyboard_ee: left/right x, up/down y, shifts z
DEFAULT_SCRIPT = "1,0,0;1,1,0;0,1,-1;0,0,0"     # base,shoulder,elbow waypoints in degrees; ends at home


def home() -> dict[str, float]:
    return dict.fromkeys(MOTORS, 0.0)


def check_limits(step_deg: float, cap_deg: float) -> None:
    for name, value, top in (("step_deg", step_deg, STEP_MAX_DEG), ("travel_cap_deg", cap_deg, DEFAULT_TRAVEL_CAP_DEG)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= top:
            raise ValueError(f"{name} must be above 0 and at most {top:g}, got {value}")


def to_action(target: dict) -> dict[str, float]:
    return {m + ".pos": float(target[m]) for m in MOTORS}


def step_toward(target: dict, goal: dict, step_deg: float, cap_deg: float) -> dict[str, float]:
    """The next target: ``goal`` (absolute degrees) approached by at most ``step_deg`` per motor. Pure."""
    delta = {m: goal[m] - target[m] for m in MOTORS if m in goal}
    new, _ = clamp(delta, target, target, cap_deg=cap_deg, step_max_deg=step_deg, lead_margin_deg=math.inf)
    return new


def keyboard_to_target(raw: dict, target: dict, step_deg: float, cap_deg: float) -> dict[str, float]:
    """LeRobot's keyboard_ee action (delta_x/y/z in -1..1) to the next absolute target. Other keys are ignored."""
    delta = {}
    for key, motor in KEY_AXES.items():
        value = raw.get(key, 0)
        delta[motor] = float(value) * step_deg if isinstance(value, (int, float)) and not isinstance(value, bool) \
            else math.nan       # clamp holds a motor with a bad value
    new, _ = clamp(delta, target, target, cap_deg=cap_deg, step_max_deg=step_deg, lead_margin_deg=math.inf)
    return new


def parse_script(text: str, cap_deg: float = DEFAULT_TRAVEL_CAP_DEG) -> list[dict]:
    """'base,shoulder,elbow;...' absolute degrees to a list of waypoints. ValueError on anything malformed."""
    waypoints = []
    for piece in text.split(";"):
        parts = piece.split(",")
        if len(parts) != len(MOTORS):
            raise ValueError(f"each waypoint needs {len(MOTORS)} numbers (base,shoulder,elbow), got {piece.strip()!r}")
        try:
            values = [float(part) for part in parts]
        except ValueError:
            raise ValueError(f"not a number in waypoint {piece.strip()!r}") from None
        if not all(math.isfinite(v) and abs(v) <= cap_deg for v in values):
            raise ValueError(f"every number must be finite and within {cap_deg:g} degrees of home, got {piece.strip()!r}")
        waypoints.append(dict(zip(MOTORS, values)))
    return waypoints


def script_walk(waypoints: list[dict], step_deg: float, cap_deg: float):
    """Endless targets: walk to each waypoint at step_deg per call, then on to the next, and around again."""
    target, index = home(), 0
    while True:
        target = step_toward(target, waypoints[index], step_deg, cap_deg)
        if all(abs(target[m] - waypoints[index][m]) < 1e-9 for m in MOTORS):
            index = (index + 1) % len(waypoints)
        yield dict(target)


__all__ = ["ACTION_KEYS", "DEFAULT_SCRIPT", "KEY_AXES", "check_limits", "home", "keyboard_to_target", "parse_script",
           "script_walk", "step_toward", "to_action"]
