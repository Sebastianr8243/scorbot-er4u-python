"""ScorBot ER-4U as a LeRobot ``Robot``. Real-arm opt-in is unverified; all motion logic is ``scorbot.policy_arm``.

Observation and action: ``base.pos``, ``shoulder.pos``, ``elbow.pos``, ``pitch.pos``, ``roll.pos`` in
degrees from home (uncalibrated vendor prior; pitch is the gripper's angle to the floor and, with roll,
comes from the two coupled wrist motors through ``scorbot.source_model``), plus ``gripper.pos`` (0 closed
.. 100 open), plus any cameras LeRobot configures
(``type: scorbot_twin`` renders the MuJoCo twin, posed from the simulator; ``sim_cameras.py``). A joint action is an absolute target; ``PolicyArm``
clamps it to 0.5 degree per call and the lead limit, holding frames outside Intelitek's
joint and encoder windows after vendor Home. The gripper is binary
(``PolicyArm.set_gripper``): below 50 closes, 50 or more opens, and a change pauses the stream for the
blocking vendor move, so that frame is slow. Its observation is the measured jaw in percent. The real arm
needs a second explicit opt-in: LeRobot's tools connect outside their cleanup and have no stop key. A real session
registers an exit hook and stops the arm on any error or Ctrl+C inside its calls; the physical stop remains authoritative. Works without the
``lerobot`` package (stand-in base classes), so the normal test run covers it.
"""

import atexit
import math
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
import time
import uuid
from typing import Any

from scorbot import Scorbot, SimulatedScorbot
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
_last_sent: dict[str, float] | None = None     # what the robot last commanded; one arm per process (see commanded_action)
REAL_ARM_REFUSED = ("The LeRobot plugin refuses the real arm unless simulate=false and "
                    "real_arm_confirmed=true. Use simulate=true for rehearsal; the real-arm path is unverified.")


def commanded_action() -> dict[str, float] | None:
    """The action the robot last commanded (``send_action``'s return), or None before the first one or after
    disconnect. ``lerobot-record`` never hands that return back to the teleoperator, so the keyboard teleoperator
    reads it here. One arm per process, as the USB device allows."""
    return None if _last_sent is None else dict(_last_sent)


def _number(key: str, value: Any) -> Any:
    """One action value as a plain float: ints, floats, numpy scalars and 0-d tensors (anything with __float__).
    Not finite, or not a single number: ValueError naming the key. A bool or a non-number passes through
    untouched, so PolicyArm's own checks (hold) are not loosened."""
    if value is None or isinstance(value, bool) or isinstance(value, str) or not hasattr(value, "__float__"):
        return value
    try:
        number = float(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{key} must be a single number, got {value!r}") from e
    if not math.isfinite(number):
        raise ValueError(f"{key} must be a finite number, got {number}")
    return number


@RobotConfig.register_subclass("scorbot")
@dataclass
class ScorbotRobotConfig(RobotConfig):
    simulate: bool = False
    real_arm_confirmed: bool = False  # explicit second opt-in; unverified on the arm
    transport: str = "auto"        # real arm only: Intelitek's driver first, as scripts/lab_day.py
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
        if not config.simulate and not config.real_arm_confirmed:
            raise ValueError(REAL_ARM_REFUSED)
        self.name = SIM_NAME if config.simulate else "scorbot"  # dataset robot_type separates real from simulated
        super().__init__(config)
        self.config = config
        self.cameras = make_cameras_from_configs(config.cameras)
        self._arm: PolicyArm | None = None
        self._scene = None          # the MuJoCo twin, when a camera is a twin camera
        self._exit_hook = False

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
        if not self.config.simulate and not self.config.real_arm_confirmed:
            raise ValueError(REAL_ARM_REFUSED)
        if not (self.config.operator_at_stop and self.config.jaws_empty and self.config.wrist_clear):
            raise ValueError("Confirm an operator is at the physical stop, the gripper jaws are "
                             "empty and the wrist's path is clear first")
        log = Path(self.config.log_dir) / f"lerobot-{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}.jsonl"
        try:
            if self.config.simulate:
                controller = SimulatedScorbot(log_path=log, robot_id=self.config.robot_id)
            else:
                # Before connecting: LeRobot connects outside its own cleanup. A Ctrl+C or error outside
                # our calls ends the program, and this hook stops the arm then (worker threads are daemons).
                # No SIGINT handler: stopping from inside one could cut into a command the main thread is sending.
                atexit.register(self.disconnect)
                self._exit_hook = True
                controller = Scorbot(log_path=log, robot_id=self.config.robot_id, transport=self.config.transport)
            arm = PolicyArm(controller,
                            speed_pct=self.config.speed_pct,
                            step_limit=self.config.step_limit, wrist=True)
            self._arm = arm
            arm.connect(operator_at_stop=self.config.operator_at_stop, jaws_empty=self.config.jaws_empty,
                        wrist_clear=self.config.wrist_clear)    # PolicyArm unwinds a partial connection
            twins = [cam for cam in self.cameras.values() if hasattr(cam, "attach")]
            if twins:
                from .sim_cameras import TwinScene
                self._scene = TwinScene()
                observed = arm.observe(gripper=True)
                self._scene.set_counts(arm.counts_seen, observed["gripper"])
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
        try:
            obs = {m + SUFFIX: value for m, value in self._arm.observe(gripper=True).items()}
            if self._scene is not None:      # images show the pose in the same reading
                self._scene.set_counts(self._arm.counts_seen, obs[GRIPPER_KEY])
            for name, cam in self.cameras.items():
                obs[name] = cam.async_read()
            return obs
        except BaseException:
            self.disconnect()
            raise

    def send_action(self, action: dict[str, Any]) -> dict[str, Any]:
        if not self.is_connected:
            raise ConnectionError(f"{self} is not connected.")
        action = {k: _number(k, v) if k in ACTION_KEYS else v for k, v in action.items()}   # a bad value is the caller's, not a fault
        try:
            # The gripper first: its move restarts the stream, and the joint step then goes to the new one.
            gripper = self._arm.set_gripper(action.get(GRIPPER_KEY))     # missing or not a number: hold
            target = self._arm.act({k.removesuffix(SUFFIX): v for k, v in action.items() if k != GRIPPER_KEY})
        except BaseException:
            self.disconnect()       # a fault in the gripper move leaves the stream ended; close stops the arm
            raise
        global _last_sent
        _last_sent = {**{m + SUFFIX: deg for m, deg in target.items()}, GRIPPER_KEY: gripper}
        return dict(_last_sent)

    def disconnect(self) -> None:
        global _last_sent
        _last_sent = None
        arm, self._arm, self._scene = self._arm, None, None
        hook, self._exit_hook = self._exit_hook, False
        if arm is not None:
            with suppress(BaseException):     # cleanup must not mask the original fault or interrupt
                arm.close()
        if hook:
            with suppress(BaseException):
                atexit.unregister(self.disconnect)
        for cam in self.cameras.values():
            with suppress(BaseException):
                if cam.is_connected:
                    cam.disconnect()
