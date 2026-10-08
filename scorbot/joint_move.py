"""Coupled joint moves: move one joint and let the coupled motors follow.

On the ER-4U the elbow and wrist motors are mechanically coupled to the shoulder, and the
wrist pair to the elbow. The vendor's homing moves all the motors a joint needs together, so
the other joints keep their angles; its manual joint jog drives one motor
(``docs/protocol/VENDOR_MANUAL_MOVE_TRACE.md``). This SDK's jogs move one motor at a time, so a
long shoulder sweep forces the elbow into its stop. This module holds the arithmetic that
fixes that: the motor counts per degree of each joint, taken from the
vendor DLL's own counts-to-angles function (``source_model``), and a ledger that tracks a
cumulative target for a whole run and says which motors to jog next.

The vendor's own coupled vectors overshoot the wrist by about 2x
(``docs/protocol/VENDOR_COUPLING_TRACE.md``); these are the vectors that hold every other
joint angle, including the relative wrist pitch, fixed. From the vendor formulas, **never run
on the arm.** Pure: no USB, no robot, no threads.
"""

from __future__ import annotations

from collections.abc import Callable
import math

from . import source_model
from .calibration import signed_count_delta

MOTORS = source_model.MOTORS             # base, shoulder, elbow, wrist_motor_1, wrist_motor_2
JOINTS = ("base", "shoulder", "elbow")   # the joints a coupled move may be asked for
ARM_MOTORS = ("base", "shoulder", "elbow")

# A step of a coupled move, in degrees of the joint asked for. The legacy jog ceiling is 5.
SLICE_DEG = 1.0
# A legacy jog ends once the motor is within 20 counts of its target, so an error smaller than
# that cannot be corrected by a jog. The joint asked for is jogged on every slice down to this;
# the coupled motors wait for a bigger amount (a small wrist jog is mostly settle noise), and
# are flushed at the end of a move down to FINAL_MIN_COUNTS.
PRIMARY_MIN_COUNTS = 20
COUPLED_MIN_COUNTS = 60
FINAL_MIN_COUNTS = 30

# The most one jog of a coupled move asks of a motor, in counts (about a degree of the joint;
# the wrist pitch jog moves its two motors by this many counts each, opposite ways). Larger
# amounts are split, so the motors are never far from the coupled path. The wrist step is the
# smallest: the legacy wrist loop takes no stop event, so a wrist jog cannot be cut short and
# should end within about two seconds; 60 counts is also the size from which a step is
# progress-checked, and about 1.8 legacy wrist degrees, under the jog ceiling of 2.
STEP_LIMIT_COUNTS = {"base": 150, "shoulder": 120, "elbow": 120, "wrist_pitch": 60}
# A jog this large (in counts) must have moved at least 40 percent of what was asked, or the
# joint is not following (a hard stop, no power): the move stops before the next jog.
PROGRESS_CHECK_COUNTS = 60
# The pre-home move: the largest single step, and the most a joint may be moved in total from
# where the session found it (reset when the motors are disabled or a fault is latched).
PRE_HOME_STEP_DEG = 2.0
PRE_HOME_CAP_DEG = 60.0
# Park passes, each of at most one step of the largest motor.
PARK_MAX_PASSES = 200


class StallError(Exception):
    """A jog of a coupled move did not move its motor: stop before asking for another."""


_VECTORS: dict[str, dict[str, float]] = {}


def vector(joint) -> dict[str, float]:
    """Motor counts per degree when only ``joint``'s angle changes (all other joint angles
    held, including the wrist pitch relative to the forearm). Counts, signed as the
    controller reports them. Derived from the source model by differencing ten degrees
    either side of home, so the vendor's integer truncation costs under 0.1 percent."""
    if joint not in JOINTS:
        raise ValueError(f"A coupled move names one of {JOINTS}, not {joint!r}")
    if joint not in _VECTORS:
        home, step = source_model.HOME_ANGLES, 10.0
        up = source_model.counts_from_angles({**home, joint: home[joint] + step})
        down = source_model.counts_from_angles({**home, joint: home[joint] - step})
        _VECTORS[joint] = {m: (up[m] - down[m]) / (2 * step) for m in MOTORS}
    return dict(_VECTORS[joint])


