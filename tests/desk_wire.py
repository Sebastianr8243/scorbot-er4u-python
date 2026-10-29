"""A fake USB wire that records every OUT packet the real ``Scorbot`` sends, for the desk byte check.

``record_session()`` connects, enables and disables a real ``Scorbot`` (the legacy workers and their
handshake included) against a fake ``usb`` package whose endpoints only log OUT packets and answer each one
with an idle IN packet. Nothing is opened; the legacy sleeps are shortened so a session takes about two seconds.
"""

import sys
import tempfile
import threading
import time
import types
from pathlib import Path
from unittest import mock

from scorbot import Scorbot

SLEEP_SCALE = 20


class FakeWire:
    """Both endpoints share one log; each IN reply echoes the last OUT's byte 0 and command letter."""

    def __init__(self):
        self.out_packets = []
        self._last = bytes(64)
        self._lock = threading.Lock()

    def _reply(self):
        last = self._last
        packet = bytearray(64)
        packet[0], packet[1], packet[2] = last[0], last[4], 0x08
        packet[60] = (last[0] + 6) & 0xFF
        packet[61:64] = b"\xff\xff\xff"
        for joint in range(8):
            packet[19 + 5 * joint:22 + 5 * joint] = (0x7FFFFF).to_bytes(3, "little")
        return packet

    def endpoints(self):
        wire = self

        class Out:
            bEndpointAddress, wMaxPacketSize = 0x02, 64

            def write(self, data, timeout=None):
                with wire._lock:
                    wire.out_packets.append(bytes(data))
                    wire._last = bytes(data)
                return len(data)

        class In:
            bEndpointAddress, wMaxPacketSize = 0x83, 64

            def read(self, buffer, timeout=None):
                buffer[:64] = wire._reply()
                return 64

        return {0x83: In(), 0x02: Out()}


class FakeDevice:
    def reset(self):
        pass

    def __getitem__(self, index):
        return types.SimpleNamespace(interfaces=lambda: [types.SimpleNamespace(bInterfaceNumber=0)])

    def is_kernel_driver_active(self, number):
        return False

    def get_active_configuration(self):
        return {(0, 0): object()}


def fake_usb_modules(device, wire):
    endpoints = wire.endpoints()

    def find_descriptor(interface, custom_match):
        return next((e for e in endpoints.values() if custom_match(e)), None)

    core = types.SimpleNamespace(find=lambda **kw: device, USBError=type("USBError", (Exception,), {}))
    util = types.SimpleNamespace(
        ENDPOINT_IN=0x80, ENDPOINT_OUT=0x00, endpoint_direction=lambda address: address & 0x80,
        find_descriptor=find_descriptor, create_buffer=bytearray, dispose_resources=lambda dev: None)
    return {"usb": types.SimpleNamespace(core=core, util=util), "usb.core": core, "usb.util": util,
            "libusb_package": types.SimpleNamespace(find=lambda **kw: device)}


def record_session():
    """OUT packets per call, ``{"connect": [...], "enable": [...], "disable": [...], "disconnect": [...]}``."""
    wire, real_sleep, calls = FakeWire(), time.sleep, {}
    with tempfile.TemporaryDirectory() as folder:
        robot = Scorbot(log_path=Path(folder) / "run.controller.jsonl", command_timeout=120)   # the session takes 35 s on a loaded Windows runner; 30 s made it fail there
        for name in ("conf", "libdef", "libcomm", "libsync"):
            robot._legacy(name)             # import numpy and the legacy modules before sys.modules is patched
        with mock.patch.dict(sys.modules, fake_usb_modules(FakeDevice(), wire)), \
                mock.patch("time.sleep", lambda seconds: real_sleep(seconds / SLEEP_SCALE)):
            try:
                for name in ("connect", "enable", "disable"):
                    start = len(wire.out_packets)
                    getattr(robot, name)()
                    calls[name] = wire.out_packets[start:]
            finally:
                start = len(wire.out_packets)
                robot.disconnect()
                calls["disconnect"] = wire.out_packets[start:]
    return calls
