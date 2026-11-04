"""Golden OUT messages of the legacy path (``openScorbot/``): every byte, in order, for the main flows.

Each flow runs the legacy functions directly, on one thread, against a fake arm (``Arm``): every reply
echoes the message's ID and command letter, and while it follows it reports each axis at the setpoint
the last ``0D`` message carried. Nothing is opened; no USB, nothing moves. The flows are deterministic, so
a fixture pins the exact packets, the result codes, the sequence byte handed back, the media left in the
queue, the idle hold and the write/read sleeps. A refactor of the legacy code must leave every fixture
unchanged (docs/requirements/legacy-golden.md). The fake arm is our model: a golden says what the code
sends, not what the controller accepts.

Re-record (only for a deliberate byte change, listed in its PR):
    python -m tests.test_legacy_golden --record
"""

from importlib.util import find_spec
import json
import queue
import sys
import unittest
from itertools import count, groupby
from pathlib import Path
from unittest import mock

from scorbot.state import ENCODER_OFFSETS, ERROR_OFFSETS

FIXTURES = Path(__file__).parent / "fixtures" / "legacy_golden"
ZERO24 = 0x7FFFFF
START = [50, 1200, -800, 300, -300, 0]     # base starts near zero so its jogs cross it
COLLISION_BYTE = 59


class Arm:
    """Both endpoints of the fake controller. ``follow``: track the setpoints; ``fault_at``: from that
    write on, report ``collision`` in byte 59 and ``error`` on axis ``error_axis``."""

    def __init__(self, counts=START, follow=True, fault_at=None, collision=0, error_axis=None, error=0):
        self.counts, self.follow = list(counts), follow
        self.fault_at, self.collision, self.error_axis, self.error = fault_at, collision, error_axis, error
        self.sent = []
        self.reply = self._reply(bytes(64))

    def _reply(self, out):
        reply = bytearray(64)
        reply[0], reply[1] = out[0], out[4]
        for value, offset in zip(self.counts, ENCODER_OFFSETS):
            reply[offset:offset + 3] = (value + ZERO24).to_bytes(3, "little")
        if self.fault_at is not None and len(self.sent) >= self.fault_at:
            reply[COLLISION_BYTE] = self.collision
            if self.error_axis is not None:
                offset = ERROR_OFFSETS[self.error_axis]
                reply[offset:offset + 2] = self.error.to_bytes(2, "little")
        return reply

    def write(self, data, timeout=None):
        data = bytes(data)
        self.sent.append(data)
        region = data[12:36]
        if self.follow and data[4] == 0x0D and any(region):
            self.counts = [int.from_bytes(region[i:i + 4], "little", signed=True) for i in range(0, 24, 4)]
        self.reply = self._reply(data)
        return len(data)

    def read(self, buffer, timeout=None):
        buffer[:64] = self.reply
        return 64


class StopAfter:
    """A stop request that is set once the arm has seen ``writes`` messages."""

    def __init__(self, arm, writes):
        self.arm, self.writes = arm, writes

    def is_set(self):
        return len(self.arm.sent) >= self.writes


def run(legacy, call, arm=None, b_1=10, media=None):
    """Run ``call(comm, b_1, arm, buffer, reads, results)`` and return everything it left behind."""
    arm = arm or Arm()
    libdef = legacy["libdef"]
    libdef.reset_count_guard()
    libdef.ultimo_hold = None
    buffer = bytearray(arm.reply)
    reads, results, sleeps = queue.Queue(), queue.Queue(), []
    reads.put(list(media) if media is not None else [libdef.transform([buffer[p], buffer[p + 1]]) for p in libdef.VEC_POS])
    with mock.patch("time.sleep", side_effect=sleeps.append):
        returned = call(legacy, b_1, arm, buffer, reads, results)
    return {
        "packets": [packet.hex().rstrip("0") for packet in arm.sent],
        "lengths": sorted({len(packet) for packet in arm.sent}),
        "returned": returned,
        "results": list(results.queue),
        "media": [list(map(int, item)) for item in reads.queue],
        "hold": None if libdef.ultimo_hold is None else libdef.ultimo_hold.hex(),
        "sleeps": [[value, len(list(group))] for value, group in groupby(sleeps)],
    }


