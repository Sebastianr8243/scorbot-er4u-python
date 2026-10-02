"""ScorBot as a LeRobot ``Robot``, on the simulated controller only.

Observations and actions are encoder counts from the session home for base,
shoulder, elbow and the two wrist motors, as the exporter writes them. Each
``send_action`` is at most one ordinary 1 degree jog through
``scorbot.follow.TargetFollower`` (lab limits: base, shoulder and elbow only,
10 degree cap, drift check), so replay runs at the arm's pace.
"""

from __future__ import annotations

from pathlib import Path
import time

from lerobot.robots.robot import Robot

from scorbot.follow import MOTORS, TargetFollower

from .config_scorbot import ScorbotRobotConfig

REAL_ARM_REFUSED = ("The LeRobot plugin is simulator-only (simulate=true). Replay on the "
                    "real arm with python -m scorbot.lab (key p), which runs the lab "
                    "checks, typed confirmations and stop-on-key.")


class ScorbotRobot(Robot):
    config_class = ScorbotRobotConfig
    name = "scorbot"

    def __init__(self, config: ScorbotRobotConfig):
        if not config.simulate:
            raise ValueError(REAL_ARM_REFUSED)
        super().__init__(config)
        self.config = config
        self._robot = None
        self._follower: TargetFollower | None = None

    @property
    def observation_features(self) -> dict:
        return {motor: float for motor in MOTORS}

    @property
    def action_features(self) -> dict:
        return {motor: float for motor in MOTORS}

    @property
    def is_connected(self) -> bool:
        return self._robot is not None

    @property
    def is_calibrated(self) -> bool:
        return True   # calibration is this repository's own physical process (gate G2)

    def calibrate(self) -> None:
        pass

    def configure(self) -> None:
        pass

    def connect(self, calibrate: bool = True) -> None:
        from scorbot.simulated import SimulatedScorbot
        log = Path(self.config.log_dir) / f"replay-{time.strftime('%Y%m%dT%H%M%S')}.jsonl"
        robot = SimulatedScorbot(log_path=log, robot_id=self.config.robot_id).connect()
        try:
            robot.enable()
            robot.home(start_position_confirmed=True)
            home = robot.get_state().encoder_counts
            self._follower = TargetFollower(robot, home, step_deg=self.config.step_deg,
                                            speed=self.config.speed)
        except BaseException:
            # lerobot-replay calls connect() outside its cleanup block: clean up here.
            self._shutdown(robot)
            raise
        self._robot = robot

    def get_observation(self) -> dict:
        return self._follower.observe()

    def send_action(self, action: dict) -> dict:
        try:
            return self._follower.step_toward(action)
        except BaseException:
            self.disconnect()
            raise

    def disconnect(self) -> None:
        robot, self._robot, self._follower = self._robot, None, None
        if robot is not None:
            self._shutdown(robot)

    @staticmethod
    def _shutdown(robot) -> None:
        try:
            robot.disable()   # not an emergency stop; the simulator has nothing to stop
        except Exception:
            pass
        robot.disconnect()
