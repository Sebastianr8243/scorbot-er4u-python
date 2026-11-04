"""The vendor's gripper move after a finished Home, as a pure generator. Never imports ``usb``.

After a ``Home('A')`` that finished, the DLL's ``OpenGripper`` and ``CloseGripper`` are position moves to
``RangeEnc`` and 0 counts (``docs/protocol/LEDGER.md`` PL46, PL48). On the wire (PL51): ``4F 20 53`` (mode S),
``4C 20 00`` (slave), ``42 20 01`` (gripper motor on), ``0D`` setpoints ramping only the gripper slot in an
S-curve (40 for a full stroke), ``73 20`` (control off) and ``42 20 00`` (motor off). Nothing is sent when the
gripper is already there. ``JawMetric(mm)`` is the same move to ``RangeEnc`` x mm / ``RangeMM`` (PL71).

``vendor_gripper(first_reply, target)`` yields ``vendor_home.Out`` messages the way ``vendor_home`` does, so the
same ``VendorHomeSource`` and legacy runner carry it (order ``GRIPPER_ORDER``). The arm slots hold the first
reply's counts on every message. The ramp is the DLL's joint-move planner (LEDGER PL100): the duration routine at
speed factor 1.0 with the gripper's own ``MaxSpeed`` and ``MaxAccel``, then ``VendorProfile`` with the INI's A and
J of 0.3, one 24 ms tick per message from ``4F 20 53`` on. That gives every setpoint of the nine recorded moves
exactly (``tests/fixtures/dll_lab_gripper.json``, ``dll_lab_jaw.json``), full strokes and short moves.

A stop thrown in (``HomeError`` ``ERR_STOPPED``) holds the gripper where it is, sends ``73 20`` and ``42 20 00``
and returns ``stopped``. Any other ``HomeError`` sends the same two messages and is raised again, so the runner
switches the motors off. Not traced, so a lab check: whether the jaw arrives before ``73 20`` (the DLL's end
flag ``+0x4a4``). Counts are the controller's (``raw24 - 0x7FFFFF``), as in ``vendor_home``.
"""

from dataclasses import dataclass
from math import sqrt, trunc

from . import vendor_home as vh
from .vendor_profile import DEFAULT_ACCEL_FRACTION, DEFAULT_JERK_FRACTION, VendorProfile

GRIPPER_ORDER = 23
"""Command code queued to the legacy worker; it runs ``libcomm.vendor_home_run`` like order 22."""
RANGE_ENC = 5000
"""``ROB_4u.ini`` ``RangeEnc``: gripper counts at full opening."""
RANGE_MM = 70
"""``ROB_4u.ini`` ``RangeMM``: full opening in millimetres."""
MAX_SPEED_COUNTS_S = 15000
"""``ER4Ax6.ini`` ``MaxSpeed``, the gripper motor's (PL100)."""
MAX_ACCEL_COUNTS_S2 = 50000
"""``ER4Ax6.ini`` ``MaxAccel``, the gripper motor's; it sets every recorded gripper duration (PL56, PL100)."""
SPEED_FACTOR = 1.0
"""``JawMetric`` stores 1.0 as the speed factor before it plans (``0x10018ba0``, PL100)."""
CHECK_FACTOR = 1.5
"""The DLL's 1.5 in its duration terms and acceleration check (PL55, PL56), as in ``vendor_move``."""
SCALE_NUMERATOR = 0x7FFFFFF5
"""The DLL scales its integer speed term by trunc(this / term) before dividing by MaxSpeed (PL97)."""
TICK_SECONDS = 0.024
"""The DLL planner's tick, ``PCPeriod`` x ``USBCPeriod``: a full stroke is 1011 ms, 43 ticks, on the vendor.
Stack 2 sends one setpoint per message, so its own pacing sets the real duration."""
AT_TARGET_COUNTS = 50
"""Ours: within 1 % of ``RANGE_ENC`` counts as already there. The DLL tests a whole percent; its rounding was not traced."""
TOTAL_SECONDS = 30.0
"""Ours: bound on one gripper move, about 45 messages."""


def target_counts(direction: str) -> int:
    """``JawMetric``'s target: ``RangeEnc`` x mm / ``RangeMM``, with 0 mm to close and ``RangeMM`` to open."""
    if direction not in ("open", "close"):
        raise ValueError('Gripper direction must be "open" or "close"')
    return metric_target_counts(RANGE_MM if direction == "open" else 0)


def metric_target_counts(mm: int) -> int:
    """``JawMetric(mm)``'s target: a whole number of mm from 0 to ``RangeMM`` (the DLL takes a short and accepts
    ``mm < RangeMM + 0.0001``, ``0x10018ba0``). 40 mm gave 2857 counts and 10 mm 714 on the desk (PL71)."""
    if isinstance(mm, bool) or not isinstance(mm, int) or not 0 <= mm <= RANGE_MM:
        raise ValueError(f"Jaw width must be a whole number of mm from 0 to {RANGE_MM}, not {mm!r}")
    return RANGE_ENC * mm // RANGE_MM


