"""Two LeRobot teleoperators for ScorbotRobot, found by lerobot-record once this package is installed.

    scorbot_joint_keys      Letter keys step an absolute joint target by step_deg per loop; PolicyArm
                            applies vendor travel limits. Needs pynput. Letters also work with shift held.
    scorbot_joint_scripted  walks through fixed waypoints (base,shoulder,elbow[,pitch,roll[,gripper]]) at step_deg per loop, for unattended runs and
                            examples/lerobot_sim_check.py.

    Joint / gripper    Positive / open    Negative / close
    base               a                  d
    shoulder           s                  w
    elbow              k                  i
    wrist pitch        t                  g
    wrist roll         l                  j
    gripper            o (100 percent)    c (0 percent)

Both output exactly ScorbotRobot's action keys (absolute degrees from home). Named apart from the Stack 1
teleoperators (scorbot_keys, scorbot_scripted) so both plugins can share one environment. Which key moves
the real arm which way is unverified; these keys are checked only on the simulator. Right/left arrows,
Esc, n, r and q are left to lerobot-record (end episode, re-record, stop). A stopped listener holds.
"""

from __future__ import annotations

from dataclasses import dataclass

from lerobot.teleoperators import Teleoperator, TeleoperatorConfig
from lerobot.teleoperators.keyboard import KeyboardTeleop, KeyboardTeleopConfig

from scorbot.policy_arm import STEP_MAX_DEG

from .robot import ACTION_KEYS
from .teleop_map import DEFAULT_SCRIPT, check_limits, home, key_name, keys_to_target, parse_script, script_walk, to_action


@TeleoperatorConfig.register_subclass("scorbot_joint_keys")
@dataclass
class ScorbotJointKeysConfig(KeyboardTeleopConfig):
    step_deg: float = STEP_MAX_DEG                  # one loop with a key held moves the target this far

    def __post_init__(self):
        check_limits(self.step_deg)


class ScorbotJointKeys(KeyboardTeleop):
    config_class = ScorbotJointKeysConfig
    name = "scorbot_joint_keys"

    def __init__(self, config: ScorbotJointKeysConfig):
        super().__init__(config)
        self._target = home()           # ScorbotRobot starts at home after connect

    @property
    def action_features(self) -> dict:
        return {key: float for key in ACTION_KEYS}

    def _drain_pressed_keys(self):
        while not self.event_queue.empty():
            key, is_pressed = self.event_queue.get_nowait()
            name = key_name(key)
            if name is not None:
                self.current_pressed[name] = is_pressed

    def get_action(self) -> dict:
        if not self.is_connected:
            return to_action(self._target)
        self._drain_pressed_keys()
        self._target = keys_to_target([k for k, down in self.current_pressed.items() if down],
                                     self._target, self.config.step_deg)
        return to_action(self._target)


@TeleoperatorConfig.register_subclass("scorbot_joint_scripted")
@dataclass
class ScorbotJointScriptedConfig(TeleoperatorConfig):
    script: str = DEFAULT_SCRIPT        # "base,shoulder,elbow[,pitch,roll[,gripper]];..." absolute, repeated
    step_deg: float = STEP_MAX_DEG

    def __post_init__(self):
        check_limits(self.step_deg)
        self.waypoints = parse_script(self.script)


class ScorbotJointScripted(Teleoperator):
    config_class = ScorbotJointScriptedConfig
    name = "scorbot_joint_scripted"

    def __init__(self, config: ScorbotJointScriptedConfig):
        super().__init__(config)
        self.config = config
        self._walk = None

    @property
    def action_features(self) -> dict:
        return {key: float for key in ACTION_KEYS}

    @property
    def feedback_features(self) -> dict:
        return {}

    @property
    def is_connected(self) -> bool:
        return self._walk is not None

    @property
    def is_calibrated(self) -> bool:
        return True

    def connect(self, calibrate: bool = True) -> None:
        self._walk = script_walk(self.config.waypoints, self.config.step_deg)

    def calibrate(self) -> None:
        pass

    def configure(self) -> None:
        pass

    def get_action(self) -> dict:
        return to_action(next(self._walk))

    def send_feedback(self, feedback: dict) -> None:
        pass

    def disconnect(self) -> None:
        self._walk = None
