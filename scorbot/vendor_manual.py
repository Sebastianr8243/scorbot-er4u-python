"""The vendor's joint jog (``EnterManual(0)``, ``MoveManual``, ``CloseManual``) as a pure generator. Never imports ``usb``.

``EnterManual(0)`` queues ``47`` when settled. That first message carries the start setpoint;
otherwise it is a plain ``0D``. ``MoveManual`` scales the axis file's manual speed into an integer slot,
then advances a jerk-limited S-curve by 24 ms per message. Only the jogged slot changes. Same-axis calls
re-arm a 300-message keep-alive without changing speed or sign. ``CloseManual`` or keep-alive expiry
starts a mirrored ramp down on the next message, then ``4F 3F 53``, ``73 20``, ``42 20 00 00`` hold the last
setpoint. A close during acceleration reflects the profile about the request time.

``velocity_jog(first, axis, speed, control, window)`` yields ``vendor_home.Out`` messages like ``vendor_gripper``, so
the same ``VendorHomeSource`` and legacy runner carry it (order ``MANUAL_ORDER``). The profile reproduces every
recorded message exactly (``VENDOR_MANUAL_MOVE_TRACE.md`` section 4). Ours, not the DLL's:

* It ends with the vendor's ramp-down and close when ``control.stop()`` is called, when ``control.renew()`` was not
  called for ``control.deadline_s``, after ``MAX_SECONDS`` in all, or before the
  setpoint would pass ``window`` (counts from the first reply, ramp-down included).
* A stop thrown in (``HomeError`` ``ERR_STOPPED``: ``request_stop``, the pendant's Abort) does not ramp: it holds the
  newest reply's counts and sends ``47``, ``4F 3F 53``, ``73 20``, ``42 20 00 00``, the legacy jog's stop (``libdef.stopMov``),
  and returns ``stopped``. The DLL's own ``Stop('&')`` switches control off instead (``73 FF``, ``42 FF``).
* A setpoint more than ``LEAD_LIMIT_COUNTS`` ahead of the measured count, or an impact flag, is a fault: the same
  stop messages, then the error is raised and the runner switches the motors off. Excessive lead during the close,
  or an impact flag during the close or the stop, is a fault too, raised once those messages are out.

Counts are the controller's (``raw24 - 0x7FFFFF``), as in ``vendor_home``. Unverified on the arm.
"""

import math
import struct
import threading
import time
from dataclasses import dataclass

from . import vendor_home as vh
from .vendor_profile import VendorProfile

MANUAL_ORDER = 26
"""Command code queued to the legacy worker; it runs ``libcomm.vendor_home_run`` like orders 22 to 25."""
AXES = {"base": 0, "shoulder": 1, "elbow": 2}
"""``MoveManual``'s axis numbers, which are also the setpoint slots. The wrist is not offered (its jog is disabled)."""
MANUAL = ((-145, 145), (145, -145), (160, -160))
"""``Manual_1`` and ``Manual_2`` from the lab's base, shoulder and elbow axis files."""
HALF_PI = 1.5708
"""The DLL's divisor for ``NoEnc90`` on these axes."""
TICK_S = 0.024
"""``PCPeriod`` 16 x ``USBCPeriod`` 1.5 x 0.001, one planner tick per message."""
_PROFILE = VendorProfile(0.6, 0.5, 0.05)
"""The manual planner's T, p and j; its acceleration half is shared with joint moves."""
KEEP_ALIVE_MESSAGES = 300
"""Messages allowed before a same-axis ``MoveManual`` must re-arm the jog; this count is unverified."""
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
        self._messages_left = KEEP_ALIVE_MESSAGES
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def renew(self):
        with self._lock:
            self.renewed = self.clock()
            self._messages_left = KEEP_ALIVE_MESSAGES

    def tick(self):
        with self._lock:
            self._messages_left -= 1       # renew during this message re-arms the next 300

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
        if self._messages_left <= 0:
            return "keepalive"
        return None


@dataclass(frozen=True)
class JogRun:
    axis: int
    speed: int
    start: int
    end: int
    """The last setpoint sent, controller counts."""
    setpoints: tuple[int, ...]
    """Every slot of the last message, or the start counts if nothing was sent."""
    reason: str
    """"stop", "deadline", "keepalive", "limit", "total", or "stopped" (held, no ramp)."""

    @property
    def stopped(self):
        return self.reason == "stopped"


def _curve(t):
    """The manual planner's acceleration followed by an open cruise."""
    if t <= 0:
        return 0.0
    ramp = _PROFILE.times[2]
    if t < ramp:
        return _PROFILE.sample(t).position
    return 0.5 + (t - ramp) * _PROFILE.peak_velocity


def slot(axis, speed):
    """The DLL's integer manual slot, including its stored float32 divide."""
    check_speed(axis, speed)
    manual = MANUAL[axis][speed > 0]
    divided = struct.unpack("f", struct.pack("f", manual * (abs(speed) / 100) / 1000))[0]
    return math.trunc(divided * (vh.AXES[axis].noenc90 / HALF_PI))


