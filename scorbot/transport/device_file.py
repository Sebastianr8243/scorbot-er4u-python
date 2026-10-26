"""The link to the controller through Intelitek's own driver, the way the vendor's ``USBC.dll`` does it.

Read from the DLL's communication thread (``FUN_1003bc88`` in the 2008 build, see
``docs/protocol/VENDOR_DLL_PROTOCOL.md``): it opens the driver's device name with plain ``CreateFileA``
(read and write, **no sharing**, ``OPEN_EXISTING``, no overlapped flag), then does blocking 64-byte
``WriteFile`` and ``ReadFile`` calls. There is no ``DeviceIoControl``, no event and no ``GetOverlappedResult``
anywhere in the DLL. A read that returns zero bytes is retried, ten times in a row at most. The device
name is the ``DriverLink`` entry of ``USBC.INI`` (``\\\\.\\ERUSBDevice0`` in the shipped file).

This module does the same in Python so our code could run on Intelitek's driver and skip the Zadig swap.
Not used by ``Scorbot`` yet, never run against a device, unverified on the arm: whether the driver honours
``CancelSynchronousIo`` (used for the timeout) is not known, and the exclusive open means SCORBASE and
``usbc_home`` must be closed first. The endpoints mimic what ``openScorbot`` calls on pyusb's endpoints:
``write(data, timeout_ms)`` and ``read(buffer, timeout_ms)`` filling the buffer in place.

Standard library only. ``import`` opens nothing; the real Windows layer is built only when no fake is given.
"""

from array import array
import threading

DEFAULT_PATH = "\\\\.\\ERUSBDevice0"
PACKET_SIZE = 64
MAX_EMPTY_READS = 10   # the DLL counts consecutive zero-byte reads and gives up at 10

ERROR_FILE_NOT_FOUND = 2
ERROR_PATH_NOT_FOUND = 3
ERROR_ACCESS_DENIED = 5
ERROR_SHARING_VIOLATION = 32
ERROR_OPERATION_ABORTED = 995

_GENERIC_READ_WRITE = 0xC0000000
_OPEN_EXISTING = 3
_THREAD_TERMINATE = 0x0001


class Win32Error(OSError):
    """A failed kernel32 call; ``code`` is the Win32 error number."""

    def __init__(self, code, message=""):
        super().__init__(f"Win32 error {code}: {message}" if message else f"Win32 error {code}")
        self.code = code


class DeviceLinkError(RuntimeError):
    """The link to the controller failed or was used wrongly."""


class DeviceNotFoundError(DeviceLinkError):
    """The device name does not exist: the driver is not bound, or the controller is absent."""


