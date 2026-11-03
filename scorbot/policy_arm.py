"""The arm as a policy sees it: joint degrees from home in, clamped joint targets out.

``clamp`` is the one safety clamp between any policy and the arm (``examples/policy_loop.py`` and
the LeRobot plugin both use it). ``PolicyArm`` is the small connect / observe / act / close loop
around ``Scorbot.start_stream``, so a LeRobot ``Robot`` needs no motion code of its own.
Degrees come from the vendor prior (``preview_jog``), uncalibrated. Never run on the arm.

With ``wrist=True`` it also drives ``pitch`` and ``roll``: the gripper's pitch to the floor and its
roll, in degrees from home, which the two coupled wrist motors set alone (``source_model``: pitch is
half their difference, roll half their sum; the vendor's formula, not measured). Holding pitch while
the shoulder or elbow moves therefore leaves the wrist motors still, as the elbow motor alone sets the
forearm's angle. Each wrist motor carries both, so its speed step, cap and lead are shared.
"""

from __future__ import annotations

from contextlib import ExitStack
import math
from time import monotonic

from . import limits, source_model
from .calibration import signed_count_delta
from .errors import MotionStopped, ScorbotError
from .gripper import GripperMove
from .streaming import DEFAULT_HOLD_TIMEOUT_S, VENDOR_MAX_SPEED_COUNTS_S

MOTORS = limits.ARM_MOTORS
WRIST_JOINTS = ("pitch", "roll")    # source_model's names; PolicyArm(wrist=True) only
JOINTS = MOTORS + WRIST_JOINTS
DEFAULT_TRAVEL_CAP_DEG = 2.0
LEAD_LIMIT_DEG = 2.0            # the SDK's own lead limit; our targets stay inside LEAD_MARGIN_DEG of it
LEAD_MARGIN_DEG = 1.5           # a target never leads the measured position by more than this
STEP_MAX_DEG = 0.5              # largest delta applied in one call
DEFAULT_SPEED_PCT = 25          # percent of the vendor's MaxSpeed (streaming.VENDOR_MAX_SPEED_COUNTS_S)
# ours: "fixed" is STEP_MAX_DEG per call (the default); "speed" is what the vendor's MaxSpeed
# (vendor: ER4AxN.ini MaxSpeed, via streaming) at speed_pct covers since the previous call.
STEP_LIMITS = ("fixed", "speed")


def speed_step_deg(speed_pct, elapsed_s, per_degree):
    """Per motor, the degrees MaxSpeed x speed_pct covers in ``elapsed_s``. Pure.

    The time counts up to the stream's hold timeout (ours, streaming.DEFAULT_HOLD_TIMEOUT_S: after
    that the stream holds anyway); none, negative or non-finite time is no step. Never above
    LEAD_MARGIN_DEG (ours), so never above the stream's lead limit either. One entry per key of
    ``per_degree``.
    """
    dt = min(elapsed_s, DEFAULT_HOLD_TIMEOUT_S) if math.isfinite(elapsed_s) and elapsed_s > 0 else 0.0
    counts = VENDOR_MAX_SPEED_COUNTS_S * speed_pct / 100 * dt
    return {m: min(LEAD_MARGIN_DEG, counts / abs(per_degree[m])) for m in per_degree}


def _check_speed(speed_pct):
    if isinstance(speed_pct, bool) or not isinstance(speed_pct, (int, float)) \
            or not math.isfinite(speed_pct) or not 1 <= speed_pct <= 100:
        raise ValueError(f"speed_pct must be a number from 1 to 100, not {speed_pct!r}")
    return speed_pct


def clamp(delta_deg, target_deg, measured_deg, *, cap_deg, step_max_deg=STEP_MAX_DEG,
          lead_margin_deg=LEAD_MARGIN_DEG):
    """The new commanded target (degrees from home) per motor. Pure.

    Motors are base, shoulder, elbow, pitch and roll. ``step_max_deg`` is one number for every motor
    or a mapping with a finite value of at least 0 for each arm motor and each motor of ``target_deg``
    (else ``ValueError``). Each delta is cut to +-step_max_deg, added to the last commanded target, cut to +-cap_deg from
    home, and kept within lead_margin_deg of the measured position. The step limit always wins:
    when feedback jumps or the last target sits past the cap, the target walks back one step at a
    time (the lead and the overshoot only shrink). Unknown motors, non-finite numbers (delta,
    measured or cap) and missing motors are ignored (the motor holds). A non-finite last target
    raises ``ValueError``: holding it would hand it on. Returns (target, clamped_motors).
    """
    def finite(v):
        return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)

    if isinstance(step_max_deg, dict):
        steps = step_max_deg
        if any(not finite(steps.get(m)) or steps[m] < 0 for m in JOINTS if m in MOTORS or m in target_deg):
            raise ValueError(f"step_max_deg needs a finite step of at least 0 per motor: {steps!r}")
    else:
        steps = dict.fromkeys(JOINTS, step_max_deg)
    out, clamped = dict(target_deg), []
    for motor, asked in (delta_deg or {}).items():
        if motor in JOINTS and not finite(target_deg.get(motor)):
            raise ValueError(f"last target for {motor} is not a finite number: {target_deg.get(motor)!r}")
        if motor not in JOINTS or not all(finite(v) for v in (asked, measured_deg.get(motor), cap_deg)) \
                or cap_deg < 0:
            clamped.append(motor)
            continue
        base, step_max_deg = target_deg[motor], steps[motor]
        step = max(-step_max_deg, min(step_max_deg, asked))
        wanted = max(-cap_deg, min(cap_deg, base + step))
        lo, hi = measured_deg[motor] - lead_margin_deg, measured_deg[motor] + lead_margin_deg
        wanted = max(lo, min(hi, wanted))
        # One allowed range that always holds the last target: one step from it, never further past the cap.
        lo = max(base - step_max_deg, min(-cap_deg, base))
        hi = min(base + step_max_deg, max(cap_deg, base))
        wanted = max(lo, min(hi, wanted))
        if abs(wanted - (target_deg[motor] + asked)) > 1e-9:
            clamped.append(motor)
        out[motor] = wanted
    return out, clamped


