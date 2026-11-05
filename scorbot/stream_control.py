"""Starting, following and ending a stream: the streaming methods of Scorbot.

Moved out of robot.py unchanged (specs/2026-10-12-code-health-design.md (in git history)): every method body is
identical to the one that was in ``Scorbot``. ``Scorbot`` takes them from ``StreamMixin``; they use the
session state, ``_command``-side queues, ``_latch_fault``, ``_motion_state``, ``_record`` and the stop
events through ``self``. The class constant below moved with ``start_stream``, whose default argument needs it.
"""

import math
import queue
import time
from dataclasses import asdict

from . import limits, nominal, pacing, source_model
from .calibration import MAX_COUNTS_FROM_HOME, signed_count_delta
from .errors import MotionStopped, ScorbotError
from .state import RobotState
from .streaming import MOTORS as STREAM_MOTORS
from .streaming import WRIST_STREAM_MOTORS
from .streaming import (DEFAULT_HOLD_TIMEOUT_S, DEFAULT_PERIOD_S, DEFAULT_SPEED_FRACTION,
                        HOLD_TIMEOUT_RANGE_S, PERIOD_RANGE_S, STREAM_ORDER, Stream, StreamCore,
                        prior_limits)


def wrist_cap_problem(counts, cap_counts: float) -> str | None:
    """A sentence if the wrist motors' counts from home put pitch or roll past ``cap_counts``, else None.

    Pitch is half the motors' difference and roll half their sum (``source_model``), so a per-motor bound
    alone lets a pair through whose pitch or roll is past the cap (Ruckig can overshoot one motor on a
    reversal). Checked on the start, every target and every setpoint of a wrist stream.
    """
    m1, m2 = counts["wrist_motor_1"], counts["wrist_motor_2"]
    pitch, roll = (m1 - m2) / 2, (m1 + m2) / 2
    if abs(pitch) <= cap_counts and abs(roll) <= cap_counts:
        return None
    return (f"wrist pitch {pitch:g} or roll {roll:g} counts from home is past the travel cap of "
            f"{cap_counts:g} counts")


