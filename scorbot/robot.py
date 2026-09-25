"""Small, synchronous Python adapter around the original USB controller code."""

from dataclasses import asdict
import importlib
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
import queue
import sys
import threading

from .calibration import load_calibration
from .packet import TrackedInputEndpoint
from .state import RobotState, decode_state


class ScorbotError(RuntimeError):
    """A command or controller operation failed."""


class _WorkerCrashed(ScorbotError):
    """The command worker reported an exception and is exiting; it answers nothing more."""


class Scorbot:
    """ER-4U legacy USB adapter.

    Legacy jogs are relative. Calibrated absolute steps require measured data;
    the queued disable command is not an emergency stop.
    """

    _JOG_CODES = {
        "base": (5, 4),
        "shoulder": (6, 7),
        "elbow": (8, 9),
        "wrist_pitch": (10, 11),
        "wrist_roll": (12, 13),
    }

    def __init__(self, *, log_path: str | Path | None = None,
                 command_timeout: float = 30.0, max_jog_degrees: float = 5.0,
                 response_timeout: float = 2.0, robot_id: str | None = None,
                 calibration_path: str | Path | None = None):
        if (not math.isfinite(command_timeout) or command_timeout <= 0
                or not math.isfinite(max_jog_degrees) or not 0 < max_jog_degrees <= 5
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
            endpoint_in = TrackedInputEndpoint(endpoint_in)
            self._input = endpoint_in
            buffer = usb.util.create_buffer(endpoint_in.wMaxPacketSize)
            sequence, encoder_mean = legacy_sync.msg_start(endpoint_out, endpoint_in, buffer)
            self._buffer = buffer
            self._sync.put(sequence)
            self._reads.put(encoder_mean)
            self._sync_thread = threading.Thread(
                target=legacy_sync.syncro,
                args=(self._sync, self._reads, endpoint_out, endpoint_in, buffer),
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
            self._fault = str(exc)
            self._enabled = None
            self._record("connect_failed", error=self._fault)
            self._cancel_event.set()
            self._home_counts = None
            try:
                self.disconnect()
            except Exception as cleanup_error:
                self._record("connect_cleanup_failed", error=str(cleanup_error))
            raise ScorbotError(
                f"Connection failed: {exc}. Controller motor state is unverified; "
                "use the physical stop if needed") from exc

    def _run_command_worker(self, *args):
        *legacy_args, legacy_comm = args
        try:
            legacy_comm.execute(*legacy_args, self._cancel_event)
        except Exception as exc:
            self._results.put(exc)

    def _command(self, payload: list[int | float], *, timeout: float | None = None) -> None:
        if self._device is None:
            raise ScorbotError("Not connected")
        if self._fault:
            raise ScorbotError(f"Controller is faulted: {self._fault}")
        wait_timeout = self.command_timeout if timeout is None else timeout
        with self._lock:
            self._record("command_start", payload=payload)
            self._commands.put(payload)
            try:
                result = self._results.get(timeout=wait_timeout)
                if isinstance(result, Exception):
                    raise _WorkerCrashed(
                        f"USB command worker crashed: {result}. Motor state is unverified; "
                        "use the physical stop if needed") from result
                if result != 0:
                    while True:
                        next_result = self._results.get(timeout=wait_timeout)
                        if isinstance(next_result, Exception):
                            raise _WorkerCrashed(
                                f"USB command worker crashed: {next_result}. Motor state is "
                                "unverified; use the physical stop if needed") from next_result
                        if next_result == 0:
                            break
                    raise ScorbotError(f"Legacy controller returned error code {result}")
            except queue.Empty as exc:
                self._fault = "Command timed out; physical stop may be required"
                self._cancel_event.set()
                self._commands.put([16, 1, 1])  # Best effort if worker recovers.
                self._enabled = None
                self._homed = False
                self._home_counts = None
                self._record("command_timeout", payload=payload)
                raise ScorbotError(self._fault) from exc
            except ScorbotError as exc:
                self._fault = str(exc)
                # A crashed worker may still look alive while it exits, but it will
                # never answer a disable; waiting for one only delays the fault.
                if (payload[0] != 16 and not isinstance(exc, _WorkerCrashed)
                        and self._command_thread is not None
                        and self._command_thread.is_alive()):
                    self._commands.put([16, 1, 1])
                    try:
                        disable_result = self._results.get(timeout=min(2.0, self.command_timeout))
                    except queue.Empty:
                        disable_result = "timeout"
                    self._record("disable_after_error", result=disable_result)
                elif isinstance(exc, _WorkerCrashed):
                    self._record("disable_skipped_worker_crashed")
                self._enabled = None
                self._homed = False
                self._home_counts = None
                self._record("command_error", payload=payload, error=self._fault)
                raise
            except (KeyboardInterrupt, SystemExit):
                self._fault = "Python interrupted during a controller command"
                self._cancel_event.set()
                self._commands.put([16, 1, 1])
                self._enabled = None
                self._homed = False
                self._home_counts = None
                self._record("command_interrupted", payload=payload)
                raise
            self._record("command_complete", payload=payload)

    def get_state(self, *, after_index: int | None = None) -> RobotState:
        """Return a copied, recent USB response; optionally wait for a newer one."""
        if self._device is None or self._input is None:
            raise ScorbotError("Not connected")
        try:
            with self._state_lock:
                required_index = max(self._last_state_index, after_index or 0)
                sample = self._input.snapshot(
                    after_index=required_index, timeout=self.response_timeout,
                    max_age=self.response_timeout)
                self._last_state_index = sample.index
        except TimeoutError as exc:
            raise ScorbotError(str(exc)) from exc
        return decode_state(sample.data, connected=True,
                            enabled=self._enabled, homed=self._homed,
                            fault=self._fault, packet_index=sample.index,
                            host_monotonic_ns=sample.host_monotonic_ns)

    def _motion_state(self, *, after_index=None):
        try:
            return self.get_state(after_index=after_index)
        except (ScorbotError, ValueError) as exc:
            self._fault = f"Controller feedback is unavailable: {exc}"
            self._enabled = None
            self._homed = False
            self._home_counts = None
            if self._command_thread is not None and self._command_thread.is_alive():
                self._commands.put([16, 1, 1])  # Best effort; never an emergency stop.
            self._record("feedback_fault", error=self._fault)
            raise ScorbotError(self._fault) from exc

    def enable(self):
        with self._motion_lock:
            self._motion_state()
            self._command([17, 1, 1])
            self._enabled = True

    def disable(self):
        """Queue a motor-disable command while the worker is responsive."""
        with self._motion_lock:
            self._command([16, 1, 1])
            self._enabled = False
            self._homed = False
            self._home_counts = None

    def home(self, *, start_position_confirmed: bool = False):
        """Search switches from the confirmed legacy start pose; never Go Home."""
        with self._motion_lock:
            if not start_position_confirmed:
                raise ValueError("Confirm the legacy homing start position first")
            if not self._enabled:
                raise ScorbotError("Enable motors before homing")
            before = self._motion_state()
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
                    self._fault = f"Home verification failed: {exc}"
                    self._enabled = None
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
            positive, negative = self._JOG_CODES[joint]
            preview = self.preview_jog(
                joint, delta_degrees, speed=speed,
                starting_signed_counts=before.signed_encoder_counts)
            self._record("motion_preview", plan=preview, state=asdict(before))
            self._record("motion_start", joint=joint, requested_delta_deg=delta_degrees,
                         speed=speed, state=asdict(before))
            self._command([positive if delta_degrees > 0 else negative,
                           speed, abs(delta_degrees)])
            after = self._motion_state(after_index=before.packet_index)
            self._record("motion_complete", joint=joint,
                         requested_delta_deg=delta_degrees, speed=speed,
                         state=asdict(after))
            return after

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
                self._fault = "Calibrated move did not reach target within 2 degrees"
                self._enabled = None
                self._homed = False
                self._home_counts = None
                self._record("following_error", joint=joint,
                             target_deg=target_degrees, achieved_deg=achieved)
                raise ScorbotError(self._fault)
            self._record("absolute_move_complete", joint=joint,
                         target_deg=target_degrees, achieved_deg=achieved)
            return achieved

    def disconnect(self):
        if self._device is None:
            return
        self._cancel_event.set()
        if self._command_thread is not None and self._command_thread.is_alive():
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
        self._last_state_index = 0
        self._enabled = False
        self._homed = False
        self._home_counts = None
