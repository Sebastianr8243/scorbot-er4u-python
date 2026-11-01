"""Fake ER-4U controller on a Windows named pipe, for running the vendor USBC.dll with no arm.

The DLL opens its ``DriverLink`` with CreateFileA and does 64-byte WriteFile / ReadFile, so a byte-mode
named pipe is enough. Every OUT packet is logged as hex; the reply is a valid IN packet whose axis counts
and home-switch bits come from a small model of the arm. No USB device is opened.

The model (from docs/protocol/VENDOR_HOMING_TRACE.md; unverified on the arm, and it does NOT model the
controller stopping a mode-H drive in hardware when a switch trips):
  - 6 axes: 0 base, 1 shoulder, 2 elbow, 3 wrist m1, 4 wrist m2, 5 gripper. Bit N of the OUT mask and of
    IN byte 5 is axis N.
  - Mode T/H: a `4D` drive value moves the axis every message; a positive drive LOWERS the counts.
  - The gripper stops at its ends (the vendor's count limits, -200 to 6000 from closed) and moves at its own
    gain, so a full stroke takes about 75 messages: shorter than the DLL's 120-tick gripper timer, so both the
    DLL and Stack 2 end on the stall, not on a timer (docs/protocol/LEDGER.md).
  - Mode S: the axis follows the setpoint in OUT bytes 12 + 4N, at a limited rate.
  - `48 mask value`: re-zero the reported count (reported = physical + offset).
  - Each axis has a switch band in joint counts; IN byte 5 has the axis bit set while inside it. The
    shoulder's band is above the start (found by driving -V), every other band is below it.

    python pipe_server.py OUT.jsonl [--seconds 90] [--band-width 2000] [--roll-width 2000]

Refuses to run if a ScorBot USB device (VID_09F1) is present, or if that cannot be checked.
"""
import argparse
import ctypes
import json
import subprocess
import sys
import threading
import time
from ctypes import wintypes

PIPE = r"\\.\pipe\scorbot_fake"
NAMES = ("base", "shoulder", "elbow", "wrist_m1", "wrist_m2", "gripper")
DRIVE_GAIN = 0.1          # counts per message per unit of drive value
FOLLOW_STEP = 400         # counts per message an axis can follow a setpoint
GRIPPER = 5
GRIPPER_GAIN = 0.5        # drive 165 is about 82 counts per message
GRIPPER_STOPS = (-200.0, 6000.0)
GRIPPER_START = 3000.0    # half open, so the first close has somewhere to go
ZERO24 = 0x7FFFFF
EDGE = 2500               # the switch edge every band shares (shoulder: lower edge, others: upper edge)
ARM_QUERY = ("Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -match 'VID_09F1' } "
             "| Measure-Object | ForEach-Object Count")


def refuse_if_arm_present():
    """Fail closed: the fake must never run while a real controller could answer the DLL."""
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ARM_QUERY], capture_output=True, text=True,
                             timeout=60, check=True).stdout.strip()
        present = int(out)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"cannot check for a ScorBot USB device ({exc}); not running") from exc
    if present:
        raise SystemExit("a ScorBot USB device (VID_09F1) is present; unplug it or power it off first")


def bands(width, roll_width):
    """Joint-count bands per axis: the roll's width is separate because it is the one that matters."""
    return {0: (-EDGE - width, -EDGE), 1: (EDGE, EDGE + width), 2: (-EDGE - width, -EDGE),
            3: (-EDGE - width, -EDGE), 4: (-EDGE - roll_width, -EDGE)}


def s32(data, at):
    return int.from_bytes(data[at:at + 4], "little", signed=True)


