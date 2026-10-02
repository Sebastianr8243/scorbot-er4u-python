from dataclasses import dataclass
from pathlib import Path

from lerobot.robots.config import RobotConfig


@RobotConfig.register_subclass("scorbot")
@dataclass
class ScorbotRobotConfig(RobotConfig):
    # Must stay True in this version: the plugin refuses the real arm.
    simulate: bool = False
    speed: int = 10            # legacy jog speed, 1-20
    step_deg: float = 1.0      # one jog per send_action, as in lab teleop
    log_dir: Path = Path("logs/replay")
    robot_id: str = "lab-er4u-1"