class CoupledTarget:
    """The cumulative target of one run (a pre-home move, a homing search, a park).

    Counts are held as signed distances from the run's start, taken with
    ``signed_count_delta``, never by subtracting raw counts. Every move adds to the
    target; the jogs are chosen from the difference between the target and where the arm
    is now, so a jog that lands 19 counts short, or a coupled amount that was too small to
    jog, is not lost: it stays in the error until it is worth correcting.
    """

    def __init__(self, start_counts: dict, *, coupled: bool = True):
        """``coupled=False`` asks only for the motor of the joint being moved, as the vendor's
        manual joint jog does (``docs/protocol/VENDOR_MANUAL_MOVE_TRACE.md``): the other joints
        then change with the mechanics, and no motor that was not asked for is ever commanded,
        the wrist included, whatever its reading does."""
        if not isinstance(start_counts, dict) or set(MOTORS) - set(start_counts):
            raise ValueError(f"start_counts needs a count for each of {MOTORS}")
        self.start = {m: start_counts[m] for m in MOTORS}
        self.desired = dict.fromkeys(MOTORS, 0.0)
        self.coupled = bool(coupled)
        self.active = set(MOTORS) if self.coupled else set()    # motors a move has asked for

    def add(self, joint, degrees) -> None:
        """Add ``degrees`` of ``joint`` to the target."""
        if isinstance(degrees, bool) or not isinstance(degrees, (int, float)) \
                or not math.isfinite(degrees):
            raise ValueError("degrees must be a finite number")
        for motor, per_degree in vector(joint).items():
            if self.coupled or motor == joint:
                self.desired[motor] += per_degree * degrees
                self.active.add(motor)

    def errors(self, actual: dict) -> dict[str, float]:
        """Counts still owed to each motor: target minus where the arm is now."""
        return {m: (self.desired[m] - signed_count_delta(actual[m], self.start[m])
                    if m in self.active else 0.0)
                for m in MOTORS}

    def owed(self, actual: dict) -> dict[str, float]:
        """What is still owed, as the four things a jog can move: ``base``, ``shoulder``,
        ``elbow`` and ``wrist_pitch`` (in counts of wrist motor 1; motor 2 the opposite way).
        A roll component (both wrist motors the same way) is dropped."""
        error = self.errors(actual)
        return {"base": error["base"], "shoulder": error["shoulder"], "elbow": error["elbow"],
                "wrist_pitch": (error["wrist_motor_1"] - error["wrist_motor_2"]) / 2}

    def next_jogs(self, actual: dict, *, primary: str | None = None,
                  final: bool = False) -> list[tuple[str, int]]:
        """The jogs to make now, in order, as ``(name, counts)``.

        ``primary`` is the motor of the joint being moved: it is jogged down to the settle
        band. Other arm motors (the elbow following the shoulder) and the wrist, asked for
        as ``"wrist_pitch"`` with the counts of wrist motor 1 (motor 2 moves the opposite
        way), wait for ``COUPLED_MIN_COUNTS``, or ``FINAL_MIN_COUNTS`` when ``final``.
        A roll error (both wrist motors the same way) is never asked for.
        """
        error = self.errors(actual)
        coupled_min = FINAL_MIN_COUNTS if final else COUPLED_MIN_COUNTS
        jogs = []
        if primary is not None and abs(error[primary]) >= PRIMARY_MIN_COUNTS:
            jogs.append((primary, round(error[primary])))
        for motor in ARM_MOTORS:
            if motor != primary and abs(error[motor]) >= coupled_min:
                jogs.append((motor, round(error[motor])))
        pitch = (error["wrist_motor_1"] - error["wrist_motor_2"]) / 2
        if abs(pitch) >= coupled_min:
            jogs.append(("wrist_pitch", round(pitch)))
        return jogs


def run(target: CoupledTarget, read: Callable[[], dict], jog: Callable[[str, int], None], *,
        primary: str | None, final: bool = False, max_passes: int = 3) -> bool:
    """Make the jogs ``target`` asks for until none is left or ``max_passes`` is reached.

    ``read()`` returns the current encoder counts; ``jog(name, counts)`` performs one jog.
    Each pass re-reads the arm, so a jog that landed short is corrected on the next. Returns
    whether nothing was left to do (False: the passes ran out with an error still owed).
    """
    for _ in range(max_passes):
        jogs = target.next_jogs(read(), primary=primary, final=final)
        if not jogs:
            return True
        for name, counts in jogs:
            jog(name, counts)
    return not target.next_jogs(read(), primary=primary, final=final)


def proportional_jogs(owed: dict[str, float], limits: dict[str, int], *,
                      min_counts: int = PRIMARY_MIN_COUNTS) -> list[tuple[str, int]]:
    """One step toward everything owed, with the motors kept in proportion.

    The step is scaled so no motor is asked for more than its limit, and every motor gets the
    same share of what it is owed: a long move then follows the coupled path instead of one
    motor running ahead of the others. Amounts under ``min_counts`` are left owed.
    """
    sizes = [limits[name] / abs(value) for name, value in owed.items() if abs(value) >= 1]
    scale = min([1.0, *sizes])
    jogs = []
    for name, value in owed.items():
        counts = round(value * scale)
        if abs(counts) >= min_counts:
            jogs.append((name, counts))
    return jogs
