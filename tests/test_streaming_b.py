"""Streaming part B (issue #171): ST03, ST07, ST08, ST09, ST10 and ST16 of docs/requirements/streaming.md.

Written before the code. The simulator and fake endpoints only: nothing here opens USB or moves an arm.
The ST16 tests run the real ``Scorbot`` workers against a fake wire (``desk_wire``) whose arm follows each
setpoint, plus a drift the test switches on after the stream: an idle message that held its setpoint keeps it,
one that follows the encoders chases the drift.
"""

from importlib.util import find_spec
import math
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from scorbot import MotionStopped, Scorbot, ScorbotError, nominal
from scorbot.calibration import signed_count_delta

try:
    from tests.sim_support import SimulatedRobotCase
    from tests.desk_wire import FakeDevice, FakeWire, SLEEP_SCALE, fake_usb_modules, stop_workers
except ImportError:      # discovered with -s tests: the folder itself is on sys.path
    from sim_support import SimulatedRobotCase
    from desk_wire import FakeDevice, FakeWire, SLEEP_SCALE, fake_usb_modules, stop_workers

HAS_RUCKIG = find_spec("ruckig") is not None
PERIOD = 0.024
STREAM = ("base", "shoulder", "elbow")
IDLE, OPEN, MOTORS_CMD = 0x0D, 0x47, 0x42


def core(**kwargs):
    from scorbot.streaming import StreamCore
    wide = {m: 20000.0 for m in STREAM}
    settings = {"travel_cap": wide, "lead_limit": {m: 1000.0 for m in STREAM}, "period_s": PERIOD}
    settings.update(kwargs)
    return StreamCore({m: 0 for m in STREAM}, **settings)


def drive(stream_core, steps, *, target, dt=PERIOD, keep_sending=True, measured=None):
    """Step a core against a perfect follower; returns the Step records."""
    counts = dict(measured or {m: 0 for m in STREAM})
    records = []
    for index in range(steps):
        now = index * dt
        if keep_sending:
            stream_core.set_target(target, now)
        step = stream_core.step(counts, now)
        records.append(step)
        if step.action == "send":
            counts = dict(step.commanded)
        if step.action in ("stop", "end"):
            break
    return records


# ---- ST03, ST07, ST08, ST10 through Scorbot.start_stream on the simulator ----

@unittest.skipUnless(HAS_RUCKIG, "ruckig not installed (pip install .[planning])")
class StreamSettingsTests(SimulatedRobotCase):
    CONTROLLER_DEFAULTS = {"step_delay_s": 0.001}

    def refused(self, robot, **settings):
        with self.assertRaises(ValueError):
            robot.start_stream(**settings)
        self.assertIsNone(robot._stream)
        self.assertTrue(robot._commands.empty(), "a refused start queues nothing")

    def accepted(self, robot, **settings):
        stream = robot.start_stream(**settings)
        try:
            return stream.core
        finally:
            stream.close()

    def test_st03_the_default_bounds_are_the_vendor_window_exactly(self):
        robot = self.robot()
        home = {m: signed_count_delta(robot._home_counts[m], 0) for m in robot._home_counts}
        for wrist in (False, True):
            with self.subTest(wrist=wrist):
                stream_core = self.accepted(robot, wrist=wrist)
                for motor in stream_core.motors:
                    window = nominal.VENDOR_ENCODER_SOFT_LIMITS[motor]
                    self.assertEqual(stream_core.bounds[motor],
                                     (window.minimum - home[motor], window.maximum - home[motor]), motor)

    def test_st07_one_setpoint_per_period_0_024_by_default_0_01_to_0_05_accepted(self):
        robot = self.robot()
        self.assertEqual(self.accepted(robot).period_s, 0.024)
        for period in (0.01, 0.05):
            self.assertEqual(self.accepted(robot, period_s=period).period_s, period)
        for period in (0.0099, 0.0501, 0, -0.024, math.nan):
            with self.subTest(period=period):
                self.refused(robot, period_s=period)

    def test_st08_hold_timeout_0_5_by_default_0_05_to_2_accepted(self):
        robot = self.robot()
        self.assertEqual(self.accepted(robot).hold_timeout_s, 0.5)
        for timeout in (0.05, 2.0):
            self.assertEqual(self.accepted(robot, hold_timeout_s=timeout).hold_timeout_s, timeout)
        for timeout in (0.049, 2.01, math.inf):
            with self.subTest(timeout=timeout):
                self.refused(robot, hold_timeout_s=timeout)

    def test_st10_lead_limit_2_degrees_by_default_at_most_max_jog_deg(self):
        from scorbot import limits
        robot = self.robot()
        per_degree = {m: abs(robot.preview_jog(m, 1.0)["motor_count_deltas"][m]) for m in STREAM}
        self.assertEqual(self.accepted(robot).lead_limit, {m: 2.0 * per_degree[m] for m in STREAM})
        self.assertEqual(self.accepted(robot, lead_limit_deg=limits.MAX_JOG_DEG).lead_limit,
                         {m: limits.MAX_JOG_DEG * per_degree[m] for m in STREAM})
        for lead in (limits.MAX_JOG_DEG + 0.01, 0, -1.0):
            with self.subTest(lead=lead):
                self.refused(robot, lead_limit_deg=lead)


