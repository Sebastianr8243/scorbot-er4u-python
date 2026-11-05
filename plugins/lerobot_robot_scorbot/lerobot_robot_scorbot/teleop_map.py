"""Letter-key and scripted targets that need no lerobot, so the normal test run covers them.

Both teleoperators hold their own absolute target (degrees from home) and move it with
``scorbot.policy_arm.clamp``: at most ``step_deg`` per call. Vendor travel guards live on the robot,
which knows this session's vendor Home counts; the teleoperator has no separate travel cap.
The measured position is not known to a teleoperator, so the lead band is left to ``PolicyArm``
on the robot side (``clamp`` is called with measured = target, which never binds).
Letters are case-insensitive; duplicate keys count once, opposite keys cancel and record controls hold.
"""

from __future__ import annotations

import math

from scorbot.policy_arm import JOINTS, MOTORS, STEP_MAX_DEG, clamp

from .robot import ACTION_KEYS

GRIPPER = "gripper"         # 0 closed .. 100 open; ScorbotRobot thresholds it at 50
JOINT_KEYS = {"a": ("base", 1), "d": ("base", -1),
              "s": ("shoulder", 1), "w": ("shoulder", -1),
              "k": ("elbow", 1), "i": ("elbow", -1),
              "t": ("pitch", 1), "g": ("pitch", -1),
              "l": ("roll", 1), "j": ("roll", -1)}
GRIPPER_KEYS = {"o": 100.0, "c": 0.0}
RECORD_KEYS = frozenset({"right", "left", "esc", "n", "r", "q"})
# base,shoulder,elbow[,pitch,roll[,gripper]] waypoints (degrees, gripper 0..100); ends at home.
DEFAULT_SCRIPT = "1,0,0,0,0;1,1,0,1,0;0,1,-1,1,-1;0,0,0,0,0"


def home() -> dict[str, float]:
    return dict.fromkeys((*JOINTS, GRIPPER), 0.0)      # the vendor Home closes the jaw


def check_limits(step_deg: float) -> None:
    if isinstance(step_deg, bool) or not isinstance(step_deg, (int, float)) or not 0 < step_deg <= STEP_MAX_DEG:
        raise ValueError(f"step_deg must be above 0 and at most {STEP_MAX_DEG:g}, got {step_deg}")


def to_action(target: dict) -> dict[str, float]:
    return {m + ".pos": float(target[m]) for m in (*JOINTS, GRIPPER)}


def step_toward(target: dict, goal: dict, step_deg: float) -> dict[str, float]:
    """The next target: ``goal`` (absolute degrees) approached by at most ``step_deg`` per motor; the gripper
    goes to its goal at once (it is binary). Pure."""
    delta = {m: goal[m] - target[m] for m in JOINTS if m in goal}
    new, _ = clamp(delta, target, target, step_max_deg=step_deg, lead_margin_deg=math.inf)
    return {**new, GRIPPER: goal.get(GRIPPER, target[GRIPPER])}


def key_name(key) -> str | None:
    """Lowercase text or a named pynput key; invalid values have no name. Needs no pynput."""
    name = key if isinstance(key, str) else getattr(key, "name", None)
    return name.lower() if isinstance(name, str) else None


def keys_to_target(pressed, target: dict, step_deg: float) -> dict[str, float]:
    """Held keys to a new absolute target. Each joint steps once, opposite keys cancel; o/c open/close,
    both or neither hold. Unbound keys (including record controls) do nothing. Pure."""
    held = {key_name(key) for key in pressed}
    delta = dict.fromkeys(JOINTS, 0.0)
    for key, (motor, sign) in JOINT_KEYS.items():
        if key in held:
            delta[motor] += sign * step_deg
    new, _ = clamp(delta, target, target, step_max_deg=step_deg, lead_margin_deg=math.inf)
    gripper = held & GRIPPER_KEYS.keys()
    if len(gripper) == 1:
        new[GRIPPER] = GRIPPER_KEYS[gripper.pop()]
    return new


def parse_script(text: str) -> list[dict]:
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
        if not all(math.isfinite(v) for v in joints):
            raise ValueError(f"every joint must be finite, got {piece.strip()!r}")
        if not all(0 <= v <= 100 for v in gripper):
            raise ValueError(f"the gripper must be from 0 to 100, got {piece.strip()!r}")
        waypoints.append({**home(), **dict(zip(JOINTS, joints)), **dict(zip((GRIPPER,), gripper))})
    return waypoints


def script_walk(waypoints: list[dict], step_deg: float):
    """Endless targets: walk to each waypoint at step_deg per call, then on to the next, and around again."""
    target, index = home(), 0
    while True:
        target = step_toward(target, waypoints[index], step_deg)
        if all(abs(target[m] - waypoints[index][m]) < 1e-9 for m in JOINTS):
            index = (index + 1) % len(waypoints)
        yield dict(target)


__all__ = ["ACTION_KEYS", "DEFAULT_SCRIPT", "GRIPPER", "GRIPPER_KEYS", "JOINT_KEYS", "RECORD_KEYS", "check_limits",
           "home", "key_name", "keys_to_target", "parse_script", "script_walk", "step_toward", "to_action"]
