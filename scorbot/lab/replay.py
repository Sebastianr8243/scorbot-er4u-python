"""Replay an exported episode in the guided session: load, preflight, plan. Pure.

Reads the dataset's ``scorbot_episodes.jsonl`` sidecar (standard library only)
and turns its actions, encoder counts from the session home, into ordinary lab
moves on the 0.5 degree grid. Every rule that could let a wrong dataset drive
the arm is checked here before anything moves; the session then runs the
moves with its own gates (_run_plan: typed confirmation, stop on any key, the
travel cap, drift check, fault latch).
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from ..lerobot_export.load import MOTORS
from ..lerobot_export.sidecar import SIDECAR, STEP_JOINTS, UNITS
from .moves import HALF_STEP_DEG, Move, plan_moves

PROVENANCE = "scorbot_provenance.json"
WRIST_TOLERANCE_COUNTS = 20     # wrist motion stays disabled: within the settle band
# Off-grid limit in degrees: above the 20-count settle band on every joint (20/113
# counts is 0.18 on the elbow) and below 0.25, the most a 0.5 degree grid allows.
GRID_TOLERANCE = 0.2


class ReplayRefused(Exception):
    """The episode cannot be replayed; nothing has moved."""


@dataclass
class ReplayEpisode:
    record: dict
    provenance: dict
    dataset_dir: Path
    sidecar_sha256: str


def load_episode(dataset_dir, number: int) -> ReplayEpisode:
    folder = Path(dataset_dir)
    provenance_path, sidecar_path = folder / PROVENANCE, folder / SIDECAR
    if not provenance_path.is_file():
        raise ReplayRefused(f"no {PROVENANCE} in {folder}: not an exported dataset")
    if not sidecar_path.is_file():
        raise ReplayRefused(f"no {SIDECAR} in {folder}: export it again with this version")
    try:
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        rows = [json.loads(line) for line in
                sidecar_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except ValueError as error:
        raise ReplayRefused(f"dataset files do not parse: {error}") from None
    record = next((r for r in rows if r.get("dataset_episode") == number), None)
    if record is None:
        raise ReplayRefused(f"episode {number} is not in this dataset "
                            f"(episodes {[r.get('dataset_episode') for r in rows]})")
    sha256 = hashlib.sha256(sidecar_path.read_bytes()).hexdigest()
    return ReplayEpisode(record, provenance, folder, sha256)


def to_travel(rel, step_counts) -> dict[str, float]:
    """Degrees from home per arm joint on the 0.5 degree grid (legacy scale)."""
    travel = {}
    for index, joint in enumerate(MOTORS):
        if joint not in STEP_JOINTS:
            continue
        degrees = float(rel[index]) / step_counts[joint]
        grid = round(degrees / HALF_STEP_DEG) * HALF_STEP_DEG
        if abs(degrees - grid) > GRID_TOLERANCE:
            raise ReplayRefused(f"{joint} at {degrees:.2f} degrees is off the step grid")
        travel[joint] = grid
    return travel


def _travels(episode, step_counts) -> list[dict]:
    record = episode.record
    return [to_travel(record["first_state"], step_counts),
            *(to_travel(a, step_counts) for a in record["actions"])]


def preflight(episode, *, data_source: str, robot_id: str, step_counts) -> list[str]:
    """Every reason not to replay this episode here; empty means replay may proceed."""
    from .session import TRAVEL_CAP_DEG
    record, provenance = episode.record, episode.provenance
    problems = []
    if provenance.get("episodes_sidecar_sha256") != episode.sidecar_sha256:
        problems.append(f"{SIDECAR} changed since export (sha256 differs from provenance)")
    if provenance.get("data_source") != data_source:
        problems.append(f"data source {provenance.get('data_source')!r} does not match this "
                        f"{data_source!r} session")
    robots = {s.get("robot_id") for s in provenance.get("sessions", [])}
    if robots != {robot_id}:
        problems.append(f"recorded on robot {sorted(map(str, robots))}, not {robot_id!r}")
    if record.get("units") != UNITS or provenance.get("units") != UNITS:
        problems.append(f"units {record.get('units')!r} are not {UNITS!r}")
    if record.get("motors") != list(MOTORS):
        problems.append(f"motor order {record.get('motors')} is not {list(MOTORS)}")
    if record.get("step_counts") != dict(step_counts):
        problems.append(f"step counts {record.get('step_counts')} differ from this SDK's "
                        f"{dict(step_counts)}")
    if problems:
        return problems
    wrist = [i for i, m in enumerate(MOTORS) if m not in STEP_JOINTS]
    for rel in [record["first_state"], *record["actions"]]:
        if any(abs(rel[i]) > WRIST_TOLERANCE_COUNTS for i in wrist):
            problems.append("a wrist motor moves; wrist motion is disabled")
            break
    try:
        travels = _travels(episode, step_counts)
    except ReplayRefused as error:
        return problems + [str(error)]
    if any(abs(t) > TRAVEL_CAP_DEG + 1e-9 for travel in travels for t in travel.values()):
        problems.append(f"an action is beyond the {TRAVEL_CAP_DEG:g} degree cap from home")
    for before, after in zip(travels, travels[1:]):
        changed = [j for j in STEP_JOINTS if after[j] != before[j]]
        if len(changed) > 1 or any(abs(after[j] - before[j]) > 1.0 for j in changed):
            problems.append("consecutive actions change more than one step on one joint")
            break
    return problems


def start_travel(episode, step_counts) -> dict[str, float]:
    return to_travel(episode.record["first_state"], step_counts)


def play_moves(episode, step_counts) -> list[Move]:
    travels = _travels(episode, step_counts)
    moves: list[Move] = []
    for before, after in zip(travels, travels[1:]):
        moves.extend(plan_moves(before, after))
    return moves


def counts_for(rel, home_raw: dict) -> dict[str, int]:
    """Raw encoder counts for counts-from-home ``rel`` (wrap-aware), for arrival checks."""
    counts = dict(home_raw)
    for index, motor in enumerate(MOTORS):
        counts[motor] = (home_raw[motor] + round(rel[index])) % 65535
    return counts


def final_counts(episode, home_raw: dict) -> dict[str, int]:
    """Raw encoder counts of the episode's final state, for the arrival check."""
    return counts_for(episode.record["final_state"], home_raw)