# ---- ST07, ST08, ST09, ST10 on the core ----

@unittest.skipUnless(HAS_RUCKIG, "ruckig not installed (pip install .[planning])")
class StreamCoreBTests(unittest.TestCase):
    def test_st07_a_slow_loop_is_not_caught_up(self):
        """Steps five periods apart plan exactly what steps one period apart plan: slower, never faster."""
        target = {"base": 3000, "shoulder": -2000, "elbow": 1000}
        paced = [r.commanded for r in drive(core(), 120, target=target)]
        slow = [r.commanded for r in drive(core(), 120, target=target, dt=5 * PERIOD)]
        self.assertEqual(slow, paced)

    def test_st08_holding_never_drifts_and_a_new_target_resumes_tracking(self):
        stream_core = core()
        counts = {m: 0 for m in STREAM}
        stream_core.set_target({"base": 400}, 0.0)
        held = []
        for index in range(200):                     # targets stop at once: 4.8 s, ten hold timeouts
            step = stream_core.step(counts, index * PERIOD)
            counts = dict(step.commanded)
            if index * PERIOD > 1.5:
                held.append(step.commanded)
        self.assertEqual(stream_core.state, "holding")
        self.assertEqual(len({tuple(sorted(c.items())) for c in held}), 1, "the held setpoint never moves")
        start = 200 * PERIOD
        stream_core.set_target({"base": -300}, start)
        self.assertEqual(stream_core.state, "tracking")
        for index in range(300):
            stream_core.set_target({"base": -300}, start + index * PERIOD)
            step = stream_core.step(counts, start + index * PERIOD)
            counts = dict(step.commanded)
        self.assertEqual(counts["base"], -300)

    def test_st09_no_setpoint_steps_further_than_max_velocity_times_period(self):
        """The bound is strict: max_velocity x period_s counts, with no count of rounding on top."""
        from scorbot.streaming import prior_limits
        self.assertEqual(prior_limits().max_velocity * PERIOD, 39.0)      # the spec's 39 counts at 24 ms
        self.assertEqual(prior_limits(1.0).max_velocity * PERIOD, 156.0)  # and 156 at full speed
        target = {"base": 15000, "shoulder": -15000, "elbow": 9000}
        for fraction in (0.25, 0.3, 0.5, 0.77, 1.0):
            with self.subTest(speed_fraction=fraction):
                limit = prior_limits(fraction).max_velocity * PERIOD
                previous = {m: 0 for m in STREAM}
                worst = 0
                for record in drive(core(limits=prior_limits(fraction)), 400, target=target):
                    for m in STREAM:
                        worst = max(worst, abs(record.commanded[m] - previous[m]))
                    previous = record.commanded
                self.assertLessEqual(worst, limit + 1e-9)

    def test_st10_a_setpoint_is_never_sent_past_the_lead_limit_and_the_stream_then_faults(self):
        lead = {m: 100.0 for m in STREAM}
        stream_core = core(lead_limit=lead)
        stuck = {m: 0 for m in STREAM}                   # the arm never moves
        sent = []
        for index in range(200):
            stream_core.set_target({"base": 2000}, index * PERIOD)
            step = stream_core.step(stuck, index * PERIOD)
            if step.action == "send":
                sent.append(step.commanded)
            else:
                break
        self.assertTrue(sent)
        self.assertLessEqual(max(abs(c["base"]) for c in sent), 100)
        self.assertEqual((step.action, stream_core.state), ("stop", "faulted"))
        self.assertIn("not following", stream_core.fault)


# ---- ST16: the idle messages after the stream, on the real workers ----

class FollowingWire(FakeWire):
    """The fake arm goes where each setpoint message says, plus ``drift`` counts per motor once switched on."""

    def __init__(self):
        super().__init__()
        self.counts = [0] * 5
        self.drift = [0] * 5
        self.emergency = False

    def _reply(self):
        packet = super()._reply()
        last = self._last
        if last[4] in (IDLE, OPEN, 0x4F, 0x73, MOTORS_CMD):
            for joint in range(5):
                setpoint = int.from_bytes(last[12 + 4 * joint:16 + 4 * joint], "little", signed=True)
                self.counts[joint] = setpoint + self.drift[joint]
        for joint in range(5):
            packet[19 + 5 * joint:22 + 5 * joint] = (0x7FFFFF + self.counts[joint]).to_bytes(3, "little")
        if self.emergency:
            packet[2] |= 1
        return packet


