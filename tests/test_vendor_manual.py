"""The velocity jog against the lab USBC.dll's own EnterManual / MoveManual / CloseManual (desk recording).

The golden fixture (``fixtures/dll_lab_manual.json``, made by ``scripts/make_manual_fixture.py``) holds what the lab
DLL wrote to a fake controller. Stack 2's side is the pure generator (``scorbot/vendor_manual.py``), the real
``Scorbot`` on the desk fake (``desk_wire.py``) and the simulator. The fake arm is our model: this compares messages
and setpoints and says nothing about how the arm moves.
"""

import json
import math
import struct
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from scorbot import ScorbotError, limits, nominal, source_model, vendor_home as vh, vendor_manual as vm
from scorbot.robot import MotionStopped
from scorbot.simulated import SimulatedScorbot

try:
    from tests.desk_wire import record_gripper
except ImportError:                                     # plain `unittest discover -s tests`
    from desk_wire import record_gripper

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "dll_lab_manual.json").read_text(encoding="utf-8"))
CLOSE = ["0000004f3f530000000000", "0000007320000000000000", "0000004220000000000000"]   # CloseManual's end
LEGACY_STOP = ["47000000", "4f3f5300", "73200000", "42200000"]
OPEN = (-10 ** 6, 10 ** 6)
AXIS_FILES = ((-145, 145, -12770), (145, -145, -10216), (160, -160, 10216))


def slot(axis, speed, half_pi=1.5708):
    """VJ01: the lab axis files and the stored float32 divide."""
    negative, positive, noenc90 = AXIS_FILES[axis]
    manual = negative if speed < 0 else positive
    divided = struct.unpack("f", struct.pack("f", manual * (abs(speed) / 100) / 1000))[0]
    # Both pi constants match every fixture message; 1.5708 is the constant read in the DLL.
    return math.trunc(divided * (noenc90 / half_pi))


def curve(t):
    """VJ02: integrate the three acceleration segments in section 4."""
    velocity = 10 / 3
    acceleration = velocity / 0.285
    jerk = acceleration / 0.015
    if t <= 0:
        return 0.0
    if t < 0.015:
        return jerk * t ** 3 / 6
    if t < 0.285:
        return acceleration * (t * t / 2 - 0.015 * t / 2 + 0.015 ** 2 / 6)
    if t < 0.3:
        return 0.5 - velocity * (0.3 - t) + jerk * (0.3 - t) ** 3 / 6
    return 0.5 + (t - 0.3) * velocity


def trace_positions(axis, speed, close_at, *, half_pi=1.5708):
    """VJ02/VJ05: request close after zero-based message close_at."""
    scale = slot(axis, speed, half_pi)
    requested = (close_at + 1) * 0.024
    end = requested + 0.3 if requested > 0.3 else 2 * requested
    positions = []
    for k in range(close_at + 16):
        t = (k + 1) * 0.024
        if k > close_at and t > end:
            return positions + [positions[-1]] * 3
        if k <= close_at:
            value = curve(t)
        elif requested > 0.3:
            value = curve(requested) + 0.5 - curve(0.3 - (t - requested))
        else:
            value = 2 * curve(requested) - curve(2 * requested - t)
        positions.append(math.trunc(scale * value))
    raise AssertionError("the close did not end")


def steps(positions):
    return [b - a for a, b in zip(positions, positions[1:])]


def recorded_steps(sent, axis, start=0):
    """The fixture's ``steps`` for what we sent: from the start, the last message left out."""
    return steps([start, *[out.setpoints[axis] for out in sent][:-1]])


def trace_commands(length, *, settled=True):
    return ([[0, "0000004700000000000000"]] if settled else []) + [
        [length - 3 + i, command] for i, command in enumerate(CLOSE)]


def reply(counts=(0,) * 6, **fields):
    return vh.Reply(counts=tuple(counts), switches=0, now=0.0, echo=1, **fields)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def drive(generator, *, follow=True, first=None, each=None, stop_at=None):
    """Run the generator against an arm that follows its setpoints (or holds still)."""
    sent, current = [], first or reply()
    try:
        out = next(generator)
        while True:
            sent.append(out)
            if len(sent) > 1600:
                raise AssertionError("jog did not close within 1600 messages")
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