def jog(order, degrees, speed, stop_after=None):
    def call(legacy, b_1, arm, buffer, reads, results):
        comm = legacy["libcomm"]
        mover = {4: comm.move_hips, 5: comm.move_hips, 6: comm.move_shoulder, 7: comm.move_shoulder,
                 8: comm.move_elbow, 9: comm.move_elbow}.get(order, comm.move_wrist)
        stop = None if stop_after is None else StopAfter(arm, stop_after)
        return mover(b_1, arm, arm, buffer, order, reads, results, speed, degrees, stop)
    return call


def clamp(order, stop_after=None):
    def call(legacy, b_1, arm, buffer, reads, results):
        stop = None if stop_after is None else StopAfter(arm, stop_after)
        return legacy["libcomm"].clamp(b_1, arm, arm, buffer, order, reads, results, stop)
    return call


def stream(steps, finish=("end",)):
    def call(legacy, b_1, arm, buffer, reads, results):
        todo = [("send", step) for step in steps] + [finish]
        return legacy["libcomm"].stream_targets(b_1, arm, arm, buffer, reads, results, lambda reply: todo.pop(0))
    return call


def connect(legacy, b_1, arm, buffer, reads, results):
    reads.get()
    b_1, media = legacy["libsync"].msg_start(arm, arm, buffer)
    reads.put(media)
    return b_1


def motors_on(legacy, b_1, arm, buffer, reads, results):
    return legacy["libcomm"].motors_on(b_1, arm, arm, buffer, reads)


def motors_off(legacy, b_1, arm, buffer, reads, results):
    return legacy["libcomm"].motors_off(b_1, arm, arm, buffer, reads)


def disconnect(legacy, b_1, arm, buffer, reads, results):
    """Order EXIT: motors off, then the close (``execute``)."""
    comm = legacy["libcomm"]
    b_1 = comm.motors_off(b_1, arm, arm, buffer, reads)
    comm.scorbotoff(b_1, arm, arm, buffer, reads)
    return b_1


def failed_vendor_home(legacy, b_1, arm, buffer, reads, results):
    """A vendor home whose source stops after three idle messages: the legacy motors-off follows."""
    calls = count()

    def source(reply, sequence):
        if next(calls) < 3:
            return ("send", bytes([sequence, 0, 0, 0, 0x0D]).ljust(64, b"\0"))
        return ("stop",)
    return legacy["libcomm"].vendor_home_run(b_1, arm, arm, buffer, reads, results, source)


