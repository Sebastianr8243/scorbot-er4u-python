"""Read-only setup checks for the original ER-4U USB controller.

Run with ``python -m scorbot.preflight`` before connecting with the SDK.
This module only asks the USB backend to enumerate the controller. It never
opens the device for application I/O, resets it, configures it, or sends data.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module, metadata
import sys


VID = 0x09F1
PID = 0x0007


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str


def run_checks() -> list[Check]:
    """Inspect local dependencies, backend loading, and USB enumeration only."""
    checks = []
    supported_python = sys.version_info >= (3, 10)
    checks.append(Check("Python", supported_python,
                        f"{sys.version.split()[0]} (requires 3.10 or newer)"))

    for distribution, module in (("numpy", "numpy"), ("pyusb", "usb.core")):
        try:
            version = metadata.version(distribution)
            import_module(module)
        except (metadata.PackageNotFoundError, ImportError, OSError) as exc:
            checks.append(Check(distribution, False,
                                f"Unavailable: {exc}. Run: python -m pip install -e '.[windows]'"))
        else:
            checks.append(Check(distribution, True, f"{version} imports successfully"))

    if not supported_python or any(not item.passed for item in checks):
        return checks

    try:
        if sys.platform == "win32":
            package = import_module("libusb_package")
            version = metadata.version("libusb-package")
            backend = package.get_libusb1_backend()
            backend_name = "Windows libusb-package"
        else:
            libusb = import_module("usb.backend.libusb1")
            backend = libusb.get_backend()
            version = "system library"
            backend_name = "libusb-1.0"
    except (metadata.PackageNotFoundError, ImportError, OSError) as exc:
        checks.append(Check("libusb backend", False,
                            f"Unavailable: {exc}. On Windows, run: python -m pip install -e '.[windows]'"))
        return checks
    except Exception as exc:
        checks.append(Check("libusb backend", False,
                            f"Could not load backend: {type(exc).__name__}: {exc}"))
        return checks

    if backend is None:
        checks.append(Check("libusb backend", False,
                            "No libusb-1.0 backend loaded. On Windows, install the '.[windows]' extra and use the same Python environment."))
        return checks
    checks.append(Check("libusb backend", True, f"{backend_name} {version} loaded"))

    try:
        usb_core = import_module("usb.core")
        device = usb_core.find(idVendor=VID, idProduct=PID, backend=backend)
    except Exception as exc:
        checks.append(Check("USB 09F1:0007", False,
                            f"Enumeration failed: {type(exc).__name__}: {exc}. Check the Windows USB driver binding and device access."))
    else:
        if device is None:
            checks.append(Check("USB 09F1:0007", False,
                                "Not visible to libusb. Confirm the controller appears in Windows Device Manager; its current driver may not allow libusb access."))
        else:
            checks.append(Check("USB 09F1:0007", True,
                                "Controller enumerated; no connection, reset, configuration, or application packets were sent."))
    return checks


def main() -> int:
    checks = run_checks()
    for check in checks:
        print(f"{'PASS' if check.passed else 'FAIL'} {check.name}: {check.detail}")
    if all(check.passed for check in checks):
        print("Preflight passed. Motion and homing still require supervised hardware validation.")
        return 0
    print("Preflight failed. Resolve the failed checks before using scorbot.Scorbot.connect().")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