class Plant:
    def __init__(self, width=2000, roll_width=None):
        self.bands = bands(width, width if roll_width is None else roll_width)
        self.phys = [0.0] * 5 + [GRIPPER_START]
        self.off = [0.0] * 5 + [-GRIPPER_START]
        self.mode = ["P"] * 6
        self.drive = [0] * 6
        self.events = []

    def reported(self, n):
        return int(round(self.phys[n] + self.off[n]))

    def joints(self):
        """Joint coordinates from motor counts, using the coupling the DLL's own drives imply
        (shoulder -90 : elbow +45 : m1 +22 : m2 -22 keeps elbow, pitch and roll still)."""
        base, shoulder, elbow, m1, m2 = self.phys[:5]
        return {0: base, 1: shoulder, 2: elbow + 0.5 * shoulder,
                3: (m1 - m2) / 2 - 0.5 * elbow, 4: (m1 + m2) / 2}

    def switch_bits(self):
        bits, joints = 0, self.joints()
        for n, (lo, hi) in self.bands.items():
            if lo <= joints[n] <= hi:
                bits |= 1 << n
        return bits

    def on_out(self, data):
        cmd, mask = data[4], data[5]
        axes = [n for n in range(6) if mask >> n & 1]
        if cmd == 0x4F:                                   # mode
            letter = chr(data[6])
            for n in axes:
                self.mode[n] = letter
                if letter in "SP":
                    self.drive[n] = 0
        elif cmd == 0x4D:                                 # move: drive value
            for n in axes:
                self.drive[n] = s32(data, 8)
        elif cmd == 0x48:                                 # set position
            value = s32(data, 8)
            for n in axes:
                self.off[n] = value - self.phys[n]
            self.events.append({"event": "set_position", "axes": [NAMES[n] for n in axes], "value": value})
        before = self.switch_bits()
        for n in range(6):                                # integrate one message of motion
            if self.mode[n] in "TH" and self.drive[n]:
                self.phys[n] -= self.drive[n] * (GRIPPER_GAIN if n == GRIPPER else DRIVE_GAIN)
            elif self.mode[n] == "S":
                target = s32(data, 12 + 4 * n) - self.off[n]
                self.phys[n] += max(-FOLLOW_STEP, min(FOLLOW_STEP, target - self.phys[n]))
        self.phys[GRIPPER] = max(GRIPPER_STOPS[0], min(GRIPPER_STOPS[1], self.phys[GRIPPER]))
        after = self.switch_bits()
        if after != before:
            self.events.append({"event": "switches", "before": before, "after": after, "phys": list(self.phys)})

    def reply(self, data):
        packet = bytearray(64)
        packet[0] = data[0]                               # echo of the message ID
        packet[1] = data[4]                               # echo of the command letter
        packet[2] = 0x08                                  # idle baseline, e-stop released
        packet[5] = self.switch_bits()
        for n in range(8):
            raw = (self.reported(n) if n < 6 else 0) + ZERO24
            packet[19 + 5 * n:22 + 5 * n] = (raw & 0xFFFFFF).to_bytes(3, "little")
        packet[60] = (data[0] + 6) & 0xFF                 # queue tail guess
        packet[61:64] = b"\xff\xff\xff"
        return bytes(packet)


def serve(out_path, seconds, plant, connect_seconds=60.0):
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateNamedPipeW.restype = wintypes.HANDLE
    k32.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                     wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    k32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    for name in ("ReadFile", "WriteFile"):
        getattr(k32, name).argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                       ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    handle = k32.CreateNamedPipeW(PIPE, 3, 0, 1, 4096, 4096, 0, None)
    if handle == wintypes.HANDLE(-1).value:
        raise SystemExit(f"CreateNamedPipe failed: {ctypes.get_last_error()}")
    print("listening", flush=True)
    connector = threading.Thread(target=k32.ConnectNamedPipe, args=(handle, None), daemon=True)
    connector.start()
    connector.join(connect_seconds)
    if connector.is_alive():
        raise SystemExit(f"no client connected within {connect_seconds:.0f} s; giving up")
    print("client connected", flush=True)
    start, count = time.monotonic(), 0
    buffer = ctypes.create_string_buffer(4096)
    with open(out_path, "x", encoding="utf-8") as log:
        while time.monotonic() - start < seconds:
            got = wintypes.DWORD(0)
            if not k32.ReadFile(handle, buffer, 64, ctypes.byref(got), None):
                log.write(json.dumps({"event": "read_failed", "err": ctypes.get_last_error()}) + "\n")
                break
            data = buffer.raw[:got.value].ljust(64, b"\0")
            count += 1
            plant.on_out(data)
            log.write(json.dumps({"n": count, "t": round(time.monotonic() - start, 4), "len": got.value,
                                  "out": data.hex(), "rep": [plant.reported(n) for n in range(6)],
                                  "sw": plant.switch_bits()}) + "\n")
            for event in plant.events:
                log.write(json.dumps({"n": count, **event}) + "\n")
            plant.events.clear()
            log.flush()
            reply = plant.reply(data)
            sent = wintypes.DWORD(0)
            k32.WriteFile(handle, reply, len(reply), ctypes.byref(sent), None)
    print("done", count, "packets", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Fake ER-4U controller on a named pipe (no arm, no USB)")
    parser.add_argument("out", help="JSONL log of every OUT packet (must not exist)")
    parser.add_argument("--seconds", type=float, default=90, help="how long to serve once a client has connected")
    parser.add_argument("--connect-seconds", type=float, default=60, help="how long to wait for the DLL to connect")
    parser.add_argument("--band-width", type=int, default=2000, help="switch width, counts, every axis but the roll")
    parser.add_argument("--roll-width", type=int, help="the wrist roll switch width (default: --band-width)")
    args = parser.parse_args()
    refuse_if_arm_present()
    serve(args.out, args.seconds, Plant(args.band_width, args.roll_width), args.connect_seconds)


if __name__ == "__main__":
    sys.exit(main())
