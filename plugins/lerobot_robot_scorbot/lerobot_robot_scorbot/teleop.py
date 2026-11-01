"""Two LeRobot teleoperators for ScorbotRobot, found by lerobot-record once this package is installed.

    scorbot_joint_keys      LeRobot's keyboard_ee keys (left/right: base, up/down: shoulder, shifts: elbow) step an absolute
                            joint target by step_deg per loop, within travel_cap_deg of home. Needs pynput.
    scorbot_joint_scripted  walks through fixed waypoints at step_deg per loop, for unattended runs and
                            examples/lerobot_sim_check.py.

Both output exactly ScorbotRobot's action keys (absolute degrees from home). Named apart from the Stack 1
teleoperators (scorbot_keys, scorbot_scripted) so both plugins can share one environment. Which key moves
the real arm which way is unverified; the robot is simulator-only.
"""

from __future__ import annotations

from dataclasses import dataclass

from lerobot.teleoperators import Teleoperator, TeleoperatorConfig
from lerobot.teleoperators.keyboard import KeyboardEndEffectorTeleop, KeyboardEndEffectorTeleopConfig

from scorbot.policy_arm import DEFAULT_TRAVEL_CAP_DEG, STEP_MAX_DEG

from .robot import ACTION_KEYS
from .teleop_map import DEFAULT_SCRIPT, check_limits, home, keyboard_to_target, parse_script, script_walk, to_action


@TeleoperatorConfig.register_subclass("scorbot_joint_keys")
@dataclass
class ScorbotJointKeysConfig(KeyboardEndEffectorTeleopConfig):
    step_deg: float = STEP_MAX_DEG                  # one loop with a key held moves the target this far
    travel_cap_deg: float = DEFAULT_TRAVEL_CAP_DEG  # the target never leaves this band around home

    def __post_init__(self):
        check_limits(self.step_deg, self.travel_cap_deg)
        self.use_gripper = False        # PolicyArm drives base, shoulder and elbow only


class ScorbotJointKeys(KeyboardEndEffectorTeleop):
    config_class = ScorbotJointKeysConfig
    name = "scorbot_joint_keys"

    def __init__(self, config: ScorbotJointKeysConfig):
        super().__init__(config)
        self._target = home()           # ScorbotRobot starts at home after connect

    @property
    def action_features(self) -> dict:
        return {key: float for key in ACTION_KEYS}

    def get_action(self) -> dict:
        self._target = keyboard_to_target(super().get_action(), self._target, self.config.step_deg,
                                          self.config.travel_cap_deg)
        return to_action(self._target)


@TeleoperatorConfig.register_subclass("scorbot_joint_scripted")
@dataclass
class ScorbotJointScriptedConfig(TeleoperatorConfig):
    script: str = DEFAULT_SCRIPT        # "base,shoulder,elbow;..." absolute degrees, repeated
    step_deg: float = STEP_MAX_DEG
    travel_cap_deg: float = DEFAULT_TRAVEL_CAP_DEG

    def __post_init__(self):
        check_limits(self.step_deg, self.travel_cap_deg)
        self.waypoints = parse_script(self.script, self.travel_cap_deg)


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
        self._walk = script_walk(self.config.waypoints, self.config.step_deg, self.config.travel_cap_deg)

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
