"""The vendor's joint jog (``EnterManual(0)``, ``MoveManual``, ``CloseManual``) as a pure generator. Never imports ``usb``.

What the lab DLL sends (desk recording, ``tests/fixtures/dll_lab_manual.json``, ``docs/protocol/LEDGER.md``):
``EnterManual(0)`` sends nothing. The first ``MoveManual(axis, speed)`` sends ``47``, then ``0D`` setpoints that move
only that axis' slot: a ramp of about 10 messages up to a constant step of ``speed x COUNTS_PER_TICK[axis]``
counts per message. ``CloseManual`` ramps down over about 12 messages, then sends ``4F 3F 53``, ``73 20`` and
``42 20 00``. A ``MoveManual`` while the jog runs is accepted and changes nothing (not the speed, not the sign),
and the DLL has no keep-alive: one call ran 3 s until ``CloseManual``.

``velocity_jog(first, axis, speed, control, window)`` yields ``vendor_home.Out`` messages like ``vendor_gripper``, so
the same ``VendorHomeSource`` and legacy runner carry it (order ``MANUAL_ORDER``). The ramps are straight lines fitted
to the recording (within a few counts at every message at speed 20). Ours, not the DLL's:

* It ends with the vendor's ramp-down and close when ``control.stop()`` is called, when ``control.renew()`` was not
  called for ``control.deadline_s`` (the DLL has no such deadline), after ``MAX_SECONDS`` in all, or before the
  setpoint would pass ``window`` (counts from the first reply, ramp-down included).
* A stop thrown in (``HomeError`` ``ERR_STOPPED``: ``request_stop``, the pendant's Abort) does not ramp: it holds the
  last reply's counts and sends ``47``, ``4F 3F 53``, ``73 20``, ``42 20 00``, the legacy jog's stop (``libdef.stopMov``),
  and returns ``stopped``. The DLL's own ``Stop('&')`` switches control off instead (``73 FF``, ``42 FF``).
* A setpoint more than ``LEAD_LIMIT_COUNTS`` ahead of the measured count, or an impact flag, is a fault: the same
  stop messages, then the error is raised and the runner switches the motors off.

Counts are the controller's (``raw24 - 0x7FFFFF``), as in ``vendor_home``. Unverified on the arm.
"""

import math
import threading
import time
from dataclasses import dataclass

from . import vendor_home as vh

MANUAL_ORDER = 26
"""Command code queued to the legacy worker; it runs ``libcomm.vendor_home_run`` like orders 22 to 25."""
AXES = {"base": 0, "shoulder": 1, "elbow": 2}
"""``MoveManual``'s axis numbers, which are also the setpoint slots. The wrist is not offered (its jog is disabled)."""
COUNTS_PER_TICK = {0: -0.9426, 1: 0.7522, 2: -0.8326}
"""Counts per message per unit of speed, fitted to the recording (``ER4Ax*.ini`` ``Manual_1``/``Manual_2`` 145)."""
RAMP_UP_TICKS = 10.0
RAMP_UP_DELAY_TICKS = 1.25
RAMP_DOWN_TICKS = 12.0
"""The straight ramps fitted to the recording, in messages from ``47`` and before ``4F 3F 53``."""
MAX_SPEED = 100
"""``MoveManual`` speed is a signed percent of the axis' manual speed."""
DEADLINE_RANGE_S = (0.1, 3.0)
DEFAULT_DEADLINE_S = 1.0
"""Ours: how long a jog runs without ``renew``."""
MAX_SECONDS = 30.0
"""Ours: bound on one jog."""
LEAD_LIMIT_COUNTS = 1000
"""Ours: how far the setpoint may run ahead of the measured count before it is a fault."""
ERR_LEAD = 579
"""Stack 2's code for that fault (570 to 579 are unused by the vendor, ``LEDGER.md`` PL60)."""


class JogControl:
    """What the caller's thread tells a running jog. ``renew`` and ``stop`` only set fields; nothing is sent."""

    def __init__(self, deadline_s=DEFAULT_DEADLINE_S, clock=time.monotonic):
        low, high = DEADLINE_RANGE_S
        if isinstance(deadline_s, bool) or not isinstance(deadline_s, (int, float)) \
                or not math.isfinite(deadline_s) or not low <= deadline_s <= high:
            raise ValueError(f"deadline_s must be {low:g} through {high:g} seconds")
        self.deadline_s = float(deadline_s)
        self.clock = clock
        self.renewed = clock()
        self._stop = threading.Event()

    def renew(self):
        self.renewed = self.clock()

    def stop(self):
        self._stop.set()

    def why_end(self, started):
        """The reason to start the ramp-down now, or None."""
        now = self.clock()
        if self._stop.is_set():
            return "stop"
        if now - self.renewed > self.deadline_s:
            return "deadline"
        if now - started > MAX_SECONDS:
            return "total"
        return None