def closed_jog(axis, speed, close_at, *, first=None, **kwargs):
    control = vm.JogControl(3.0, clock=Clock())
    return drive(vm.velocity_jog(first or reply(), axis, speed, control, OPEN, **kwargs),
                 each=lambda n: control.stop() if n == close_at + 1 else None)


class Planner(unittest.TestCase):
    def test_slot_matches_the_dll_formula(self):
        """VJ01: every offered axis, sign and whole percent, through its setpoints."""
        self.assertEqual(slot(0, 50), -589)
        mismatches = []
        for axis in range(3):
            for speed in (*range(-100, 0), *range(1, 101)):
                sent, _ = closed_jog(axis, speed, 24)
                actual = [out.setpoints[axis] for out in sent[:25]]
                expected = [math.trunc(slot(axis, speed) * curve((k + 1) * 0.024)) for k in range(25)]
                if actual != expected:
                    mismatches.append((axis, speed))
        self.assertEqual(len(mismatches), 0, f"axis/speed cases differ; first five: {mismatches[:5]}")

    def test_the_trace_table_speed_50(self):
        """VJ02: every row of section 4's k = 0..85 table."""
        expected = [-1, -5, -14, -27, -43, -64, -88, -117, -149, -186, -226, -271, -318, -365]
        expected += [math.trunc(-589 * (0.5 + ((k + 1) * 0.024 - 0.3) * (10 / 3)))
                     for k in range(14, 71)]
        expected += [-3097, -3139, -3177, -3212, -3242, -3269, -3292, -3310,
                     -3325, -3335, -3342, -3345, -3345, -3345, -3345]
        candidates = [c for c in range(60, 80) if trace_positions(0, 50, c) == expected]
        self.assertEqual(candidates, [70], "request after k=70; ramp down starts at k=71")
        first = reply((100, -200, 300, -400, 500, -600))
        sent, run = closed_jog(0, 50, candidates[0], first=first)
        self.assertEqual(commands(sent), trace_commands(86))
        self.assertEqual([out.setpoints for out in sent],
                         [(100 + position, *first.counts[1:]) for position in expected])
        self.assertEqual((run.reason, run.end), ("stop", 100 - 3345))

    def test_every_recorded_jog_matches_exactly(self):
        """VJ03/VJ08: exact recordings, including ignored speed/sign re-sends.

        The fixture's steps are message 0 minus the start, then each message minus the one before,
        without the last (always 0): ``recorded_steps`` builds the same from what we send.
        """
        for golden in FIXTURE["jogs"]:
            axis, speed = golden["axis"], golden["speed"]
            with self.subTest(axis=axis, speed=speed, label=golden["label"]):
                if golden["label"].startswith("Stop("):
                    before_stop = golden["commands"][1][0]
                    sent, _ = closed_jog(axis, speed, before_stop + 5)
                    self.assertEqual(commands(sent[:before_stop]), golden["commands"][:1])
                    self.assertEqual(recorded_steps(sent[:before_stop + 1], axis),
                                     golden["steps"][:before_stop])
                    continue
                candidates = []
                close_command = golden["commands"][1][0]
                close_range = range(max(0, close_command - 16), close_command)
                for c in close_range:
                    # The reducer takes differences from the held opening 47.
                    recorded = [0, *trace_positions(axis, speed, c)[:-1]]
                    if steps(recorded) == golden["steps"] and trace_commands(len(recorded)) == golden["commands"]:
                        candidates.append(c)
                self.assertEqual(len(candidates), 1, candidates)
                c = candidates[0]
                for half_pi in (1.5708, math.pi / 2):
                    self.assertEqual(steps([0, *trace_positions(axis, speed, c, half_pi=half_pi)[:-1]]),
                                     golden["steps"])
                matches = []
                for candidate in close_range:
                    sent, _ = closed_jog(axis, speed, candidate)
                    if (commands(sent) == golden["commands"]
                            and recorded_steps(sent, axis) == golden["steps"]):
                        matches.append(candidate)
                self.assertEqual(matches, candidates, "exactly one close must reproduce commands and every step")
                sent, run = closed_jog(axis, speed, c)
                self.assertEqual(commands(sent), golden["commands"])
                self.assertEqual(recorded_steps(sent, axis), golden["steps"])
                self.assertTrue(all(out.setpoints[k] == 0 for out in sent for k in range(6) if k != axis))
                self.assertFalse(golden["other_slots_move"])
                self.assertEqual(run.reason, "stop")

    def test_no_47_when_not_settled(self):
        """VJ04, unverified case: follows the trace, no recording."""
        sent, _ = closed_jog(0, 50, 70, settled=False)
        expected = trace_positions(0, 50, 70)
        self.assertEqual(sent[0].cmd, vh.NOP)
        self.assertEqual(commands(sent), trace_commands(len(expected), settled=False))
        self.assertEqual([out.setpoints[0] for out in sent], expected)

    def test_close_after_the_ramp(self):
        """VJ05: cruise ends with the mirror ramp and a held three-message close.

        c=12/30 unverified case: follows the trace, no recording.
        """
        for c in (12, 30, 70):
            with self.subTest(close_at=c):
                sent, run = closed_jog(0, -50, c)
                expected = trace_positions(0, -50, c)
                self.assertEqual(commands(sent), trace_commands(len(expected)))
                self.assertEqual([out.setpoints[0] for out in sent], expected)
                self.assertEqual(run.end, math.trunc(slot(0, -50) * (curve((c + 1) * 0.024) + 0.5)))
                self.assertEqual(run.reason, "stop")

    def test_close_during_the_ramp_is_reflected(self):
        """VJ05, unverified case: follows the trace, no recording."""
        for c in (0, 4, 10, 11):
            with self.subTest(close_at=c):
                sent, run = closed_jog(0, -50, c)
                expected = trace_positions(0, -50, c)
                self.assertEqual(commands(sent), trace_commands(len(expected)))
                self.assertEqual([out.setpoints[0] for out in sent], expected)
                self.assertEqual(run.end, math.trunc(2 * slot(0, -50) * curve((c + 1) * 0.024)))

    def test_keep_alive_runs_out_after_300_messages(self):
        """VJ06, unverified case: follows the trace, no recording.

        Off-by-one unverified: 300 messages (k=0..299) precede the ramp at k=300.
        """
        sent, run = drive(vm.velocity_jog(reply(), 0, -20, vm.JogControl(3.0, clock=Clock()), OPEN))
        expected = trace_positions(0, -20, 299)
        self.assertEqual(commands(sent), trace_commands(len(expected)))
        self.assertEqual([out.setpoints[0] for out in sent], expected)
        self.assertEqual(run.end, expected[-1])

    def test_renew_rearms_300_messages(self):
        """VJ06, unverified case: follows the trace, no recording.

        Off-by-one unverified: renew after k=199 gives k=200..499 before closing.
        """
        control = vm.JogControl(3.0, clock=Clock())
        sent, run = drive(vm.velocity_jog(reply(), 0, -20, control, OPEN),
                          each=lambda n: control.renew() if n == 200 else None)
        expected = trace_positions(0, -20, 499)
        self.assertEqual(commands(sent), trace_commands(len(expected)))
        self.assertEqual([out.setpoints[0] for out in sent], expected)
        self.assertEqual(run.end, expected[-1])

    def test_deadline_without_renew_closes(self):
        """VJ07: the wall clock closes even with messages left on the keep-alive."""
        clock = Clock()
        control = vm.JogControl(0.5, clock=clock)
        sent, run = drive(vm.velocity_jog(reply(), 0, -20, control, OPEN),
                          each=lambda n: setattr(clock, "now", n * 0.01))
        self.assertEqual(run.reason, "deadline")
        self.assertEqual([vh.encode_out(o, 0)[1:12].hex() for o in sent[-3:]], CLOSE)
        self.assertLess(len(sent), 70)           # 50 messages to the deadline, then the ramp-down

    def test_renew_keeps_it_going(self):
        """VJ07: renew also re-arms the wall-clock deadline."""
        clock = Clock()
        control = vm.JogControl(0.5, clock=clock)

        def tick(n):
            clock.now = n * 0.01
            if n < 200:
                control.renew()
        sent, run = drive(vm.velocity_jog(reply(), 0, -20, control, OPEN), each=tick)
        self.assertEqual(run.reason, "deadline")
        self.assertGreater(len(sent), 240)

    def test_30_s_cap(self):
        """VJ07: renew cannot extend the total wall-clock bound."""
        clock = Clock()
        control = vm.JogControl(3.0, clock=clock)

        def tick(n):
            clock.now = n * 0.1
            control.renew()
        _, run = drive(vm.velocity_jog(reply(), 0, -20, control, OPEN), each=tick)
        self.assertEqual(run.reason, "total")

    def test_window_includes_the_ramp_down(self):
        """VJ07: the remaining close fits, including a close during acceleration."""
        for speed, window in ((-20, (-50, 600)), (20, (-600, 50)), (-5, (0, 40))):
            with self.subTest(speed=speed, window=window):
                sent, run = drive(vm.velocity_jog(reply(), 0, speed, vm.JogControl(3.0), window))
                self.assertEqual(run.reason, "limit")
                positions = [o.setpoints[0] for o in sent]
                self.assertTrue(all(window[0] <= p <= window[1] for p in positions), (min(positions), max(positions)))
                self.assertEqual([vh.encode_out(o, 0)[1:12].hex() for o in sent[-3:]], CLOSE)

    def test_at_the_edge_sends_nothing(self):
        """VJ07: an outward jog at the edge queues nothing."""
        sent, run = drive(vm.velocity_jog(reply(), 0, -20, vm.JogControl(), (-500, 0)))
        self.assertEqual((sent, run.reason), ([], "limit"))

    def test_a_stop_request_holds_the_measured_counts_with_the_legacy_stop(self):
        """VJ09: stopMov holds every slot at the last reply, without a ramp."""
        sent, run = drive(vm.velocity_jog(reply(), 0, -20, vm.JogControl(3.0), OPEN), follow=False, stop_at=30,
                          first=reply((5, 0, 0, 0, 0, 0)))
        self.assertTrue(run.stopped)
        self.assertEqual([vh.encode_out(o, 0)[4:8].hex() for o in sent[-4:]], LEGACY_STOP)
        self.assertEqual({o.setpoints for o in sent[-4:]}, {(5, 0, 0, 0, 0, 0)}, "the last reply's counts")

    def test_an_emergency_stops_then_faults(self):
        """VJ09: an emergency input sends the stop before raising."""
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

    def test_an_impact_while_closing_or_stopping_still_closes_then_faults(self):
        """VJ09: an impact on the tail cannot cut its messages short."""
        for stop_at in (None, 30):
            with self.subTest(stop_at=stop_at):
                control = vm.JogControl(3.0, clock=Clock())
                generator = vm.velocity_jog(reply(), 0, -20, control, OPEN)
                sent = [next(generator)]
                with self.assertRaises(vh.HomeError) as caught:
                    while True:
                        if len(sent) == 40:
                            control.stop()
                        last = sent[-1]
                        hit = last.cmd != vh.NOP and last.cmd != vh.CLEAR or (stop_at and len(sent) > stop_at)
                        answer = reply(last.setpoints, impact=1 if hit else 0)
                        if stop_at and len(sent) == stop_at:
                            sent.append(generator.throw(vh.HomeError(vh.ERR_STOPPED, "a stop was requested")))
                        else:
                            sent.append(generator.send(answer))
                self.assertEqual(caught.exception.code, vh.ERR_IMPACT)
                self.assertEqual([vh.encode_out(o, 0)[4] for o in sent[-3:]], [0x4F, 0x73, 0x42])

    def test_an_arm_that_does_not_follow_is_a_fault(self):
        """VJ09: a lead beyond LEAD_LIMIT_COUNTS faults."""
        with self.assertRaises(vh.HomeError) as caught:
            drive(vm.velocity_jog(reply(), 0, -100, vm.JogControl(3.0), OPEN), follow=False)
        self.assertEqual(caught.exception.code, vm.ERR_LEAD)

    def test_speed_and_axis_are_checked(self):
        """VJ07/VJ09: reject unsupported axes, speeds and deadlines before sending."""
        for axis, speed in ((0, 0), (0, 101), (0, 2.5), (0, True), (3, 20)):
            with self.subTest(axis=axis, speed=speed), self.assertRaises(ValueError):
                next(vm.velocity_jog(reply(), axis, speed, vm.JogControl(), OPEN))
        for deadline in (0.0, 5.0, float("nan"), True):
            with self.subTest(deadline=deadline), self.assertRaises(ValueError):
                vm.JogControl(deadline)


