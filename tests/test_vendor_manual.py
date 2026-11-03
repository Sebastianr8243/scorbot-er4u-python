"""The velocity jog against the lab USBC.dll's own EnterManual / MoveManual / CloseManual (desk recording).

The golden fixture (``fixtures/dll_lab_manual.json``, made by ``scripts/make_manual_fixture.py``) holds what the lab
DLL wrote to a fake controller. Stack 2's side is the pure generator (``scorbot/vendor_manual.py``), the real
``Scorbot`` on the desk fake (``desk_wire.py``) and the simulator. The fake arm is our model: this compares messages
and setpoints and says nothing about how the arm moves.
"""

import json
import math
import threading
import time
import unittest
from pathlib import Path

from scorbot import ScorbotError, source_model, vendor_home as vh, vendor_manual as vm
from scorbot.robot import MotionStopped
from scorbot.simulated import SimulatedScorbot

try:
    from tests.desk_wire import record_gripper
except ImportError:                                     # plain `unittest discover -s tests`
    from desk_wire import record_gripper

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "dll_lab_manual.json").read_text(encoding="utf-8"))
CLOSED = [j for j in FIXTURE["jogs"] if j["commands"][-1][1] == "0000004220000000000000"]
CLOSE = ["0000004f3f530000000000", "0000007320000000000000", "0000004220000000000000"]   # CloseManual's end
LEGACY_STOP = ["47", "4f3f53", "732000", "422000"]      # libdef.stopMov: mov_comm(2) to mov_comm(5)
OPEN = (-10 ** 6, 10 ** 6)
SHAPE_FLOOR_COUNTS = 5          # the fitted ramps against the DLL at speed 5, where one message is 4.7 counts


def reply(counts=(0,) * 6, **fields):
    return vh.Reply(counts=tuple(counts), switches=0, now=0.0, echo=1, **fields)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class StopAfter(vm.JogControl):
    """``stop()`` once ``n`` setpoints have gone out, the way ``VelocityJog.stop`` would."""

    def __init__(self, n, **kwargs):
        super().__init__(**kwargs)
        self.n, self.asked = n, 0

    def why_end(self, started):
        self.asked += 1
        if self.asked > self.n:
            self.stop()
        return super().why_end(started)


def drive(generator, *, follow=True, first=None, each=None, stop_at=None):
    """Run the generator against an arm that follows its setpoints (or holds still)."""
    sent, current = [], first or reply()
    try:
        out = next(generator)
        while True:
            sent.append(out)
            if each is not None:
                each(len(sent))
            if follow:
                current = reply(out.setpoints)
            if stop_at is not None and len(sent) == stop_at:
                out = generator.throw(vh.HomeError(vh.ERR_STOPPED, "a stop was requested"))
            else:
                out = generator.send(current)
    except StopIteration as done:
        return sent, done.value


def commands(sent):
    return [[i, vh.encode_out(o, 0)[1:12].hex()] for i, o in enumerate(sent) if o.cmd != vh.NOP]


