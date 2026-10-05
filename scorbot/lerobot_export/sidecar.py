"""The replay sidecar written into every exported dataset (scorbot_episodes.jsonl).

One JSON row per dataset episode with its actions in counts from home, so the
lab PC can replay an episode with the standard library alone (no LeRobot or
pyarrow). The exporter checks it against LeRobot's own copy before publishing.
"""

from __future__ import annotations

from .. import limits
from .load import MOTORS

SIDECAR = "scorbot_episodes.jsonl"
UNITS = "uncalibrated encoder counts from session home"
STEP_JOINTS = limits.ARM_MOTORS


def step_counts() -> dict[str, int]:
    """Signed encoder counts of one legacy +1 degree jog per arm joint (SDK, offline).

    The sign is the joint's legacy direction: a +1 degree elbow jog lowers the
    elbow count. Replay divides by these signed values, so a recorded move
    replays in its recorded direction.
    """
    from ..robot import Scorbot
    robot = Scorbot()
    return {joint: robot.preview_jog(joint, 1.0)["motor_count_deltas"][joint]
            for joint in STEP_JOINTS}


def episode_record(dataset_episode: int, data, frames, fps: int) -> dict:
    return {"dataset_episode": dataset_episode, "session": data.name,
            "source_episode": frames.episode, "task": frames.task, "fps": fps,
            "motors": list(MOTORS), "units": UNITS, "step_counts": step_counts(),
            "first_state": frames.state[0], "final_state": frames.state[-1],
            "actions": frames.action}