def check_speed(axis, speed):
    if axis not in AXES.values():
        raise ValueError(f"Unknown jog axis {axis!r}")
    if isinstance(speed, bool) or not isinstance(speed, int) or not 1 <= abs(speed) <= MAX_SPEED:
        raise ValueError(f"Speed must be a whole number from 1 to {MAX_SPEED} in size, either sign")


def velocity_jog(first, axis, speed, control, window, *, settled=True):
    """Jog ``axis`` at ``speed`` until ``control`` ends it. ``window`` is (low, high) counts from ``first``'s count.

    Message k carries the profile at k x 24 ms. The 300-message keep-alive count is unverified.
    """
    check_speed(axis, speed)
    low, high = window
    run = vh._Run(first, 0)
    start = first.counts[axis]
    scale = slot(axis, speed)
    if first.emergency:
        raise vh.HomeError(vh.ERR_EMERGENCY, "emergency input active")
    if (scale > 0 and high <= 0) or (scale < 0 and low >= 0):
        return JogRun(axis, speed, start, start, tuple(run.setp), "limit")      # already at the window's edge this way: send nothing
    started = control.clock()
    reason = None
    try:
        tick = 0
        ramp = _PROFILE.times[2]
        while reason is None:
            following = tick * TICK_S
            braking = scale * (_curve(following) + 0.5 if following > ramp else 2 * _curve(following))
            if tick > 0 and not low <= math.trunc(braking) <= high:
                reason = "limit"
                break
            control.tick()
            cmd = vh.CLEAR if settled and tick == 0 else vh.NOP
            yield from _step(run, axis, start, scale * _curve(tick * TICK_S), cmd)
            reason = control.why_end(started)
            tick += 1
        requested = (tick - 1) * TICK_S
        end = requested + ramp if requested > ramp else 2 * requested
        while True:
            t = tick * TICK_S
            if t > end:
                break
            if requested > ramp:
                position = _curve(requested) + 0.5 - _curve(ramp - (t - requested))
            else:
                position = 2 * _curve(requested) - _curve(2 * requested - t)
            if (tick + 1) * TICK_S > end:
                run.setp[axis] = start + int(scale * position)  # the last ramp setpoint rides on 4F
                break
            yield from _step(run, axis, start, scale * position)
            tick += 1
        impact, lead = 0, 0
        for cmd, mask, arg in ((vh.MODE, 0x3F, vh.MODE_S), (vh.CONTROL_OFF, 0x20, 0), (vh.MOTORS, 0x20, 0)):
            yield from run.send(cmd, mask, arg)
            impact = impact or run.reply.impact
            lead = max(lead, abs(run.setp[axis] - run.reply.counts[axis]))
    except vh.HomeError as error:
        # The legacy jog's stop: hold the newest reply's counts on every message (libdef.stopMov), whatever the replies say.
        run.aborting = True
        run.setp = list((getattr(error, "reply", None) or run.reply).counts)
        alarm, impact = False, 0
        for cmd, mask, arg in ((vh.CLEAR, 0, 0), (vh.MODE, 0x3F, vh.MODE_S), (vh.CONTROL_OFF, 0x20, 0),
                               (vh.MOTORS, 0x20, 0)):
            yield from run.send(cmd, mask, arg)
            alarm = alarm or run.reply.emergency
            impact = impact or run.reply.impact
        if error.code != vh.ERR_STOPPED:
            raise
        if alarm:
            raise vh.HomeError(vh.ERR_EMERGENCY, "emergency input active while the jog stopped") from error
        if impact:
            raise vh.HomeError(vh.ERR_IMPACT, f"impact flag {impact:#04x} while the jog stopped", axis) from error
        return JogRun(axis, speed, start, run.setp[axis], tuple(run.setp), "stopped")
    if impact:      # the close went out in full; the runner now switches the motors off and the session faults
        raise vh.HomeError(vh.ERR_IMPACT, f"impact flag {impact:#04x} while the jog closed", axis)
    if lead > LEAD_LIMIT_COUNTS:
        raise vh.HomeError(ERR_LEAD, f"the setpoint was {lead} counts from the measured count while the jog closed, "
                                    f"more than {LEAD_LIMIT_COUNTS}", axis)
    return JogRun(axis, speed, start, run.setp[axis], tuple(run.setp), reason)


def _step(run, axis, start, position, cmd=vh.NOP):
    run.setp[axis] = start + int(position)
    reply = yield from run.send(cmd)
    if reply.impact:
        raise vh.HomeError(vh.ERR_IMPACT, f"impact flag {reply.impact:#04x} during a jog", axis)
    if abs(run.setp[axis] - reply.counts[axis]) > LEAD_LIMIT_COUNTS:
        raise vh.HomeError(ERR_LEAD, f"the setpoint is {run.setp[axis] - reply.counts[axis]:+d} counts from the "
                                     f"measured count, more than {LEAD_LIMIT_COUNTS}", axis)
    return reply
