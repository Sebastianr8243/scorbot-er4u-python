"""Step planning for multi-step moves in the guided session. Pure: no robot, no I/O.

A plan is a list of ordinary 1 or 0.5 degree jogs, one joint at a time, in
RETURN_ORDER (retract the elbow and shoulder before swinging the base).
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping

RETURN_ORDER = ("elbow", "shoulder", "base")
MAX_MARKS = 9
STEP_DEG = 1.0
HALF_STEP_DEG = 0.5
_TOLERANCE = 1e-9


@dataclass(frozen=True)
class Move:
    joint: str
    delta_deg: float

    @property
    def label(self) -> str:
        return f"{self.joint.upper()} {self.delta_deg:+g}"


@dataclass(frozen=True)
class MarkedPosition:
    """A pose marked in this session. Never saved: home is re-found each session."""

    name: str
    travel: dict
    counts: dict


def _check_joint(joint: str) -> None:
    if joint not in RETURN_ORDER:
        raise ValueError(f"Unknown joint for a multi-step move: {joint!r}")


def plan_moves(current: Mapping[str, float], target: Mapping[str, float]) -> list[Move]:
    """Whole steps toward the target per joint, then one half step if needed."""
    for joint in (*current, *target):
        _check_joint(joint)
    moves: list[Move] = []
    for joint in RETURN_ORDER:
        difference = float(target.get(joint, 0.0)) - float(current.get(joint, 0.0))
        if not math.isfinite(difference):
            raise ValueError(f"{joint}: travel must be finite")
        halves = round(difference / HALF_STEP_DEG)
        if abs(difference - halves * HALF_STEP_DEG) > _TOLERANCE:
            raise ValueError(f"{joint}: {difference:g} degrees is not a multiple of 0.5")
        sign = 1.0 if halves > 0 else -1.0
        whole, half = divmod(abs(halves), 2)
        moves += [Move(joint, sign * STEP_DEG)] * whole
        if half:
            moves.append(Move(joint, sign * HALF_STEP_DEG))
    return moves


def apply(travel: Mapping[str, float], move: Move) -> dict[str, float]:
    """The travel dict after ``move``; the input is not changed."""
    _check_joint(move.joint)
    new = dict(travel)
    new[move.joint] = new.get(move.joint, 0.0) + move.delta_deg
    return new
