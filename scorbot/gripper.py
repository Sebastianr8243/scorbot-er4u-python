"""The gripper: ``GripperMove`` and the ``Scorbot`` methods that move it.

Moved out of robot.py unchanged (docs/archive/specs/2026-10-12-code-health-design.md): the class and every method
body are identical to what was in robot.py. ``Scorbot`` takes the methods from ``GripperMixin``; they use
the session state, ``_command``, ``_latch_fault``, ``_motion_state``, ``_record`` and the stop events
through ``self``. ``scorbot.robot`` still exports ``GripperMove``.
"""

from dataclasses import asdict, dataclass

from .calibration import signed_count_delta
from .errors import MotionStopped, ScorbotError
from .state import JOINTS, RobotState


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
        """Open the gripper by the legacy fixed travel. See ``move_gripper``."""
        return self.move_gripper("open")

    def close_gripper(self) -> GripperMove:
        """Close the gripper by the legacy fixed travel. See ``move_gripper``."""
        return self.move_gripper("close")

    def move_gripper(self, direction: str) -> GripperMove:
        """Open or close the gripper with the legacy sequence. Never run on the arm.

        The gripper is switched on, its setpoint is ramped by a fixed number
        of counts (2700 with the inherited settings), held until the
        gripper stops moving, and the gripper is switched off. There is no
        force control and no force limit: closing on an object drives the
        setpoint well past it for a fraction of a second, so the first trials
        are with empty jaws, then something soft. Stopping short is not a
        fault; the result says how far it went. The move is fixed-size, not a
        position: a second close from closed pushes again.

        Needs motors enabled; it does not need homing. ``request_stop`` ends
        it early (``MotionStopped``); that is not an emergency stop. If an
        arm motor reads differently afterwards the session faults. The
        sequence uses a command (``4C``) this project has not yet sent to the
        arm, and the vendor closes its gripper another way
        (docs/protocol/VENDOR_DLL_PROTOCOL.md section 12).
        """
        with self._motion_lock:
            self._refuse_if_streaming()
            profile = self._legacy("motion_profile")
            if direction not in profile.GRIPPER_ORDERS:
                raise ValueError('Gripper direction must be "open" or "close"')
            if self._device is None:
                raise ScorbotError("Not connected")
            if not self._enabled:
                raise ScorbotError("Enable motors before moving the gripper")
            before = self._motion_state()
            if self._consume_stop():
                self._record("stop_before_motion", joint="gripper", direction=direction)
                raise MotionStopped("A stop was requested; the gripper was not moved",
                                    state=before, started=False)
            # A jog can return while the arm is still inside its settle band.
            # The gripper is never commanded on a moving arm.
            before = self._settled_after_stop(
                before, fault="The arm was not at rest before a gripper move")
            conf = self._legacy("conf")
            planned = sum(profile.gripper_increments(conf.readData("pinza", "vel"),
                                                     conf.readData("pinza", "ite_clamp")))
            self._record("gripper_start", direction=direction, planned_counts=planned,
                         state=asdict(before))
            trace = self._trace
            if trace is not None:
                trace.start()
            stopped = False
            try:
                self._command([profile.GRIPPER_ORDERS[direction], 1, 1])
            except MotionStopped:
                stopped = True
            finally:
                with self._stop_lock:
                    too_late = self._stop_event.is_set() and not stopped
                    self._stop_event.clear()
                if too_late:
                    self._record("stop_too_late", joint="gripper")
                if trace is not None:
                    packets, dropped = trace.stop()
                    self._record("motion_trace", joint="gripper", packets=packets,
                                 dropped_packets=dropped)
            if stopped:
                after = self._settled_after_stop(before)
                self._record("gripper_stopped", direction=direction, state=asdict(after))
                raise MotionStopped("The gripper move ended early on a stop request",
                                    state=after)
            after = self._motion_state(after_index=before.packet_index)
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
            # The count rises to open and falls to close in the inherited code.
            # That mapping is not measured, so the wrong sign is a fault, not
            # a successful move.
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
