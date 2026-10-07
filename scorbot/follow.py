"""Follow LeRobot-style joint targets with the same bounded steps as lab teleop.

``TargetFollower`` turns "be at these counts" into at most one ordinary
``jog_joint`` step per call: the arm joint with the largest error beyond half
a step moves one step toward its target. It keeps the lab's limits (base,
shoulder and elbow only; wrist targets refused; travel cap from home (limits.TRAVEL_CAP_DEG)
counted in steps; drift check) and adds no motion capability. Targets and
observations are encoder counts from the session home, as the exporter
writes them. Used by the simulator-only LeRobot plugin; real-arm replay goes
through ``python -m scorbot.lab`` (key p).

``StreamFollower`` has the same interface on the streaming driver, for a
policy loop: every call just moves the target.
"""

from __future__ import annotations

from . import limits
from .calibration import signed_count_delta

MOTORS = limits.RECORDED_MOTORS
STEP_JOINTS = limits.ARM_MOTORS
TRAVEL_CAP_DEG = limits.TRAVEL_CAP_DEG
DRIFT_COUNTS = limits.DRIFT_COUNTS
WRIST_TOLERANCE_COUNTS = limits.DRIFT_COUNTS


class FollowRefused(Exception):
    """The target cannot be followed within the limits; nothing was queued."""


class TargetFollower:
    def __init__(self, robot, home_raw: dict, *, step_deg: float = 1.0, speed: int = 10):
        self.robot, self.home = robot, dict(home_raw)
        self.step_deg, self.speed = step_deg, speed
        # Counts moved by a positive step, signed: the legacy direction per joint.
        self.positive_counts = {j: robot.preview_jog(j, step_deg)["motor_count_deltas"][j]
                                for j in STEP_JOINTS}
        self.step_counts = {j: abs(c) for j, c in self.positive_counts.items()}
        self.travel = {j: 0.0 for j in STEP_JOINTS}
        self.last_raw = None

    def _current(self):
        raw = self.robot.get_state().encoder_counts
        rel = {m: float(signed_count_delta(raw[m], self.home[m])) for m in MOTORS}
        return raw, rel

    def observe(self) -> dict[str, float]:
        return self._current()[1]

    def step_toward(self, target: dict) -> dict[str, float]:
        """Move at most one joint one step toward ``target``; return what was commanded."""
        raw, current = self._current()
        if self.last_raw is not None:
            moved = {m: signed_count_delta(raw[m], self.last_raw[m]) for m in MOTORS}
            if any(abs(d) > DRIFT_COUNTS for d in moved.values()):
                raise FollowRefused(f"counts drift since the last step: {moved}")
        for motor in MOTORS:
            if motor not in STEP_JOINTS and \
                    abs(float(target[motor]) - current[motor]) > WRIST_TOLERANCE_COUNTS:
                raise FollowRefused(f"{motor} target moves the wrist; wrist motion is disabled")
        errors = {j: float(target[j]) - current[j] for j in STEP_JOINTS}
        moving = [j for j in STEP_JOINTS if abs(errors[j]) > self.step_counts[j] / 2]
        commanded = dict(current)
        if not moving:
            self.last_raw = raw
            return commanded
        joint = max(moving, key=lambda j: abs(errors[j]) / self.step_counts[j])
        direction = 1.0 if (errors[joint] > 0) == (self.positive_counts[joint] > 0) else -1.0
        delta = direction * self.step_deg
        if abs(self.travel[joint] + delta) > TRAVEL_CAP_DEG + 1e-9:
            raise FollowRefused(f"{joint} would pass the {TRAVEL_CAP_DEG:g} degree cap "
                                "from home")
        after = self.robot.jog_joint(joint, delta, speed=self.speed)
        self.travel[joint] += delta
        self.last_raw = dict(after.encoder_counts)
        commanded[joint] = current[joint] + direction * self.positive_counts[joint]
        return commanded


class StreamFollower:
    """The same ``observe`` / ``step_toward`` interface on ``Scorbot.start_stream``.

    ``TargetFollower`` moves one joint one degree per call and waits for it,
    which is right for replaying a keyboard recording and far too slow for a
    policy that sends a new target many times a second. Here ``step_toward``
    only updates the stream's target and returns at once; the streaming
    driver moves all three arm motors toward it together, smoothly, inside
    its own limits (travel cap, lead limit, speed, acceleration, jerk).

    Targets and observations are encoder counts from the session home, as the
    exporter writes them. Wrist targets are refused. Call ``close`` when done.
    Never run on the arm.
    """

    def __init__(self, robot, home_raw: dict, **stream_settings):
        self.robot, self.home = robot, dict(home_raw)
        self.stream = robot.start_stream(**stream_settings)

    def observe(self) -> dict[str, float]:
        raw = self.robot.get_state().encoder_counts
        return {m: float(signed_count_delta(raw[m], self.home[m])) for m in MOTORS}

    def step_toward(self, target: dict) -> dict[str, float]:
        """Make ``target`` the stream's target; return what was commanded."""
        current = self.observe()
        for motor in MOTORS:
            if motor not in STEP_JOINTS and \
                    abs(float(target[motor]) - current[motor]) > WRIST_TOLERANCE_COUNTS:
                raise FollowRefused(f"{motor} target moves the wrist; wrist motion is disabled")
        try:
            self.stream.set_target({j: float(target[j]) for j in STEP_JOINTS})
        except ValueError as exc:      # StreamRefused: outside the cap, or the stream has ended
            raise FollowRefused(str(exc)) from exc
        return {**current, **{j: float(target[j]) for j in STEP_JOINTS}}

    def close(self) -> None:
        """Slow to rest and end the stream. Safe to call twice."""
        stream, self.stream = self.stream, None
        if stream is not None and stream.final_state is None \
                and self.robot._stream is stream:
            stream.close()
