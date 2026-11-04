"""The lab USBC.dll's ``MoveJoint`` setpoints. Never imports ``usb``; only ``Scorbot.move_joint_vendor`` opts in.

What the DLL does, read from the lab build (sha256 ``6118465d...``) and recorded on the pipe fake (LEDGER, this PR):
a joint move is one ``VendorProfile`` S-curve, in the DLL's joint radians, from the joints it last commanded to the
target. Every reply moves the planner clock on by one fixed tick (``TICK_SECONDS``), the clock is a running sum
starting at 0, and each message carries ``joints_to_counts(start + s * (target - start))`` in slots 0-4. Once the
clock passes the duration, the next message carries the target's counts and the setpoints then hold. Nothing else
is sent: no mode, drive or end command. For all 26 recorded moves (``tests/fixtures/dll_lab_move_joint.json``),
single and multi-axis, at speeds 1 to 100 and with ``Time``, ``setpoints`` gives every message from the first change
of a setpoint to the last, repeats included. The messages before the first change and after the last hold the
start and target counts, so the wire cannot show where a move begins or ends: the lead and the message count are
this model's, read from the decompile, not measured.

The duration is in whole milliseconds. ``duration_ms`` is the DLL's joint-move duration routine (``FUN_10010896``,
LEDGER PL97): a MaxSpeed term at the speed factor 0.3 + 0.007 x percent (0.5 before any ``Speed`` call), then the
acceleration check (``accel_bound_ms``, PL56). It gives every recorded duration in the fixture. ``Time(group, ms)``
sets the duration directly, and the DLL refuses (909) a time below ``duration_ms`` at factor 1.0
(``minimum_ms``). Desk only: nothing here was run on the arm, and the fake's message pacing says nothing about
the controller's.
"""

from math import sqrt, trunc

from . import limits, nominal, source_model, vendor_home as vh, vendor_model
from .vendor_profile import DEFAULT_ACCEL_FRACTION, DEFAULT_JERK_FRACTION, VendorProfile

TICK_SECONDS = 24.0 / 1000
"""The planner tick, ``PCPeriod`` x ``USBCPeriod`` = 16 x 1.5 ms, added to the move clock once per message."""
MILLIDEGREE_RADIANS = 1.7453292519943296e-05
"""The DLL's own constant: ``SetJoints`` multiplies its millidegree values by this to get its joint radians."""
MAX_ACCEL_COUNTS_S2 = 11000
"""``ER4AxN.ini`` ``MaxAccel``, the same for the five arm motors (PL56)."""
ACCEL_CHECK_FACTOR = 1.5
"""The DLL's margin in its acceleration check and duration terms (PL55, PL56)."""
MAX_SPEED_COUNTS_S = 6500
"""``ER4AxN.ini`` ``MaxSpeed``, the same for the five arm motors; counts per second (PL55)."""
DEFAULT_SPEED_FACTOR = 0.5
"""The speed factor before any ``Speed`` call, from the DLL's settings constructor ``FUN_1001fc70`` (PL97)."""
SCALE_NUMERATOR = 0x7FFFFFF5
"""The DLL scales its per-axis integer speed terms by trunc(this / largest term) before dividing by MaxSpeed."""


def joints_from_millidegrees(values) -> tuple:
    """The DLL's joint radians for five ``SetJoints`` / ``GetCurrentPosition`` millidegree values (base, shoulder,
    elbow, pitch, roll)."""
    values = tuple(values)
    if len(values) != 5:
        raise ValueError("Five joint values are required: base, shoulder, elbow, pitch, roll")
    return tuple(value * MILLIDEGREE_RADIANS for value in values)


def home_joints() -> tuple:
    """The DLL's joints after a finished ``Home('A')`` (all counts zero): the start of the first move."""
    return tuple(vendor_model.counts_to_joints([0] * 5).values())


def _counts(joints) -> tuple:
    return tuple(vendor_model.joints_to_counts(dict(zip(vendor_model.JOINTS, joints))).values())


def accel_bound_ms(start, target, *, accel=DEFAULT_ACCEL_FRACTION, jerk=DEFAULT_JERK_FRACTION) -> int:
    """The duration the DLL's acceleration check gives a move, in ms: from the largest motor count change D,
    trunc(1000 x sqrt(1.5 / ((1 - J)(1 - A) x (MaxAccel / D) x A)) + 1). 0 when no motor moves."""
    distance = max(abs(b - a) for a, b in zip(_counts(start), _counts(target)))
    if distance == 0:
        return 0
    ratio = float(MAX_ACCEL_COUNTS_S2) / float(distance)
    seconds = sqrt(ACCEL_CHECK_FACTOR / ((1.0 - jerk) * (1.0 - accel) * ratio * accel))
    return trunc(1000.0 * seconds + 1.0)


def speed_factor(speed_percent) -> float:
    """The DLL's speed factor for ``Speed(group, percent)``: 0.3 + 0.007 x percent, percent 1 to 100."""
    if speed_percent is None:
        return DEFAULT_SPEED_FACTOR
    if isinstance(speed_percent, bool) or not isinstance(speed_percent, (int, float)) \
            or not 1 <= speed_percent <= 100:
        raise ValueError("speed_percent must be 1 to 100 (the DLL refuses below 1 with 586, above 100 with 913)")
    return speed_percent * ((1.0 - 0.3) / 100.0) + 0.3


