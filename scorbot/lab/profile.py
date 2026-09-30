"""Saved lab identity for the guided session. A settings file, not evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path

from .operator import ENTER

FIELDS = ("robot_id", "arm_label", "controller_label", "driver", "operator")
LABELS = {"robot_id": "Robot id", "arm_label": "Arm label (nameplate)",
          "controller_label": "Controller label (nameplate)",
          "driver": "USB driver (Device Manager)", "operator": "Operator initials"}
# The placeholder labels examples/bench_joint.py also rejects.
EXAMPLE_VALUES = frozenset({
    "arm nameplate", "controller nameplate", "current windows driver",
    "your initials", "photo/sketch of known start pose",
    "same known pose as idle capture",
})
PROFILE_ATTEMPTS = 3


class ProfileError(ValueError):
    """A lab profile value is missing or invalid."""


@dataclass(frozen=True)
class LabProfile:
    robot_id: str
    arm_label: str
    controller_label: str
    driver: str
    operator: str
    speed: int = 10

    def __post_init__(self):
        for name in FIELDS:
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ProfileError(f"{LABELS[name]} must not be empty")
            if value.strip().lower() in EXAMPLE_VALUES:
                raise ProfileError(f"{LABELS[name]}: replace the example value {value!r}")
        if type(self.speed) is not int or not 1 <= self.speed <= 20:
            raise ProfileError("Speed must be an integer from 1 to 20")

    def summary(self) -> str:
        return ", ".join(f"{LABELS[name].split(' (')[0]}: {getattr(self, name)}"
                         for name in FIELDS) + f", speed: {self.speed}"


def load_profile(path) -> LabProfile | None:
    path = Path(path)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return LabProfile(**{name: data[name] for name in FIELDS},
                          speed=data.get("speed", 10))
    except (ValueError, KeyError, TypeError) as error:
        raise ProfileError(f"{path}: unreadable lab profile ({error})") from None


def save_profile(profile: LabProfile, path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(asdict(profile), indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _ask(operator, current: LabProfile | None) -> LabProfile:
    for _ in range(PROFILE_ATTEMPTS):
        values = {}
        for name in FIELDS:
            old = getattr(current, name) if current else ""
            hint = f" [{old}]" if old else ""
            values[name] = operator.text(f"{LABELS[name]}{hint}: ") or old
        old_speed = current.speed if current else 10
        speed_text = operator.text(f"Speed 1-20 [{old_speed}]: ")
        try:
            speed = int(speed_text) if speed_text else old_speed
            return LabProfile(**values, speed=speed)
        except (ProfileError, ValueError) as error:
            operator.show(f"Not accepted: {error}", "warn")
    raise ProfileError("No valid lab profile after three attempts")


def ensure_profile(path, operator) -> LabProfile:
    """Show the saved profile (Enter keeps it) or ask for one; save on confirmation."""
    current = load_profile(path)
    if current is not None:
        operator.show(f"Lab profile: {current.summary()}")
        if operator.choose("[Enter] keep, [c] change: ", {ENTER: "keep", "c": "change"}) != "change":
            return current
    profile = _ask(operator, current)
    operator.show(f"Lab profile: {profile.summary()}")
    if operator.choose(f"Save to {path}? [y/n] ", {"y": "yes", "n": "no"}) == "yes":
        save_profile(profile, path)
    return profile