def slots(packet):
    return [int.from_bytes(packet[12 + 4 * j:16 + 4 * j], "little", signed=True) for j in range(5)]


def wait_for(predicate, seconds=10.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


@unittest.skipUnless(HAS_RUCKIG, "ruckig not installed (pip install .[planning])")
class IdleHoldAfterStreamTests(unittest.TestCase):
    """ST16 on the real Scorbot workers and fake endpoints: after close or stop the idle messages hold; after a
    fault they follow the encoders."""

    def session(self, body):
        wire, real_sleep = FollowingWire(), time.sleep
        with tempfile.TemporaryDirectory() as folder:
            robot = Scorbot(log_path=Path(folder) / "run.controller.jsonl", command_timeout=120)
            for name in ("conf", "libdef", "libcomm", "libsync"):
                robot._legacy(name)
            # Import ruckig before sys.modules is patched: patch.dict drops modules first imported inside it, and
            # importing ruckig's native extension a second time aborts the process.
            import ruckig  # noqa: F401
            with mock.patch.dict(sys.modules, fake_usb_modules(FakeDevice(), wire)), \
                    mock.patch("time.sleep", lambda seconds: real_sleep(seconds / SLEEP_SCALE)):
                try:
                    robot.connect()
                    robot.enable()
                    # The fake cannot run a Home: the session is marked homed at counts 0, as desk_wire does.
                    robot._homed, robot._gripper_homed = True, True
                    robot._home_counts = robot.get_state().encoder_counts.copy()
                    return body(robot, wire)
                finally:
                    try:
                        robot.disconnect()
                    except Exception:
                        stop_workers(robot)

    @staticmethod
    def stream_to(robot, stream, base):
        def there():
            stream.set_target({"base": base})
            return robot.get_state().signed_encoder_counts["base"] == base
        return wait_for(there)

    @staticmethod
    def idle_after(wire, index, count=15):
        """At least ``count`` idle setpoint messages sent after OUT packet ``index``."""
        wait_for(lambda: sum(p[4] == IDLE for p in wire.out_packets[index:]) >= count)
        return [p for p in wire.out_packets[index:] if p[4] == IDLE]

    def held_after_end(self, end_stream):
        def body(robot, wire):
            stream = robot.start_stream()
            self.assertTrue(self.stream_to(robot, stream, 40))
            end_stream(stream)
            tail = max(i for i, p in enumerate(wire.out_packets) if p[4] == MOTORS_CMD)
            hold = slots(wire.out_packets[tail])
            wire.drift = [15, -15, 10, 0, 0]          # the arm creeps after the end; the hold must not chase it
            idle = self.idle_after(wire, tail + 1)
            return hold, [slots(p) for p in idle], robot._idle_setpoint(), robot._fault
        return self.session(body)

    def test_after_close_the_idle_messages_hold_the_last_setpoint(self):
        hold, idle, setpoint, fault = self.held_after_end(lambda stream: stream.close())
        self.assertIsNone(fault)
        self.assertEqual(hold[0], 40)
        self.assertGreaterEqual(len(idle), 15)
        self.assertEqual({tuple(s) for s in idle}, {tuple(hold)}, "every idle message holds the close setpoint")
        self.assertEqual(setpoint[:20], b"".join(int(v).to_bytes(4, "little", signed=True) for v in hold))

    def test_after_a_stop_the_idle_messages_hold_the_stop_counts(self):
        def stop(stream):
            try:
                stream.stop()
            except MotionStopped:
                pass
        hold, idle, setpoint, fault = self.held_after_end(stop)
        self.assertIsNone(fault)
        self.assertGreaterEqual(len(idle), 15)
        self.assertEqual({tuple(s) for s in idle}, {tuple(hold)}, "every idle message holds the stop counts")
        self.assertIsNotNone(setpoint)

    def test_after_a_fault_no_hold_is_left(self):
        def body(robot, wire):
            stream = robot.start_stream()
            self.assertTrue(self.stream_to(robot, stream, 40))
            wire.emergency = True                      # the stream faults on the emergency bit (ST13)
            self.assertTrue(wait_for(lambda: robot._fault is not None))
            with self.assertRaises(ScorbotError):
                stream.close()
            wire.emergency = False
            index = len(wire.out_packets)
            wire.drift = [15, 0, 0, 0, 0]
            idle = [slots(p)[0] for p in self.idle_after(wire, index)]
            return robot._idle_setpoint(), idle
        setpoint, idle = self.session(body)
        self.assertIsNone(setpoint, "a faulted session holds nothing")
        self.assertGreater(len(set(idle)), 1, "the idle messages follow the drifting encoders")


if __name__ == "__main__":
    unittest.main()
