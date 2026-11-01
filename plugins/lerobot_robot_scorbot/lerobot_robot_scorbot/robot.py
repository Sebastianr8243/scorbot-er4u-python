"""ScorBot ER-4U as a LeRobot ``Robot``. Simulator only; all motion logic is ``scorbot.policy_arm``.

Observation and action: ``base.pos``, ``shoulder.pos``, ``elbow.pos`` in degrees from home
(uncalibrated vendor prior), plus any cameras LeRobot configures. An action is an absolute target;
``PolicyArm`` clamps it to 0.5 degree per call, the travel cap and the lead limit. The real arm is
refused: LeRobot's tools connect outside their cleanup and have no stop key. Works without the
``lerobot`` package (stand-in base classes), so the normal test run covers it.
"""

from dataclasses import dataclass, field
from pathlib import Path
import time
import uuid
from typing import Any

from scorbot import SimulatedScorbot
from scorbot.policy_arm import MOTORS, PolicyArm

try:        # lerobot is optional: tests run without it. Checked against lerobot 0.6 by examples/lerobot_sim_check.py.
    from lerobot.cameras import CameraConfig, make_cameras_from_configs
    from lerobot.robots import Robot, RobotConfig
    HAVE_LEROBOT = True
except ImportError:
    HAVE_LEROBOT = False

    class RobotConfig:      # stand-in with the two things the real one offers that we use
        @classmethod
        def register_subclass(cls, _name):
            return lambda subclass: subclass

        def __post_init__(self):
            pass

    class Robot:
        def __init__(self, config):
            self.config = config

    class CameraConfig:
        pass

    def make_cameras_from_configs(configs):
        if configs:
            raise RuntimeError("cameras need the lerobot package")
        return {}

SUFFIX = ".pos"
ACTION_KEYS = tuple(m + SUFFIX for m in MOTORS)
SIM_NAME = "scorbot_sim"        # lerobot-record writes the robot's name as the dataset's robot_type
REAL_ARM_REFUSED = ("The LeRobot plugin is simulator-only (simulate=true). Use python -m scorbot.lab "
                    "on the real arm.")


@RobotConfig.register_subclass("scorbot")
@dataclass
class ScorbotRobotConfig(RobotConfig):
    simulate: bool = False          # must be true: the real arm is refused
    travel_cap_deg: float = 2.0     # most degrees from home, per joint
    log_dir: Path = Path("rehearsal/lerobot")
    robot_id: str = "lab-er4u-1"
    cameras: dict[str, CameraConfig] = field(default_factory=dict)


class ScorbotRobot(Robot):
    config_class = ScorbotRobotConfig
    name = "scorbot"

    def __init__(self, config: ScorbotRobotConfig):
        if not config.simulate:
            raise ValueError(REAL_ARM_REFUSED)
        self.name = SIM_NAME        # a v3 dataset has no data_source field: "simulated" lives in robot_type
        super().__init__(config)
        self.config = config
        self.cameras = make_cameras_from_configs(config.cameras)
        self._arm: PolicyArm | None = None

    @property
    def _motors_ft(self) -> dict[str, type]:
        return {key: float for key in ACTION_KEYS}

    @property
    def _cameras_ft(self) -> dict[str, tuple]:
        return {name: (cam.height, cam.width, 3) for name, cam in self.cameras.items()}

    @property
    def observation_features(self) -> dict:
        return {**self._motors_ft, **self._cameras_ft}

    @property
    def action_features(self) -> dict:
        return self._motors_ft

    @property
    def is_connected(self) -> bool:
        return self._arm is not None and self._arm.is_connected

    @property
    def is_calibrated(self) -> bool:
        return True     # counts per degree is a vendor prior; calibration is this repository's own process

    def calibrate(self) -> None:
        pass

    def configure(self) -> None:
        pass

    def connect(self, calibrate: bool = True) -> None:
        if self._arm is not None:
            raise RuntimeError(f"{self} is already connected.")
        log = Path(self.config.log_dir) / f"lerobot-{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}.jsonl"
        arm = PolicyArm(SimulatedScorbot(log_path=log, robot_id=self.config.robot_id),
                        travel_cap_deg=self.config.travel_cap_deg)
        arm.connect()           # unwinds itself on failure: LeRobot calls us outside its cleanup
        self._arm = arm
        try:
            for cam in self.cameras.values():
                cam.connect()
        except BaseException:
            self.disconnect()
            raise

    def get_observation(self) -> dict[str, Any]:
        if not self.is_connected:
            raise ConnectionError(f"{self} is not connected.")
        obs = {m + SUFFIX: deg for m, deg in self._arm.observe().items()}
        for name, cam in self.cameras.items():
            obs[name] = cam.async_read()
        return obs

    def send_action(self, action: dict[str, Any]) -> dict[str, Any]:
        if not self.is_connected:
            raise ConnectionError(f"{self} is not connected.")
        try:
            target = self._arm.act({k.removesuffix(SUFFIX): v for k, v in action.items()})
        except BaseException:
            self.disconnect()
            raise
        return {m + SUFFIX: deg for m, deg in target.items()}

    def disconnect(self) -> None:
        arm, self._arm = self._arm, None
        try:
            for cam in self.cameras.values():
                if cam.is_connected:
                    cam.disconnect()
        finally:
            if arm is not None:
                arm.close()
