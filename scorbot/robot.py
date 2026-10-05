"""Small, synchronous Python adapter around the original USB controller code."""

from dataclasses import asdict, dataclass
import importlib
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
import queue
import sys
import threading
import traceback

from . import limits
from .calibration import load_calibration, signed_count_delta
from .nominal import vendor_limit_report
from .packet import PacketTrace, TrackedInputEndpoint, TrackedOutputEndpoint
from .state import HOME_SWITCH_BITS, JOINTS, RobotState, decode_state
from .streaming import MOTORS as STREAM_MOTORS
from .streaming import (DEFAULT_HOLD_TIMEOUT_S, DEFAULT_PERIOD_S, DEFAULT_SPEED_FRACTION,
                        HOLD_TIMEOUT_RANGE_S, PERIOD_RANGE_S, STREAM_ORDER, Stream, StreamCore,
                        prior_limits)


def _home_relative_targets(encoder_counts, home_counts, deltas) -> dict[str, int | None]:
    """Jog targets as wrap-aware counts from this session's home, for the diagnostic.

    None where the offset is unknown (no home, or ambiguous near half the
    counter range). Never raises: the diagnostic must not affect motion.
    """
    targets = {}
    for motor, delta in deltas.items():
        try:
            targets[motor] = signed_count_delta(encoder_counts[motor], home_counts[motor]) + delta
        except (KeyError, TypeError, ValueError):
            targets[motor] = None
    return targets


class ScorbotError(RuntimeError):
    """A command or controller operation failed."""


class _WorkerCrashed(ScorbotError):
    """The command worker reported an exception and is exiting; it answers nothing more."""


class MotionStopped(ScorbotError):
    """A jog ended early because ``request_stop`` was called. Not a fault.

    ``state`` is the controller state read after the arm settled, or the state
    before the jog when it was never started (``started`` is then False and
    nothing was sent). A stop does not promise the arm travelled less than the
    jog: compare ``state`` with the state before.
    """

    def __init__(self, message: str, state: RobotState | None = None, *,
                 started: bool = True):
        super().__init__(message)
        self.state = state
        self.started = started


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


