"""The homing methods of Scorbot: the legacy home, the pre-home jogs, the park, home by inching and
the vendor home.

Moved out of robot.py unchanged (docs/specs/2026-10-12-code-health-design.md): every method body is
identical to the one that was in ``Scorbot``. ``Scorbot`` takes them from ``HomingMixin``; they use the
session state, ``_command``, ``_latch_fault`` and the stop events through ``self``.
"""

import contextlib
import math
import time
from dataclasses import asdict
from types import SimpleNamespace

from . import inch_home, joint_move, source_model
from . import vendor_home as vendor_home_port
from .calibration import signed_count_delta
from .errors import MotionStopped, ScorbotError
from .state import HOME_SWITCH_BITS
from .vendor_home_source import TOTAL_SECONDS as VENDOR_HOME_SECONDS
from .vendor_home_source import VENDOR_HOME_ORDER, VendorHomeSource


class HomingMixin:

    def home(self, *, start_position_confirmed: bool = False):
        """Search switches from the confirmed legacy start pose; never Go Home."""
        with self._motion_lock:
            self._refuse_if_streaming()
            if not start_position_confirmed:
                raise ValueError("Confirm the legacy homing start position first")
            if not self._enabled:
                raise ScorbotError("Enable motors before homing")
            before = self._motion_state()
            unknown_bits = before.home_switch_bits & ~sum(HOME_SWITCH_BITS.values())
            if unknown_bits:
                # libdef.get_switch assumes byte 5 < 32; higher bits make it misread
                # every switch, so the search would miss or skip joints.
                self._record("home_refused", state=asdict(before))
                raise ScorbotError(
                    f"Home switch byte {before.home_switch_bits} has unexpected bits "
                    f"{unknown_bits:#x}; legacy homing would misread the switches")
            self._homed = False
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
            except BaseException:
                # The home's own error wins: a log failure here must not replace it.
                if trace is not None:
                    self._record_home_trace(trace, failing=True)
                raise
            if trace is not None:
                self._record_home_trace(trace, failing=False)
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

    def _honour_late_stop(self, what: str, state) -> None:
        """A stop that arrived with the last jog of a coupled move ends the move now."""
        if self._consume_stop():
            self._record(f"{what}_stopped", state=asdict(state))
            raise MotionStopped(f"A stop was requested during the {what}", state=state)

    def _coupled_jog(self, name: str, counts: int, speed: int, *, homing: bool) -> None:
        """Jog motor ``name`` by ``counts``, or the wrist pitch (``"wrist_pitch"``, in the counts
        of wrist motor 1; motor 2 moves the opposite way). Part of a coupled joint move.

        Split into steps of about a degree (``joint_move.STEP_LIMIT_COUNTS``), each one a legacy
        bounded jog and each checked: a step that moved under 40 percent of what was asked
        raises ``joint_move.StallError`` before the next jog. The wrist pitch jog is the only
        wrist jog the SDK ever sends, and only from here. The legacy wrist loop takes no stop
        event, so ``request_stop`` cannot cut a wrist jog short: each is small, and the stop is
        checked before the next one by ``_jog_joint``.
        """
        key = "wrist_motor_1" if name == "wrist_pitch" else name
        step = joint_move.STEP_LIMIT_COUNTS[name]
        remaining = int(counts)
        while remaining:
            chunk = max(-step, min(step, remaining))
            remaining -= chunk
            one = self.preview_jog(name, 1.0)["motor_count_deltas"][key]     # counts per degree
            degrees = chunk / one                                            # signed as the jog is
            expected = self.preview_jog(name, degrees, speed=speed)["motor_count_deltas"][key]
            keys = ("wrist_motor_1", "wrist_motor_2") if name == "wrist_pitch" else (key,)
            sides = {"wrist_motor_1": 1, "wrist_motor_2": -1}        # motor 2 moves the other way
            before_counts = self._motion_state().encoder_counts
            after = self._jog_joint(name, degrees, speed, homing=homing, wrist_ok=True)
            if abs(expected) < joint_move.PROGRESS_CHECK_COUNTS:
                continue
            for motor in keys:
                asked = expected * sides.get(motor, 1)
                moved = signed_count_delta(after.encoder_counts[motor], before_counts[motor])
                if moved * asked < 0.4 * asked * asked:
                    raise joint_move.StallError(
                        f"the {name} did not follow ({motor}): asked {asked:+d} counts, "
                        f"moved {moved:+d}")

    def _coupled_run(self, speed: int, *, homing: bool, coupled: bool = True):
        """One run of coupled joint moves with one cumulative target: ``run.move(joint, degrees)``
        moves one joint with the coupled motors following, as the vendor's joint mode does;
        ``run.settle()`` flushes what the coupled motors still owe; ``run.speed`` may be changed
        between moves. What a small move owes the coupled motors (a quarter of a degree of the
        shoulder owes the wrist 7 counts) stays in the target until it is worth a jog, so it
        is not lost between moves: keep one run for a whole phase, never one per move.
        Counts come from ``_motion_state``, so a stale reading latches a fault like any other.
        """
        target = joint_move.CoupledTarget(self._motion_state().encoder_counts, coupled=coupled)
        run = SimpleNamespace(speed=speed, anchor=dict(target.start), coupled=coupled)

        def read():
            return self._motion_state().encoder_counts

        def jog(name, counts):
            self._coupled_jog(name, counts, run.speed, homing=homing)

        def move(joint, degrees):
            target.add(joint, degrees)
            joint_move.run(target, read, jog, primary=joint)
            run.anchor = read()

        def settle():
            converged = joint_move.run(target, read, jog, primary=None, final=True)
            run.anchor = read()
            if not converged:
                # The coupled motors still owe more than a jog can fix: the elbow angle and the
                # wrist are no longer where the move meant to hold them. Not a success.
                owed = target.owed(run.anchor)
                self._record("coupled_unsettled", owed=owed)
                raise joint_move.StallError(
                    f"the coupled motors did not settle; still owed (counts): {owed}")

        run.move, run.settle = move, settle
        return run

    def _moved_since(self, before, threshold_counts: int = 10) -> bool:
        """True if any arm motor is now more than a few counts from ``before``; fails closed."""
        try:
            now = self._motion_state().encoder_counts
            return any(abs(signed_count_delta(now[m], before.encoder_counts[m])) > threshold_counts
                       for m in source_model.MOTORS)
        except Exception:
            return True

    def _fail_coupled(self, kind: str, joint, message: str) -> ScorbotError:
        """A coupled move failed for a reason that is a fault: motors off, latch, record."""
        self._disable_best_effort()
        self._latch_fault(message)
        self._record(kind, joint=joint, error=message)
        return ScorbotError(message)

    def pre_home_jog(self, joint, degrees, *, operator_at_stop: bool = False, speed: int = 6,
                     coupled: bool = False):
        """Bring the arm toward its home pose before homing: one joint, a few degrees.

        The vendor's routine is "bring the robot to a position near home, and activate the
        homing procedure" (arm manual p. 15), and SCORBASE allows a joint-mode move before
        homing for exactly that (its manual p. 31). By default this drives only the motor of
        the joint asked for, which is what the vendor DLL's manual jog does
        (``docs/protocol/VENDOR_MANUAL_MOVE_TRACE.md``): no wrist command is sent, and the
        elbow and wrist pitch change with the mechanics. ``coupled=True`` is this SDK's own
        option, never run on the arm: the elbow and the wrist pitch follow the shoulder, and
        the wrist pitch follows the elbow, so the other joints keep their angles
        (``joint_move``). Base, shoulder and elbow only,
        at most 2 degrees a call and 60 degrees in all from where the session found each,
        and only while the arm is not homed (afterwards use ``jog_joint``). No joint limit
        is checked: there is no home to measure them from. From the vendor formulas, never
        run on the arm. ``request_stop`` ends it between jogs (``MotionStopped``, no fault);
        a joint that does not follow, or an interrupt, latches a fault and switches the
        motors off. Not an emergency stop: the physical stop is authoritative.
        """
        with self._motion_lock:
            self._refuse_if_streaming()
            if not operator_at_stop:
                raise ValueError("Confirm an operator is at the physical stop and the "
                                 "arm's path is clear first")
            if joint not in joint_move.JOINTS:
                raise ValueError(f"A pre-home move names one of {joint_move.JOINTS}, "
                                 f"not {joint!r}")
            if (isinstance(degrees, bool) or not isinstance(degrees, (int, float))
                    or not math.isfinite(degrees)
                    or not 0 < abs(degrees) <= joint_move.PRE_HOME_STEP_DEG):
                raise ValueError(f"A pre-home move is finite, nonzero and at most "
                                 f"{joint_move.PRE_HOME_STEP_DEG:g} degrees")
            if isinstance(speed, bool) or not isinstance(speed, int) or not 1 <= speed <= 20:
                raise ValueError("Legacy speed must be an integer from 1 to 20")
            if self.max_jog_degrees < 2.0:
                raise ValueError("A coupled move needs a jog ceiling of at least 2 degrees")
            if self._device is None:
                raise ScorbotError("Not connected")
            if not self._enabled:
                raise ScorbotError("Enable motors before moving the arm")
            if self._homed:
                raise ScorbotError("The arm is homed: use jog_joint, not a pre-home move")
            if abs(self._prehome_travel[joint] + degrees) > joint_move.PRE_HOME_CAP_DEG + 1e-9:
                raise ValueError(f"The {joint} would pass the {joint_move.PRE_HOME_CAP_DEG:g} "
                                 "degree pre-home cap from where this session found it")
            before = self._motion_state()
            self._record("pre_home_start", joint=joint, degrees=degrees, speed=speed,
                         coupled=bool(coupled),
                         travel=dict(self._prehome_travel), wrist_homed=False,
                         state=asdict(before))
            try:
                run = self._prehome_run
                if run is not None and (not coupled or not run.coupled or any(
                        abs(signed_count_delta(before.encoder_counts[m], run.anchor[m])) > 60
                        for m in source_model.MOTORS)):
                    run = None       # the arm was moved some other way since: start from where it is
                # A single-motor move starts a fresh target every time, so it can only ever
                # command the motor it was asked for, never "correct" an earlier move's motor.
                if run is None:                         # one target for the whole coupled phase
                    self._prehome_run = self._coupled_run(speed, homing=True,
                                                          coupled=bool(coupled))
                self._prehome_run.speed = speed
                self._prehome_run.move(joint, degrees)
                self._prehome_run.settle()
            except BaseException as exc:
                if isinstance(exc, KeyboardInterrupt) or (
                        isinstance(exc, Exception)
                        and not isinstance(exc, (joint_move.StallError, ValueError, ScorbotError))):
                    # Motors off and latched first: accounting below does serial reads, and a
                    # second interrupt landing in them must not find the motors still on.
                    self._disable_best_effort()
                    self._latch_fault(
                        "Python interrupted during a pre-home move"
                        if isinstance(exc, KeyboardInterrupt)
                        else f"Pre-home move of the {joint} failed unexpectedly: {exc!r}")
                    self._record("pre_home_failed", joint=joint, error=self._fault)
                # Whatever stopped it, count what the joint really moved (not what was asked)
                # and drop the target: its unmet distance must not be done by the next command.
                self._account_prehome(joint, before)
                self._prehome_run = None
                if isinstance(exc, (joint_move.StallError, ValueError)):
                    raise self._fail_coupled(
                        "pre_home_failed", joint,
                        f"Pre-home move of the {joint} failed: {exc}") from exc
                if isinstance(exc, MotionStopped):
                    raise                # not a fault: the arm is where the last jog left it
                if isinstance(exc, ScorbotError):
                    self._record("pre_home_failed", joint=joint, error=str(exc))
                    raise
                raise
            self._account_prehome(joint, before)
            after = self._motion_state()
            self._record("pre_home_complete", joint=joint, degrees=degrees,
                         travel=dict(self._prehome_travel), state=asdict(after))
            self._honour_late_stop("pre-home move", after)
            return after

    def _account_prehome(self, joint, before) -> None:
        """Add what ``joint`` really moved since ``before`` (measured, in degrees) to the
        pre-home travel. Best effort: a fault that makes the arm unreadable adds nothing."""
        try:
            now = self._motion_state()
            moved = signed_count_delta(now.encoder_counts[joint], before.encoder_counts[joint])
            self._prehome_travel[joint] += moved / joint_move.vector(joint)[joint]
        except (ScorbotError, ValueError, KeyError):
            pass

    def park_at_home(self, *, operator_at_stop: bool = False, speed: int = 6,
                     tolerance_counts: int = 45) -> dict:
        """Send the arm back to the home it was homed at: the vendor's "Go Home".

        Every motor goes back to its home count, base, shoulder and elbow with the wrist pitch
        following, in steps of about a degree kept in proportion (``joint_move``), so the next
        session starts near home. Runs through the homed jog path, so the whole-pose joint-limit
        check applies to every step. Returns ``{"parked": bool, "reason": str | None,
        "errors": {...}}``: not being parked, because a step was refused or the passes ran out,
        is a result, not a fault. ``request_stop`` ends it between jogs (``MotionStopped``); a
        joint that does not follow latches a fault and switches the motors off. From the vendor
        formulas, never run on the arm; the physical stop is authoritative.
        """
        with self._motion_lock:
            self._refuse_if_streaming()
            if not operator_at_stop:
                raise ValueError("Confirm an operator is at the physical stop and the "
                                 "arm's path is clear first")
            if isinstance(speed, bool) or not isinstance(speed, int) or not 1 <= speed <= 20:
                raise ValueError("Legacy speed must be an integer from 1 to 20")
            if self.max_jog_degrees < 2.0:
                raise ValueError("A coupled move needs a jog ceiling of at least 2 degrees")
            if self._device is None:
                raise ScorbotError("Not connected")
            if not self._enabled:
                raise ScorbotError("Enable motors before moving the arm")
            if not self._homed or self._home_counts is None:
                raise ScorbotError("Home first: there is no home to return to")
            before = self._motion_state()
            try:
                target = joint_move.CoupledTarget(before.encoder_counts)
                target.desired = {m: float(signed_count_delta(self._home_counts[m],
                                                              before.encoder_counts[m]))
                                  for m in source_model.MOTORS}
            except (KeyError, ValueError) as exc:
                raise self._fail_coupled(
                    "park_failed", None, f"Cannot place the arm relative to home: {exc!r}") from exc
            self._record("park_start", state=asdict(before))
            roll_start = target.roll_error(before.encoder_counts)
            reason = None
            completed = 0                 # park jogs that ran to the end
            try:
                for _ in range(joint_move.PARK_MAX_PASSES):
                    counts = self._motion_state().encoder_counts
                    owed = target.owed(counts)
                    drift = target.roll_error(counts) - roll_start
                    if abs(drift) > joint_move.ROLL_FAULT_COUNTS:
                        raise joint_move.StallError(
                            f"the wrist roll has drifted {drift:+.0f} counts during the park: "
                            "the wrist jogs keep landing short on one motor and nothing jogs "
                            "the roll")
                    jogs = joint_move.proportional_jogs(owed, joint_move.STEP_LIMIT_COUNTS)
                    if not jogs:
                        break
                    for name, counts in jogs:
                        self._coupled_jog(name, counts, speed, homing=False)
                        completed += 1
                else:
                    reason = "the park ran out of passes"
            except ValueError as exc:     # a gate refused the next step; nothing was sent for it
                if completed or self._moved_since(before):
                    # Part of the park already ran: motor and home state are no longer what the
                    # session believes, so this is a fault, not a polite "not parked".
                    raise self._fail_coupled(
                        "park_failed", None, f"Park stopped part-way: {exc}") from exc
                reason = str(exc)
            except joint_move.StallError as exc:
                raise self._fail_coupled(
                    "park_failed", None, f"Park failed: {exc}") from exc
            except MotionStopped:
                raise
            except ScorbotError as exc:
                self._record("park_failed", error=str(exc))
                raise
            except KeyboardInterrupt:
                self._disable_best_effort()
                self._latch_fault("Python interrupted during the park")
                self._record("park_failed", error=self._fault)
                raise
            except Exception as exc:        # anything unexpected leaves the park half done
                self._disable_best_effort()
                self._latch_fault(f"Park failed unexpectedly: {exc!r}")
                self._record("park_failed", error=self._fault)
                raise
            with self._fail_closed("park_failed", "Park failed unexpectedly after the last jog"):
                after = self._motion_state()
                self._honour_late_stop("park", after)
                owed = target.owed(after.encoder_counts)
                error = target.errors(after.encoder_counts)
                roll = (error["wrist_motor_1"] + error["wrist_motor_2"]) / 2
                parked = reason is None and all(abs(v) <= tolerance_counts for v in owed.values())
                if reason is None and not parked:
                    reason = "the arm stopped short of its home counts"
                elif parked and abs(roll) > tolerance_counts:
                    parked = False
                    reason = (f"the wrist roll is {round(roll)} counts from home and nothing jogs "
                              "the roll: set it by hand")
                errors = {name: round(value) for name, value in owed.items()}
                errors["wrist_roll"] = round(roll)
                self._record("parked", parked=parked, reason=reason, errors=errors,
                             state=asdict(after))
                return {"parked": parked, "reason": reason, "errors": errors}

    def home_inch(self, *, operator_at_stop: bool = False,
                  max_search_deg: float | dict | None = None, speed: int = 6):
        """Home shoulder, elbow and base by inching each to its switch; no start pose needed.

        Unlike ``home``, this tolerates any starting pose: it finds each joint's
        switch by small joint moves (each way from where the joint started, up to
        ``max_search_deg``: a number for all three or a dict per joint), reading the switch
        bit after every step, then moves the vendor's offset from the switch edge and records
        the counts as the session home. Each step is a coupled joint move (``joint_move``):
        the elbow and wrist pitch follow the shoulder, and the wrist pitch follows the elbow,
        so the other joints keep their angles, as in the vendor's own homing. It sends nothing
        the legacy code does not send: no order 18, no ``48``, no wrist roll. Directions, order,
        polarity and offsets are from the vendor DLL's homing, from disassembly and
        **unverified on the arm** (``docs/protocol/VENDOR_HOMING_TRACE.md``). Supervised: a
        joint whose switch never reads on is driven toward its stop until the progress check
        or the controller's error word stops it. During the search the source model's joint
        limits are not checked (there is no home to measure them from). The wrist roll and the
        gripper are not homed, and the pose checks assume the wrist already stands where it
        would at home. ``request_stop`` ends the search (``MotionStopped``, no fault, no
        home); a joint that cannot be homed, or an interrupt, latches a fault and switches the
        motors off. Not an emergency stop: the physical stop is authoritative.
        """
        with self._motion_lock:
            self._refuse_if_streaming()
            if not operator_at_stop:
                raise ValueError("Confirm an operator is at the physical stop and the "
                                 "arm's path is clear first")
            caps = dict(inch_home.DEFAULT_SEARCH_DEG) if max_search_deg is None else (
                {j: max_search_deg for j in inch_home.ORDER}
                if not isinstance(max_search_deg, dict) else dict(max_search_deg))
            if set(caps) != set(inch_home.ORDER) or any(
                    isinstance(v, bool) or not isinstance(v, (int, float))
                    or not math.isfinite(v) or not 0 < v <= 180 for v in caps.values()):
                raise ValueError("max_search_deg must be above 0 and at most 180 degrees, "
                                 f"as one number or one value for each of {inch_home.ORDER}")
            if self.max_jog_degrees < 2.0:
                raise ValueError("home_inch needs a jog ceiling of at least 2 degrees "
                                 "(its offset move for the shoulder is 1.7)")
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
            self._homed = False
            self._home_counts = None
            # The wrist is not homed: its counts below are whatever they were, so the
            # pitch and roll limits of the pose checks assume the wrist already stands
            # where it would at home. The operator sets it by eye.
            self._record("home_start", method="inch", max_search_deg=caps, speed=speed,
                         wrist_homed=False, state=asdict(before))
            joint = None
            jogs, travel = [0], [0.0]      # on this joint: steps started, degrees moved toward its switch
            try:
                run = self._coupled_run(speed, homing=True)
                move_joint, settle = run.move, run.settle
                for joint in inch_home.ORDER:
                    jogs[0], travel[0] = 0, 0.0
                    # Joint degrees that raise this joint's own motor count, and the sign of the
                    # approach to its switch in counts: the vendor's sign convention decides
                    # (vendor_model, through joint_move), not an assumption here.
                    per_degree = joint_move.vector(joint)[joint]
                    approach = inch_home.APPROACH_COUNTS[joint] * (1 if per_degree > 0 else -1)
                    bit = HOME_SWITCH_BITS[joint]

                    def read_once(bit=bit):
                        bits = self._motion_state().home_switch_bits
                        if bits & ~known_bits:
                            raise inch_home.InchHomeError(f"unexpected switch byte {bits}")
                        return bool(bits & bit)

                    def read_on(read_once=read_once):
                        # Two readings must agree, or a third decides: one false reading
                        # must not become a wrong home.
                        first, second = read_once(), read_once()
                        return first if first == second else read_once()

                    def move(delta, joint=joint, approach=approach):
                        jogs[0] += 1
                        move_joint(joint, approach * delta)
                        travel[0] += delta

                    start = self._motion_state()
                    net = inch_home.inch_to_edge(
                        read_on, move, cap_deg=caps[joint],
                        deadline_s=time.monotonic() + inch_home.AXIS_SECONDS[joint],
                        now=time.monotonic)
                    offset_counts = inch_home.OFFSET_COUNTS[joint]
                    if offset_counts:
                        move_joint(joint, offset_counts / per_degree)
                    settle()
                    edge = self._motion_state()
                    self._record("home_axis_complete", joint=joint, method="inch",
                                 travel_toward_switch_deg=net, offset_counts=offset_counts,
                                 start=asdict(start), state=asdict(edge))
            except (inch_home.InchHomeError, joint_move.StallError, ValueError) as exc:
                message = (f"Home search failed on the {joint} on jog {jogs[0]} "
                           f"({travel[0]:+.1f} degrees toward its switch before it): {exc}")
                self._disable_best_effort()
                self._latch_fault(message)
                self._record("home_failed", method="inch", joint=joint, error=message)
                raise ScorbotError(message) from exc
            except MotionStopped:
                raise                    # not a fault: no home, motors as they are
            except ScorbotError as exc:
                # A jog or a read failed: ``_command`` has already latched and switched
                # the motors off. Say which joint, which jog and how far it had come: the
                # controller's own text ("error code 1") says neither.
                message = (f"Home search failed on the {joint} on jog {jogs[0]} "
                           f"({travel[0]:+.1f} degrees toward its switch before it): {exc}")
                self._record("home_failed", method="inch", joint=joint, error=message)
                raise ScorbotError(message) from exc
            except KeyboardInterrupt:
                self._disable_best_effort()
                self._latch_fault("Python interrupted during homing")
                self._record("home_failed", method="inch", joint=joint, error=self._fault)
                raise
            except Exception as exc:        # anything unexpected leaves the search half done
                self._disable_best_effort()
                self._latch_fault(f"Home search failed unexpectedly on the {joint}: {exc!r}")
                self._record("home_failed", method="inch", joint=joint, error=self._fault)
                raise
            with self._fail_closed("home_failed",
                                   "Home search failed unexpectedly after the last jog"):
                after = self._motion_state()
                if self._consume_stop():
                    # A stop that arrived with the last jog: the operator asked to end the
                    # homing, so no home is recorded.
                    self._record("home_stopped", method="inch", state=asdict(after))
                    raise MotionStopped("A stop was requested during homing; no home was recorded",
                                        state=after)
                if self._calibration:
                    try:
                        for calibration in self._calibration.joints.values():
                            calibration.validate_home(after.encoder_counts[calibration.encoder])
                    except ValueError as exc:
                        self._disable_best_effort()
                        self._latch_fault(f"Home verification failed: {exc}")
                        self._record("home_failed", error=self._fault, state=asdict(after))
                        raise ScorbotError(self._fault) from exc
                self._home_counts = after.encoder_counts.copy()
                self._homed = True
                self._reset_prehome()
                self._record("home_complete", method="inch", wrist_homed=False,
                             state=asdict(self._motion_state()))

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
        recording were a model, so **the controller's acceptance is unverified**. Every driven
        motor is checked for stalls, travel and a wrong drive sign, which stops the home within
        a few degrees; the thresholds are guesses chosen to fail safe.

        Needs: an operator at the physical stop, empty jaws, a clear wrist path, motors enabled,
        no stream, and switch bits below 32. Any error, a stop request or an interrupt first
        sends the vendor's stop (``47``, ``4D 0`` to every motor, hold the setpoints, ``4F FF
        53``), then switches the motors off and latches a fault: the counters may be partly
        zeroed, so build a new ``Scorbot`` before going on. ``request_stop`` ends it that way.
        Not an emergency stop: it needs a live USB link and a responsive worker, and the arm
        coasts; the physical stop is authoritative. Never run on the arm.
        """
        with self._motion_lock:
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
            self._home_counts = None
            source = VendorHomeSource(version=controller_version, stop_event=self._stop_event,
                                      cancel_event=self._cancel_event)
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