@dataclass(frozen=True)
class JogRun:
    axis: int
    speed: int
    start: int
    end: int
    """The last setpoint sent, controller counts."""
    reason: str
    """"stop", "deadline", "limit", "total", or "stopped" (a stop request: held, no ramp)."""

    @property
    def stopped(self):
        return self.reason == "stopped"


def _ramp(t, ticks):
    """Distance covered in ``t`` messages at a velocity that rises from 0 to 1 over ``ticks`` (then stays 1)."""
    if t <= 0:
        return 0.0
    return t * t / (2 * ticks) if t < ticks else ticks / 2 + (t - ticks)


def check_speed(axis, speed):
    if axis not in COUNTS_PER_TICK:
        raise ValueError(f"Unknown jog axis {axis!r}")
    if isinstance(speed, bool) or not isinstance(speed, int) or not 1 <= abs(speed) <= MAX_SPEED:
        raise ValueError(f"Speed must be a whole number from 1 to {MAX_SPEED} in size, either sign")


def velocity_jog(first, axis, speed, control, window):
    """Jog ``axis`` at ``speed`` until ``control`` ends it. ``window`` is (low, high) counts from ``first``'s count."""
    check_speed(axis, speed)
    low, high = window
    run = vh._Run(first, 0)
    start = first.counts[axis]
    cruise = speed * COUNTS_PER_TICK[axis]
    if first.emergency:
        raise vh.HomeError(vh.ERR_EMERGENCY, "emergency input active")
    if not low < cruise / abs(cruise) < high:
        return JogRun(axis, speed, start, start, "limit")      # already at the window's edge this way: send nothing
    started = control.clock()
    reason = None
    try:
        yield from run.send(vh.CLEAR)
        tick, position, velocity = 0, 0.0, 0.0      # of the last setpoint sent
        while reason is None:
            following = cruise * min(1.0, max(0.0, tick + 1 - RAMP_UP_DELAY_TICKS) / RAMP_UP_TICKS)
            nxt = cruise * _ramp(tick + 1 - RAMP_UP_DELAY_TICKS, RAMP_UP_TICKS)
            braking = following * RAMP_DOWN_TICKS * abs(following / cruise) / 2
            reason = control.why_end(started)
            if reason is None and not low <= nxt + braking <= high:
                reason = "limit"        # the ramp-down from here (shorter than from nxt) stays inside
            if reason is None:
                tick, position, velocity = tick + 1, nxt, following
                yield from _step(run, axis, start, position)
        share = abs(velocity / cruise)             # the ramp-down is shorter from below full speed
        ticks = max(1, math.ceil(RAMP_DOWN_TICKS * share))
        decel = cruise / RAMP_DOWN_TICKS
        for k in range(1, ticks):
            yield from _step(run, axis, start, position + velocity * k - decel * k * k / 2)
        if velocity:
            position += velocity * velocity / (2 * decel)
        run.setp[axis] = start + int(position)
        yield from run.send(vh.MODE, 0x3F, vh.MODE_S)
        yield from run.send(vh.CONTROL_OFF, 0x20)
        yield from run.send(vh.MOTORS, 0x20)
    except vh.HomeError as error:
        # The legacy jog's stop: hold the last reply's counts on every message (libdef.stopMov), whatever the replies say.
        run.aborting = True
        run.setp = list(run.reply.counts)
        alarm = False
        for cmd, mask, arg in ((vh.CLEAR, 0, 0), (vh.MODE, 0x3F, vh.MODE_S), (vh.CONTROL_OFF, 0x20, 0),
                               (vh.MOTORS, 0x20, 0)):
            yield from run.send(cmd, mask, arg)
            alarm = alarm or run.reply.emergency
        if error.code != vh.ERR_STOPPED:
            raise
        if alarm:
            raise vh.HomeError(vh.ERR_EMERGENCY, "emergency input active while the jog stopped") from error
        return JogRun(axis, speed, start, run.setp[axis], "stopped")
    return JogRun(axis, speed, start, run.setp[axis], reason)


def _step(run, axis, start, position):
    run.setp[axis] = start + int(position)
    reply = yield from run.cycle()
    if reply.impact:
        raise vh.HomeError(vh.ERR_IMPACT, f"impact flag {reply.impact:#04x} during a jog", axis)
    if abs(run.setp[axis] - reply.counts[axis]) > LEAD_LIMIT_COUNTS:
        raise vh.HomeError(ERR_LEAD, f"the setpoint is {run.setp[axis] - reply.counts[axis]:+d} counts from the "
                                     f"measured count, more than {LEAD_LIMIT_COUNTS}", axis)
    return reply