class Scorbot:
    """ER-4U legacy USB adapter.

    Legacy jogs are relative. Calibrated absolute steps require measured data;
    neither the queued disable command nor ``request_stop`` is an emergency stop.
    """

    _STOPPED_CODE = 14  # openScorbot/libcomm.py:STOPPED
    # "Has it stopped" follows the USNA ScorBot Toolbox for MATLAB
    # (ScorWaitForMove): successive readings 0.05 s apart that no longer change,
    # giving up after 8 s. We ask for three such pairs in a row, so one pause
    # in a slow move is not taken for rest. The count band is our lab idle
    # band (limits.STABLE_COUNTS). None of it is measured on our arm.
    STOP_SETTLE_INTERVAL_S = 0.05
    STOP_SETTLE_TIMEOUT_S = 8.0
    STOP_SETTLE_COUNTS = limits.STABLE_COUNTS
    STOP_SETTLE_QUIET_PAIRS = 3
    # Streaming travel from home: the shared cap in scorbot/limits.py.
    STREAM_TRAVEL_CAP_MAX_DEG = limits.TRAVEL_CAP_DEG
    # Gripper (ours, not measured): how far short of the planned travel still
    # counts as the whole move, and how far an arm motor may read differently
    # after a gripper move before the session faults.
    GRIPPER_FULL_TRAVEL_TOLERANCE_COUNTS = 100
    GRIPPER_ARM_TOLERANCE_COUNTS = limits.DRIFT_COUNTS

    _JOG_CODES = {
        "base": (5, 4),
        "shoulder": (6, 7),
        "elbow": (8, 9),
        "wrist_pitch": (10, 11),
        "wrist_roll": (12, 13),
    }

    def __init__(self, *, log_path: str | Path | None = None,
                 command_timeout: float = 30.0, max_jog_degrees: float = limits.MAX_JOG_DEG,
                 response_timeout: float = 2.0, robot_id: str | None = None,
                 calibration_path: str | Path | None = None):
        if (not math.isfinite(command_timeout) or command_timeout <= 0
                or not math.isfinite(max_jog_degrees) or not 0 < max_jog_degrees <= limits.MAX_JOG_DEG
                or not math.isfinite(response_timeout) or response_timeout <= 0):
            raise ValueError("Timeouts must be positive; maximum jog must be 0-5 degrees")
        if calibration_path is not None and not robot_id:
            raise ValueError("robot_id is required when loading calibration")
        self.log_path = Path(log_path) if log_path else None
        self.command_timeout = command_timeout
        self.max_jog_degrees = max_jog_degrees
        self.response_timeout = response_timeout
        self.robot_id = robot_id
        self._calibration = (load_calibration(calibration_path, robot_id=robot_id)
                             if calibration_path is not None else None)
        self._input = None
        self._trace = None
        self._last_state_index = 0
        self._home_counts = None
        self._motion_lock = threading.RLock()
        self._state_lock = threading.Lock()
        self._device = None
        self._buffer = None
        self._sync_thread = None
        self._command_thread = None
        self._commands = queue.Queue()
        self._results = queue.Queue()
        self._sync = queue.Queue()
        self._reads = queue.Queue()
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()
        self._stop_event = threading.Event()
        self._stream = None
        self._enabled = False
        self._homed = False
        self._fault = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, _type, _value, _traceback):
        self.disconnect()

    def _legacy(self, name):
        # The original modules use bare imports. Keep this compatibility shim
        # here until the protocol code is converted into a proper package.
        directory = str(Path(__file__).resolve().parent.parent / "openScorbot")
        if directory not in sys.path:
            sys.path.insert(0, directory)
        return importlib.import_module(name)

    def _record(self, event: str, **fields):
        if self.log_path is None:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        row = {"event": event,
               "host_monotonic_ns": time.monotonic_ns(),
               "timestamp_utc": datetime.now(timezone.utc).isoformat(),
               "robot_id": self.robot_id, **fields}
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, allow_nan=False) + "\n")

    def connect(self):
        if self._device is not None:
            raise ScorbotError("Already connected")
        if self._fault is not None:
            raise ScorbotError("Create a new Scorbot instance after a fault")
        self._cancel_event.clear()
        self._stop_event.clear()
        try:
            import usb.core
            import usb.util
        except ImportError as exc:
            raise ScorbotError("Install the package dependencies before connecting") from exc

        self._legacy("conf").setup()
        legacy_sync = self._legacy("libsync")
        legacy_comm = self._legacy("libcomm")
        try:
            finder = usb.core.find
            if sys.platform == "win32":
                try:
                    import libusb_package
                except ImportError:
                    pass
                else:
                    finder = libusb_package.find
            device = finder(idVendor=0x09F1, idProduct=0x0007)
            if device is None:
                raise ScorbotError("ER-4U USB controller 09F1:0007 was not found")
            self._device = device
            device.reset()
            try:
                interface_number = device[0].interfaces()[0].bInterfaceNumber
                if device.is_kernel_driver_active(interface_number):
                    device.detach_kernel_driver(interface_number)
            except (NotImplementedError, usb.core.USBError):
                # Kernel driver management is unavailable on some Windows backends.
                pass
            interface = device.get_active_configuration()[(0, 0)]
            endpoint_in = usb.util.find_descriptor(
                interface, custom_match=lambda endpoint:
                usb.util.endpoint_direction(endpoint.bEndpointAddress) == usb.util.ENDPOINT_IN)
            endpoint_out = usb.util.find_descriptor(
                interface, custom_match=lambda endpoint:
                usb.util.endpoint_direction(endpoint.bEndpointAddress) == usb.util.ENDPOINT_OUT)
            if endpoint_in is None or endpoint_out is None:
                raise ScorbotError("The USB controller endpoints were not found")
            # Copies packets during jogs (motion_trace); packets are unchanged.
            self._trace = PacketTrace()
            endpoint_in = TrackedInputEndpoint(endpoint_in, self._trace)
            endpoint_out = TrackedOutputEndpoint(endpoint_out, self._trace)
            self._input = endpoint_in
            buffer = usb.util.create_buffer(endpoint_in.wMaxPacketSize)
            sequence, encoder_mean = legacy_sync.msg_start(endpoint_out, endpoint_in, buffer)
            self._buffer = buffer
            self._sync.put(sequence)
            self._reads.put(encoder_mean)
            self._sync_thread = threading.Thread(
                target=self._run_sync_worker,
                args=(legacy_sync, self._sync, self._reads, endpoint_out, endpoint_in, buffer),
                daemon=True,
                name="scorbot-sync")
            self._command_thread = threading.Thread(
                target=self._run_command_worker,
                args=(self._sync, self._commands, self._reads, endpoint_out,
                      endpoint_in, buffer, self._results, legacy_comm),
                daemon=True,
                name="scorbot-commands")
            self._sync_thread.start()
            self._command_thread.start()
            # The legacy handshake sends motor-on packets. Disable immediately
            # after it completes; enabling requires a separate explicit call.
            self._command([16, 1, 1])
            self._enabled = False
            self._record("connect", state=asdict(self.get_state()))
            return self
        except Exception as exc:
            self._latch_fault(str(exc))
            self._record("connect_failed", error=self._fault)
            self._cancel_event.set()
            try:
                self.disconnect()
            except Exception as cleanup_error:
                self._record("connect_cleanup_failed", error=str(cleanup_error))
            raise ScorbotError(
                f"Connection failed: {exc}. Controller motor state is unverified; "
                "use the physical stop if needed") from exc

    def _latch_fault(self, message: str, *, keep_first: bool = False) -> None:
        """Reject motion from now on; motor and home state become unverified."""
        if not (keep_first and self._fault):
            self._fault = message
        self._enabled = None
        self._homed = False
        self._home_counts = None

    def _sync_dead(self) -> bool:
        return self._sync_thread is not None and not self._sync_thread.is_alive()

    def _link_alive(self) -> bool:
        """Whether a queued command can still reach USB.

        The legacy workers pass the sequence byte between them, so if either one
        has died a queued command is never executed.
        """
        return (self._command_thread is not None and self._command_thread.is_alive()
                and not self._sync_dead())

    def _run_sync_worker(self, legacy_sync, *args):
        try:
            legacy_sync.syncro(*args)
        except Exception as exc:
            self._worker_died("sync", exc)

    def _run_command_worker(self, *args):
        *legacy_args, legacy_comm = args
        try:
            legacy_comm.execute(*legacy_args, self._cancel_event, self._stop_event)
        except Exception as exc:
            self._worker_died("command", exc)

    def _worker_died(self, name: str, exc: Exception) -> None:
        # The legacy loops have no error handling. A dead worker stops idle packets,
        # possibly while the operator is at a prompt and nothing reads state, so
        # fault, wake any waiting command, and say so on the terminal.
        message = f"USB {name} worker stopped: {exc}"
        self._latch_fault(message, keep_first=True)
        self._cancel_event.set()
        self._results.put(_WorkerCrashed(
            f"{message}. Motor state is unverified; use the physical stop if needed"))
        print(f"\n*** {message}. The controller is no longer receiving idle "
              "packets and motor state is unverified; use the physical stop. ***",
              file=sys.stderr, flush=True)
        try:
            self._record(f"{name}_worker_crashed", error=str(exc),
                         traceback=traceback.format_exc())
        except Exception:
            pass  # A log write failure must not replace the banner with a traceback.

    def _next_result(self, timeout: float):
        result = self._results.get(timeout=timeout)
        if isinstance(result, _WorkerCrashed):
            raise result
        if isinstance(result, Exception):
            raise _WorkerCrashed(
                f"USB command worker crashed: {result}. Motor state is unverified; "
                "use the physical stop if needed") from result
        return result

    def _command(self, payload: list[int | float], *, timeout: float | None = None) -> None:
        if self._device is None:
            raise ScorbotError("Not connected")
        if self._fault:
            raise ScorbotError(f"Controller is faulted: {self._fault}")
        if self._stream is not None:
            raise ScorbotError("A stream is active; close or stop it first")
        wait_timeout = self.command_timeout if timeout is None else timeout
        with self._lock:
            self._record("command_start", payload=payload)
            self._commands.put(payload)
            try:
                result = self._next_result(wait_timeout)
                if result != 0:
                    while self._next_result(wait_timeout) != 0:
                        pass
                    if result == self._STOPPED_CODE:
                        self._record("command_stopped", payload=payload)
                        raise MotionStopped("The jog ended early on a stop request")
                    raise ScorbotError(f"Legacy controller returned error code {result}")
            except MotionStopped:
                raise  # not a fault; jog_joint checks that the arm settled
            except queue.Empty as exc:
                self._latch_fault("Command timed out; physical stop may be required")
                self._cancel_event.set()
                self._commands.put([16, 1, 1])  # Best effort if worker recovers.
                self._record("command_timeout", payload=payload)
                raise ScorbotError(self._fault) from exc
            except ScorbotError as exc:
                self._latch_fault(str(exc))
                # A crashed worker may still look alive while it exits, but it will
                # never answer a disable; waiting for one only delays the fault.
                if (payload[0] != 16 and not isinstance(exc, _WorkerCrashed)
                        and self._link_alive()):
                    self._commands.put([16, 1, 1])
                    try:
                        disable_result = self._results.get(timeout=min(2.0, self.command_timeout))
                    except queue.Empty:
                        disable_result = "timeout"
                    self._record("disable_after_error", result=disable_result)
                elif isinstance(exc, _WorkerCrashed):
                    self._record("disable_skipped_worker_crashed")
                self._record("command_error", payload=payload, error=self._fault)
                raise
            except (KeyboardInterrupt, SystemExit):
                self._latch_fault("Python interrupted during a controller command")
                self._cancel_event.set()
                self._commands.put([16, 1, 1])
                self._record("command_interrupted", payload=payload)
                raise
            self._record("command_complete", payload=payload)

    def get_state(self, *, after_index: int | None = None) -> RobotState:
        """Return a copied, recent USB response; optionally wait for a newer one."""
        return self.get_state_and_packet(after_index=after_index)[0]

    def get_state_and_packet(self, *, after_index: int | None = None
                             ) -> tuple[RobotState, bytes]:
        """``get_state`` plus a copy of the reply it was decoded from.

        The packet holds bytes this SDK does not decode; it is evidence for
        offline review, never an input to a motion decision.
        """
        if self._device is None or self._input is None:
            raise ScorbotError("Not connected")
        if self._sync_dead():
            raise ScorbotError(f"USB sync worker is not running: {self._fault}")
        try:
            with self._state_lock:
                required_index = max(self._last_state_index, after_index or 0)
                sample = self._input.snapshot(
                    after_index=required_index, timeout=self.response_timeout,
                    max_age=self.response_timeout)
                self._last_state_index = sample.index
        except TimeoutError as exc:
            raise ScorbotError(str(exc)) from exc
        state = decode_state(sample.data, connected=True,
                             enabled=self._enabled, homed=self._homed,
                             fault=self._fault, packet_index=sample.index,
                             host_monotonic_ns=sample.host_monotonic_ns)
        return state, bytes(sample.data)

    def _motion_state(self, *, after_index=None):
        try:
            state = self.get_state(after_index=after_index)
        except (ScorbotError, ValueError) as exc:
            self._latch_fault(f"Controller feedback is unavailable: {exc}")
            if self._link_alive():
                self._commands.put([16, 1, 1])  # Best effort; never an emergency stop.
            self._record("feedback_fault", error=self._fault)
            raise ScorbotError(self._fault) from exc
        # A worker can fault the session between commands; no motion step may
        # proceed (or mark the arm homed) on a state read after that.
        if self._fault:
            raise ScorbotError(f"Controller is faulted: {self._fault}")
        return state

    def _refuse_if_streaming(self) -> None:
        # Before any log row or trace change, so a refused call leaves no mark.
        if self._stream is not None:
            raise ScorbotError("A stream is active; close or stop it first")

    def enable(self):
        with self._motion_lock:
            self._refuse_if_streaming()
            self._motion_state()
            self._command([17, 1, 1])
            self._enabled = True

    def request_stop(self) -> None:
        """Ask the jog in progress to end early. This is NOT an emergency stop.

        Safe to call from another thread while ``jog_joint`` is running; it takes
        no lock. The legacy loop checks it after each step, then sends the
        vendor's arm-stop sequence (clear the controller's buffer, then the
        end-of-move commands, all carrying the measured position instead of
        the jog target) and ``jog_joint`` raises ``MotionStopped``. It needs a
        live USB link and a responsive worker, acts at the next packet, and the
        arm coasts. A request that arrives while the move is already being
        closed is logged as ``stop_too_late`` and the jog completes. A request
        made while nothing is moving refuses the next jog once. The sequence is
        from disassembly and unverified on the arm; the physical stop is
        authoritative.
        """
        self._stop_event.set()
        stream = self._stream
        if stream is not None:
            stream.request_stop()      # the caller still ends it with stop() or close()
        self._record("stop_requested")

    def _settled_after_stop(self, before: RobotState, *,
                            fault: str = "The arm was still moving after a stop request"
                            ) -> RobotState:
        """Wait until successive readings stop changing, or latch a fault."""
        deadline = time.monotonic() + self.STOP_SETTLE_TIMEOUT_S
        current = self._motion_state(after_index=before.packet_index)
        quiet = 0
        while True:
            previous = current
            time.sleep(self.STOP_SETTLE_INTERVAL_S)
            current = self._motion_state(after_index=previous.packet_index)
            try:
                moved = max(abs(signed_count_delta(current.encoder_counts[name],
                                                   previous.encoder_counts[name]))
                            for name in JOINTS)
            except ValueError:
                moved = None  # ambiguous near half the counter range: not settled
            quiet = quiet + 1 if moved is not None and moved <= self.STOP_SETTLE_COUNTS else 0
            if quiet >= self.STOP_SETTLE_QUIET_PAIRS:
                return current
            if time.monotonic() >= deadline:
                break
        self._latch_fault(f"{fault}; use the physical stop if needed")
        if self._link_alive():
            self._commands.put([16, 1, 1])  # Best effort; never an emergency stop.
        self._record("stop_settle_failed", error=self._fault, state=asdict(current))
        raise ScorbotError(self._fault)

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
            if self._stop_event.is_set():
                self._stop_event.clear()
                self._record("stop_before_motion", joint="stream")
                raise MotionStopped("A stop was requested; the stream was not started",
                                    state=before, started=False)
            per_degree = {m: abs(self.preview_jog(m, 1.0)["motor_count_deltas"][m])
                          for m in STREAM_MOTORS}
            try:
                start = {m: signed_count_delta(before.encoder_counts[m], self._home_counts[m])
                         for m in STREAM_MOTORS}
            except ValueError as exc:
                raise ScorbotError(f"Cannot place the arm relative to home: {exc}") from exc
            try:
                core = StreamCore(
                    start,
                    travel_cap={m: travel_cap_deg * per_degree[m] for m in STREAM_MOTORS},
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

    def disable(self):
        """Queue a motor-disable command while the worker is responsive."""
        with self._motion_lock:
            self._refuse_if_streaming()
            self._command([16, 1, 1])
            self._enabled = False
            self._homed = False
            self._home_counts = None

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
            self._command([18, 1, 1], timeout=180.0)
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
            self._record("home_complete", state=asdict(self._motion_state()))

    def preview_jog(self, joint: str, delta_degrees: float, *, speed: int = 10,
                    starting_signed_counts: dict[str, int] | None = None) -> dict:
        """Plan motor setpoints offline; this never opens USB or queues a command."""
        if joint not in self._JOG_CODES:
            raise ValueError(f"Unknown joint: {joint}")
        if (isinstance(delta_degrees, bool)
                or not isinstance(delta_degrees, (int, float))
                or not math.isfinite(delta_degrees)
                or not 0 < abs(delta_degrees) <= self.max_jog_degrees):
            raise ValueError("Jog must be finite, nonzero and within the jog ceiling")
        order = self._JOG_CODES[joint][0 if delta_degrees > 0 else 1]
        plan = self._legacy("motion_profile").plan_jog(order, abs(delta_degrees), speed)
        plan["increments"] = list(plan["increments"])
        plan["execution_status"] = (
            "wrist jog disabled pending physical two-motor verification"
            if joint.startswith("wrist_") else "supervised jog only after homing")
        if starting_signed_counts is not None:
            if not isinstance(starting_signed_counts, dict):
                raise ValueError("Starting signed counts must be a mapping")
            targets = {}
            for motor, delta in plan["motor_count_deltas"].items():
                value = starting_signed_counts.get(motor)
                if type(value) is not int or not -65535 <= value <= 65535:
                    raise ValueError(f"Missing or invalid signed count for {motor}")
                targets[motor] = value + delta
            plan["starting_signed_counts"] = {
                motor: starting_signed_counts[motor]
                for motor in targets
            }
            plan["target_signed_counts"] = targets
        return plan

    def jog_joint(self, joint: str, delta_degrees: float, *, speed: int = 10):
        """Move one joint by a bounded legacy relative jog; read back raw state."""
        with self._motion_lock:
            self._refuse_if_streaming()
            if joint not in self._JOG_CODES:
                raise ValueError(f"Unknown joint: {joint}")
            if joint.startswith("wrist_"):
                raise ScorbotError("Wrist jogs are disabled until two-motor bench verification")
            if (isinstance(delta_degrees, bool)
                    or not isinstance(delta_degrees, (int, float))
                    or not math.isfinite(delta_degrees)
                    or not 0 < abs(delta_degrees) <= self.max_jog_degrees):
                raise ValueError(f"Jog must be finite, nonzero and at most {self.max_jog_degrees} degrees")
            if isinstance(speed, bool) or not isinstance(speed, int) or not 1 <= speed <= 20:
                raise ValueError("Legacy speed must be an integer from 1 to 20")
            if not self._enabled or not self._homed:
                raise ScorbotError("Enable and home before jogging")
            before = self._motion_state()
            if self._calibration and joint in self._calibration.joints:
                calibration = self._calibration.joints[joint]
                if self._home_counts is None:
                    raise ScorbotError("Session home count is unavailable")
                try:
                    current_angle = calibration.angle(
                        before.encoder_counts[calibration.encoder],
                        self._home_counts[calibration.encoder])
                except ValueError as exc:
                    self._fault = f"Calibrated state invalid: {exc}"
                    self._homed = False
                    self._home_counts = None
                    raise ScorbotError(self._fault) from exc
                if (not calibration.soft_min_deg <= current_angle <= calibration.soft_max_deg
                        or not calibration.soft_min_deg <= current_angle + delta_degrees <= calibration.soft_max_deg):
                    raise ValueError("Jog would leave measured soft limits")
            if self._stop_event.is_set():
                # Asked to stop while nothing was moving: refuse this jog once,
                # so a request made just before a jog starts is never lost.
                self._stop_event.clear()
                self._record("stop_before_motion", joint=joint,
                             requested_delta_deg=delta_degrees)
                raise MotionStopped("A stop was requested; the jog was not started",
                                    state=before, started=False)
            positive, negative = self._JOG_CODES[joint]
            preview = self.preview_jog(
                joint, delta_degrees, speed=speed,
                starting_signed_counts=before.signed_encoder_counts)
            preview["vendor_limit_report"] = vendor_limit_report(_home_relative_targets(
                before.encoder_counts, self._home_counts, preview["motor_count_deltas"]))
            self._record("motion_preview", plan=preview, state=asdict(before))
            self._record("motion_start", joint=joint, requested_delta_deg=delta_degrees,
                         speed=speed, state=asdict(before))
            trace = self._trace
            if trace is not None:
                trace.start()
            stopped = False
            try:
                self._command([positive if delta_degrees > 0 else negative,
                               speed, abs(delta_degrees)])
            except MotionStopped:
                stopped = True
            finally:
                too_late = self._stop_event.is_set() and not stopped
                self._stop_event.clear()
                if too_late:
                    # Asked for while the move was already being closed: nothing
                    # was cut short. Say so instead of dropping it silently.
                    self._record("stop_too_late", joint=joint)
                if trace is not None:
                    packets, dropped = trace.stop()
                    self._record("motion_trace", joint=joint, packets=packets,
                                 dropped_packets=dropped)
            if stopped:
                after = self._settled_after_stop(before)
                self._record("motion_stopped", joint=joint,
                             requested_delta_deg=delta_degrees, speed=speed,
                             state=asdict(after))
                raise MotionStopped("The jog ended early on a stop request", state=after)
            after = self._motion_state(after_index=before.packet_index)
            self._record("motion_complete", joint=joint,
                         requested_delta_deg=delta_degrees, speed=speed,
                         state=asdict(after))
            return after

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
            if self._stop_event.is_set():
                self._stop_event.clear()
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

    def get_joint_angles(self) -> dict[str, float]:
        """Return calibrated angles only after a verified home on this arm."""
        with self._motion_lock:
            if self._calibration is None:
                raise ScorbotError("No validated physical calibration is loaded")
            if not self._homed or self._home_counts is None or self._fault:
                raise ScorbotError("A verified home is required for calibrated angles")
            state = self._motion_state()
            result = {}
            try:
                for name, calibration in self._calibration.joints.items():
                    result[name] = calibration.angle(
                        state.encoder_counts[calibration.encoder],
                        self._home_counts[calibration.encoder])
            except ValueError as exc:
                self._fault = f"Calibrated state invalid: {exc}"
                self._homed = False
                self._home_counts = None
                self._record("calibration_fault", error=self._fault)
                raise ScorbotError(self._fault) from exc
            return result

    def move_joint(self, joint: str, target_degrees: float, *, speed: int = 10):
        """One bounded calibrated target step, using the existing jog backend."""
        with self._motion_lock:
            if self._calibration is None or joint not in self._calibration.joints:
                raise ScorbotError(f"No validated physical calibration for {joint}")
            if (isinstance(target_degrees, bool)
                    or not isinstance(target_degrees, (int, float))
                    or not math.isfinite(target_degrees)):
                raise ValueError("Target angle must be finite")
            calibration = self._calibration.joints[joint]
            if not calibration.soft_min_deg <= target_degrees <= calibration.soft_max_deg:
                raise ValueError("Target exceeds the measured soft limits")
            current = self.get_joint_angles()[joint]
            delta = target_degrees - current
            if abs(delta) > self.max_jog_degrees:
                raise ValueError("Absolute target exceeds one bounded jog")
            if abs(delta) < 0.01:
                return current
            self.jog_joint(joint, delta, speed=speed)
            achieved = self.get_joint_angles()[joint]
            if abs(achieved - target_degrees) > 2.0:
                self._latch_fault("Calibrated move did not reach target within 2 degrees")
                self._record("following_error", joint=joint,
                             target_deg=target_degrees, achieved_deg=achieved)
                raise ScorbotError(self._fault)
            self._record("absolute_move_complete", joint=joint,
                         target_deg=target_degrees, achieved_deg=achieved)
            return achieved

    def disconnect(self):
        if self._device is None:
            return
        self._abandon_stream()
        self._cancel_event.set()
        # If either worker died, an exit command can never reach USB; leave the
        # daemon thread, since the session is already faulted.
        if self._link_alive():
            if self._fault is None:
                self._command([528, 1, 1])
            else:
                self._commands.put([528, 1, 1])
            self._command_thread.join(timeout=5)
            if self._command_thread.is_alive():
                raise ScorbotError(
                    "Command worker is still active; USB resources remain open. "
                    "Use the physical stop if motor state is uncertain")
        if self._sync_thread is not None and self._sync_thread.is_alive():
            self._sync.put(528)
            self._sync_thread.join(timeout=5)
            if self._sync_thread.is_alive():
                raise ScorbotError(
                    "Sync worker is still active; USB resources remain open. "
                    "Use the physical stop if motor state is uncertain")
        import usb.util
        usb.util.dispose_resources(self._device)
        self._device = None
        self._buffer = None
        self._input = None
        self._trace = None
        self._last_state_index = 0
        self._enabled = False
        self._homed = False
        self._home_counts = None