class Planner(unittest.TestCase):
    def test_every_closed_jog_matches_the_dlls_messages(self):
        for golden in CLOSED:
            close_at = golden["commands"][1][0]                     # the 4F 3F 53
            n = close_at - math.ceil(vm.RAMP_DOWN_TICKS)            # setpoints sent before stop()
            with self.subTest(axis=golden["axis"], speed=golden["speed"], label=golden["label"]):
                sent, run = drive(vm.velocity_jog(reply(), golden["axis"], golden["speed"], StopAfter(n), OPEN))
                self.assertEqual(commands(sent), golden["commands"])
                ours = [o.setpoints[golden["axis"]] for o in sent]
                theirs = [0]
                for step in golden["steps"]:
                    theirs.append(theirs[-1] + step)
                self.assertEqual(len(ours), len(theirs))
                cruise = abs(golden["speed"] * vm.COUNTS_PER_TICK[golden["axis"]])
                worst = max(abs(a - b) for a, b in zip(ours, theirs))
                self.assertLessEqual(worst, max(cruise, SHAPE_FLOOR_COUNTS), "within one message's travel")
                self.assertTrue(all(v == 0 for o in sent for k, v in enumerate(o.setpoints[:5]) if k != golden["axis"]),
                                "no other slot moves")
                self.assertFalse(golden["other_slots_move"])
                self.assertEqual(run.reason, "stop")

    def test_the_dll_ignores_a_new_speed_so_a_jog_has_one(self):
        # The recording re-sent -20, then -40 and +20 during one jog: its steady step never changed.
        renewed = next(j for j in FIXTURE["jogs"] if j["label"].startswith("renewed"))
        steady = renewed["steps"][20:-20]
        self.assertLessEqual(max(steady) - min(steady), 1)
        self.assertFalse(hasattr(vm, "update"))
        sent, _ = drive(vm.velocity_jog(reply(), 0, -20, StopAfter(100), OPEN))
        steps = [b.setpoints[0] - a.setpoints[0] for a, b in zip(sent, sent[1:])][20:90]
        self.assertEqual(set(steps), set(steady))

    def test_the_sign_follows_the_dll(self):
        for golden in CLOSED:
            with self.subTest(axis=golden["axis"], speed=golden["speed"]):
                sent, _ = drive(vm.velocity_jog(reply(), golden["axis"], golden["speed"], StopAfter(30), OPEN))
                moved = sent[-1].setpoints[golden["axis"]]
                self.assertEqual(moved > 0, sum(golden["steps"]) > 0)

    def test_no_renew_ends_with_the_vendor_close(self):
        clock = Clock()
        control = vm.JogControl(0.5, clock=clock)
        sent, run = drive(vm.velocity_jog(reply(), 0, -20, control, OPEN),
                          each=lambda n: setattr(clock, "now", n * 0.01))
        self.assertEqual(run.reason, "deadline")
        self.assertEqual([vh.encode_out(o, 0)[1:12].hex() for o in sent[-3:]], CLOSE)
        self.assertLess(len(sent), 70)           # 50 messages to the deadline, then the ramp-down

    def test_renew_keeps_it_going(self):
        clock = Clock()
        control = vm.JogControl(0.5, clock=clock)

        def tick(n):
            clock.now = n * 0.01
            if n < 200:
                control.renew()
        sent, run = drive(vm.velocity_jog(reply(), 0, -20, control, OPEN), each=tick)
        self.assertEqual(run.reason, "deadline")
        self.assertGreater(len(sent), 240)

    def test_the_whole_jog_is_bounded(self):
        clock = Clock()
        control = vm.JogControl(3.0, clock=clock)

        def tick(n):
            clock.now = n * 0.1
            control.renew()
        _, run = drive(vm.velocity_jog(reply(), 0, -20, control, OPEN), each=tick)
        self.assertEqual(run.reason, "total")

    def test_the_window_holds_ramp_down_included(self):
        for speed, window in ((-20, (-50, 600)), (20, (-600, 50)), (-5, (0, 40))):
            with self.subTest(speed=speed, window=window):
                sent, run = drive(vm.velocity_jog(reply(), 0, speed, vm.JogControl(3.0), window))
                self.assertEqual(run.reason, "limit")
                positions = [o.setpoints[0] for o in sent]
                self.assertTrue(all(window[0] <= p <= window[1] for p in positions), (min(positions), max(positions)))
                self.assertEqual([vh.encode_out(o, 0)[1:12].hex() for o in sent[-3:]], CLOSE)

    def test_at_the_edge_sends_nothing(self):
        sent, run = drive(vm.velocity_jog(reply(), 0, -20, vm.JogControl(), (-500, 0)))
        self.assertEqual((sent, run.reason), ([], "limit"))

    def test_a_stop_request_holds_the_measured_counts_with_the_legacy_stop(self):
        sent, run = drive(vm.velocity_jog(reply(), 0, -20, vm.JogControl(3.0), OPEN), follow=False, stop_at=30,
                          first=reply((5, 0, 0, 0, 0, 0)))
        self.assertTrue(run.stopped)
        self.assertEqual([vh.encode_out(o, 0)[4:7].hex().rstrip("0") or "0" for o in sent[-4:]],
                         [c.rstrip("0") for c in LEGACY_STOP])
        self.assertEqual({o.setpoints for o in sent[-4:]}, {(5, 0, 0, 0, 0, 0)}, "the last reply's counts")

    def test_an_emergency_stops_then_faults(self):
        alarm = reply(emergency=True)
        generator = vm.velocity_jog(reply(), 0, -20, vm.JogControl(3.0), OPEN)
        sent = [next(generator)]
        for _ in range(10):
            sent.append(generator.send(reply(sent[-1].setpoints)))
        with self.assertRaises(vh.HomeError) as caught:
            while True:
                sent.append(generator.send(alarm))
        self.assertEqual(caught.exception.code, vh.ERR_EMERGENCY)
        self.assertEqual([vh.encode_out(o, 0)[4] for o in sent[-4:]], [0x47, 0x4F, 0x73, 0x42])

    def test_an_arm_that_does_not_follow_is_a_fault(self):
        with self.assertRaises(vh.HomeError) as caught:
            drive(vm.velocity_jog(reply(), 0, -100, vm.JogControl(3.0), OPEN), follow=False)
        self.assertEqual(caught.exception.code, vm.ERR_LEAD)

    def test_speed_and_axis_are_checked(self):
        for axis, speed in ((0, 0), (0, 101), (0, 2.5), (0, True), (3, 20)):
            with self.subTest(axis=axis, speed=speed), self.assertRaises(ValueError):
                next(vm.velocity_jog(reply(), axis, speed, vm.JogControl(), OPEN))
        for deadline in (0.0, 5.0, float("nan"), True):
            with self.subTest(deadline=deadline), self.assertRaises(ValueError):
                vm.JogControl(deadline)


