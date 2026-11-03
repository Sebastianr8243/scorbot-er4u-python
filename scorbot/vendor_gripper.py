"""The vendor's gripper move after a finished Home, as a pure generator. Never imports ``usb``.

After a ``Home('A')`` that finished, the DLL's ``OpenGripper`` and ``CloseGripper`` are position moves to
``RangeEnc`` and 0 counts (``docs/protocol/LEDGER.md`` PL46, PL48). On the wire (PL51): ``4F 20 53`` (mode S),
``4C 20 00`` (slave), ``42 20 01`` (gripper motor on), 40 ``0D`` setpoints ramping only the gripper slot in an
S-curve, ``73 20`` (control off) and ``42 20 00`` (motor off). Nothing is sent when the gripper is already there.

``vendor_gripper(first_reply, target)`` yields ``vendor_home.Out`` messages the way ``vendor_home`` does, so the
same ``VendorHomeSource`` and legacy runner carry it (order ``GRIPPER_ORDER``). The arm slots hold the first
reply's counts on every message. The ramp is ``VendorProfile`` sampled once per message, with the profile fitted
to the DLL's setpoints (``tests/fixtures/dll_lab_gripper.json``, within 8 counts at every message).

A stop thrown in (``HomeError`` ``ERR_STOPPED``) holds the gripper where it is, sends ``73 20`` and ``42 20 00``
and returns ``stopped``. Any other ``HomeError`` sends the same two messages and is raised again, so the runner
switches the motors off. Not traced, so a lab check: whether the jaw arrives before ``73 20`` (the DLL's end
flag ``+0x4a4``). Counts are the controller's (``raw24 - 0x7FFFFF``), as in ``vendor_home``.
"""

from dataclasses import dataclass
from math import ceil

from . import vendor_home as vh
from .vendor_profile import VendorProfile

GRIPPER_ORDER = 23
"""Command code queued to the legacy worker; it runs ``libcomm.vendor_home_run`` like order 22."""
RANGE_ENC = 5000
"""``ROB_4u.ini`` ``RangeEnc``: gripper counts at full opening."""
RANGE_MM = 70
"""``ROB_4u.ini`` ``RangeMM``: full opening in millimetres."""
PROFILE_TICKS = 40.1
"""The DLL's gripper profile length in planner ticks, fitted to the PL51 setpoints."""
ACCEL_FRACTION = 0.264
"""Share of the profile spent speeding up (and slowing down), fitted to the PL51 setpoints."""
JERK_FRACTION = 0.11
"""Share of each ramp spent changing the acceleration, fitted to the PL51 setpoints."""
TICK_SECONDS = 0.024
"""The DLL planner's tick, ``PCPeriod`` x ``USBCPeriod`` (``VENDOR_DLL_PROTOCOL.md`` section 11): the profile is
about 0.96 s on the vendor. Stack 2 sends one setpoint per message, so its own pacing sets the real duration."""
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
    ``mm < RangeMM + 0.0001``, ``0x10018ba0``). 40 mm gave 2857 counts and 10 mm 714 on the desk (PL71).

    The commands and the target are the DLL's; the ramp is not. The DLL's ramp is shorter for a shorter move,
    and that profile is not decoded, so ``setpoints`` always takes the full-stroke length (PL71)."""
    if isinstance(mm, bool) or not isinstance(mm, int) or not 0 <= mm <= RANGE_MM:
        raise ValueError(f"Jaw width must be a whole number of mm from 0 to {RANGE_MM}, not {mm!r}")
    return RANGE_ENC * mm // RANGE_MM


def setpoints(start: int, target: int) -> list[int]:
    """The gripper setpoint of each message from ``42 20 01`` on: one profile tick each, truncated toward zero."""
    profile = VendorProfile(PROFILE_TICKS, ACCEL_FRACTION, JERK_FRACTION)
    distance = target - start
    return [start + int(distance * profile.sample(k).position) for k in range(1, ceil(PROFILE_TICKS) + 1)]


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
        yield from run.send(vh.MODE, mask, vh.MODE_S)
        yield from run.send(vh.SLAVE, mask)
        run.setp[gripper] = ramp[0]
        yield from run.send(vh.MOTORS, mask, 1)
        for value in ramp[1:]:
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
