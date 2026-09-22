"""Small, synchronous Python adapter around the original USB controller code."""

from dataclasses import asdict
import importlib
import json
from pathlib import Path
import queue
import sys
import threading

from .state import RobotState, decode_state


class ScorbotError(RuntimeError):
    """A command or controller operation failed."""


class Scorbot:
    """ER-4U legacy USB adapter.

    Joint motions are *relative* jogs. This is not a calibrated absolute-motion
    controller and its disable command is not an emergency stop.
    """

    _JOG_CODES = {
        "base": (5, 4),
        "shoulder": (6, 7),
        "elbow": (8, 9),
        "wrist_pitch": (10, 11),
        "wrist_roll": (12, 13),
    }

    def __init__(self, *, log_path: str | Path | None = None,
                 command_timeout: float = 30.0, max_jog_degrees: float = 5.0):
        if command_timeout <= 0 or max_jog_degrees <= 0:
            raise ValueError("Timeout and maximum jog angle must be positive")
        self.log_path = Path(log_path) if log_path else None
        self.command_timeout = command_timeout
        self.max_jog_degrees = max_jog_degrees
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
        row = {"event": event, **fields}
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
            self._commands.put(payload)
            try:
                result = self._results.get(timeout=wait_timeout)
                if isinstance(result, Exception):
                    raise ScorbotError(f"USB command worker crashed: {result}") from result
                if result != 0:
                    while True:
                        next_result = self._results.get(timeout=wait_timeout)
                        if isinstance(next_result, Exception):
                            raise ScorbotError(
                                f"USB command worker crashed: {next_result}") from next_result
                        if next_result == 0:
                            break
                    raise ScorbotError(f"Legacy controller returned error code {result}")
            except queue.Empty as exc:
                self._fault = "Command timed out; physical stop may be required"
                self._cancel_event.set()
                self._commands.put([16, 1, 1])  # Best effort if worker recovers.
                self._enabled = None
                self._homed = False
                self._record("command_timeout", payload=payload)
                raise ScorbotError(self._fault) from exc
            except ScorbotError as exc:
                self._fault = str(exc)
                if payload[0] != 16 and self._command_thread is not None and self._command_thread.is_alive():
                    self._commands.put([16, 1, 1])
                    try:
                        disable_result = self._results.get(timeout=min(2.0, self.command_timeout))
                    except queue.Empty:
                        disable_result = "timeout"
                    self._record("disable_after_error", result=disable_result)
                self._enabled = None
                self._homed = False
                self._record("command_error", payload=payload, error=self._fault)
                raise
            except (KeyboardInterrupt, SystemExit):
                self._fault = "Python interrupted during a controller command"
                self._cancel_event.set()
                self._commands.put([16, 1, 1])
                self._enabled = None
                self._homed = False
                self._record("command_interrupted", payload=payload)
                raise
            self._record("command", payload=payload, state=asdict(self.get_state()))

    def get_state(self) -> RobotState:
        if self._device is None or self._buffer is None:
            raise ScorbotError("Not connected")
        return decode_state(bytes(self._buffer), connected=True,
                            enabled=self._enabled, homed=self._homed,
                            fault=self._fault)

    def enable(self):
        self._command([17, 1, 1])
        self._enabled = True

    def disable(self):
        """Queue a motor-disable command while the worker is responsive."""
        self._command([16, 1, 1])
        self._enabled = False
        self._homed = False

    def home(self, *, start_position_confirmed: bool = False):
        """Run legacy homing only from its required physical start position."""
        if not start_position_confirmed:
            raise ValueError("Confirm the legacy homing start position first")
        if not self._enabled:
            raise ScorbotError("Enable motors before homing")
        self._homed = False
        self._command([18, 1, 1], timeout=180.0)
        self._homed = True

    def jog_joint(self, joint: str, delta_degrees: float, *, speed: int = 10):
        """Move one named joint by a small relative angle in legacy speed units."""
        if joint not in self._JOG_CODES:
            raise ValueError(f"Unknown joint: {joint}")
        if isinstance(delta_degrees, bool) or not isinstance(delta_degrees, (int, float)) or not 0 < abs(delta_degrees) <= self.max_jog_degrees:
            raise ValueError(f"Jog must be nonzero and at most {self.max_jog_degrees} degrees")
        if isinstance(speed, bool) or not isinstance(speed, int) or not 1 <= speed <= 20:
            raise ValueError("Legacy speed must be an integer from 1 to 20")
        if not self._enabled or not self._homed:
            raise ScorbotError("Enable and home before jogging")
        positive, negative = self._JOG_CODES[joint]
        self._command([positive if delta_degrees > 0 else negative,
                       speed, abs(delta_degrees)])

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
        self._enabled = False
        self._homed = False
