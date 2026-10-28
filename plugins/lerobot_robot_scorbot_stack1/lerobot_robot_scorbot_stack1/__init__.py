"""LeRobot-style adapter for Stack 1 (scorbot.bridge.Arm, vendor DLL path). See the plugin README."""

from .dataset import DatasetWriter, MixedDataError, read_episode
from .robot import HAVE_LEROBOT, ActionRefused, AdapterStopped, Stack1RobotConfig, Stack1Robot

__all__ = ["ActionRefused", "AdapterStopped", "DatasetWriter", "HAVE_LEROBOT", "MixedDataError", "Stack1RobotConfig",
           "Stack1Robot", "read_episode"]

if HAVE_LEROBOT:        # registers the teleoperators scorbot_keys and scorbot_scripted and the camera scorbot_fake;
    from .camera import ScorbotFakeCamera                   # noqa: F401  LeRobot finds each class here by its config's name
    from .teleop import ScorbotKeys, ScorbotScripted        # noqa: F401
    __all__ += ["ScorbotFakeCamera", "ScorbotKeys", "ScorbotScripted"]