def _duration_at(start, target, factor, accel, jerk) -> int:
    distance = [abs(b - a) for a, b in zip(_counts(start), _counts(target))]
    terms = [trunc(ACCEL_CHECK_FACTOR / ((1.0 - accel) * factor) * d) for d in distance]
    largest = max(terms)
    if largest == 0:
        return 0
    scale = float(SCALE_NUMERATOR // largest)
    seconds = max(trunc(scale * t) // MAX_SPEED_COUNTS_S for t in terms) / scale + 0.001
    peak = ACCEL_CHECK_FACTOR / ((1.0 - jerk) * (1.0 - accel) * seconds * seconds * accel)
    if any(trunc(peak * d) > MAX_ACCEL_COUNTS_S2 for d in distance):
        ratio = float(MAX_ACCEL_COUNTS_S2) / float(max(distance))
        seconds = sqrt(ACCEL_CHECK_FACTOR / ((1.0 - jerk) * (1.0 - accel) * ratio * accel))
    return trunc(1000.0 * seconds + 1.0)


def duration_ms(start, target, *, speed_percent=None, accel=DEFAULT_ACCEL_FRACTION,
                jerk=DEFAULT_JERK_FRACTION) -> int:
    """The duration the DLL's ``MoveJoint`` gives a move in speed mode, in ms (``FUN_10010896``, PL97): per motor
    v = trunc(1.5 / ((1 - A) f) x D), scaled by c = trunc(0x7ffffff5 / max v), T = max(trunc(c v) // MaxSpeed) / c
    + 0.001 s, then lengthened by the acceleration check. ``speed_percent=None`` is the DLL's default factor 0.5.
    0 when no motor moves."""
    return _duration_at(start, target, speed_factor(speed_percent), accel, jerk)


def minimum_ms(start, target, *, accel=DEFAULT_ACCEL_FRACTION, jerk=DEFAULT_JERK_FRACTION) -> int:
    """The shortest ``Time`` the DLL accepts for a move: ``duration_ms`` at factor 1.0; shorter is refused (909)."""
    return _duration_at(start, target, 1.0, accel, jerk)


def setpoints(start, target, total_ms, *, accel=DEFAULT_ACCEL_FRACTION, jerk=DEFAULT_JERK_FRACTION) -> list:
    """Slots 0-4 (motor counts) of every message of one move, from its first tick (clock 0) to the message that
    carries the target. ``start`` and ``target`` are the DLL's joint radians; ``start`` is the previous move's
    target (``home_joints()`` after a Home), not where the encoders read. The setpoints hold after the last one."""
    if isinstance(total_ms, bool) or not isinstance(total_ms, int) or total_ms < 1:
        raise ValueError("total_ms must be a whole number of milliseconds, at least 1")
    start, target = tuple(start), tuple(target)
    if len(start) != 5 or len(target) != 5:
        raise ValueError("start and target need five joints: base, shoulder, elbow, pitch, roll")
    profile = VendorProfile(total_ms / 1000.0, accel, jerk)
    out, clock = [], 0.0
    while True:
        s = profile.sample(clock).position
        out.append(_counts(a + s * (b - a) for a, b in zip(start, target)))
        clock += TICK_SECONDS
        if profile.total_time < clock:
            break
    out.append(_counts(target))
    return out


def run_move(first, ramp, *, lead_limit_deg):
    """Feed a validated profile to ``VendorHomeSource``; return whether a stop ended it early.

    Lead and whole-pose checks are Stack 2's, not decoded DLL behavior. A stop holds the measured arm
    counts and switches its control and motors off, using the same abort path as the post-Home gripper.
    """
    run = vh._Run(first, 0)
    try:
        for counts in ramp:
            if run.reply.emergency:
                raise vh.HomeError(vh.ERR_EMERGENCY, "emergency input active")
            run.check_impact(0)
            problem = nominal.vendor_limit_problem(dict(zip(limits.RECORDED_MOTORS, counts)))
            if problem:
                raise vh.HomeError(vh.ERR_TRAVEL, f"vendor limit (USBC.dll EncLimit / ROB_4u.INI), not measured: {problem}")
            lead = [abs(a - b) / source_model.COUNTS_PER_DEGREE[m]
                    for m, a, b in zip(limits.RECORDED_MOTORS, counts, run.reply.counts)]
            if any(value > lead_limit_deg for value in lead[:3]) or sum(lead[3:]) > 2 * lead_limit_deg:
                raise vh.HomeError(vh.ERR_TRAVEL, "the arm is not following: the next command leads too far")
            run.setp = [*counts, 0, 0, 0]
            yield from run.cycle()
    except vh.HomeError as error:
        run.aborting = True
        run.setp = [*run.reply.counts[:5], 0, 0, 0]
        yield from run.send(vh.CONTROL_OFF, 0x1F)
        alarm = run.reply.emergency
        yield from run.send(vh.MOTORS, 0x1F)
        alarm = alarm or run.reply.emergency
        if error.code != vh.ERR_STOPPED:
            raise
        if alarm:
            raise vh.HomeError(vh.ERR_EMERGENCY, "emergency input active while the arm stopped") from error
        return True
    return False
