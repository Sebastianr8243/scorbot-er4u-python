"""The vendor's Home as a pure state machine: commands in, replies out. Never imports ``usb``.

``vendor_home(first_reply)`` is a generator. It yields one ``Out`` (a command plus the setpoint
table every message carries), receives the controller's ``Reply``, and returns a ``HomeResult``
when the home is done or raises ``HomeError``. It sends nothing, sleeps nowhere and knows no
transport; ``encode_out`` and ``decode_reply`` turn the two ends into the 64-byte packets.

Where it comes from. The order, the per-axis search types, the coupled-motor drives and the ``48``
zeroing follow ``docs/protocol/VENDOR_HOMING_TRACE.md``. The command sequence was checked against a
recording of Intelitek's own ``USBC.dll`` running ``Home('A')`` against a fake controller
(``tests/fixtures/vendor_home_trace04.json``, ``tests/test_vendor_home.py``). The OUT bytes are the
vendor's; the replies in that recording were a model of the arm, and the real controller's
acceptance is unverified (root ``CLAUDE.md``, changed 2026-10-08 by the owner).

Where it differs from the vendor, on purpose (a safety margin, not a port):

* Planner moves (the fine back-off, the offset and the 1050-count pitch move) are straight ramps of
  at most ``RAMP_COUNTS_PER_CYCLE`` per cycle, not the vendor's jerk-limited profile.
* Every driven motor, coupled ones included, must keep moving (``STALL_CYCLES``) and must not move
  against its drive (``DIRECTION_FAULT_COUNTS``: the sign of ``4D`` was never seen on the arm, so a
  wrong sign stops the home within a few degrees). Every search has a travel bound and the axis'
  ``max_time``. The vendor's shoulder and wrist-roll searches have none of these.
* An impact flag during a search is a fault (a type 14 or 15 search treats it as a hard stop and
  reverses, like a stall). A switch that is not found, a move that does not settle, and a gripper
  that never reaches an end stop are faults; the vendor would carry on and zero the counts.
* The gripper drives end on a stall (after ``GRIPPER_MIN_TRAVEL``) or an impact flag. The vendor's
  rule for ending a gripper move was not traced.
* On any ``HomeError`` the generator first yields the stop (``47``, ``4D 0`` to every motor, two
  cycles, hold the setpoints at the measured position, ``4F FF 53``) and only then raises. The caller must keep driving it until it raises,
  then switch the motors off. None of these errors is an emergency stop. A caller that abandons the
  generator (``close()``) gets no stop messages and must send its own.

Counts here are the controller's: ``raw24 - 0x7FFFFF`` (``docs/protocol/VENDOR_DLL_PROTOCOL.md``
section 6), not ``scorbot.state`` counts, which read one low on the positive side.
"""

from collections.abc import Generator
from dataclasses import dataclass

ZERO24 = 0x7FFFFF
"""Raw 24-bit reading of a count of zero."""
UNUSED_SLOT = 0x800001 - 0x1000000
"""Placeholder in setpoint slots 6 and 7 (no axis): the vendor sends raw ``0x800001`` there."""

NOP, CLEAR, MODE, SLAVE, MOVE, SET_POSITION = 0x0D, 0x47, 0x4F, 0x4C, 0x4D, 0x48
CONTROL_OFF, MOTORS = 0x73, 0x42
MODE_T, MODE_H, MODE_S = 0x54, 0x48, 0x53

BASE, SHOULDER, ELBOW, WRIST_M1, WRIST_M2, GRIPPER = range(6)
NAMES = ("base", "shoulder", "elbow", "wrist_m1", "wrist_m2", "gripper")

