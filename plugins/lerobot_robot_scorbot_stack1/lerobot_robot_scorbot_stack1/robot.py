"""Stack 1 as a LeRobot-style Robot: scorbot.bridge.Arm (vendor DLL path), xyz in mm. Unverified on the arm.

Observation: x.mm, y.mm, z.mm, gripper.pos. Action: dx.mm, dy.mm, dz.mm (a step, not a target), gripper.pos
(0.5 and up opens). Every action passes the safety layer in send_action before the arm sees it; see
docs/requirements/stack1-lerobot-adapter.md (L01 to L12). Works without the `lerobot` package.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from scorbot.bridge import Arm, FakeArm

try:        # lerobot is optional: tests and the demo run without it. Checked against lerobot 0.6.1 by examples/lerobot_install_check.py.
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

MAX_STEP_MM = 50.0      # the server's own cap (tools/usbc_server MAX_STEP_MM); the adapter never allows more
NAME = "scorbot_stack1"
SIM_BOX = {"x": [200.0, 400.0], "y": [-100.0, 100.0], "z": [100.0, 300.0]}     # around the FakeArm start (300, 0, 200); simulate only
AXES = ("x", "y", "z")
ACTION_KEYS = ("dx.mm", "dy.mm", "dz.mm", "gripper.pos")


class ActionRefused(RuntimeError):
    """The safety layer refused this action. Nothing was sent to the arm and the adapter is not latched."""


class AdapterStopped(RuntimeError):
    """The arm raised an error, arm.stop() was called and the adapter is latched until reset()."""


@RobotConfig.register_subclass("scorbot_stack1")
@dataclass
class Stack1RobotConfig(RobotConfig):
    arm_id: str = "arm0"
    host: str = "127.0.0.1"
    port: int = 8765                            # the arm server on this PC (one PC per arm)
    calibration_dir: Path | str = Path("calibration")   # arm.json (the box) lives here; same as bridge --calibration-dir
    allow_real: bool = False                    # must be True to connect to an arm server
    simulate: bool = False                      # connect() builds its own FakeArm, already homed; never an Arm, even with allow_real
    box_mm: dict | None = None                  # {"x": [lo, hi], ...}; overrides calibration_dir/arm.json
    max_step_mm: float = MAX_STEP_MM

    def __post_init__(self):
        super().__post_init__()
        self.calibration_dir = Path(self.calibration_dir)       # LeRobot's Robot.__init__ calls .mkdir() on it
        if not 0 < self.max_step_mm <= MAX_STEP_MM:     # also false for nan
            raise ValueError(f"max_step_mm must be above 0 and at most {MAX_STEP_MM:g}, got {self.max_step_mm}")


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def load_box(config: Stack1RobotConfig) -> dict | None:
    """The calibrated box {"x": [lo, hi], ...} in mm, or None when there is none. With `simulate`, SIM_BOX when there
    is no box_mm and no arm.json at all (a broken arm.json is still None)."""
    box = config.box_mm
    if box is None:
        try:
            box = json.loads((Path(config.calibration_dir) / "arm.json").read_text(encoding="utf-8")).get("box_mm")
        except FileNotFoundError:
            return {axis: list(span) for axis, span in SIM_BOX.items()} if config.simulate else None
        except (OSError, ValueError, AttributeError):
            return None
    try:
        if all(len(box[axis]) == 2 and box[axis][0] <= box[axis][1] and all(_number(v) for v in box[axis]) for axis in AXES):
            return {axis: [float(box[axis][0]), float(box[axis][1])] for axis in AXES}
    except (TypeError, KeyError):
        pass
    return None


class Stack1Robot(Robot):
    config_class = Stack1RobotConfig
    name = NAME

    def __init__(self, config: Stack1RobotConfig):
        if config.simulate:
            self.name = f"{NAME}_sim"       # lerobot-record writes robot.name as the dataset's robot_type: the only place v3 can say "simulated"
        super().__init__(config)
        self.config = config
        self.cameras = {}           # lerobot-record reads len(robot.cameras) on every robot
        self._arm = None
        self._box = None
        self._homed = False
        self._stopped = False
        self._open = False          # last commanded gripper; Arm cannot read it. Vendor Home closes it (unverified)
        self.data_source = None     # "simulated" or "real", set by connect() from the arm, never by the caller

    @property
    def observation_features(self) -> dict:
        return {"x.mm": float, "y.mm": float, "z.mm": float, "gripper.pos": float}

    @property
    def action_features(self) -> dict:
        return {key: float for key in ACTION_KEYS}

    @property
    def is_connected(self) -> bool:
        return self._arm is not None

    @property
    def is_calibrated(self) -> bool:
        return self._box is not None

    def calibrate(self) -> None:
        pass        # the box comes from examples/arm_characterise.py, not from here

    def configure(self) -> None:
        pass

    def connect(self, calibrate: bool = True, arm=None) -> None:
        """Open the arm. `arm` is for tests and the demo (a FakeArm). Without it, config.simulate builds a FakeArm that
        starts homed (lerobot-record never calls home()); otherwise a real arm server is needed and config.allow_real
        must be True."""
        if self._arm is not None:
            raise RuntimeError(f"{self.config.arm_id} is already connected")
        built_homed = False
        if arm is None:
            if self.config.simulate:        # checked before allow_real: simulate never reaches Arm
                arm, built_homed = FakeArm(homed=True), True
                arm.gripper(open=False)     # the adapter's idea of the gripper after a Home
                self._open = False
            elif not self.config.allow_real:
                raise ValueError("refusing to connect to an arm server: set allow_real=True (--allow-real) to drive the real arm")
            else:
                arm = Arm(host=self.config.host, port=self.config.port)
        try:
            simulated = isinstance(arm, FakeArm) or arm.ping().get("simulated") is not False   # "real" only on an explicit false
        except Exception:
            arm.close()
            raise
        self.data_source = "simulated" if simulated else "real"
        self._arm, self._box = arm, load_box(self.config)
        self._homed, self._stopped = built_homed, False

    def disconnect(self) -> None:
        arm, self._arm = self._arm, None
        if arm is not None:
            arm.close()

    def _need_connection(self):
        if self._arm is None:
            raise ConnectionError(f"{self.config.arm_id} is not connected")
        if self._stopped:
            raise AdapterStopped(f"{self.config.arm_id} is latched stopped after an arm error: reset(), then home()")

    def _fail(self, error):
        """Any arm error: ask the arm to stop once, latch, and say so. Stop is not an emergency stop."""
        if not self._stopped:
            self._stopped = True
            try:
                self._arm.stop()
            except Exception:
                pass
        raise AdapterStopped(f"arm error, stop requested, adapter latched (check where; use the red stop if it moves): {error}") from error

    def reset(self) -> None:
        """Leave the latch. The arm may have moved, so a new home() is needed before any move."""
        self._stopped = self._homed = False

    def home(self) -> None:
        """Vendor Home (about 135 s on the lab arm; closes the gripper). Needed once per session before any action."""
        self._need_connection()
        try:
            self._arm.home()
        except Exception as error:
            self._fail(error)
        self._homed, self._open = True, False

    def get_observation(self) -> dict:
        self._need_connection()
        try:
            here = self._arm.where()
        except Exception as error:
            self._fail(error)
        return {"x.mm": float(here.x), "y.mm": float(here.y), "z.mm": float(here.z), "gripper.pos": 1.0 if self._open else 0.0}

    def _check(self, action):
        if not isinstance(action, dict) or set(action) != set(ACTION_KEYS):
            raise ActionRefused(f"action must have exactly the keys {list(ACTION_KEYS)}")
        if not all(_number(action[key]) for key in ACTION_KEYS):
            raise ActionRefused("every action value must be a finite number")
        big = [key for key in ACTION_KEYS[:3] if abs(action[key]) > self.config.max_step_mm]
        if big:
            raise ActionRefused(f"step over {self.config.max_step_mm:g} mm on {', '.join(big)}")
        if not self._homed:
            raise ActionRefused("not homed: call home() first (nothing moves before a Home)")
        if self._box is None:
            raise ActionRefused("no calibrated box (box_mm or calibration_dir/arm.json): run examples/arm_characterise.py")

    def send_action(self, action: dict) -> dict:
        """Run one step. Returns the action as sent (gripper snapped to 0 or 1). Raises ActionRefused (nothing sent)
        or AdapterStopped (the arm failed)."""
        self._need_connection()
        self._check(action)
        step = [float(action[key]) for key in ACTION_KEYS[:3]]
        want_open = action["gripper.pos"] >= 0.5
        try:
            here = self._arm.where()
            outside = [axis for axis, now, d in zip(AXES, (here.x, here.y, here.z), step)
                       if not self._box[axis][0] <= now + d <= self._box[axis][1]]
            if outside:
                raise ActionRefused(f"target outside the calibrated box on {', '.join(outside)}: from "
                                    f"({here.x:.1f}, {here.y:.1f}, {here.z:.1f}) mm by {step}, box {self._box}")
            if any(step):
                self._arm.move_by(*step)
            if want_open != self._open:
                self._arm.gripper(open=want_open)
                self._open = want_open
        except ActionRefused:
            raise
        except Exception as error:
            self._fail(error)
        return {**{key: value for key, value in zip(ACTION_KEYS[:3], step)}, "gripper.pos": 1.0 if want_open else 0.0}
