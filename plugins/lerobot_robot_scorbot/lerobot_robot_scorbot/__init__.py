"""ScorBot ER-4U as a LeRobot ``Robot`` (Stack 2, real-arm opt-in unverified). See the plugin README."""

from .robot import ACTION_KEYS, HAVE_LEROBOT, REAL_ARM_REFUSED, ScorbotRobot, ScorbotRobotConfig

__all__ = ["ACTION_KEYS", "HAVE_LEROBOT", "REAL_ARM_REFUSED", "ScorbotRobot", "ScorbotRobotConfig"]

if HAVE_LEROBOT:        # registers the teleoperators scorbot_joint_keys and scorbot_joint_scripted
    from .teleop import ScorbotJointKeys, ScorbotJointScripted     # noqa: F401  LeRobot finds each class here by its config's name
    from .sim_cameras import ScorbotTwinCamera, ScorbotTwinCameraConfig    # noqa: F401  registers camera type scorbot_twin
    __all__ += ["ScorbotJointKeys", "ScorbotJointScripted", "ScorbotTwinCamera", "ScorbotTwinCameraConfig"]