class StreamMixin:
    # Streaming travel from home: the shared cap in scorbot/limits.py.
    STREAM_TRAVEL_CAP_MAX_DEG = limits.TRAVEL_CAP_DEG

    def start_stream(self, *, travel_cap_deg: float | None = STREAM_TRAVEL_CAP_MAX_DEG,
                     speed_fraction: float = DEFAULT_SPEED_FRACTION,
                     lead_limit_deg: float = 2.0,
                     hold_timeout_s: float = DEFAULT_HOLD_TIMEOUT_S,
                     period_s: float = DEFAULT_PERIOD_S,
                     use_emergency_bit: bool = True,
                     adaptive_pacing: bool = False,
                     wrist: bool = False,
                     resume: bool = False) -> Stream:
        """Start following a stream of targets for base, shoulder and elbow.

        Returns a ``Stream``: call ``set_target`` with motor counts from home as
        often as you like, then ``close`` (or use it as a context manager).
        Until it ends, every other command is refused. Unverified on the arm:
        the period, limits and stop sequence are priors, so the first trials
        are one motor, about a degree, supervised. Not an emergency stop;
        the physical stop is authoritative. Needs the ``planning`` extra.
        ``adaptive_pacing`` replaces the fixed read delay with the vendor's echoed-ID rule
        (``scorbot/pacing.py``); off by default, unverified on the arm.
        ``wrist`` (off by default) streams both wrist motors as well. They are coupled (pitch
        is half their difference, roll half their sum: ``source_model``), so each wrist motor's
        travel cap and lead limit is the cap or limit of pitch plus that of roll, in the source
        model's counts per degree, plus a count of rounding per joint on the cap; pitch and roll
        are also each held to the cap on every target and setpoint (``wrist_cap_problem``). The wrist
        jogs stay disabled; only this opt-in path streams the wrist. Unverified on the arm.
        ``travel_cap_deg=None`` selects only Intelitek's encoder and joint windows,
        after vendor Home. A positive cap adds an optional bound without an upper ceiling.
        Every arm motor must be within ``limits.DRIFT_COUNTS`` of home and at rest.
        ``resume=True`` permits one restart near the final pose of a cleanly closed stream.
        """
        with self._motion_lock:
            if self._device is None:
                raise ScorbotError("Not connected")
            if self._stream is not None:
                raise ScorbotError("A stream is already active")
            if travel_cap_deg is not None and (isinstance(travel_cap_deg, bool)
                    or not isinstance(travel_cap_deg, (int, float))
                    or not math.isfinite(travel_cap_deg) or travel_cap_deg <= 0):
                raise ValueError("travel_cap_deg must be a positive finite number or None")
            if (isinstance(lead_limit_deg, bool) or not isinstance(lead_limit_deg, (int, float))
                    or not math.isfinite(lead_limit_deg) or not 0 < lead_limit_deg <= self.max_jog_degrees):
                raise ValueError(f"lead_limit_deg must be above 0 and at most {self.max_jog_degrees:g} degrees")
            for name, value, (low, high) in (("period_s", period_s, PERIOD_RANGE_S),
                                             ("hold_timeout_s", hold_timeout_s,
                                              HOLD_TIMEOUT_RANGE_S)):
                if isinstance(value, bool) or not isinstance(value, (int, float)) \
                        or not math.isfinite(value) or not low <= value <= high:
                    raise ValueError(f"{name} must be {low:g} through {high:g} seconds")
            if not isinstance(wrist, bool):
                raise ValueError("wrist must be True or False")
            if not isinstance(resume, bool):
                raise ValueError("resume must be True or False")
            if not self._enabled or not self._homed or self._home_counts is None:
                raise ScorbotError("Enable and home before streaming")
            if not self._gripper_homed:
                raise ScorbotError("Run vendor Home before streaming with vendor limits")
            before = self._motion_state()
            if before.teach_mode:
                # Stack 2's own rule (the DLL refuses nothing in teach mode, LEDGER.md PL64); checked before
                # the stop so a pending stop is kept for the next motion.
                self._record("stream_refused_teach_mode")
                raise ScorbotError("The teach pendant is in teach mode; switch it to Auto before streaming")
            if self._consume_stop():
                self._record("stop_before_motion", joint="stream")
                raise MotionStopped("A stop was requested; the stream was not started",
                                    state=before, started=False)
            resume_from, self._stream_resume_from = self._stream_resume_from, None
            before, settled = self._wait_for_rest(before)
            if not settled:
                self._record("stream_refused_not_at_rest", state=asdict(before))
                raise ScorbotError("The arm is not at rest; the stream was not started")
            motors = WRIST_STREAM_MOTORS if wrist else STREAM_MOTORS
            per_degree = {m: abs(self.preview_jog(m, 1.0)["motor_count_deltas"][m])
                          for m in STREAM_MOTORS}
            # One wrist motor carries pitch and roll (m1 = p + r, m2 = r - p, source model).
            per_degree.update({m: 2 * source_model.COUNTS_PER_DEGREE[m] for m in motors[3:]})
            rounding = {m: 2 if m in limits.WRIST_MOTORS else 0 for m in motors}
            try:
                home = {m: signed_count_delta(self._home_counts[m], 0) for m in limits.RECORDED_MOTORS}
            except ValueError as exc:
                raise ScorbotError(f"Cannot place the arm relative to home: {exc}") from exc
            # The stream reads each motor from home by its low word, which aliases past 32766 counts
            # (wrist_motor_2's vendor EncLimit is +/-1,000,000): refuse rather than misread it (CT14).
            reach = {m: before.signed_encoder_counts[m] - home[m] for m in limits.RECORDED_MOTORS}
            far = {m: v for m, v in reach.items() if abs(v) > MAX_COUNTS_FROM_HOME}
            if far:
                raise ScorbotError("Cannot place the arm relative to home: "
                                   + ", ".join(f"{m} is {v} counts from home" for m, v in far.items())
                                   + f", past the {MAX_COUNTS_FROM_HOME} a stream can read; jog it back first")
            resumed = resume and resume_from is not None and all(
                abs(reach[m] - resume_from[m]) <= limits.DRIFT_COUNTS for m in limits.RECORDED_MOTORS)
            far = {m: v for m, v in reach.items() if abs(v) > limits.DRIFT_COUNTS}
            if far and not resumed:
                self._record("stream_refused_not_home", counts_from_home=far,
                             tolerance_counts=limits.DRIFT_COUNTS)
                raise ScorbotError(
                    ", ".join(f"{m} is {v} counts from home" for m, v in far.items())
                    + f"; every arm motor must be within {limits.DRIFT_COUNTS} counts of home")
            try:
                start = {m: signed_count_delta(before.encoder_counts[m], self._home_counts[m])
                         for m in motors}
                # The wrist pitch follows the wrist motors too, and a stream without
                # them leaves them where they are: the pose check must use where they are now.
                held = {m: signed_count_delta(before.encoder_counts[m], self._home_counts[m])
                        for m in limits.WRIST_MOTORS if m not in motors}
            except ValueError as exc:
                raise ScorbotError(f"Cannot place the arm relative to home: {exc}") from exc
            window = {m: (nominal.VENDOR_ENCODER_SOFT_LIMITS[m].minimum - home[m],
                          nominal.VENDOR_ENCODER_SOFT_LIMITS[m].maximum - home[m]) for m in motors}
            travel_cap = {m: max(abs(low), abs(high)) for m, (low, high) in window.items()}
            if travel_cap_deg is not None:
                travel_cap = {m: min(travel_cap[m], travel_cap_deg * per_degree[m] + rounding[m])
                              for m in motors}
            wrist_cap = (None if travel_cap_deg is None else
                         travel_cap_deg * source_model.COUNTS_PER_DEGREE["wrist_motor_1"] + 1)

            def pose_check(counts):
                delta = {**held, **counts}
                return nominal.vendor_limit_problem({m: home[m] + delta[m]
                                                     for m in limits.RECORDED_MOTORS}) or (
                    wrist and wrist_cap is not None and wrist_cap_problem(counts, wrist_cap)) or None
            try:
                core = StreamCore(
                    start,
                    travel_cap=travel_cap, window=window, pose_check=pose_check,
                    lead_limit={m: lead_limit_deg * per_degree[m] for m in motors},
                    limits=prior_limits(speed_fraction), period_s=period_s,
                    hold_timeout_s=hold_timeout_s, now=time.monotonic(), motors=motors)
            except ImportError as exc:
                raise ScorbotError("Streaming needs the planning extra "
                                   "(pip install .[planning])") from exc
            stream = Stream(self, core, self._home_counts, before, home_signed=home,
                            use_emergency_bit=use_emergency_bit)
            self._record("stream_start", travel_cap_deg=travel_cap_deg,
                         speed_fraction=speed_fraction, lead_limit_deg=lead_limit_deg,
                         hold_timeout_s=hold_timeout_s, period_s=period_s,
                         use_emergency_bit=use_emergency_bit, adaptive_pacing=adaptive_pacing,
                         wrist=wrist, resume=resume, home_tolerance_counts=limits.DRIFT_COUNTS,
                         start_counts_from_home=reach,
                         state=asdict(before))
            if self._trace is not None:
                self._trace.start()
            with self._lock:
                self._vendor_joint_target = None
                self._vendor_joint_hold = None
                self._stream = stream
                self._commands.put([STREAM_ORDER, stream,
                                       pacing.make_pace() if adaptive_pacing else 0])
            return stream

    def _stream_faulted(self, stream: Stream) -> None:
        """Latch a fault the stream found. Runs on the command worker; never raises.

        The stream has already told the worker to stop. Latching here, not in
        ``_end_stream``, means a caller that never ends the stream still
        leaves a faulted session.
        """
        try:
            self._latch_fault(f"Stream fault: {stream.core.fault}", keep_first=True)
            self._commands.put([16, 1, 1])  # Runs after the stream; never an emergency stop.
            self._record("stream_fault", error=self._fault)
        except Exception:
            pass  # like _worker_died: a log failure must not kill the worker mid-stream

    def _end_stream(self, stream: Stream) -> RobotState:
        """Collect the worker's answer for a stream and settle the session."""
        with self._motion_lock:
            if self._stream is not stream:
                if stream.final_state is None:
                    raise ScorbotError("The stream has already ended")
                return stream.final_state
            self._stream_resume_from = None
            result, failure, interrupt = None, None, None
            try:
                with self._lock:
                    result = self._next_result(self.command_timeout)
                    if result != 0:
                        while self._next_result(self.command_timeout) != 0:
                            pass
            except queue.Empty:
                failure = "The stream did not end; physical stop may be required"
                self._cancel_event.set()
            except ScorbotError as exc:
                failure = str(exc)
            except (KeyboardInterrupt, SystemExit) as exc:
                # As in _command: the worker's answer is still to come, so the
                # session must fault or a later command would take it as its own.
                failure = "Python interrupted while a stream was ending; physical stop " \
                          "may be required"
                interrupt = exc
                self._cancel_event.set()
            if failure is None and stream.core.fault:
                failure = f"Stream fault: {stream.core.fault}"
            if failure is None and result not in (0, self._STOPPED_CODE):
                if result == 1:
                    failure = ("Stream failed (USB error); motors-off was sent but is not confirmed: "
                               "check the MOTORS LED; motor state is unverified, "
                               "use the physical stop if needed")
                else:
                    failure = f"Legacy controller returned error code {result}"
            if failure is not None:
                # A worker that comes back must stop, not carry on slowing
                # down after the session has reported a fault. A setpoint it
                # had already computed may still go out first (one step,
                # inside every limit), and it is not in the trace. Its late
                # answer stays unread: a latched session takes no commands.
                stream.core.fail(failure)
                with stream.report_lock:   # the worker may be reporting the same fault
                    if not stream.fault_reported:
                        self._latch_fault(failure)
                        if self._link_alive():
                            self._commands.put([16, 1, 1])  # Best effort; never an emergency stop.
                        stream.fault_reported = True
                        self._record("stream_fault", error=self._fault)
            self._stream = None
            with self._stop_lock:
                too_late = self._stop_event.is_set() and failure is None and result == 0
                self._stop_event.clear()
            if too_late:
                # As for a jog: the stream had already ended by itself, so the
                # request cut nothing short. Say so instead of dropping it.
                # Keep it on the ended stream too: a request that came after the
                # stream was unpublished reached only the event, and PolicyArm's
                # gripper handoff reads the stream to refuse the jaw move.
                stream.core.request_stop()
                self._record("stop_too_late", joint="stream")
            packets, dropped = self._trace.stop() if self._trace is not None else ([], 0)
            try:
                # Written after the latch: this row can be large, and a write
                # error must not be what decides whether the session faults.
                self._record("stream_trace", packets=packets, dropped_packets=dropped,
                             steps=stream.steps, dropped_steps=stream.dropped_steps)
            except Exception as exc:
                if failure is None:
                    # The worker answered, but the record of every step is lost
                    # and the final state has not been read. Not a clean end.
                    failure = f"The stream's record could not be written: {exc}"
                    self._latch_fault(failure)
                    if self._link_alive():
                        self._commands.put([16, 1, 1])  # Best effort; never an emergency stop.
                    try:
                        self._record("stream_fault", error=self._fault)
                    except Exception:
                        pass   # the log is what just failed
            if interrupt is not None:
                raise interrupt
            if failure is not None:
                raise ScorbotError(self._fault)
            if result == self._STOPPED_CODE:
                after = self._settled_after_stop(stream._before)
                self._record("stream_stopped", state=asdict(after), reason=stream.stop_reason)
            else:
                after = self._motion_state(after_index=stream._before.packet_index)
                self._record("stream_complete", state=asdict(after))
                if not stream.core._stop_requested:
                    self._stream_resume_from = {
                        m: after.signed_encoder_counts[m] - stream._home_signed[m]
                        for m in limits.RECORDED_MOTORS}
            stream.final_state = after
            return after

    def _abandon_stream(self) -> None:
        """Before disconnecting: end a stream the caller left open."""
        self._stream_resume_from = None
        stream = self._stream
        if stream is not None:
            try:
                stream.stop()
            except ScorbotError:
                pass  # the session is latched; disconnect carries on
