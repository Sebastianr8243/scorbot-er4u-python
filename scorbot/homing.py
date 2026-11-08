"""The homing methods of Scorbot: the legacy home and the vendor home.

Moved out of robot.py unchanged (specs/2026-10-12-code-health-design.md (in git history)): every method body is
identical to the one that was in ``Scorbot``. ``Scorbot`` takes them from ``HomingMixin``; they use the
session state, ``_command``, ``_latch_fault`` and the stop events through ``self``.
"""

import contextlib
from dataclasses import asdict

from . import vendor_home as vendor_home_port
from .errors import MotionStopped, ScorbotError
from .pacing import HOMING_BUFFERS
from .state import HOME_SWITCH_BITS
from .vendor_home_source import TOTAL_SECONDS as VENDOR_HOME_SECONDS
from .vendor_home_source import VENDOR_HOME_ORDER, VendorHomeSource


class HomingMixin:

    def home(self, *, start_position_confirmed: bool = False):
        """Search switches from the confirmed legacy start pose; never Go Home."""
        with self._motion_lock:
            self._stream_resume_from = None
            self._refuse_if_streaming()
            if not start_position_confirmed:
                raise ValueError("Confirm the legacy homing start position first")
            if not self._enabled:
                raise ScorbotError("Enable motors before homing")
            before = self._motion_state()
            if self._consume_stop():
                self._record("stop_before_motion", joint="home")
                raise MotionStopped("A stop was requested; the Home was not started", state=before,
                                    started=False)
            unknown_bits = before.home_switch_bits & ~sum(HOME_SWITCH_BITS.values())
            if unknown_bits:
                # libdef.get_switch assumes byte 5 < 32; higher bits make it misread
                # every switch, so the search would miss or skip joints.
                self._record("home_refused", state=asdict(before))
                raise ScorbotError(
                    f"Home switch byte {before.home_switch_bits} has unexpected bits "
                    f"{unknown_bits:#x}; legacy homing would misread the switches")
            self._homed = False
            self._gripper_homed = False
            self._home_counts = None
            self._record("home_start", state=asdict(before))
            # The legacy search logs nothing between its first and last message. Copy its
            # packets (counts, error words, switch bits, collision flag) so a failed home
            # can be read back; the trace never changes a packet or a sleep.
            trace = self._trace
            if trace is not None:
                trace.start(keep="last")     # a failure is at the end of the search
            try:
                self._command([18, 1, 1], timeout=180.0)
            except MotionStopped:
                # A stop ended the switch search: not a fault, and not homed. Wait for the arm to settle.
                with self._stop_lock:
                    self._stop_event.clear()
                if trace is not None:
                    self._record_home_trace(trace, failing=True)
                after = self._settled_after_stop(before)
                self._record("home_stopped", state=asdict(after))
                raise MotionStopped("The Home ended early on a stop request", state=after) from None
            except BaseException:
                # The home's own error wins: a log failure here must not replace it.
                if trace is not None:
                    self._record_home_trace(trace, failing=True)
                raise
            if trace is not None:
                self._record_home_trace(trace, failing=False)
            with self._stop_lock:     # a stop after the search ended: the Home finished, keep no stale stop
                too_late = self._stop_event.is_set()
                self._stop_event.clear()
            if too_late:
                self._record("stop_too_late", joint="home")
            after = self._motion_state(after_index=before.packet_index)
            counts = after.encoder_counts
            if self._calibration:
                try:
                    for calibration in self._calibration.joints.values():
                        calibration.validate_home(counts[calibration.encoder])
                except ValueError as exc:
                    self._latch_fault(f"Home verification failed: {exc}")
                    self._record("home_failed", error=self._fault, state=asdict(after))
                    raise ScorbotError(self._fault) from exc
            self._home_counts = counts.copy()
            self._homed = True
            self._reset_prehome()
            self._record("home_complete", state=asdict(self._motion_state()))

    def _record_home_trace(self, trace, *, failing: bool, joint: str = "home") -> None:
        """Write the home's packets. After a failed home a log error is swallowed so the
        home's own error reaches the caller; after a good home it raises like any log write."""
        packets, dropped = trace.stop()
        try:
            self._record("motion_trace", joint=joint, kept="last", packets=packets,
                         dropped_packets=dropped)
        except Exception:
            if not failing:
                raise

    def _disable_best_effort(self) -> None:
        """Queue a motor-disable before a fault is latched (``_command`` refuses afterwards)."""
        try:
            self._command([16, 1, 1])
        except ScorbotError:
            pass

    @contextlib.contextmanager
    def _fail_closed(self, kind: str, label: str):
        """After the last jog of a coupled move: anything unexpected (a log write, a read)
        switches the motors off and latches a fault. A stop and errors that already
        latched pass through."""
        try:
            yield
        except (MotionStopped, ScorbotError):
            raise
        except BaseException as exc:
            self._disable_best_effort()
            self._latch_fault(f"{label}: {exc!r}")
            self._record(kind, error=self._fault)
            raise

    def vendor_home(self, *, operator_at_stop: bool = False, jaws_empty: bool = False,
                    wrist_clear: bool = False, controller_version: int = 0):
        """Home every axis the way the vendor's software does, from any pose near home.

        Runs ``scorbot.vendor_home``: the ER-4u pre-pass (shoulder, elbow, **close the gripper**,
        wrist roll, wrist pitch, then a wrist pitch move of about 37 degrees), then shoulder,
        elbow, roll, pitch, base and gripper, each found by a controller-side drive onto its
        switch, a fine search and the vendor's offset, then ``48`` zeroes the counters. So the
        wrist roll, the wrist pitch as an axis and the gripper all move here (owner decision
        2026-10-08, and only here: ``jog_joint`` and ``move_gripper`` are unchanged). The command
        bytes are the vendor DLL's own, recorded against a fake controller; the replies in that
        recording were a model, so **the controller's acceptance is unverified**. It stops only on
        the vendor's own checks (each axis' ``MaxTime``, emergency, impact, the vendor's stall and
        distance rules), plus two of ours: a motor moving against its drive (the drive sign is
        inferred) and a controller more than ``MAX_LAG`` messages behind.

        Needs: an operator at the physical stop, empty jaws, a clear wrist path, motors enabled,
        no stream, and switch bits below 32. Any error, a stop request or an interrupt first
        sends the vendor's stop (``47``, ``4D 0`` to every motor, hold the setpoints, ``4F FF
        53``), then switches the motors off and latches a fault: the counters may be partly
        zeroed, so build a new ``Scorbot`` before going on. ``request_stop`` ends it that way.
        Not an emergency stop: it needs a live USB link and a responsive worker, and the arm
        coasts; the physical stop is authoritative. Never run on the arm.
        """
        with self._motion_lock:
            self._stream_resume_from = None
            self._refuse_if_streaming()
            if not (operator_at_stop and jaws_empty and wrist_clear):
                raise ValueError("Confirm an operator is at the physical stop, the gripper jaws are "
                                 "empty and the wrist's path is clear first")
            if self._device is None:
                raise ScorbotError("Not connected")
            if not self._enabled:
                raise ScorbotError("Enable motors before homing")
            before = self._motion_state()
            known_bits = sum(HOME_SWITCH_BITS.values())
            if before.home_switch_bits & ~known_bits:
                self._record("home_refused", state=asdict(before))
                raise ScorbotError(f"Home switch byte {before.home_switch_bits} has unexpected "
                                   f"bits {before.home_switch_bits & ~known_bits:#x}")
            if self._consume_stop():
                # An old request must not abort the home the moment it starts: refuse once, like a jog.
                self._record("home_stopped", method="vendor", state=asdict(before))
                raise MotionStopped("A stop was requested before the vendor home started; "
                                    "nothing was sent", state=before, started=False)
            self._homed = False
            self._gripper_homed = False
            self._home_counts = None
            source = VendorHomeSource(version=controller_version, stop_event=self._stop_event,
                                      cancel_event=self._cancel_event, queue_limit=HOMING_BUFFERS)
            self._record("home_start", method="vendor", controller_version=controller_version,
                         wrist_homed=True, gripper_homed=True, state=asdict(before))
            trace = self._trace
            if trace is not None:
                trace.start(keep="last")     # a failure is at the end; also the evidence for the first run
            try:
                self._command([VENDOR_HOME_ORDER, source, 0], timeout=VENDOR_HOME_SECONDS + 60.0)
            except ScorbotError as exc:
                if trace is not None:
                    self._record_home_trace(trace, failing=True, joint="vendor_home")
                self._consume_stop()         # the stop that ended it must not stay set
                message = self._vendor_home_failure(source, exc)
                self._latch_fault(message)
                error = source.error
                self._record("home_failed", method="vendor", error=message,
                             code=getattr(error, "code", None), axis=getattr(error, "axis", None),
                             messages=source.messages, lag_first=source.lag_first, lag_max=source.lag_max)
                raise ScorbotError(message) from exc
            except BaseException:
                if trace is not None:
                    self._record_home_trace(trace, failing=True, joint="vendor_home")
                raise
            with self._fail_closed("home_failed", "Vendor home failed unexpectedly after the last message"):
                if trace is not None:     # a log write that fails here must still switch the motors off
                    self._record_home_trace(trace, failing=False, joint="vendor_home")
                after = self._motion_state(after_index=before.packet_index)
                if self._consume_stop():
                    # The last message has been sent, so 48 has already re-zeroed the controller's
                    # counters: "stopped, still homed and enabled" would be false either way. Treat it
                    # like any other failed vendor home (review 2026-10-11).
                    self._disable_best_effort()
                    self._latch_fault("A stop was requested as the vendor home ended. The controller's "
                                      "counters were already zeroed and motors-off was sent (check the MOTORS "
                                      "LED): create a new Scorbot before continuing")
                    self._record("home_failed", method="vendor", error=self._fault, code=vendor_home_port.ERR_STOPPED,
                                 axis=None, messages=source.messages, lag_first=source.lag_first,
                                 lag_max=source.lag_max, state=asdict(after))
                    raise ScorbotError(self._fault)
                if self._calibration:
                    try:
                        for calibration in self._calibration.joints.values():
                            calibration.validate_home(after.encoder_counts[calibration.encoder])
                    except ValueError as exc:
                        self._disable_best_effort()
                        self._latch_fault(f"Home verification failed: {exc}")
                        self._record("home_failed", method="vendor", error=self._fault, lag_first=source.lag_first,
                                     lag_max=source.lag_max, state=asdict(after))
                        raise ScorbotError(self._fault) from exc
                self._home_counts = after.encoder_counts.copy()
                self._homed = True
                self._gripper_homed = True       # the vendor Home zeroes the gripper (48); the others do not
                self._vendor_joint_target = ()   # lazily use the vendor's home joints on the first opt-in move
                self._reset_prehome()
                result = source.result
                self._record("home_complete", method="vendor", wrist_homed=True, gripper_homed=True,
                             axes=[vendor_home_port.NAMES[a] for a in result.axes_done],
                             cycles=result.cycles, seconds=round(result.seconds, 1),
                             lag_first=source.lag_first, lag_max=source.lag_max,
                             state=asdict(self._motion_state()))

    @staticmethod
    def _vendor_home_failure(source, exc) -> str:
        """The message for a failed vendor home: which axis, what error, what state it leaves."""
        error = source.error
        tail = ("Motors-off was sent but is not confirmed (it needs a live USB link): check the MOTORS "
                "LED. The counters may be partly zeroed: create a new Scorbot before continuing, "
                "and use the physical stop if the arm is moving")
        if getattr(source, "abandoned", False):
            tail = ("The USB link was dead, so the stop messages could not be sent. " + tail)
        if isinstance(error, vendor_home_port.HomeError):
            axis = (f" on the {vendor_home_port.NAMES[error.axis]}" if error.axis is not None else "")
            return f"Vendor home failed{axis} (error {error.code}): {error}. {tail}"
        if error is not None:
            return f"Vendor home failed unexpectedly: {error!r}. {tail}"
        return f"{exc}. {tail}"
