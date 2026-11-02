"""The gripper: ``GripperMove`` and the ``Scorbot`` methods that move it.

Moved out of robot.py on 2026-10-12 (specs/2026-10-12-code-health-design.md (in git history)). ``Scorbot`` takes
the methods from ``GripperMixin``; they use the session state, ``_command``, ``_latch_fault``, ``_motion_state``,
``_record`` and the stop events through ``self``. ``scorbot.robot`` still exports ``GripperMove``. Since 2026-11-01
the move is the vendor's post-Home one (``scorbot/vendor_gripper.py``, ``docs/protocol/LEDGER.md`` PL61).
"""

from dataclasses import asdict, dataclass

from . import vendor_gripper
from .calibration import signed_count_delta
from .errors import MotionStopped, ScorbotError
from .state import JOINTS, RobotState
from .vendor_home_source import VendorHomeSource


@dataclass(frozen=True)
class GripperMove:
    """What one gripper move did, in gripper encoder counts (rising opens).

    ``full_travel`` is False when the gripper stopped short of the planned
    travel: it closed on something, or reached the end of its own travel. The
    counts cannot tell those two apart.
    """

    direction: str
    planned_counts: int
    moved_counts: int
    full_travel: bool
    state: RobotState


# GripperMove was defined in robot.py. Keep that identity: a pickle names ``scorbot.robot``, which still exports it,
# so what an older install wrote still loads and what this one writes still loads on an older install.
GripperMove.__module__ = "scorbot.robot"


