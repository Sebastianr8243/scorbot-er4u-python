"""LeRobot robot plugin for the ScorBot ER-4U. Simulator only for now.

LeRobot discovers this package by its name prefix (``lerobot_robot_``).
Real-arm replay goes through ``python -m scorbot.lab`` (key p), which runs
the lab's checks, typed confirmations and stop-on-key; real-arm use through
LeRobot needs its own reviewed design (M2).
"""

from .config_scorbot import ScorbotRobotConfig
from .scorbot_robot import ScorbotRobot

__all__ = ["ScorbotRobot", "ScorbotRobotConfig"]
