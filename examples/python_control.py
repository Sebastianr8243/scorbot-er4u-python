"""First supervised Python control session with the original USB controller."""

from dataclasses import asdict

from scorbot import Scorbot


# Physically place the arm at the legacy homing start pose before running.
# Keep the controller's physical emergency stop accessible.
with Scorbot(log_path="session.jsonl") as robot:
    print(asdict(robot.get_state()))
    robot.enable()
    robot.home(start_position_confirmed=True)
    print(asdict(robot.get_state()))
    robot.jog_joint("base", 1, speed=10)  # Relative, one-degree jog.
    robot.disable()