# name -> (call, arm factory or None, starting sequence byte)
FLOWS = {
    "connect": (connect, None, 0),
    "motors_on": (motors_on, None, 10),
    "motors_off": (motors_off, None, 10),
    "disconnect": (disconnect, None, 200),
    **{f"jog_{order}": (jog(order, degrees, 10), None, 250)
       for order, degrees in ((4, 1.5), (5, 1.5), (6, 2), (7, 2), (8, 2), (9, 2), (10, 6), (11, 6), (12, 6), (13, 6))},
    "jog_5_slow": (jog(5, 1, 3), None, 10),
    "jog_4_stop": (jog(4, 1.5, 10, stop_after=8), None, 10),
    "jog_7_stop": (jog(7, 2, 10, stop_after=8), None, 10),
    "jog_9_stop": (jog(9, 2, 10, stop_after=8), None, 10),
    "jog_12_stop": (jog(12, 6, 10, stop_after=8), None, 10),
    # One fault per joint through its own collision bit; a wrong mask would not fault.
    "jog_5_collision": (jog(5, 1.5, 10), lambda: Arm(fault_at=6, collision=0x01), 10),
    "jog_6_collision": (jog(6, 2, 10), lambda: Arm(fault_at=6, collision=0x02), 10),
    "jog_8_collision": (jog(8, 2, 10), lambda: Arm(fault_at=6, collision=0x04), 10),
    "jog_11_collision": (jog(11, 6, 10), lambda: Arm(fault_at=6, collision=0x10), 10),
    "jog_6_other_bit": (jog(6, 2, 10), lambda: Arm(fault_at=6, collision=0x01), 10),
    # Impact through the error word, the vendor's ImpactDetect (LEDGER.md PL98).
    "jog_4_impact": (jog(4, 1.5, 10), lambda: Arm(fault_at=6, error_axis=0, error=71), 10),
    "jog_9_impact": (jog(9, 2, 10), lambda: Arm(fault_at=6, error_axis=2, error=71), 10),
    "jog_13_impact_motor_2": (jog(13, 6, 10), lambda: Arm(fault_at=6, error_axis=4, error=71), 10),
    "jog_7_impact_late": (jog(7, 2, 10), lambda: Arm(fault_at=28, error_axis=1, error=71), 10),
    # An arm that does not follow: the settle loop gives up after 100 messages (result 2).
    "jog_5_no_settle": (jog(5, 1.5, 10), lambda: Arm(follow=False), 10),
    "jog_10_no_settle": (jog(10, 6, 10), lambda: Arm(follow=False), 10),
    # A start outside the sign-byte range: the jog is refused after its open message (CT17).
    "jog_6_refused": (jog(6, 2, 10), lambda: Arm(counts=[50, 70000, -800, 300, -300, 0]), 10),
    "gripper_open": (clamp(14), None, 10),
    "gripper_close": (clamp(15), None, 10),
    "gripper_stop": (clamp(14, stop_after=12), None, 10),
    "stream": (stream([(60, 1190, -790), (75, 1170, -770), (90, 1150, -750)]), None, 10),
    "stream_wrist": (stream([(50, 1200, -800, 310, -290), (50, 1200, -800, 330, -270)]), None, 10),
    "stream_stop": (stream([(60, 1190, -790), (75, 1170, -770)], ("stop",)), None, 10),
    "stream_empty": (stream([]), None, 10),
    "vendor_home_failed": (failed_vendor_home, None, 254),
}


def record(legacy, name):
    call, make_arm, b_1 = FLOWS[name]
    return run(legacy, call, make_arm() if make_arm else None, b_1)


def load_legacy():
    from scorbot import Scorbot
    robot = Scorbot()
    robot._legacy("conf").setup()
    return {name: robot._legacy(name) for name in ("libdef", "libcomm", "libsync")}


@unittest.skipUnless(find_spec("usb"), "PyUSB is needed to import the legacy modules")
class LegacyGoldenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.legacy = load_legacy()

    def tearDown(self):
        self.legacy["libdef"].reset_count_guard()
        self.legacy["libdef"].ultimo_hold = None

    def test_every_flow_has_a_fixture_and_every_fixture_a_flow(self):
        self.assertEqual(sorted(path.stem for path in FIXTURES.glob("*.json")), sorted(FLOWS))

    def test_every_flow_sends_its_golden_messages(self):
        for name in FLOWS:
            with self.subTest(name):
                want = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
                self.assertEqual(record(self.legacy, name), want)

    def test_the_goldens_cover_what_they_claim(self):
        # The fixtures are not empty recordings: each kind of ending shows up where it should.
        def golden(name):
            return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
        for name in FLOWS:
            with self.subTest(name):
                self.assertEqual(golden(name)["lengths"], [64])
        self.assertEqual(golden("jog_5")["results"], [])
        for name in ("jog_4_stop", "jog_12_stop", "gripper_stop", "stream_stop"):
            self.assertEqual(golden(name)["results"], [14], name)
        for name in ("jog_5_collision", "jog_6_collision", "jog_8_collision", "jog_11_collision",
                     "jog_4_impact", "jog_9_impact", "jog_13_impact_motor_2", "jog_7_impact_late",
                     "jog_6_refused", "vendor_home_failed"):
            self.assertEqual(golden(name)["results"], [1], name)
        self.assertEqual(golden("jog_6_other_bit")["results"], [])
        self.assertEqual(golden("jog_5_no_settle")["results"], [2])


if __name__ == "__main__":
    if "--record" in sys.argv:
        FIXTURES.mkdir(parents=True, exist_ok=True)
        legacy = load_legacy()
        names = [arg for arg in sys.argv[1:] if arg != "--record"] or list(FLOWS)
        for name in names:
            (FIXTURES / f"{name}.json").write_text(json.dumps(record(legacy, name), indent=1) + "\n", encoding="utf-8")
            print("recorded", name)
    else:
        unittest.main()