def ready():
    robot = SimulatedScorbot().connect()
    robot.enable()
    robot.home(start_position_confirmed=True)
    return robot


class Simulated(unittest.TestCase):
    def setUp(self):
        self.robot = ready()
        self.addCleanup(self.robot.disconnect)

    def test_stop_ramps_down_and_returns_the_state(self):
        jog = self.robot.start_velocity_jog("base", -20)
        time.sleep(0.3)
        jog.renew()
        after = jog.stop()
        self.assertEqual(jog.reason, "stop")
        self.assertGreater(after.signed_encoder_counts["base"], 100)
        self.assertIsNone(self.robot._fault)
        packets = self.robot.sim.vendor_packets
        self.assertEqual(packets[0][4], 0x47)
        self.assertEqual([p[1:12].hex() for p in packets[-3:]], CLOSE)

    def test_the_deadline_ends_it_and_renew_then_says_so(self):
        jog = self.robot.start_velocity_jog("base", 20, deadline_s=0.2)
        time.sleep(0.8)
        with self.assertRaisesRegex(ScorbotError, "deadline"):
            jog.renew()
        after = jog.stop()
        self.assertEqual(jog.reason, "deadline")
        self.assertLess(after.signed_encoder_counts["base"], -50)
        self.assertIsNone(self.robot._fault)

    def test_request_stop_and_the_pendant_abort_end_it_without_a_fault(self):
        for stop in (self.robot.request_stop, self.robot._pendant_abort):
            with self.subTest(stop=stop.__name__):
                jog = self.robot.start_velocity_jog("elbow", 20)
                time.sleep(0.2)
                stop()
                with self.assertRaises(MotionStopped):
                    jog.stop()
                self.assertEqual(jog.reason, "stopped")
                self.assertIsNone(self.robot._fault)
                packets = self.robot.sim.vendor_packets
                self.assertEqual([p[4:7].hex().rstrip("0") for p in packets[-4:]],
                                 [c.rstrip("0") for c in LEGACY_STOP])
        self.robot.jog_joint("base", 1.0)        # the session still moves

    def test_the_joint_limit_ends_it(self):
        # Under 4 degrees up to the shoulder's limit (source model): each jog ends short of it, ramp-down included.
        for _ in range(3):
            jog = self.robot.start_velocity_jog("shoulder", 20, deadline_s=3.0)
            deadline = time.monotonic() + 10
            while jog.reason is None and time.monotonic() < deadline:
                time.sleep(0.05)
            after = jog.stop()
            self.assertEqual(jog.reason, "limit")
            self.assertGreater(after.signed_encoder_counts["shoulder"], 0)
            self.assertEqual(source_model.pose_limit_excess({"shoulder": after.signed_encoder_counts["shoulder"]}), {})
        self.robot.start_velocity_jog("shoulder", -20).stop()       # away from it is still allowed

    def test_refusals(self):
        with self.assertRaisesRegex(ValueError, "one of"):
            self.robot.start_velocity_jog("wrist_pitch", 10)
        with self.assertRaisesRegex(ValueError, "Speed"):
            self.robot.start_velocity_jog("base", 21)
        jog = self.robot.start_velocity_jog("base", -5)
        try:
            with self.assertRaisesRegex(ScorbotError, "active"):
                self.robot.start_velocity_jog("elbow", 5)
            with self.assertRaisesRegex(ScorbotError, "active"):
                self.robot.jog_joint("base", 1.0)
            with self.assertRaisesRegex(ScorbotError, "active"):
                self.robot.start_stream()
        finally:
            jog.stop()

    def test_refused_during_another_motion(self):
        held, release = threading.Event(), threading.Event()

        def other():
            with self.robot._motion_lock:
                held.set()
                release.wait(5)
        thread = threading.Thread(target=other)
        thread.start()
        held.wait(5)
        try:
            with self.assertRaisesRegex(ScorbotError, "Another motion"):
                self.robot.start_velocity_jog("base", -5)
        finally:
            release.set()
            thread.join()

    def test_needs_enabled_and_homed(self):
        robot = SimulatedScorbot().connect()
        self.addCleanup(robot.disconnect)
        with self.assertRaisesRegex(ScorbotError, "Enable and home"):
            robot.start_velocity_jog("base", -5)
        robot.enable()
        with self.assertRaisesRegex(ScorbotError, "Enable and home"):
            robot.start_velocity_jog("base", -5)
        self.assertFalse(getattr(robot.sim, "vendor_packets", []))

    def test_a_stop_asked_before_refuses_once(self):
        self.robot.request_stop()
        with self.assertRaises(MotionStopped):
            self.robot.start_velocity_jog("base", -5)
        self.robot.start_velocity_jog("base", -5).stop()

    def test_disconnect_ends_an_open_jog(self):
        robot = ready()
        robot.start_velocity_jog("base", -5)
        time.sleep(0.1)
        robot.disconnect()
        self.assertIsNone(robot._stream)


class DeskWire(unittest.TestCase):
    def test_the_real_scorbot_sends_the_dlls_commands(self):
        def jog(robot):
            handle = robot.start_velocity_jog("base", -20)
            deadline = time.monotonic() + 30
            while handle._source.messages < 30 and handle.reason is None and time.monotonic() < deadline:
                time.sleep(0.01)
            return handle.stop(), handle._source.messages
        packets, outcome = record_gripper([("jog", 0, jog)])["jog"]
        self.assertNotIsInstance(outcome, Exception, outcome)
        self.assertGreaterEqual(outcome[1], 30)
        start = next(i for i, p in enumerate(packets) if p[4] == 0x47)
        end = next(i for i in range(start, len(packets)) if packets[i][4] == 0x42)
        ours = packets[start:end + 1]
        self.assertEqual([p[1:12].hex() for p in ours if p[4] != 0x0D][1:],
                         [c[1] for c in CLOSED[0]["commands"]][1:])
        self.assertEqual({p[16:32] for p in ours}, {bytes(16)}, "only the base slot moves")
        self.assertGreater(int.from_bytes(ours[-1][12:16], "little", signed=True), 0)


if __name__ == "__main__":
    unittest.main()