class GripperMixin:

    def open_gripper(self) -> GripperMove:
        """Open the gripper to ``RangeEnc`` counts after a vendor Home. See ``move_gripper``."""
        return self.move_gripper("open")

    def close_gripper(self) -> GripperMove:
        """Close the gripper to 0 counts after a vendor Home. See ``move_gripper``."""
        return self.move_gripper("close")

    def move_gripper(self, direction: str) -> GripperMove:
        """Open or close the gripper the way the vendor does after a Home. Never run on the arm.

        Refused, with nothing sent, unless a vendor Home finished in this session (``vendor_home``): before
        it the jaw position is unknown. A legacy or inch Home does not count, because neither zeroes the
        gripper. A fault or a lost link clears the Home, and with it the gripper.

        After a vendor Home the gripper goes to an absolute target: ``RangeEnc`` counts (5000, fully open)
        or 0 (closed). The messages are the lab DLL's (``docs/protocol/LEDGER.md`` PL51): mode S, slave,
        gripper motor on, an S-curve of setpoints on the gripper slot only (the arm slots hold their
        measured counts), then control off and motor off. If the gripper is already within
        ``vendor_gripper.AT_TARGET_COUNTS`` of the target, nothing is sent and the move is 0 counts.
        There is no force control and no force limit; stopping short (an object, or the end of travel)
        is a result, not a fault. Whether the jaw arrives before the control-off is a lab check.

        Needs motors enabled and the arm at rest. ``request_stop`` ends it early (``MotionStopped``): the
        gripper is held where it is and switched off; that is not an emergency stop. The emergency input, a
        USB error or an unreadable reply (also during a stop, or with the gripper already at its target)
        switches the motors off and latches a fault, as does an arm motor that reads
        differently afterwards or a gripper that moved the wrong way.
        """
        with self._motion_lock:
            self._refuse_if_streaming()
            target = vendor_gripper.target_counts(direction)
            if self._device is None:
                raise ScorbotError("Not connected")
            if not self._enabled:
                raise ScorbotError("Enable motors before moving the gripper")
            if not (self._homed and self._gripper_homed):
                raise ScorbotError("Home the arm before moving the gripper (vendor_home; a legacy or inch "
                                   "Home does not zero the gripper)")
            before = self._motion_state()
            if self._consume_stop():
                self._record("stop_before_motion", joint="gripper", direction=direction)
                raise MotionStopped("A stop was requested; the gripper was not moved",
                                    state=before, started=False)
            # A jog can return while the arm is still inside its settle band.
            # The gripper is never commanded on a moving arm.
            before = self._settled_after_stop(
                before, fault="The arm was not at rest before a gripper move")
            source = VendorHomeSource(start=lambda first: vendor_gripper.vendor_gripper(first, target),
                                      stop_event=self._stop_event, cancel_event=self._cancel_event,
                                      total_seconds=vendor_gripper.TOTAL_SECONDS)
            self._record("gripper_start", direction=direction, target_counts=target, state=asdict(before))
            trace = self._trace
            if trace is not None:
                trace.start()
            try:
                self._command([vendor_gripper.GRIPPER_ORDER, source, 0],
                              timeout=vendor_gripper.TOTAL_SECONDS + 30.0)
            except ScorbotError as exc:
                # _command has latched the fault and sent motors-off; say what the move saw.
                self._consume_stop()
                error = source.error
                self._record("gripper_fault", error=self._fault, code=getattr(error, "code", None),
                             detail=str(error) if error is not None else None, messages=source.messages)
                raise ScorbotError(f"Gripper move failed: {error or exc}. Motors-off was sent but is not "
                                   f"confirmed: check the MOTORS LED") from exc
            finally:
                if trace is not None:
                    packets, dropped = trace.stop()
                    self._record("motion_trace", joint="gripper", packets=packets,
                                 dropped_packets=dropped)
            run = source.result
            with self._stop_lock:
                too_late = self._stop_event.is_set() and not run.stopped
                self._stop_event.clear()
            if too_late:
                self._record("stop_too_late", joint="gripper")
            if run.stopped:
                after = self._settled_after_stop(before)
                self._record("gripper_stopped", direction=direction, state=asdict(after))
                raise MotionStopped("The gripper move ended early on a stop request", state=after)
            if not run.sent:
                after = self._motion_state(after_index=before.packet_index)
                self._record("gripper_complete", direction=direction, planned_counts=0, moved_counts=0,
                             full_travel=True, already_there=True, state=asdict(after))
                return GripperMove(direction=direction, planned_counts=0, moved_counts=0,
                                   full_travel=True, state=after)
            planned = abs(run.target - run.start)
            # The jaw may still be closing in on the target when the control goes off; report where it stops.
            after = self._settled_after_stop(
                before, fault="The gripper was still moving after its move")
            try:
                moved = {name: signed_count_delta(after.encoder_counts[name],
                                                  before.encoder_counts[name])
                         for name in JOINTS}
            except ValueError as exc:
                moved, disturbed = None, f"counts cannot be compared: {exc}"
            else:
                disturbed = {name: counts for name, counts in moved.items()
                             if name != "gripper"
                             and abs(counts) > self.GRIPPER_ARM_TOLERANCE_COUNTS}
            if disturbed:
                self._latch_fault(f"Arm motors moved during a gripper move: {disturbed}")
                if self._link_alive():
                    self._commands.put([16, 1, 1])  # Best effort; never an emergency stop.
                self._record("gripper_fault", error=self._fault, state=asdict(after))
                raise ScorbotError(self._fault)
            travelled = moved["gripper"]
            # The count rises to open (RangeEnc is the open end). The wrong sign is a fault, not a move.
            along = travelled if direction == "open" else -travelled
            if along < -self.GRIPPER_ARM_TOLERANCE_COUNTS:
                self._latch_fault(f"The gripper moved the wrong way: asked to {direction}, "
                                  f"its count changed by {travelled:+d}")
                if self._link_alive():
                    self._commands.put([16, 1, 1])  # Best effort; never an emergency stop.
                self._record("gripper_fault", error=self._fault, state=asdict(after))
                raise ScorbotError(self._fault)
            result = GripperMove(
                direction=direction, planned_counts=planned, moved_counts=travelled,
                full_travel=along >= planned - self.GRIPPER_FULL_TRAVEL_TOLERANCE_COUNTS,
                state=after)
            self._record("gripper_complete", direction=direction, planned_counts=planned,
                         moved_counts=travelled, full_travel=result.full_travel,
                         state=asdict(after))
            return result