def duration_ms(distance: int) -> int:
    """The DLL's duration for a gripper move of ``distance`` counts, in ms (PL100): 1011 for a full stroke.
    The joint-move routine ``FUN_10010896`` (PL97, ``vendor_move.duration_ms``, not imported here: that module
    is opt-in) for one motor: v = trunc(1.5 / ((1 - A) f) x D), T = trunc(c v) // MaxSpeed / c + 0.001 s with
    c = trunc(0x7ffffff5 / v), lengthened by the acceleration check, returned as trunc(1000 T + 1)."""
    accel, jerk, distance = DEFAULT_ACCEL_FRACTION, DEFAULT_JERK_FRACTION, abs(distance)
    term = trunc(CHECK_FACTOR / ((1.0 - accel) * SPEED_FACTOR) * distance)
    if term == 0:
        return 0
    scale = float(SCALE_NUMERATOR // term)
    seconds = trunc(scale * term) // MAX_SPEED_COUNTS_S / scale + 0.001
    peak = CHECK_FACTOR / ((1.0 - jerk) * (1.0 - accel) * seconds * seconds * accel)
    if trunc(peak * distance) > MAX_ACCEL_COUNTS_S2:
        ratio = float(MAX_ACCEL_COUNTS_S2) / float(distance)
        seconds = sqrt(CHECK_FACTOR / ((1.0 - jerk) * (1.0 - accel) * ratio * accel))
    return trunc(1000.0 * seconds + 1.0)


def setpoints(start: int, target: int) -> list[int]:
    """The gripper setpoint of each message from ``4F 20 53`` on, one planner tick each (clock 0 first), truncated
    toward zero, up to the last tick inside the duration. ``73 20`` repeats the last; ``42 20 00`` has the target."""
    profile = VendorProfile(duration_ms(target - start) / 1000.0, DEFAULT_ACCEL_FRACTION, DEFAULT_JERK_FRACTION)
    out, clock = [], 0.0
    while clock <= profile.total_time:
        out.append(start + int((target - start) * profile.sample(clock).position))
        clock += TICK_SECONDS
    return out


def jaw_reading(count: int) -> tuple[int, int]:
    """``GetJaw``: the jaw as a whole percent open and whole mm, from the gripper count (``0x10018d19``, PL101).
    percent = 100 x count / RangeEnc, below 0 read as 0, then truncated; mm = trunc(percent / 100 x RangeMM
    + 0.5) from the untruncated percent. 2857 counts is 57 % and 40 mm. The DLL's other branch (x ``GraspKoef``
    1.13 when the jaw stopped 3 % or more short of its target) needs a flag that ``Initialization`` sets, so it
    is never taken online and is not reproduced."""
    percent = max(0.0, 100.0 * count / RANGE_ENC)
    return trunc(percent), trunc(percent / 100.0 * RANGE_MM + 0.5)


@dataclass(frozen=True)
class GripperRun:
    start: int
    target: int
    sent: bool
    stopped: bool


def vendor_gripper(first, target: int):
    """Move the gripper to ``target`` counts the vendor's way. ``first`` is the latest ``vendor_home.Reply``."""
    run = vh._Run(first, 0)
    gripper, mask = vh.GRIPPER, 1 << vh.GRIPPER
    start = first.counts[gripper]
    if first.emergency:
        raise vh.HomeError(vh.ERR_EMERGENCY, "emergency input active")
    if abs(start - target) <= AT_TARGET_COUNTS:
        return GripperRun(start, target, sent=False, stopped=False)
    ramp = setpoints(start, target)
    try:
        run.setp[gripper] = ramp[0]
        yield from run.send(vh.MODE, mask, vh.MODE_S)
        run.setp[gripper] = ramp[1]
        yield from run.send(vh.SLAVE, mask)
        run.setp[gripper] = ramp[2]
        yield from run.send(vh.MOTORS, mask, 1)
        for value in ramp[3:]:
            run.setp[gripper] = value
            yield from run.cycle()
        yield from run.send(vh.CONTROL_OFF, mask)
        run.setp[gripper] = target
        yield from run.send(vh.MOTORS, mask)
    except vh.HomeError as error:
        # Both off messages go out whatever the replies say; an emergency seen meanwhile still faults.
        run.aborting = True
        run.setp[gripper] = run.reply.counts[gripper]
        yield from run.send(vh.CONTROL_OFF, mask)
        alarm = run.reply.emergency
        yield from run.send(vh.MOTORS, mask)
        alarm = alarm or run.reply.emergency
        if error.code != vh.ERR_STOPPED:
            raise
        if alarm:
            raise vh.HomeError(vh.ERR_EMERGENCY, "emergency input active while the gripper stopped") from error
        return GripperRun(start, target, sent=True, stopped=True)
    return GripperRun(start, target, sent=True, stopped=False)
