"""Starting, following and ending a stream: the streaming methods of Scorbot.

Moved out of robot.py unchanged (docs/specs/2026-10-12-code-health-design.md): every method body is
identical to the one that was in ``Scorbot``. ``Scorbot`` takes them from ``StreamMixin``; they use the
session state, ``_command``-side queues, ``_latch_fault``, ``_motion_state``, ``_record`` and the stop
events through ``self``. The class constant below moved with ``start_stream``, whose default argument needs it.
"""

import math
import queue
import time
from dataclasses import asdict

from . import limits, source_model
from .calibration import signed_count_delta
from .errors import MotionStopped, ScorbotError
from .state import RobotState
from .streaming import MOTORS as STREAM_MOTORS
from .streaming import (DEFAULT_HOLD_TIMEOUT_S, DEFAULT_PERIOD_S, DEFAULT_SPEED_FRACTION,
                        HOLD_TIMEOUT_RANGE_S, PERIOD_RANGE_S, STREAM_ORDER, Stream, StreamCore,
                        prior_limits)


class StreamMixin:
    # Streaming travel from home: the shared cap in scorbot/limits.py.
    STREAM_TRAVEL_CAP_MAX_DEG = limits.TRAVEL_CAP_DEG

    def start_stream(self, *, travel_cap_deg: float = STREAM_TRAVEL_CAP_MAX_DEG,
                     speed_fraction: float = DEFAULT_SPEED_FRACTION,
                     lead_limit_deg: float = 2.0,
                     hold_timeout_s: float = DEFAULT_HOLD_TIMEOUT_S,
                     period_s: float = DEFAULT_PERIOD_S,
                     use_emergency_bit: bool = False) -> Stream:
        """Start following a stream of targets for base, shoulder and elbow.

        Returns a ``Stream``: call ``set_target`` with motor counts from home as
        often as you like, then ``close`` (or use it as a context manager).
        Until it ends, every other command is refused. Unverified on the arm:
        the period, limits and stop sequence are priors, so the first trials
        are one motor, about a degree, supervised. Not an emergency stop;
        the physical stop is authoritative. Needs the ``planning`` extra.
        """
        with self._motion_lock:
            if self._device is None:
                raise ScorbotError("Not connected")
            if self._stream is not None:
                raise ScorbotError("A stream is already active")
            for name, value, top in (("travel_cap_deg", travel_cap_deg,
                                      self.STREAM_TRAVEL_CAP_MAX_DEG),
                                     ("lead_limit_deg", lead_limit_deg, self.max_jog_degrees)):
                if isinstance(value, bool) or not isinstance(value, (int, float)) \
                        or not math.isfinite(value) or not 0 < value <= top:
                    raise ValueError(f"{name} must be above 0 and at most {top:g} degrees")
            for name, value, (low, high) in (("period_s", period_s, PERIOD_RANGE_S),
                                             ("hold_timeout_s", hold_timeout_s,
                                              HOLD_TIMEOUT_RANGE_S)):
                if isinstance(value, bool) or not isinstance(value, (int, float)) \
                        or not math.isfinite(value) or not low <= value <= high:
                    raise ValueError(f"{name} must be {low:g} through {high:g} seconds")
            if not self._enabled or not self._homed or self._home_counts is None:
                raise ScorbotError("Enable and home before streaming")
            before = self._motion_state()
            if self._consume_stop():
                self._record("stop_before_motion", joint="stream")
                raise MotionStopped("A stop was requested; the stream was not started",
                                    state=before, started=False)
            per_degree = {m: abs(self.preview_jog(m, 1.0)["motor_count_deltas"][m])
                          for m in STREAM_MOTORS}
            try:
                start = {m: signed_count_delta(before.encoder_counts[m], self._home_counts[m])
                         for m in STREAM_MOTORS}
                # The wrist pitch follows the wrist motors too, and a stream leaves
                # them where they are: the pose check must use where they are now.
                wrist = {m: signed_count_delta(before.encoder_counts[m], self._home_counts[m])
                         for m in limits.WRIST_MOTORS}
            except ValueError as exc:
                raise ScorbotError(f"Cannot place the arm relative to home: {exc}") from exc
            try:
                core = StreamCore(
                    start,
                    travel_cap={m: travel_cap_deg * per_degree[m] for m in STREAM_MOTORS},
                    window=source_model.limit_window(),
                    pose_check=lambda counts: source_model.pose_limit_problem(
                        {**wrist, **counts}),
                    lead_limit={m: lead_limit_deg * per_degree[m] for m in STREAM_MOTORS},
                    limits=prior_limits(speed_fraction), period_s=period_s,
                    hold_timeout_s=hold_timeout_s, now=time.monotonic())
            except ImportError as exc:
                raise ScorbotError("Streaming needs the planning extra "
                                   "(pip install .[planning])") from exc
            stream = Stream(self, core, self._home_counts, before,
                            use_emergency_bit=use_emergency_bit)
            self._record("stream_start", travel_cap_deg=travel_cap_deg,
                         speed_fraction=speed_fraction, lead_limit_deg=lead_limit_deg,
                         hold_timeout_s=hold_timeout_s, period_s=period_s,
                         use_emergency_bit=use_emergency_bit, start_counts_from_home=start,
                         state=asdict(before))
            if self._trace is not None:
                self._trace.start()
            with self._lock:
                self._stream = stream
                self._commands.put([STREAM_ORDER, stream.source, 0])
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
                self._record("stream_stopped", state=asdict(after))
            else:
                after = self._motion_state(after_index=stream._before.packet_index)
                self._record("stream_complete", state=asdict(after))
            stream.final_state = after
            return after

    def _abandon_stream(self) -> None:
        """Before disconnecting: end a stream the caller left open."""
        stream = self._stream
        if stream is not None:
            try:
                stream.stop()
            except ScorbotError:
                pass  # the session is latched; disconnect carries on
