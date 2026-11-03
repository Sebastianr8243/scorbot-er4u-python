"""The arm as a policy sees it: joint degrees from home in, clamped joint targets out.

``clamp`` is the one safety clamp between any policy and the arm (``examples/policy_loop.py`` and
the LeRobot plugin both use it). ``PolicyArm`` is the small connect / observe / act / close loop
around ``Scorbot.start_stream``, so a LeRobot ``Robot`` needs no motion code of its own.
Degrees come from the vendor prior (``preview_jog``), uncalibrated. Never run on the arm.

With ``wrist=True`` it also drives ``pitch`` and ``roll``: the gripper's pitch to the floor and its
roll, in degrees from home, which the two coupled wrist motors set alone (``source_model``: pitch is
half their difference, roll half their sum; the vendor's formula, not measured). Holding pitch while
the shoulder or elbow moves therefore leaves the wrist motors still, as the elbow motor alone sets the
forearm's angle. Each wrist motor carries both, so its speed step and lead are shared.
Travel is guarded by Intelitek's encoder and coupled joint windows after this connection's
vendor Home; zero and signs on our arm are unverified until lab G/X.
"""

from __future__ import annotations

from contextlib import ExitStack
import math
from time import monotonic

from . import limits, nominal, source_model, vendor_gripper
from .calibration import signed_count_delta
from .errors import MotionStopped, ScorbotError
from .gripper import GripperMove
from .streaming import DEFAULT_HOLD_TIMEOUT_S, DEFAULT_PERIOD_S, VENDOR_MAX_SPEED_COUNTS_S

MOTORS = limits.ARM_MOTORS
WRIST_JOINTS = ("pitch", "roll")    # source_model's names; PolicyArm(wrist=True) only
JOINTS = MOTORS + WRIST_JOINTS
LEAD_LIMIT_DEG = 2.0            # the SDK's own lead limit; our targets stay inside LEAD_MARGIN_DEG of it
LEAD_MARGIN_DEG = 1.5           # a target never leads the measured position by more than this
STEP_MAX_DEG = 0.5              # largest delta applied in one call
# ours: act() reuses the preceding observe() only this soon after it (two stream periods); older
# (a slow policy between them) and act() reads fresh, so the lead clamp never uses an old position.
MAX_REUSE_AGE_S = 2 * DEFAULT_PERIOD_S
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