RAMP_COUNTS_PER_CYCLE = 6
"""Largest setpoint change per cycle in any planner move (about 370 counts/s at 16 ms a cycle)."""
SETTLE_COUNTS = 8
SETTLE_CYCLE_CAP = 400
STALL_CYCLES = 150
"""Every driven motor (the axis and its coupled motors) must move ``STALL_WINDOW_COUNTS`` in each window of this many cycles."""
STALL_WINDOW_COUNTS = 10
DIRECTION_FAULT_COUNTS = 150
"""Net counts a driven motor may move AGAINST its drive before the home stops: a wrong drive sign."""
MAX_CREEPS = 2000
MAX_LEAD_COUNTS = 400
"""How far a setpoint may run ahead of the measured count of a motor in a ramp or a creep. The ramp is 6 counts
a cycle, so 400 allows about 67 cycles of lag (more than the 47-message queue the controller may run behind,
docs/protocol/BRING_UP_SEQUENCE.md) and still stops a motor that does not follow within about 3.5 degrees,
not after the whole 1050-count pitch move."""
COUPLED_MIN_AXIS_COUNTS = 60
"""A search must have moved its axis this far before the motors driven with it are judged: a shorter
transition (the arm was already on its switch) predicts only a few counts for the slow wrist motors, which
gear backlash can hide. A motor that is never judged here is caught later by the offset ramp's settle check."""
TICK_GRACE_CYCLES = 60
"""Cycles after a search starts before its stall ticks count. The controller may run about 47 messages
behind (docs/protocol/BRING_UP_SEQUENCE.md), so a healthy drive can show no motion in the replies for that long.
When the caller measures the lag (``Reply.lag``) the grace is the larger of this and the lag plus ``TICK_GRACE_MARGIN``."""
TICK_GRACE_MARGIN = 15
MAX_LAG = 60
"""A controller further behind than this is not homed: the vendor never lets more than 45 messages be in
flight (its queue limits are 4 to 45, 20 while homing), the thesis saw 6 and this arm showed 47. It is also
the most credit the measured lag can earn: the stall grace is at most 75 cycles and the setpoint lead limit
at most 400 + 6 x 60 = 760 counts, so an unverified lag cannot let a stuck motor take most of the 1050-count
pre-pass move."""
COUPLED_FOLLOW_FRACTION = 0.25
"""A coupled motor must have covered this fraction of what its drive ratio to the axis predicts."""
TICK_STALL_LIMIT = 4
"""Stalled ticks in a row that end a type 16 search (the types 14 and 15 use their own, 4 and 1, and reverse)."""
GRIPPER_MAX_CYCLES = 600
GRIPPER_MAX_SECONDS = 30.0
GRIPPER_STALL_CYCLES = 25
GRIPPER_STALL_COUNTS = 4
GRIPPER_MIN_TRAVEL = 200
"""A gripper drive must travel this far before a stall counts as the end of its travel."""

ERR_EMERGENCY, ERR_IMPACT, ERR_NOT_FOUND, ERR_TIMEOUT = 300, 201, 563, 561
ERR_STALL, ERR_DIRECTION, ERR_TRAVEL, ERR_NOT_SETTLED, ERR_GRIPPER = 570, 571, 572, 573, 574
ERR_BAD_REPLY, ERR_STOPPED = 575, 14
"""A reply that cannot be read; a stop or cancel asked for from outside (14 is the legacy ``STOPPED``)."""
ERR_FOLLOW = 576
"""A motor did not follow its setpoint (the lead passed ``MAX_LEAD_COUNTS``)."""
ERR_USB = 577
"""The USB link failed part-way (raised by the worker through ``VendorHomeSource.interrupt``)."""
ERR_LAG = 578
"""The controller is more than ``MAX_LAG`` messages behind: its replies are too stale to trust, and a stop may land late."""


