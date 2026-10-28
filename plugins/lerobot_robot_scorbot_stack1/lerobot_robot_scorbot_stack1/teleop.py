"""Two LeRobot teleoperators for Stack1Robot, found by lerobot-record once this package is installed.

    scorbot_keys      LeRobot's keyboard_ee (arrow keys: x and y, shift: z, ctrl: gripper) scaled to mm steps. Needs pynput.
    scorbot_scripted  replays a fixed list of steps, for unattended runs and the check in examples/lerobot_record_check.py.

Both output exactly Stack1Robot's action keys. Which key moves the real arm which way is unverified on the arm.
"""

from __future__ import annotations

from dataclasses import dataclass

from lerobot.teleoperators import Teleoperator, TeleoperatorConfig
from lerobot.teleoperators.keyboard import KeyboardEndEffectorTeleop, KeyboardEndEffectorTeleopConfig

from .robot import ACTION_KEYS
from .teleop_map import DEFAULT_SCRIPT, check_step, keyboard_to_action, parse_script


@TeleoperatorConfig.register_subclass("scorbot_keys")
@dataclass
class ScorbotKeysConfig(KeyboardEndEffectorTeleopConfig):
    step_mm: float = 10.0       # one loop with a key held asks for this many mm; the robot caps a step at 50

    def __post_init__(self):
        check_step(self.step_mm)
        self.use_gripper = True


class ScorbotKeys(KeyboardEndEffectorTeleop):
    config_class = ScorbotKeysConfig
    name = "scorbot_keys"

    def __init__(self, config: ScorbotKeysConfig):
        super().__init__(config)
        self._open = False      # a Stack1Robot's gripper is closed after Home

    @property
    def action_features(self) -> dict:
        return {key: float for key in ACTION_KEYS}

    def get_action(self) -> dict:
        action, self._open = keyboard_to_action(super().get_action(), self.config.step_mm, self._open)
        return action


@TeleoperatorConfig.register_subclass("scorbot_scripted")
@dataclass
class ScorbotScriptedConfig(TeleoperatorConfig):
    script: str = DEFAULT_SCRIPT        # "dx,dy,dz,gripper;..." in mm, repeated from the start when it runs out

    def __post_init__(self):
        self.steps = parse_script(self.script)


class ScorbotScripted(Teleoperator):
    config_class = ScorbotScriptedConfig
    name = "scorbot_scripted"

    def __init__(self, config: ScorbotScriptedConfig):
        super().__init__(config)
        self.config = config
        self._connected, self._next = False, 0

    @property
    def action_features(self) -> dict:
        return {key: float for key in ACTION_KEYS}

    @property
    def feedback_features(self) -> dict:
        return {}

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def is_calibrated(self) -> bool:
        return True

    def connect(self, calibrate: bool = True) -> None:
        self._connected, self._next = True, 0

    def calibrate(self) -> None:
        pass

    def configure(self) -> None:
        pass

    def get_action(self) -> dict:
        step = self.config.steps[self._next % len(self.config.steps)]
        self._next += 1
        return dict(step)

    def send_feedback(self, feedback: dict) -> None:
        pass

    def disconnect(self) -> None:
        self._connected = False