def wrist_degrees(counts_from_home) -> dict[str, float]:
    """Pitch (the gripper's angle to the floor) and roll, degrees from home, from the two wrist motors'
    counts from home. ``source_model`` with the arm motors at home, so only the wrist motors count. Pure."""
    angles = source_model.angles_from_counts({**dict.fromkeys(MOTORS, 0), **counts_from_home})
    return {j: angles[j] - source_model.HOME_ANGLES[j] for j in WRIST_JOINTS}


def wrist_counts(pitch_deg: float, roll_deg: float) -> dict[str, int]:
    """The two wrist motors' counts from home for a pitch and roll in degrees from home: the inverse of
    ``wrist_degrees`` to within a count per joint (``source_model`` truncates, as the vendor does). Pure."""
    home = source_model.HOME_ANGLES
    counts = source_model.counts_from_angles({**home, "pitch": home["pitch"] + pitch_deg,
                                              "roll": home["roll"] + roll_deg})
    return {m: counts[m] for m in limits.WRIST_MOTORS}


class PolicyArm:
    """``connect()``, then ``observe()`` and ``act()`` at your own rate, then ``close()``.

    ``robot`` is an unconnected ``Scorbot`` or ``SimulatedScorbot``. ``act`` takes absolute targets
    in degrees from home for any of base, shoulder, elbow (and pitch, roll with ``wrist=True``,
    opt-in: the wrist is coupled and unmeasured), and moves the stream toward them by at
    most ``STEP_MAX_DEG`` per call (``step_limit="fixed"``, the default) or, opt-in with
    ``step_limit="speed"``, by what MaxSpeed at ``speed_pct`` covers since the previous call
    (``speed_step_deg``). Call it at least every 0.5 s or the stream holds in place.
    Needs the ``planning`` extra (Ruckig). ``simulate=False`` runs are the caller's to supervise.
    """

    def __init__(self, robot, *, travel_cap_deg: float = DEFAULT_TRAVEL_CAP_DEG,
                 speed_pct: float = DEFAULT_SPEED_PCT, step_limit: str = "fixed", wrist: bool = False):
        if not isinstance(step_limit, str) or step_limit not in STEP_LIMITS:
            raise ValueError(f"step_limit must be one of {STEP_LIMITS}, not {step_limit!r}")
        if not isinstance(wrist, bool):
            raise ValueError("wrist must be True or False")
        self.robot, self.cap, self.step_limit = robot, travel_cap_deg, step_limit
        self.wrist = wrist
        self.joints = JOINTS if wrist else MOTORS
        self.speed_pct = _check_speed(speed_pct)
        self._stack: ExitStack | None = None
        self._stream = None

    def set_speed(self, speed_pct: float) -> None:
        """Change the speed (percent of the vendor's MaxSpeed, 1 to 100), live if connected."""
        speed_pct = _check_speed(speed_pct)
        if self._stream is not None:
            self._stream.set_speed(speed_pct / 100)
            self._last_act = monotonic()    # the speed step never charges earlier time at the new speed
        self.speed_pct = speed_pct

    @property
    def is_connected(self) -> bool:
        return self._stack is not None

    def _require_connected(self) -> None:
        if self._stack is None:
            raise ConnectionError("PolicyArm is not connected")

    def connect(self, *, operator_at_stop: bool = False, jaws_empty: bool = False,
                wrist_clear: bool = False) -> None:
        """Connect, enable, home with ``Scorbot.vendor_home`` (moves the wrist, closes the gripper), stream.

        The three confirmations go straight to ``vendor_home``; without all three nothing is sent.
        """
        if self._stack is not None:
            raise RuntimeError("already connected")
        if not (operator_at_stop and jaws_empty and wrist_clear):
            raise ValueError("Confirm an operator is at the physical stop, the gripper jaws are "
                             "empty and the wrist's path is clear first")
        with ExitStack() as stack:           # any failure below unwinds what was opened
            stack.enter_context(self.robot)  # connects; leaving disconnects
            self._refuse_teach_mode()        # before enable, and again before home moves the arm
            self.robot.enable()
            stack.callback(self._disable)
            self._refuse_teach_mode()
            self.robot.vendor_home(operator_at_stop=operator_at_stop, jaws_empty=jaws_empty,
                                   wrist_clear=wrist_clear)
            self._home = dict(self.robot.get_state().encoder_counts)
            self._per_degree = {m: self.robot.preview_jog(m, 1.0)["motor_count_deltas"][m]
                                for m in MOTORS}
            # The speed step's counts per degree. A wrist motor moves this much for a degree of
            # pitch and as much again for a degree of roll, so each gets half its speed.
            self._step_per_degree = {**self._per_degree, **{
                j: 2 * source_model.COUNTS_PER_DEGREE["wrist_motor_1"] for j in self.joints[3:]}}
            self._start_stream()
            stack.push(self._exit_stream)
            self.target = dict.fromkeys(self.joints, 0.0)
            self._stack = stack.pop_all()

    def _refuse_teach_mode(self) -> None:
        if self.robot.get_state().teach_mode:
            raise ScorbotError("The teach pendant is in teach mode; switch it to Auto before connecting")

    def _start_stream(self) -> None:
        self._stream = self.robot.start_stream(
            travel_cap_deg=self.cap, lead_limit_deg=LEAD_LIMIT_DEG,
            speed_fraction=self.speed_pct / 100, wrist=self.wrist)
        self._last_act = monotonic()

    def _exit_stream(self, kind, value, traceback):
        # Gripper calls replace the stream; unwind the current one, including on exceptions.
        return self._stream.__exit__(kind, value, traceback)

    def open_gripper(self) -> GripperMove:
        """Stop the joint stream, open with the vendor gripper, then resume streaming.

        Blocking; needs this connection's vendor Home. Returns measured counts and full_travel,
        not a force or object measurement. On failure the stream stays ended; close and reconnect.
        Physical-arm behaviour is unverified.
        """
        return self._move_gripper("open")

    def close_gripper(self) -> GripperMove:
        """Close with the vendor gripper; same blocking checks and result as ``open_gripper``."""
        return self._move_gripper("close")

    def _move_gripper(self, direction: str) -> GripperMove:
        self._require_connected()
        if not (self.robot._homed and self.robot._gripper_homed):
            raise ScorbotError("Home the arm before moving the gripper (vendor_home)")
        state = self._stream.close()
        # Ending a stream consumes the session stop; keep it from starting the jaw instead.
        if self._stream.core._stop_requested:
            raise MotionStopped("A stop was requested; the gripper was not moved", state=state, started=False)
        result = self.robot.move_gripper(direction)
        self._start_stream()
        return result

    def _disable(self) -> None:
        try:
            self.robot.disable()    # not an emergency stop
        except Exception:
            pass                    # the session is already faulted or gone

    def observe(self) -> dict[str, float]:
        """Degrees from home, measured."""
        self._require_connected()
        counts = self.robot._motion_state().encoder_counts     # latches the session if feedback is unavailable
        seen = {m: signed_count_delta(counts[m], self._home[m]) / self._per_degree[m] for m in MOTORS}
        if self.wrist:
            seen.update(wrist_degrees({m: signed_count_delta(counts[m], self._home[m])
                                       for m in limits.WRIST_MOTORS}))
        return seen

    def act(self, goal_deg: dict) -> dict[str, float]:
        """Move toward ``goal_deg`` (absolute degrees from home); return the target now commanded.

        Raises ``StreamRefused`` if the stream has faulted or ended. Bad values hold their motor.
        """
        self._require_connected()
        delta = {m: g - self.target[m] if isinstance(g, (int, float)) and not isinstance(g, bool)
                 else math.nan for m, g in goal_deg.items() if m in self.target}
        measured = self.observe()
        if self.step_limit == "speed":
            now = monotonic()
            step = speed_step_deg(self.speed_pct, now - self._last_act, self._step_per_degree)
            self._last_act = now
            self.target, _ = clamp(delta, self.target, measured, cap_deg=self.cap, step_max_deg=step)
        else:
            self.target, _ = clamp(delta, self.target, measured, cap_deg=self.cap)
        counts = {m: round(self.target[m] * self._per_degree[m]) for m in MOTORS}
        if self.wrist:
            counts.update(wrist_counts(self.target["pitch"], self.target["roll"]))
        self._stream.set_target(counts)
        return dict(self.target)

    def close(self) -> None:
        """Slow to rest, motors off, disconnect. Safe to call twice."""
        stack, self._stack = self._stack, None
        if stack is not None:
            stack.close()
