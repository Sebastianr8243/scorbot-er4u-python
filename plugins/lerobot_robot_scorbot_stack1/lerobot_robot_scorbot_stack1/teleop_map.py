"""Teleoperator logic that needs no lerobot, so the normal test run covers it: the script parser and the keyboard_ee map."""

from __future__ import annotations

from .robot import ACTION_KEYS, MAX_STEP_MM, _number

DEFAULT_SCRIPT = "10,0,0,0;0,10,0,1;-10,0,0,1;0,-10,0,0"       # dx,dy,dz,gripper per step; back at the start every 4 steps, so a long run stays in the box


def check_step(step_mm: float) -> None:
    if not _number(step_mm) or not 0 < step_mm <= MAX_STEP_MM:
        raise ValueError(f"step_mm must be above 0 and at most {MAX_STEP_MM:g}, got {step_mm}")


def parse_script(text: str) -> list[dict]:
    """'dx,dy,dz,gripper;...' in mm and 0..1 to a list of Stack 1 actions. ValueError on anything malformed."""
    steps = []
    for piece in text.split(";"):
        parts = piece.split(",")
        if len(parts) != len(ACTION_KEYS):
            raise ValueError(f"each step needs {len(ACTION_KEYS)} numbers (dx,dy,dz,gripper), got {piece.strip()!r}")
        try:
            values = [float(part) for part in parts]
        except ValueError:
            raise ValueError(f"not a number in step {piece.strip()!r}") from None
        if not all(_number(value) for value in values):
            raise ValueError(f"every number must be finite, got {piece.strip()!r}")
        if any(abs(value) > MAX_STEP_MM for value in values[:3]):
            raise ValueError(f"a step is over {MAX_STEP_MM:g} mm: {piece.strip()!r}")
        steps.append(dict(zip(ACTION_KEYS, values)))
    return steps


def keyboard_to_action(raw: dict, step_mm: float, was_open: bool) -> tuple[dict, bool]:
    """LeRobot's keyboard_ee action (delta_x/y/z in -1..1, gripper 0 close, 1 stay, 2 open) to a Stack 1 action.
    Returns (action, gripper_is_open_now): `stay` keeps the last state, because our gripper.pos is a state."""
    gripper = raw.get("gripper", 1)
    now_open = was_open if gripper == 1 else gripper > 1
    action = {"dx.mm": float(raw["delta_x"]) * step_mm, "dy.mm": float(raw["delta_y"]) * step_mm,
              "dz.mm": float(raw["delta_z"]) * step_mm, "gripper.pos": 1.0 if now_open else 0.0}
    return action, now_open
