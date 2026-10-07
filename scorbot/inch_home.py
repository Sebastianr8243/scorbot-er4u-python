"""Home by inching: find each joint's home switch with small bounded jogs.

The vendor's ``Home`` hands the search to the controller and watches the
switch bit; ours moves the joint a degree at a time with the same legacy jog
the arm already accepted, and reads the bit after every step. It needs no
command byte the legacy code does not already send (no ``4D``, no ``48``).
The vendor's directions, order, polarity and offsets are from
``docs/protocol/VENDOR_HOMING_TRACE.md``: **from disassembly, unverified on
the arm.** Pure: no USB, no robot, standard library only.

Per joint: if the switch is not on, step toward it (the approach direction)
up to a cap from where the joint started; if it is not found, come back and
try the other way. Once the switch is on, back off against the approach until
it is off, then creep toward it until it is on again: that is its near edge,
the edge the vendor's own search ends on. ``Scorbot.home_inch`` then moves the
vendor's offset from the edge and records the counts as the session home.

Not copied from the vendor: the coupled motors that move with the shoulder and
elbow (the wrist and the forearm motor), the wrist axes, the stall detection
by the controller, and ``48``. The edge is found to within one fine step, about
a quarter of a degree, because a legacy jog ends within 20 counts of its target.
"""

from __future__ import annotations

from collections.abc import Callable
import math

# Vendor homing order, the joints the SDK may move. Wrist and gripper stay out:
# wrist jogs are disabled until measured.
ORDER = ("shoulder", "elbow", "base")
# The direction, in encoder counts, in which each joint's final approach to its
# switch runs. Inferred from the vendor's drive signs; the legacy search agrees.
APPROACH_COUNTS = {"shoulder": 1, "elbow": -1, "base": -1}
# Counts from the switch edge to home (ER4Ax INI [Homing] Offset).
OFFSET_COUNTS = {"shoulder": -190, "elbow": 45, "base": 0}
# Seconds allowed per axis. The vendor's MaxTime is 60 to 110 s for a controller-driven
# drive; a jog and a read per degree is several times slower, and a shoulder that starts
# at its lowest point needs about 150 jogs.
AXIS_SECONDS = {"shoulder": 480.0, "elbow": 360.0, "base": 480.0}
COARSE_DEG = 1.0
FINE_DEG = 0.25
# A step this large (in counts) must have moved at least 40 percent of what was asked, or
# the joint is not following. Fine steps are smaller than the legacy jog's 20 count settle
# band doubled, so they are not checked.
PROGRESS_CHECK_COUNTS = 80
# How far a joint may be searched from where it started, each way, in degrees. The
# shoulder and elbow are small on purpose: the elbow and wrist motors are coupled to the
# shoulder's motion, and the vendor homes with all of them moving together. We move one
# motor at a time, so with the other motors held, a shoulder sweep of N degrees swings the
# elbow joint by N degrees (source model; unverified on the arm) and a long sweep drives
# the elbow into its stop. That is the likely reason the legacy home failed on 2026-10-06.
# So the arm must start within a few tens of degrees of its home pose; raise the cap per
# call only knowing this. The base is not coupled.
DEFAULT_SEARCH_DEG = {"shoulder": 30.0, "elbow": 30.0, "base": 100.0}
# Once the switch is seen on, backing off it and creeping back may use at most this many
# degrees: a switch is about 2 degrees wide, so a stuck bit is found long before the cap.
EDGE_CAP_DEG = 6.0


class InchHomeError(Exception):
    """The search could not find or leave the switch safely."""


class _CapReached(InchHomeError):
    pass


def inch_to_edge(read_on: Callable[[], bool], move: Callable[[float], None], *,
                 coarse_deg: float = COARSE_DEG, fine_deg: float = FINE_DEG,
                 cap_deg: float = 60.0, edge_cap_deg: float = EDGE_CAP_DEG,
                 deadline_s: float, now: Callable[[], float]) -> float:
    """Walk one joint to its switch's near edge. Returns the net travel from the start.

    ``read_on`` takes a fresh reading of the joint's switch bit. ``move(delta)``
    performs one bounded jog of ``delta`` degrees, positive toward the near edge
    (the approach direction), and returns when the arm has settled. ``deadline_s``
    is a time on ``now``'s clock. Raises ``InchHomeError`` when the switch is not
    found within ``cap_deg`` either way, does not turn off or come back within
    ``edge_cap_deg`` of where it was first seen on, or time runs out; the joint is
    then wherever the walk left it.
    """
    for name, value in (("coarse_deg", coarse_deg), ("fine_deg", fine_deg),
                        ("cap_deg", cap_deg), ("edge_cap_deg", edge_cap_deg)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) \
                or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be a positive finite number")
    if fine_deg >= coarse_deg or coarse_deg < 0.25:
        raise ValueError("fine_deg must be smaller than coarse_deg, and coarse_deg at least 0.25")

    position = 0.0                          # degrees along the approach axis from the start

    def go(delta: float) -> None:
        nonlocal position
        if now() > deadline_s:
            raise InchHomeError("timed out")
        if abs(position + delta) > cap_deg + 1e-9:
            raise _CapReached(f"the {cap_deg:g} degree search cap")
        move(delta)
        position += delta

    def seek(sign: int) -> None:
        while not read_on():
            go(sign * coarse_deg)

    if not read_on():
        try:
            seek(1)
        except _CapReached:
            while abs(position) >= 0.05:                    # back to where it started
                go(-math.copysign(min(coarse_deg, abs(position)), position))
            try:
                seek(-1)
            except _CapReached:
                raise InchHomeError("the switch was not found within the search cap "
                                    "either way") from None
    first_on = position
    try:
        while read_on():                                    # off the switch, before its near edge
            if abs(position - first_on) >= edge_cap_deg:
                raise _CapReached("the edge cap")
            go(-fine_deg)
    except _CapReached:
        raise InchHomeError("the switch never turns off") from None
    off_at = position
    while not read_on():                                    # creep to the edge
        if abs(position - off_at) >= edge_cap_deg:
            raise InchHomeError("the switch edge was not found again after backing off")
        go(fine_deg)
    return position