class Win32Api:
    """kernel32, as little of it as the DLL uses. Built only by ``DeviceLink`` when no fake is passed."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self._c = ctypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                  wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        k.CreateFileW.restype = ctypes.c_void_p
        k.WriteFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p, wintypes.DWORD,
                                ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        k.WriteFile.restype = wintypes.BOOL
        k.ReadFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                               ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        k.ReadFile.restype = wintypes.BOOL
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        k.CloseHandle.restype = wintypes.BOOL
        k.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenThread.restype = ctypes.c_void_p
        k.CancelSynchronousIo.argtypes = [ctypes.c_void_p]
        k.CancelSynchronousIo.restype = wintypes.BOOL
        self._k = k
        self._wt = wintypes

    def _fail(self, what):
        raise Win32Error(self._c.get_last_error(), what)

    def create_file(self, path):
        handle = self._k.CreateFileW(path, _GENERIC_READ_WRITE, 0, None, _OPEN_EXISTING, 0, None)
        if handle is None or handle == self._c.c_void_p(-1).value:
            self._fail("CreateFile")
        return handle

    def write_file(self, handle, data):
        done = self._wt.DWORD(0)
        if not self._k.WriteFile(handle, bytes(data), len(data), self._c.byref(done), None):
            self._fail("WriteFile")
        return done.value

    def read_file(self, handle, size):
        buffer = self._c.create_string_buffer(size)
        done = self._wt.DWORD(0)
        if not self._k.ReadFile(handle, buffer, size, self._c.byref(done), None):
            self._fail("ReadFile")
        return buffer.raw[:done.value]

    def cancel_io(self, thread_id):
        thread = self._k.OpenThread(_THREAD_TERMINATE, False, thread_id)
        if thread:
            try:
                self._k.CancelSynchronousIo(thread)
            finally:
                self._k.CloseHandle(thread)

    def close_handle(self, handle):
        self._k.CloseHandle(handle)


def _open_message(error, path):
    if error.code == ERROR_SHARING_VIOLATION:
        return (f"{path} is already open in another program. Close SCORBASE, usbc_home and anything else "
                "that talks to the controller, then try again.")
    if error.code in (ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND):
        return (f"{path} does not exist. Intelitek's driver is not bound to the controller (it may still be on "
                "WinUSB from Zadig), or the controller is not plugged in or powered.")
    if error.code == ERROR_ACCESS_DENIED:
        return f"Access to {path} was denied; try an administrator terminal."
    return f"Could not open {path}: {error}"


class DeviceLink:
    """One open handle to the controller and the two endpoint-shaped views of it."""

    def __init__(self, path=DEFAULT_PATH, *, api=None):
        self._api = api if api is not None else Win32Api()
        self._lock = threading.Lock()
        self.path = path
        try:
            self._handle = self._api.create_file(path)
        except Win32Error as error:
            kind = DeviceNotFoundError if error.code in (ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND)                 else DeviceLinkError
            raise kind(_open_message(error, path)) from error
        self.endpoint_out = _OutEndpoint(self)
        self.endpoint_in = _InEndpoint(self)

    def _live(self):
        if self._handle is None:
            raise DeviceLinkError("the device link is closed")
        return self._handle

    def close(self):
        with self._lock:
            handle, self._handle = self._handle, None
        if handle is not None:
            self._api.close_handle(handle)

    def _timed(self, call, timeout_ms):
        """Run a blocking call; after ``timeout_ms`` cancel it from a timer thread and raise ``TimeoutError``."""
        state = {"active": True, "fired": False}
        guard = threading.Lock()
        thread_id = threading.get_native_id()

        def fire():
            with guard:
                if state["active"]:
                    state["fired"] = True
                    self._api.cancel_io(thread_id)

        timer = None
        if timeout_ms and timeout_ms > 0:
            timer = threading.Timer(timeout_ms / 1000.0, fire)
            timer.daemon = True
            timer.start()
        try:
            return call()
        except Win32Error as error:
            if state["fired"] and error.code == ERROR_OPERATION_ABORTED:
                raise TimeoutError(f"no answer from the controller within {timeout_ms} ms") from error
            raise DeviceLinkError(f"USB I/O failed: {error}") from error
        finally:
            with guard:
                state["active"] = False
            if timer is not None:
                timer.cancel()


class _OutEndpoint:
    def __init__(self, link):
        self._link = link
        self.wMaxPacketSize = PACKET_SIZE

    def write(self, data, timeout=None):
        if len(data) != PACKET_SIZE:
            raise ValueError(f"the controller takes {PACKET_SIZE}-byte packets, got {len(data)}")
        handle = self._link._live()
        written = self._link._timed(lambda: self._link._api.write_file(handle, bytes(data)), timeout)
        if written != PACKET_SIZE:
            raise DeviceLinkError(f"short write: {written} of {PACKET_SIZE} bytes")
        return written


class _InEndpoint:
    def __init__(self, link):
        self._link = link
        self.wMaxPacketSize = PACKET_SIZE

    def read(self, buffer, timeout=None):
        handle = self._link._live()
        for _ in range(MAX_EMPTY_READS):
            data = self._link._timed(lambda: self._link._api.read_file(handle, PACKET_SIZE), timeout)
            if len(data) == PACKET_SIZE:
                buffer[0:PACKET_SIZE] = array("B", data) if isinstance(buffer, array) else data
                return PACKET_SIZE
            if data:
                raise DeviceLinkError(f"partial read: {len(data)} of {PACKET_SIZE} bytes")
        raise DeviceLinkError(f"{MAX_EMPTY_READS} empty reads in a row; the controller is not answering")
