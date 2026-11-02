"""A small model of the arm for ``scorbot.vendor_home``: packets in, packets out, no USB.

Used by the simulator (``SimulatedController`` runs the vendor home against it) and by the tests.

It is the same model that stood in for the controller when Intelitek's ``USBC.dll`` ran its Home
against a named pipe (``tests/fixtures/vendor_home_trace05.json`` was recorded with it), so the
port can be checked against that recording. It is a model, not the arm:

* 6 axes: 0 base, 1 shoulder, 2 elbow, 3 wrist m1, 4 wrist m2, 5 gripper. Bit N of an OUT mask and
  of IN byte 5 is axis N.
* Mode ``T``/``H``: a ``4D`` value moves the axis every message, and a positive value LOWERS the counts.
* Mode ``S``: the axis follows the setpoint in OUT bytes ``12 + 4N`` at a limited rate.
* ``48``: re-zero the reported count (reported = physical + offset).
* The gripper starts half open, stops at its ends (the vendor's -200 to 6000 counts) and moves at its own
  gain, so a stroke is shorter than the vendor's 120-tick gripper timer and ends on the vendor's stall check
  (``tests/fixtures/vendor_home_trace05.json``; the same model as ``tools/usbc_fake/pipe_server.py``).
* Each axis has a switch band in JOINT coordinates (shoulder S, elbow E + S/2, pitch (m1 - m2)/2 - E/2,
  roll (m1 + m2)/2), set while the joint is inside it. The shoulder's band is above the start, the
  others below.

Nothing here has been measured on the arm.
"""

from . import vendor_home as vh

DRIVE_GAIN = 0.1
FOLLOW_STEP = 400
BANDS = {0: (-4500, -2500), 1: (2500, 4500), 2: (-4500, -2500), 3: (-4500, -2500), 4: (-4500, -2500)}
GRIPPER_GAIN = 0.5
GRIPPER_STOPS = (-200, 6000)
GRIPPER_START = 3000


def s32(data, at):
    return int.from_bytes(data[at:at + 4], "little", signed=True)


class Plant:
    def __init__(self, bands=None, start=None, gain=None, frozen=(), gripper_range=GRIPPER_STOPS, stops=None):
        """``gain`` is counts per message per unit of drive (negative flips the drive sign; the gripper
        moves ``GRIPPER_GAIN / DRIVE_GAIN`` times faster), ``frozen`` axes never move, ``gripper_range``
        are the gripper's end stops in physical counts. The gripper reads 0 at the start. ``stops`` maps a
        motor to its (low, high) hard stops in physical counts; without one a motor driven long enough
        wraps the 24-bit count, which a real arm's hard stops never allow."""
        self.bands = dict(BANDS if bands is None else bands)
        self.gain = DRIVE_GAIN if gain is None else gain
        self.gripper_gain = self.gain * GRIPPER_GAIN / DRIVE_GAIN
        self.frozen = set(frozen)
        self.gripper_range = gripper_range
        self.stops = dict(stops or {})
        self.phys = [float(x) for x in (start or (0,) * 5 + (GRIPPER_START,))]
        self.off = [0.0] * 5 + [-self.phys[5]]
        self.mode = ["P"] * 6
        self.drive = [0] * 6
        self.impact = 0
        self.emergency = False

    def reported(self, n):
        return int(round(self.phys[n] + self.off[n]))

    def joints(self):
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
        if cmd == vh.MODE:
            letter = chr(data[6])
            for n in axes:
                self.mode[n] = letter
                if letter in "SP":
                    self.drive[n] = 0
        elif cmd == vh.MOVE:
            for n in axes:
                self.drive[n] = s32(data, 8)
        elif cmd == vh.SET_POSITION:
            for n in axes:
                self.off[n] = s32(data, 8) - self.phys[n]
        for n in range(6):
            if n in self.frozen:
                continue
            if self.mode[n] in "TH" and self.drive[n]:
                self.phys[n] -= self.drive[n] * (self.gripper_gain if n == 5 else self.gain)
            elif self.mode[n] == "S":
                target = s32(data, 12 + 4 * n) - self.off[n]
                self.phys[n] += max(-FOLLOW_STEP, min(FOLLOW_STEP, target - self.phys[n]))
        low, high = self.gripper_range
        self.phys[5] = max(low, min(high, self.phys[5]))
        for n, (low, high) in self.stops.items():
            self.phys[n] = max(low, min(high, self.phys[n]))

    def reply(self, data):
        packet = bytearray(64)
        packet[0] = data[0]
        packet[1] = data[4]
        packet[2] = 0x08 | (1 if self.emergency else 0)
        packet[5] = self.switch_bits()
        for n in range(6):
            packet[19 + 5 * n:22 + 5 * n] = ((self.reported(n) + vh.ZERO24) & 0xFFFFFF).to_bytes(3, "little")
        packet[59] = self.impact
        packet[60] = (data[0] + 6) & 0xFF
        packet[61:64] = b"\xff\xff\xff"
        return bytes(packet)

    def exchange(self, data):
        self.on_out(data)
        return self.reply(data)