class HomeError(Exception):
    """The home must stop and the motors go off. ``code`` follows the vendor's error numbers."""

    def __init__(self, code, message, axis=None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.axis = axis


@dataclass(frozen=True)
class AxisConfig:
    """One axis' ``[Homing]`` settings (``ER4Ax1-6.ini``, ``docs/protocol/VENDOR_HOMING_TRACE.md``)."""
    name: str
    type: int
    velocity: int
    impact_enc: int
    impact_ticks: int
    max_time_s: float
    max_distance: int
    offset: int
    noenc90: int


AXES = (
    AxisConfig("base", 14, 110, 2, 5, 110.0, 45000, 0, -12770),
    AxisConfig("shoulder", 16, 90, 2, 10, 60.0, 30000, -190, -10216),
    AxisConfig("elbow", 14, 100, 2, 10, 90.0, 10000, 45, 10216),
    AxisConfig("wrist_m1", 15, 90, 2, 2, 55.0, 10000, 850, 2511),
    AxisConfig("wrist_m2", 16, -90, 5, 10, 75.0, 10000, -690, 2511),
    AxisConfig("gripper", 17, -165, 2, 2, 15.0, 10000, 0, 0),
)
"""The base bound is 310 degrees of travel (manual) at 141.89 counts a degree (about 44,000) plus a little, not
the vendor file's 99,999 (about 705 degrees): a base that has gone further than it can turn has missed its
switch and is grinding on a stop. The shoulder's 30,000 counts (about 264 degrees against a 165 degree span)
is left as the vendor has it for now."""
HOMING_SEQUENCE = (SHOULDER, ELBOW, WRIST_M2, WRIST_M1, BASE, GRIPPER)
"""``HomingSeq`` 2, 3, 5, 4, 1, 6 of ``ROB_4u.INI`` (1-based) as axis numbers."""
PREPASS_PITCH_COUNTS = 1050
"""The ER-4u pre-pass moves wrist m1 by its offset + 200 once the wrists are zeroed."""

COUPLED = {BASE: (), SHOULDER: (ELBOW, WRIST_M1, WRIST_M2), ELBOW: (WRIST_M1, WRIST_M2),
           WRIST_M1: (WRIST_M2,), WRIST_M2: (WRIST_M1,), GRIPPER: ()}
"""Motors the vendor drives and zeroes together with each axis, in its order."""


def _trunc_div(value, divisor):
    return int(value / divisor)


def drive_values(axis, value):
    """``4D`` value for ``axis`` and for each coupled motor (truncating toward zero)."""
    table = {SHOULDER: {ELBOW: -_trunc_div(value, 2), WRIST_M1: -_trunc_div(value, 4),
                        WRIST_M2: _trunc_div(value, 4)},
             ELBOW: {WRIST_M1: _trunc_div(value, 2), WRIST_M2: -_trunc_div(value, 2)},
             WRIST_M1: {WRIST_M2: -value},
             WRIST_M2: {WRIST_M1: value}}
    return {axis: value, **table.get(axis, {})}


def position_vector(axis, delta):
    """Count change of ``axis`` and of each coupled motor for a move of ``delta`` counts."""
    table = {SHOULDER: {ELBOW: -delta, WRIST_M1: -_trunc_div(delta, 2), WRIST_M2: _trunc_div(delta, 2)},
             ELBOW: {WRIST_M1: _trunc_div(delta, 2), WRIST_M2: -_trunc_div(delta, 2)},
             WRIST_M1: {WRIST_M2: -delta},
             WRIST_M2: {WRIST_M1: delta}}
    return {axis: delta, **table.get(axis, {})}


@dataclass(frozen=True)
class Out:
    """One message to the controller; the setpoint table rides along on every one."""
    cmd: int
    mask: int = 0
    arg16: int = 0
    arg32: int = 0
    setpoints: tuple = (0, 0, 0, 0, 0, 0)
    aborting: bool = False
    """True for every message of an abort (the home's own, after a fault): the caller must not throw a
    second error into it, or the stop messages are cut short."""


@dataclass(frozen=True)
class Reply:
    """What the home needs from one IN packet. ``now`` is a monotonic time in seconds."""
    counts: tuple
    switches: int
    now: float
    impact: int = 0
    emergency: bool = False
    lag: int = 0
    """How many messages the controller is behind (sent ID minus the ID this reply echoes), measured by the caller."""


@dataclass(frozen=True)
class HomeResult:
    axes_done: tuple
    cycles: int
    seconds: float


def encode_out(out, sequence):
    """The 64-byte OUT packet: ID, command, mask, 16-bit and 32-bit arguments, eight setpoint slots."""
    packet = bytearray(64)
    packet[0] = sequence & 0xFF
    packet[4] = out.cmd
    packet[5] = out.mask & 0xFF
    packet[6:8] = (out.arg16 & 0xFFFF).to_bytes(2, "little")
    packet[8:12] = out.arg32.to_bytes(4, "little", signed=True)
    slots = [*out.setpoints, UNUSED_SLOT, UNUSED_SLOT]
    for index, value in enumerate(slots[:8]):
        packet[12 + 4 * index:16 + 4 * index] = int(value).to_bytes(4, "little", signed=True)
    return bytes(packet)


REPLY_MIN_LENGTH = 60
"""An IN packet shorter than this cannot be decoded at all (``decode_reply`` raises)."""


def decode_reply(packet, now):
    """Parse an IN packet: byte 2 bit 0 emergency, byte 5 switches, 24-bit counts, byte 59 impact."""
    if len(packet) < 60:
        raise ValueError(f"reply too short: {len(packet)} bytes")
    counts = tuple(int.from_bytes(packet[19 + 5 * n:22 + 5 * n], "little") - ZERO24 for n in range(6))
    return Reply(counts=counts, switches=packet[5], now=now, impact=packet[59], emergency=bool(packet[2] & 1))


class _Run:
    """The state of one home. All its methods are generators to be driven with ``yield from``."""

    def __init__(self, first, version):
        self.reply = first
        self.version = version
        self.setp = list(first.counts)
        self.cycles = 0
        self.t0 = first.now
        self.axis_t0 = first.now
        self.axis_origin = list(first.counts)
        self.driven = {}
        self.aborting = False

    # --- primitives ---------------------------------------------------------------------------
    def send(self, cmd, mask=0, arg16=0, arg32=0):
        self.reply = yield Out(cmd, mask, arg16, arg32, tuple(self.setp), self.aborting)
        self.cycles += 1
        if self.reply.emergency and not self.aborting:
            raise HomeError(ERR_EMERGENCY, "emergency input active")
        return self.reply

    def cycle(self):
        return self.send(NOP)

    def wait(self, count):
        for _ in range(count):
            yield from self.cycle()

    def hold(self):
        self.setp = list(self.reply.counts)

    def on(self, axis):
        return bool(self.reply.switches >> axis & 1)

    def check_time(self, axis):
        if self.reply.now - self.axis_t0 > AXES[axis].max_time_s:
            raise HomeError(ERR_TIMEOUT, f"homing time elapsed on {NAMES[axis]}", axis)

    def check_travel(self, axis, *, coupled_only=False):
        """Raise if the axis (unless ``coupled_only``) or any motor coupled to it left its travel bound."""
        for motor in (*COUPLED[axis], *(() if coupled_only else (axis,))):
            limit = AXES[motor].max_distance
            if abs(self.reply.counts[motor] - self.axis_origin[motor]) > limit:
                raise HomeError(ERR_TRAVEL, f"{NAMES[motor]} travelled past {limit} counts", motor)

    def check_lead(self, motors, axis):
        """Raise if any of ``motors`` has fallen further behind its setpoint than ``MAX_LEAD_COUNTS``."""
        limit = MAX_LEAD_COUNTS + RAMP_COUNTS_PER_CYCLE * self.reply.lag    # a queue of N messages holds N ramp steps
        for motor in motors:
            if abs(self.setp[motor] - self.reply.counts[motor]) > limit:
                raise HomeError(ERR_FOLLOW, f"{NAMES[motor]} is not following its setpoint", motor)

    def check_coupled_progress(self, axis, drive_start):
        """Before a search ends in a zeroing: every motor driven with ``axis`` must have moved with it.

        The 150-cycle window of ``wait_switch`` never runs in a search that is shorter than that, and
        the tick check looks at the axis only, so a coupled motor that is jammed or unplugged would be
        zeroed. Each motor is compared with what the drive ratio predicts from how far the axis went.
        """
        reference = self.driven.get(axis)
        moved_axis = abs(self.reply.counts[axis] - drive_start[axis])
        if not reference or moved_axis < COUPLED_MIN_AXIS_COUNTS:
            return
        for motor, value in self.driven.items():
            if motor == axis:
                continue
            expected = abs(value / reference) * moved_axis
            # A positive drive lowers the count, so progress along the drive is the signed change times -sign.
            along = (self.reply.counts[motor] - drive_start[motor]) * (-1 if value > 0 else 1)
            if along < COUPLED_FOLLOW_FRACTION * expected:
                raise HomeError(ERR_STALL, f"{NAMES[motor]} did not move with the {NAMES[axis]}", motor)

    def check_impact(self, axis):
        if self.reply.impact:
            raise HomeError(ERR_IMPACT, f"impact flag {self.reply.impact:#04x} on {NAMES[axis]}", axis)

    # --- mode, drive, stop --------------------------------------------------------------------
    def modes(self, letter, axes):
        for a in axes:
            yield from self.send(MODE, 1 << a, letter)

    def mode_t(self, axis):
        yield from self.modes(MODE_T, (axis, *COUPLED[axis]))

    def mode_h(self, axis):
        yield from self.modes(MODE_H, (axis,))

    def drive(self, axis, value, axis_first):
        values = drive_values(axis, value)
        self.driven = {m: v for m, v in values.items() if v}
        order = (axis, *COUPLED[axis]) if axis_first else (*COUPLED[axis], axis)
        for a in order:
            yield from self.send(MOVE, 1 << a, 0, values[a])

    def stop(self, axis, *, clear=True):
        if clear:
            yield from self.send(CLEAR)
        for a in (*COUPLED[axis], axis):
            yield from self.send(MOVE, 1 << a, 0, 0)
        self.driven = {}

    def slave_pairs(self, axis):
        for a in (*COUPLED[axis], axis):
            yield from self.send(MODE, 1 << a, MODE_S)
            yield from self.send(SLAVE, 1 << a)

    # --- waiting for a switch ------------------------------------------------------------------
    def wait_switch(self, axis, want, *, reversal):
        """Cycle until the axis' switch bit equals ``want``.

        Returns True when it did. A type 14 or 15 search that hits a stall, an impact or its distance
        limit returns False (the vendor then reverses). Every other problem raises ``HomeError``:
        timeout, a driven motor not moving, a driven motor moving against its drive, travel past
        ``max_distance`` on a type 16 search, an impact on a type 16 search.
        """
        cfg = AXES[axis]
        limit = {14: 4, 15: 1}.get(cfg.type) if reversal else None
        tick_limit = limit if limit is not None else TICK_STALL_LIMIT
        drive_start = {m: self.reply.counts[m] for m in self.driven}
        window_start, window_cycles = dict(drive_start), 0
        tick_position, tick_cycles, stalled_ticks = self.reply.counts[axis], 0, 0
        cycles_run = 0
        while self.on(axis) != want:
            yield from self.cycle()
            cycles_run += 1
            self.check_time(axis)
            counts = self.reply.counts
            if self.reply.impact:
                if limit is not None:
                    return False
                raise HomeError(ERR_IMPACT, f"impact flag {self.reply.impact:#04x} while searching {NAMES[axis]}", axis)
            for motor, value in self.driven.items():
                along = (counts[motor] - drive_start[motor]) * (-1 if value > 0 else 1)
                if along < -DIRECTION_FAULT_COUNTS:
                    raise HomeError(ERR_DIRECTION, f"{NAMES[motor]} moves against its drive", motor)
            if abs(counts[axis] - self.axis_origin[axis]) > cfg.max_distance:
                if limit is not None:
                    return False
                raise HomeError(ERR_TRAVEL, f"{NAMES[axis]} travelled past {cfg.max_distance} counts", axis)
            self.check_travel(axis, coupled_only=True)
            window_cycles += 1
            if window_cycles >= STALL_CYCLES:
                for motor in self.driven:
                    if abs(counts[motor] - window_start[motor]) < STALL_WINDOW_COUNTS:
                        raise HomeError(ERR_STALL, f"{NAMES[motor]} stopped moving while driven", motor)
                window_start, window_cycles = {m: counts[m] for m in self.driven}, 0
            if cycles_run <= max(TICK_GRACE_CYCLES, self.reply.lag + TICK_GRACE_MARGIN):
                tick_position, tick_cycles = counts[axis], 0
                continue
            tick_cycles += 1
            if tick_cycles >= cfg.impact_ticks:
                stalled = abs(counts[axis] - tick_position) < cfg.impact_enc
                stalled_ticks = stalled_ticks + 1 if stalled else 0
                tick_position, tick_cycles = counts[axis], 0
                if stalled_ticks > tick_limit:
                    if limit is not None:
                        return False
                    raise HomeError(ERR_STALL, f"{NAMES[axis]} stopped moving while driven", axis)
        self.check_coupled_progress(axis, drive_start)
        return True

    def reach(self, axis, want):
        """Wait for a switch that must be found: no reversal on a stall, an impact or the distance bound.

        The vendor's forward pass reverses on a stall (its tick check, as short as 2 cycles for the wrist);
        this is the pass that must succeed, so a slow start must not read as a missed switch. A real stall
        still ends it, through the grace-delayed tick check, the 150-cycle window, an impact or the travel bound."""
        yield from self.wait_switch(axis, want, reversal=False)

    # --- the four search types -----------------------------------------------------------------
    def coarse_16(self, a):
        v = AXES[a].velocity
        yield from self.mode_t(a)
        if self.on(a):
            yield from self.drive(a, v, axis_first=False)
            yield from self.wait_switch(a, False, reversal=False)
            yield from self.stop(a, clear=False)
        yield from self.mode_h(a)
        yield from self.drive(a, -v, axis_first=True)
        yield from self.wait_switch(a, True, reversal=False)
        yield from self.stop(a)

    def coarse_14(self, a):
        v = AXES[a].velocity
        yield from self.mode_t(a)
        yield from self.drive(a, v, axis_first=False)
        if (yield from self.wait_switch(a, True, reversal=True)):
            yield from self.wait_switch(a, False, reversal=True)
        yield from self.stop(a)
        yield from self.mode_h(a)
        yield from self.drive(a, -v, axis_first=True)
        yield from self.reach(a, True)
        yield from self.mode_t(a)
        yield from self.drive(a, -v, axis_first=False)
        yield from self.reach(a, False)
        yield from self.stop(a)
        yield from self.mode_h(a)
        yield from self.drive(a, v, axis_first=True)
        yield from self.reach(a, True)
        yield from self.stop(a)

    def coarse_15(self, a):
        v = AXES[a].velocity
        yield from self.mode_t(a)
        yield from self.drive(a, v, axis_first=False)
        if (yield from self.wait_switch(a, True, reversal=True)):
            yield from self.wait_switch(a, False, reversal=True)
        yield from self.stop(a)
        yield from self.mode_h(a)
        yield from self.drive(a, -v, axis_first=True)
        yield from self.reach(a, True)
        yield from self.stop(a)

    def gripper_drive(self, value, *, at_end_ok=False):
        """Drive the gripper to the end of its travel: it stalls, or flags an impact.

        Motor on, mode T, the drive, ``4D 0``, then the gripper switched off again (``73 20``, ``42 20``).
        Raises ``HomeError`` for: moving against the drive, travelling past ``max_distance`` or taking
        longer than ``GRIPPER_MAX_SECONDS`` or ``GRIPPER_MAX_CYCLES``, and a stall or impact before
        ``GRIPPER_MIN_TRAVEL`` (the gripper's count is zeroed afterwards, so a half-finished move would
        zero it in the wrong place). ``at_end_ok`` accepts that early end: the pre-pass closes a
        gripper that may already be closed.
        """
        yield from self.send(MOTORS, 1 << GRIPPER, 1)
        yield from self.modes(MODE_T, (GRIPPER,))
        yield from self.send(MOVE, 1 << GRIPPER, 0, value)
        self.driven = {GRIPPER: value}
        start, t_start = self.reply.counts[GRIPPER], self.reply.now
        window_start, window_cycles = start, 0
        for _ in range(GRIPPER_MAX_CYCLES):
            yield from self.cycle()
            position = self.reply.counts[GRIPPER]
            travelled = abs(position - start)
            if (position - start) * (-1 if value > 0 else 1) < -DIRECTION_FAULT_COUNTS:
                raise HomeError(ERR_DIRECTION, "gripper moves against its drive", GRIPPER)
            if travelled > AXES[GRIPPER].max_distance:
                raise HomeError(ERR_TRAVEL, "gripper travelled past its bound", GRIPPER)
            if self.reply.now - t_start > GRIPPER_MAX_SECONDS:
                raise HomeError(ERR_TIMEOUT, "gripper drive took too long", GRIPPER)
            ended = None
            if self.reply.impact & 0x20:
                ended = "impact"
            else:
                window_cycles += 1
                if window_cycles >= GRIPPER_STALL_CYCLES:
                    if abs(position - window_start) < GRIPPER_STALL_COUNTS:
                        ended = "stall"
                    else:
                        window_start, window_cycles = position, 0
            if ended:
                if travelled < GRIPPER_MIN_TRAVEL and not at_end_ok:
                    raise HomeError(ERR_GRIPPER, f"gripper {ended} before it moved", GRIPPER)
                break
        else:
            raise HomeError(ERR_GRIPPER, "gripper never reached the end of its travel", GRIPPER)
        yield from self.send(MOVE, 1 << GRIPPER, 0, 0)
        self.driven = {}
        yield from self.gripper_off()

    def gripper_off(self):
        yield from self.send(CONTROL_OFF, 1 << GRIPPER)
        yield from self.send(MOTORS, 1 << GRIPPER)

    def coarse_17(self, a):
        """Open, wait, close; the gripper has no switch."""
        v = AXES[a].velocity
        yield from self.gripper_drive(v)
        yield from self.wait(15)
        yield from self.gripper_drive(-v)

    COARSE = {14: coarse_14, 15: coarse_15, 16: coarse_16, 17: coarse_17}

    # --- planner moves, fine search, tail -------------------------------------------------------
    def ramp(self, deltas, axis):
        """Move the setpoints of several motors to measured + delta in a straight ramp, then settle.

        Raises ``HomeError`` if a motor has not reached its target ``SETTLE_COUNTS`` after
        ``SETTLE_CYCLE_CAP`` cycles, or on an impact flag or the axis' timeout.
        """
        targets = {m: self.reply.counts[m] + d for m, d in deltas.items()}
        start = {m: self.setp[m] for m in deltas}
        longest = max((abs(targets[m] - start[m]) for m in deltas), default=0)
        steps = max(1, -(-longest // RAMP_COUNTS_PER_CYCLE))
        for k in range(1, steps + 1):
            for m in deltas:
                self.setp[m] = start[m] + (targets[m] - start[m]) * k // steps
            yield from self.cycle()
            self.check_impact(axis)
            self.check_time(axis)
            self.check_lead(deltas, axis)
        for i in range(SETTLE_CYCLE_CAP + 1):
            if all(abs(self.reply.counts[m] - targets[m]) <= SETTLE_COUNTS for m in deltas):
                break
            if i == SETTLE_CYCLE_CAP:
                late = max(deltas, key=lambda m: abs(self.reply.counts[m] - targets[m]))
                raise HomeError(ERR_NOT_SETTLED, f"{NAMES[late]} did not reach its target", late)
            yield from self.cycle()
            self.check_impact(axis)
            self.check_time(axis)
            self.check_lead(deltas, axis)
        yield from self.send(CLEAR)

    def fine(self, a):
        """Back off the switch in coarse steps, then creep back onto its edge."""
        cfg = AXES[a]
        back = max(10, abs(cfg.noenc90) // 450)
        creep = 1 if a in (WRIST_M1, WRIST_M2) else 2
        sign = 1 if cfg.velocity > 0 else -1
        back_dir, creep_dir = (1, -1) if cfg.type == 14 else (-1, 1)
        back_dir, creep_dir = back_dir * sign, creep_dir * sign
        self.hold()
        yield from self.send(CLEAR)
        yield from self.send(MODE, 0xFF, MODE_S)
        first = True
        while self.on(a):
            step = 3 * back if first else back
            first = False
            yield from self.ramp(position_vector(a, back_dir * step), a)
            self.check_travel(a)
        yield from self.send(MODE, 0xFF, MODE_S)
        for _ in range(MAX_CREEPS):
            if self.on(a):
                break
            for motor, delta in position_vector(a, creep_dir * creep).items():
                self.setp[motor] += delta
            yield from self.wait(3)
            yield from self.send(CLEAR)
            self.check_impact(a)
            self.check_time(a)
            self.check_travel(a)
            self.check_lead(position_vector(a, creep_dir * creep), a)
        else:
            raise HomeError(ERR_NOT_FOUND, f"{NAMES[a]} edge not found while creeping", a)

    def tail(self, a, offset):
        yield from self.wait(10)
        self.hold()
        yield from self.wait(1)
        yield from self.send(CLEAR)
        if offset:
            yield from self.send(MODE, 0xFF, MODE_S)
            yield from self.ramp(position_vector(a, offset), a)
        if self.version < 9:
            yield from self.mode_t(a)
        zero_order = {SHOULDER: (ELBOW, WRIST_M1, WRIST_M2, SHOULDER), ELBOW: (WRIST_M1, WRIST_M2, ELBOW),
                      WRIST_M1: (WRIST_M2, WRIST_M1), WRIST_M2: (WRIST_M1, WRIST_M2)}.get(a, (a,))
        for motor in zero_order:
            yield from self.send(SET_POSITION, 1 << motor)
            if self.version > 8:
                self.setp[motor] = 0
                yield from self.send(CLEAR)
        self.hold()
        yield from self.wait(2)
        yield from self.send(MODE, 0xFF, MODE_S)
        self.check_impact(a)

    def axis(self, a, *, slave, fine, offset):
        self.axis_t0 = self.reply.now
        self.axis_origin = list(self.reply.counts)
        if slave:
            yield from self.slave_pairs(a)
        yield from self.COARSE[AXES[a].type](self, a)
        if AXES[a].type == 17:
            yield from self.send(SET_POSITION, 1 << a)
            yield from self.wait(20)
            return
        if fine:
            yield from self.fine(a)
        yield from self.tail(a, offset)

    # --- the whole home --------------------------------------------------------------------------
    def prepass(self):
        """The ER-4u pre-pass: shoulder and elbow without offsets, close the gripper, then the wrists."""
        yield from self.axis(SHOULDER, slave=False, fine=False, offset=0)
        yield from self.axis(ELBOW, slave=False, fine=False, offset=0)
        yield from self.gripper_drive(-AXES[GRIPPER].velocity, at_end_ok=True)
        yield from self.send(SET_POSITION, 1 << GRIPPER)
        yield from self.wait(20)
        yield from self.axis(WRIST_M2, slave=False, fine=False, offset=AXES[WRIST_M2].offset)
        yield from self.axis(WRIST_M1, slave=False, fine=False, offset=0)
        self.hold()
        yield from self.send(CLEAR)
        yield from self.send(MODE, 0xFF, MODE_S)
        yield from self.ramp(position_vector(WRIST_M1, PREPASS_PITCH_COUNTS), WRIST_M1)
        self.check_impact(WRIST_M1)

    def sweep(self):
        """End-of-group sweep: zero every axis again, one at a time, and switch the gripper off."""
        yield from self.wait(1)
        yield from self.send(CLEAR)
        self.hold()
        if self.version < 9:
            yield from self.send(MODE, 0x3F, MODE_T)
        for a in range(6):
            self.setp[a] = 0
            yield from self.send(SET_POSITION, 1 << a)
            yield from self.wait(1)
            yield from self.send(CLEAR)
            yield from self.send(MODE, 1 << a, MODE_S)
            yield from self.wait(1)
        self.hold()
        yield from self.gripper_off()

    def abort(self):
        """Stop every motor, hold the setpoints at the measured position and go back to mode S."""
        self.aborting = True
        self.driven = {}
        yield from self.send(CLEAR)
        for a in range(6):
            yield from self.send(MOVE, 1 << a, 0, 0)
        yield from self.wait(2)
        self.hold()                       # after the drives are off, so the setpoints do not pull anything back
        yield from self.send(MODE, 0xFF, MODE_S)

    def run(self, axes):
        try:
            return (yield from self.home(axes))
        except HomeError:
            yield from self.abort()
            raise

    def home(self, axes):
        if axes is None:
            yield from self.prepass()
            order = HOMING_SEQUENCE
        else:
            order = tuple(axes)
        done = []
        for a in order:
            yield from self.axis(a, slave=True, fine=a != GRIPPER, offset=AXES[a].offset)
            done.append(a)
        if axes is None:
            yield from self.sweep()
            yield from self.gripper_off()
        return HomeResult(tuple(done), self.cycles, self.reply.now - self.t0)


def vendor_home(first, *, version=0, axes=None) -> Generator:
    """Home the arm the vendor's way. ``first`` is the latest reply; ``axes=None`` runs everything.

    ``version`` is the controller version from the ``72`` reply (below 9 sends the extra mode
    messages and skips the per-``48`` clears). ``axes`` homes just those axes, without the pre-pass
    or the sweep, the way the vendor's ``Home(axis_number)`` does.
    """
    return (yield from _Run(first, version).run(axes))