def clamp(delta_deg, target_deg, measured_deg, *, cap_deg=None, step_max_deg=STEP_MAX_DEG,
          lead_margin_deg=LEAD_MARGIN_DEG, count_guard=None):
    """The new commanded target (degrees from home) per motor. Pure.

    Motors are base, shoulder, elbow, pitch and roll. ``step_max_deg`` is one number for every motor
    or a mapping with a finite value of at least 0 for each arm motor and each motor of ``target_deg``
    (else ``ValueError``). Each delta is cut to +-step_max_deg, added to the last commanded target, cut to +-cap_deg from
    home, and kept within lead_margin_deg of the measured position. The step limit always wins:
    when feedback jumps or the last target sits past the cap, the target walks back one step at a
    time (the lead and the overshoot only shrink). Unknown motors, non-finite numbers (delta,
    measured or cap) and missing motors are ignored (the motor holds). A non-finite last target
    raises ``ValueError``: holding it would hand it on. ``cap_deg`` is only for legacy
    callers; PolicyArm has no degree cap. Its ``count_guard`` refuses the whole frame
    if either vendor window rejects it, reporting every changed joint as clamped.
    Returns (target, clamped_motors).
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
        if motor not in JOINTS or not all(finite(v) for v in (asked, measured_deg.get(motor))) \
                or (cap_deg is not None and (not finite(cap_deg) or cap_deg < 0)):
            clamped.append(motor)
            continue
        base, step_max_deg = target_deg[motor], steps[motor]
        step = max(-step_max_deg, min(step_max_deg, asked))
        cap = math.inf if cap_deg is None else cap_deg
        wanted = max(-cap, min(cap, base + step))
        lo, hi = measured_deg[motor] - lead_margin_deg, measured_deg[motor] + lead_margin_deg
        wanted = max(lo, min(hi, wanted))
        # One allowed range that always holds the last target: one step from it, never further past the cap.
        lo = max(base - step_max_deg, min(-cap, base))
        hi = min(base + step_max_deg, max(cap, base))
        wanted = max(lo, min(hi, wanted))
        if abs(wanted - (target_deg[motor] + asked)) > 1e-9:
            clamped.append(motor)
        out[motor] = wanted
    if count_guard is not None and count_guard(out):
        clamped.extend(m for m in out if out[m] != target_deg[m] and m not in clamped)
        return dict(target_deg), clamped
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

    def __init__(self, robot, *, speed_pct: float = DEFAULT_SPEED_PCT,
                 step_limit: str = "fixed", wrist: bool = False):
        if not isinstance(step_limit, str) or step_limit not in STEP_LIMITS:
            raise ValueError(f"step_limit must be one of {STEP_LIMITS}, not {step_limit!r}")
        if not isinstance(wrist, bool):
            raise ValueError("wrist must be True or False")
        self.robot, self.step_limit = robot, step_limit
        self.wrist = wrist
        self.joints = JOINTS if wrist else MOTORS
        self.speed_pct = _check_speed(speed_pct)
        self._stack: ExitStack | None = None
        self._stream = None
        self._seen: dict[str, float] | None = None    # the last observe(), until one act() uses it
        self._seen_at = 0.0

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
            self.gripper = 0.0               # vendor Home closes the jaw
            self._stack = stack.pop_all()

    def _refuse_teach_mode(self) -> None:
        if self.robot.get_state().teach_mode:
            raise ScorbotError("The teach pendant is in teach mode; switch it to Auto before connecting")

    def _start_stream(self) -> None:
        self._stream = self.robot.start_stream(
            travel_cap_deg=None, lead_limit_deg=LEAD_LIMIT_DEG,
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

    def set_gripper(self, pct) -> float:
        """Binary jaw from a 0 (closed) .. 100 (open) request; return the value applied (0.0 or 100.0).

        Below 50 closes, 50 or more opens. Unchanged, or not a finite number: nothing moves (hold).
        A change is a blocking ``open_gripper``/``close_gripper`` (stream paused and restarted), so the
        caller's loop stalls for the move. Compared with the last value applied, not the measured jaw: a
        jaw stopped by an object is not re-commanded every call. Physical-arm behaviour is unverified.
        """
        self._require_connected()
        if isinstance(pct, bool) or not isinstance(pct, (int, float)) or not math.isfinite(pct):
            return self.gripper
        want = 100.0 if pct >= 50 else 0.0
        if want != self.gripper:
            self._move_gripper("open" if want else "close")
        return self.gripper

    def _move_gripper(self, direction: str) -> GripperMove:
        self._require_connected()
        if not (self.robot._homed and self.robot._gripper_homed):
            raise ScorbotError("Home the arm before moving the gripper (vendor_home)")
        state = self._stream.close()
        # Ending a stream consumes the session stop; keep it from starting the jaw instead.
        if self._stream.core._stop_requested:
            raise MotionStopped("A stop was requested; the gripper was not moved", state=state, started=False)
        result = self.robot.move_gripper(direction)
        self.gripper = 100.0 if direction == "open" else 0.0     # open_gripper/close_gripper too
        self._start_stream()
        self._seen = None       # a reading from before the move is stale; act() must read again
        return result

    def _disable(self) -> None:
        try:
            self.robot.disable()    # not an emergency stop
        except Exception:
            pass                    # the session is already faulted or gone

    def observe(self, *, gripper: bool = False) -> dict[str, float]:
        """Degrees from home, measured. ``gripper=True`` adds ``"gripper"``: the jaw in percent open,
        ``100 x count / RangeEnc`` (``GetJaw``: 2857 counts is 57 %), from the same reading.

        Each call waits for a controller reply newer than the last read. The next ``act()`` clamps
        against this reading instead of waiting for another one, if it comes within
        ``MAX_REUSE_AGE_S`` of the controller reply.
        """
        self._require_connected()
        self._seen = None
        state = self.robot._motion_state()      # latches the session if feedback is unavailable
        counts = state.encoder_counts
        seen = {m: signed_count_delta(counts[m], self._home[m]) / self._per_degree[m] for m in MOTORS}
        if self.wrist:
            seen.update(wrist_degrees({m: signed_count_delta(counts[m], self._home[m])
                                       for m in limits.WRIST_MOTORS}))
        arrived = state.host_monotonic_ns     # when the reply arrived; none and act() never reuses it
        self._seen, self._seen_at = dict(seen), -math.inf if arrived is None else arrived / 1e9
        if gripper:
            seen["gripper"] = 100 * state.signed_encoder_counts["gripper"] / vendor_gripper.RANGE_ENC
        return seen

    def act(self, goal_deg: dict) -> dict[str, float]:
        """Move toward ``goal_deg`` (absolute degrees from home); return the target now commanded.

        Raises ``StreamRefused`` if the stream has faulted or ended. Bad values hold their motor.
        Clamps against the reading of the ``observe()`` just before it, used once and only within
        ``MAX_REUSE_AGE_S``; otherwise it reads a fresh one. One controller wait per frame when act
        follows observe promptly.
        """
        self._require_connected()
        delta = {m: g - self.target[m] if isinstance(g, (int, float)) and not isinstance(g, bool)
                 else math.nan for m, g in goal_deg.items() if m in self.target}
        measured, self._seen = self._seen, None
        if measured is None or monotonic() - self._seen_at > MAX_REUSE_AGE_S:
            self.observe()
            measured, self._seen = self._seen, None
        elif self.robot._fault:      # latched since that reading: refuse as a fresh read would
            raise ScorbotError(f"Controller is faulted: {self.robot._fault}")
        if not (self.robot._homed and self.robot._gripper_homed):
            raise ScorbotError("Home the arm before acting (vendor_home)")
        if self.step_limit == "speed":
            now = monotonic()
            step = speed_step_deg(self.speed_pct, now - self._last_act, self._step_per_degree)
            self._last_act = now
            target, _ = clamp(delta, self.target, measured, count_guard=self._vendor_target_problem,
                              step_max_deg=step)
        else:
            target, _ = clamp(delta, self.target, measured, count_guard=self._vendor_target_problem)
        counts = self._target_counts(target)
        self._stream.set_target(counts)
        self.target = target
        return dict(self.target)

    def _target_counts(self, target):
        counts = {m: round(target[m] * self._per_degree[m]) for m in MOTORS}
        if self.wrist:
            counts.update(wrist_counts(target["pitch"], target["roll"]))
        return counts

    def _vendor_target_problem(self, target):
        delta = self._target_counts(target)
        return nominal.vendor_limit_problem({
            m: signed_count_delta(self._home[m], 0) + delta.get(m, 0)
            for m in limits.RECORDED_MOTORS})

    def close(self) -> None:
        """Slow to rest, motors off, disconnect. Safe to call twice."""
        stack, self._stack, self._seen = self._stack, None, None
        if stack is not None:
            stack.close()
