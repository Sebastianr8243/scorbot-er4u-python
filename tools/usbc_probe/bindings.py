"""ctypes bindings for Intelitek's vendor ``USBC.dll``. UNVERIFIED.

Nothing here has been run against the real DLL or the arm. The export name
and signature come from docs/protocol/PROTOCOL.md section 12 (third-party sources),
not from a copy of the DLL we have inspected.

Two layers:

- ``_Bindings`` is the low-level layer: loads the DLL, resolves each
  decorated export and pins its ``argtypes``/``restype``.
- ``UsbcDll`` is the user API: plain Python values in and out, plus the
  ``supervised_mode`` gate.

``USBC.dll`` is 32-bit, so it only loads in a 32-bit Python. Loading runs the
DLL's own start-up code; whether that touches the controller driver is
unknown.

Parked: the project reads the DLL by static analysis and confirms with USB
captures of SCORBASE (docs/protocol/VENDOR_DLL_PROTOCOL.md), so nothing calls this.
It lives outside ``scorbot/`` on purpose, imports nothing from it, and never
imports ``usb``.

Standard library only.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import os
import struct

MAX_PATH = 260
# The DLL writes text in the Windows ANSI code page. "mbcs" exists only on
# Windows; elsewhere this module is reachable only through a test double.
_ANSI_ENCODING = "mbcs" if os.name == "nt" else "latin-1"
# Spare zeroed room after each string buffer. The DLL's string getters take
# no length, so an overlong write lands here and is detected after the call.
_GUARD_BYTES = 1024


class VendorDllError(RuntimeError):
    """The DLL could not be loaded, bound or called safely."""


@dataclass(frozen=True)
class Export:
    """One export: its Python-side name and its exact decorated symbol.

    ``energises`` marks a call that can power the motors or move the arm.
    Mark anything not known to be a pure getter: ``Initialization`` is the
    connect handshake, and ``MoveManual`` turns control on by itself
    (docs/protocol/PROTOCOL.md section 12.2).
    """

    name: str
    symbol: str
    restype: object
    argtypes: tuple
    energises: bool


# Copy symbols verbatim from `dumpbin /exports USBC.dll` and take the
# signature from `undname`. A wrong argtypes/restype corrupts memory at call
# time; it does not fail at bind time.
EXPORTS = (
    # int __cdecl GetParameterFolder(char *)
    Export(
        name="GetParameterFolder",
        symbol="?GetParameterFolder@@YAHPAD@Z",
        restype=ctypes.c_int,
        argtypes=(ctypes.c_char_p,),
        energises=False,
    ),
)


def _pointer_bits() -> int:
    return struct.calcsize("P") * 8


def check_python_is_32_bit() -> None:
    bits = _pointer_bits()
    if bits != 32:
        raise VendorDllError(
            f"USBC.dll is a 32-bit DLL and this Python is {bits}-bit. "
            "Run this module from a 32-bit Python install."
        )


def _load_cdecl(path: str):
    # CDLL is the cdecl loader (WinDLL would be stdcall).
    return ctypes.CDLL(path)


class _Bindings:
    """Low-level layer: one typed ctypes function per ``Export``."""

    def __init__(self, dll_path: str, exports=EXPORTS, loader=_load_cdecl):
        try:
            self._library = loader(dll_path)
        except OSError as error:
            # 193 = bitness mismatch, 126 = the DLL or one of its own
            # dependencies was not found.
            code = getattr(error, "winerror", None)
            detail = f" (OS error {code})" if code else ""
            raise VendorDllError(f"could not load {dll_path}{detail}: {error}") from error
        self._exports = {}
        self._functions = {}
        for export in exports:
            self._exports[export.name] = export
            self._functions[export.name] = self._resolve(export)

    def _resolve(self, export: Export):
        try:
            function = getattr(self._library, export.symbol)
        except AttributeError as error:
            # Best effort: ctypes does not promise the thread's last error
            # survives until here. 127 = procedure not found.
            get_last_error = getattr(ctypes, "GetLastError", None)
            code = get_last_error() if get_last_error else None
            raise VendorDllError(
                f"export {export.symbol} ({export.name}) not found "
                f"(OS error {code}); compare with dumpbin /exports"
            ) from error
        function.argtypes = list(export.argtypes)
        function.restype = export.restype
        return function

    def export(self, name: str) -> Export:
        try:
            return self._exports[name]
        except KeyError:
            raise VendorDllError(f"no binding named {name}") from None

    def call(self, name: str, *args):
        self.export(name)
        return self._functions[name](*args)


class UsbcDll:
    """High-level API over ``USBC.dll``.

    With ``supervised_mode=False`` (the default) every export marked
    ``energises`` is refused before the DLL is called. ``supervised_mode=True``
    is the caller's statement that a person is at the arm with the physical
    stop in reach. It is not a safety system and adds no stop of its own.
    """

    def __init__(
        self,
        dll_path,
        supervised_mode: bool = False,
        *,
        exports=EXPORTS,
        loader=_load_cdecl,
    ):
        if not isinstance(supervised_mode, bool):
            raise TypeError("supervised_mode must be True or False")
        check_python_is_32_bit()
        # An absolute path keeps Windows from picking up another USBC.dll and
        # lets it find the DLL's own dependencies next to it.
        path = os.path.abspath(os.fspath(dll_path))
        if not os.path.isfile(path):
            raise VendorDllError(f"no DLL at {path}")
        self.dll_path = path
        self.supervised_mode = supervised_mode
        self._bindings = _Bindings(path, exports=exports, loader=loader)

    def _call(self, name: str, *args):
        if self._bindings.export(name).energises and not self.supervised_mode:
            raise VendorDllError(
                f"{name} can power the motors or move the arm; "
                "refused because supervised_mode is False"
            )
        return self._bindings.call(name, *args)

    def get_parameter_folder(self, buffer_size: int = MAX_PATH) -> tuple[int, str]:
        """Return ``(return_code, folder)``.

        The meaning of the return code is unverified, so it is passed through
        and not judged. The DLL takes no length argument: it writes as much as
        it wants. A write past ``buffer_size`` raises if it stayed inside the
        guard bytes; a longer one corrupts memory undetected.
        """
        if buffer_size < MAX_PATH:
            raise ValueError(f"buffer_size must be at least {MAX_PATH}")
        buffer = ctypes.create_string_buffer(buffer_size + _GUARD_BYTES)
        return_code = self._call("GetParameterFolder", buffer)
        if any(buffer.raw[buffer_size - 1 :]):
            raise VendorDllError(
                f"GetParameterFolder wrote more than {buffer_size} bytes; "
                "process memory may be corrupted, restart Python"
            )
        folder = buffer.value.decode(_ANSI_ENCODING, errors="replace")
        return return_code, folder