def ready():
    robot = SimulatedScorbot().connect()
    robot.enable()
    robot.vendor_home(operator_at_stop=True, jaws_empty=True, wrist_clear=True)
    return robot


class Simulated(unittest.TestCase):
    def setUp(self):
        self.robot = ready()
        self.addCleanup(self.robot.disconnect)

    def test_stop_ramps_down_and_returns_the_state(self):
        """VJ05: stop collects the settled state after the close."""
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
        """VJ07: an expired handle cannot renew; collecting it leaves no fault."""
        jog = self.robot.start_velocity_jog("base", 20, deadline_s=0.2)
        time.sleep(0.8)
        with self.assertRaisesRegex(ScorbotError, "deadline"):
            jog.renew()
        after = jog.stop()
        self.assertEqual(jog.reason, "deadline")
        self.assertLess(after.signed_encoder_counts["base"], -50)
        self.assertIsNone(self.robot._fault)

    def test_request_stop_and_the_pendant_abort_end_it_without_a_fault(self):
        """VJ09: both stop sources leave the vendor-homed session usable."""
        for stop in (self.robot.request_stop, self.robot._pendant_abort):
            with self.subTest(stop=stop.__name__):
                jog = self.robot.start_velocity_jog("base", 20)
                time.sleep(0.2)
                stop()
                with self.assertRaises(MotionStopped):
                    jog.stop()
                self.assertEqual(jog.reason, "stopped")
                self.assertIsNone(self.robot._fault)
                packets = self.robot.sim.vendor_packets
                self.assertEqual([p[4:8].hex() for p in packets[-4:]], LEGACY_STOP)
        self.robot.jog_joint("base", 1.0)        # the session still moves

    def test_the_joint_limit_ends_it(self):
        """VJ10: the narrower source-model shoulder window still ends the jog."""
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

    def test_the_window_is_measured_from_where_the_worker_starts(self):
        """VJ10/VJ11: drift after the checks, from either side of zero, has no count offset."""
        real_settle = self.robot._settled_after_stop
        real_jog = vm.velocity_jog
        edge = source_model.home_window()["shoulder"][1]
        for start in (-400, 100):
            with self.subTest(start=start):
                with self.robot.sim._lock:
                    self.robot.sim.counts["shoulder"] = start
                captured = []

                def settle_then_drift(*args, **kwargs):
                    state = real_settle(*args, **kwargs)
                    with self.robot.sim._lock:
                        self.robot.sim.counts["shoulder"] += 300
                    return state

                def capture(first, axis, speed, control, window, **kwargs):
                    captured.append((first.counts[axis], window))
                    return real_jog(first, axis, speed, control, window, **kwargs)

                with mock.patch.object(vm, "velocity_jog", side_effect=capture):
                    with mock.patch.object(self.robot, "_settled_after_stop", settle_then_drift):
                        jog = self.robot.start_velocity_jog("shoulder", 20, deadline_s=3.0)
                    deadline = time.monotonic() + 10
                    while jog.reason is None and time.monotonic() < deadline:
                        time.sleep(0.01)
                    after = jog.stop()
                self.assertEqual(jog.reason, "limit")
                self.assertEqual(captured[0][0], start + 300)
                self.assertEqual(captured[0][1][1], edge - (start + 300) - 2)
                last = int.from_bytes(self.robot.sim.vendor_packets[-1][16:20], "little", signed=True)
                self.assertEqual(after.signed_encoder_counts["shoulder"], last)
                self.assertLessEqual(last, edge)

    def test_refused_before_vendor_home(self):
        """VJ10: a legacy home cannot establish the vendor-limit zero."""
        robot = SimulatedScorbot().connect()
        self.addCleanup(robot.disconnect)
        robot.enable()
        robot.home(start_position_confirmed=True)
        queued = list(robot.sim.commands)
        with mock.patch.object(robot._commands, "put", wraps=robot._commands.put) as put:
            with self.assertRaisesRegex(ScorbotError, "vendor Home"):
                robot.start_velocity_jog("base", -5)
        put.assert_not_called()
        self.assertEqual(robot.sim.commands, queued)
        self.assertFalse(getattr(robot.sim, "vendor_packets", []))

    def test_the_vendor_limit_ends_it(self):
        """VJ10: vendor limits read absolute counts, even with a shifted session home."""
        now = dict.fromkeys(limits.RECORDED_MOTORS, 0)
        low, high = 0, 25001
        while high - low > 1:
            middle = (low + high) // 2
            if nominal.vendor_limit_problem({**now, "base": middle}):
                high = middle
            else:
                low = middle
        edge = low
        self.assertIsNone(nominal.vendor_limit_problem({**now, "base": edge}))
        self.assertIsNotNone(nominal.vendor_limit_problem({**now, "base": edge + 1}))
        with self.robot.sim._lock:
            self.robot.sim.counts["base"] = edge - 80
        self.robot._home_counts["base"] = (edge - 80) & 0xFFFF
        jog = self.robot.start_velocity_jog("base", -20, deadline_s=3.0)
        deadline = time.monotonic() + 10
        while jog.reason is None and time.monotonic() < deadline:
            time.sleep(0.01)
        after = jog.stop()
        self.assertEqual(jog.reason, "limit")
        self.assertGreater(after.signed_encoder_counts["base"], edge - 80)
        self.assertLessEqual(after.signed_encoder_counts["base"], edge)
        self.assertIsNone(nominal.vendor_limit_problem(after.signed_encoder_counts))
        self.assertIsNone(self.robot._fault)

    def test_past_a_vendor_limit_can_only_move_back(self):
        """VJ10: a pose past a vendor limit may recover, never worsen it."""
        with self.robot.sim._lock:
            self.robot.sim.counts["base"] = 25010
        self.robot._home_counts["base"] = 25010
        queued = list(self.robot.sim.commands)
        with mock.patch.object(self.robot._commands, "put", wraps=self.robot._commands.put) as put:
            with self.assertRaisesRegex(ValueError, "vendor limit"):
                self.robot.start_velocity_jog("base", -5)
        put.assert_not_called()
        self.assertEqual(self.robot.sim.commands, queued)
        jog = self.robot.start_velocity_jog("base", 5)
        time.sleep(0.1)
        after = jog.stop()
        self.assertLess(after.signed_encoder_counts["base"], 25010)
        self.assertIsNone(self.robot._fault)

    def test_refusals(self):
        """VJ09: bad input and concurrent streams queue no second motion."""
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
        """VJ09: the motion lock refuses a jog before it queues anything."""
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
        """VJ10: connection and motors alone do not permit a jog."""
        robot = SimulatedScorbot().connect()
        self.addCleanup(robot.disconnect)
        with self.assertRaisesRegex(ScorbotError, "Enable and home"):
            robot.start_velocity_jog("base", -5)
        robot.enable()
        with self.assertRaisesRegex(ScorbotError, "Enable and home"):
            robot.start_velocity_jog("base", -5)
        self.assertFalse(getattr(robot.sim, "vendor_packets", []))

    def test_a_stop_asked_before_refuses_once(self):
        """VJ09: a pending stop refuses one jog, then clears."""
        self.robot.request_stop()
        with self.assertRaises(MotionStopped):
            self.robot.start_velocity_jog("base", -5)
        self.robot.start_velocity_jog("base", -5).stop()

    def test_disconnect_ends_an_open_jog(self):
        """VJ09: disconnect collects the open jog."""
        robot = ready()
        robot.start_velocity_jog("base", -5)
        time.sleep(0.1)
        robot.disconnect()
        self.assertIsNone(robot._stream)


class DeskWire(unittest.TestCase):
    def test_the_real_scorbot_sends_the_dlls_commands(self):
        """VJ12: real workers on fake endpoints send the four-byte 42 tail."""
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
                         CLOSE)
        self.assertEqual(ours[-1][4:8], bytes.fromhex("42 20 00 00"))
        self.assertEqual({p[16:32] for p in ours}, {bytes(16)}, "only the base slot moves")
        self.assertGreater(int.from_bytes(ours[-1][12:16], "little", signed=True), 0)


if __name__ == "__main__":
    unittest.main()
