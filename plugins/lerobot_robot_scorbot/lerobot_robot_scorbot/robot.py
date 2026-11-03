"""ScorBot ER-4U as a LeRobot ``Robot``. Simulator only; all motion logic is ``scorbot.policy_arm``.

Observation and action: ``base.pos``, ``shoulder.pos``, ``elbow.pos``, ``pitch.pos``, ``roll.pos`` in
degrees from home (uncalibrated vendor prior; pitch is the gripper's angle to the floor and, with roll,
comes from the two coupled wrist motors through ``scorbot.source_model``), plus ``gripper.pos`` (0 closed
.. 100 open), plus any cameras LeRobot configures
(``type: scorbot_twin`` renders the MuJoCo twin, posed from the simulator; ``sim_cameras.py``). A joint action is an absolute target; ``PolicyArm``
clamps it to 0.5 degree per call, the travel cap and the lead limit. The gripper is binary
(``PolicyArm.set_gripper``): below 50 closes, 50 or more opens, and a change pauses the stream for the
blocking vendor move, so that frame is slow. Its observation is the measured jaw in percent. The real arm is
refused: LeRobot's tools connect outside their cleanup and have no stop key. Works without the
``lerobot`` package (stand-in base classes), so the normal test run covers it.
"""

from dataclasses import dataclass, field
from pathlib import Path
import time
import uuid
from typing import Any

from scorbot import SimulatedScorbot
from scorbot.policy_arm import JOINTS, PolicyArm

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
GRIPPER_KEY = "gripper" + SUFFIX
ACTION_KEYS = (*(m + SUFFIX for m in JOINTS), GRIPPER_KEY)
SIM_NAME = "scorbot_sim"        # lerobot-record writes the robot's name as the dataset's robot_type
REAL_ARM_REFUSED = ("The LeRobot plugin is simulator-only (simulate=true). Use python -m scorbot.lab "
                    "on the real arm.")


@RobotConfig.register_subclass("scorbot")
@dataclass
class ScorbotRobotConfig(RobotConfig):
    simulate: bool = False          # must be true: the real arm is refused
    travel_cap_deg: float | None = None  # optional extra bound; vendor windows by default
    speed_pct: float = 25.0         # stream speed, percent of the vendor's MaxSpeed (1 to 100)
    step_limit: str = "fixed"       # "speed": the step per action follows speed_pct (PolicyArm)
    log_dir: Path = Path("rehearsal/lerobot")
    robot_id: str = "lab-er4u-1"
    cameras: dict[str, CameraConfig] = field(default_factory=dict)
    # The vendor Home on connect (moves the wrist, closes the gripper) needs all three, passed as given.
    operator_at_stop: bool = False  # an operator is at the physical stop
    jaws_empty: bool = False        # the gripper jaws are empty
    wrist_clear: bool = False       # the wrist's path is clear


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
        self._scene = None          # the MuJoCo twin, when a camera is a twin camera

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
                        travel_cap_deg=self.config.travel_cap_deg, speed_pct=self.config.speed_pct,
                        step_limit=self.config.step_limit, wrist=True)
        arm.connect(operator_at_stop=self.config.operator_at_stop, jaws_empty=self.config.jaws_empty,
                    wrist_clear=self.config.wrist_clear)    # unwinds itself on failure: LeRobot calls us outside its cleanup
        self._arm = arm
        try:
            twins = [cam for cam in self.cameras.values() if hasattr(cam, "attach")]
            if twins:
                from .sim_cameras import TwinScene
                self._scene = TwinScene()
                arm.observe()
                self._scene.set_counts(arm.counts_seen)
                for cam in twins:
                    cam.attach(self._scene)
            for cam in self.cameras.values():
                cam.connect()
        except BaseException:
            self.disconnect()
            raise

    def get_observation(self) -> dict[str, Any]:
        if not self.is_connected:
            raise ConnectionError(f"{self} is not connected.")
        obs = {m + SUFFIX: value for m, value in self._arm.observe(gripper=True).items()}
        if self._scene is not None:      # the reading observe() just used: images show the pose in the numbers
            self._scene.set_counts(self._arm.counts_seen)
        for name, cam in self.cameras.items():
            obs[name] = cam.async_read()
        return obs

    def send_action(self, action: dict[str, Any]) -> dict[str, Any]:
        if not self.is_connected:
            raise ConnectionError(f"{self} is not connected.")
        try:
            # The gripper first: its move restarts the stream, and the joint step then goes to the new one.
            gripper = self._arm.set_gripper(action.get(GRIPPER_KEY))     # missing or not a number: hold
            target = self._arm.act({k.removesuffix(SUFFIX): v for k, v in action.items() if k != GRIPPER_KEY})
        except BaseException:
            self.disconnect()       # a fault in the gripper move leaves the stream ended; close stops the arm
            raise
        return {**{m + SUFFIX: deg for m, deg in target.items()}, GRIPPER_KEY: gripper}

    def disconnect(self) -> None:
        arm, self._arm, self._scene = self._arm, None, None
        try:
            for cam in self.cameras.values():
                if cam.is_connected:
                    cam.disconnect()
        finally:
            if arm is not None:
                arm.close()
