"""The gripper after a vendor Home against the lab USBC.dll's own post-Home gripper calls (LEDGER PL46, PL48, PL51).

The golden fixture (``fixtures/dll_lab_gripper.json``, made by ``scripts/make_gripper_fixture.py``) holds what the
lab DLL wrote to a fake controller for ``OpenGripper`` and ``CloseGripper`` after a finished ``Home('A')``. Stack 2's
side is the pure generator, the real ``Scorbot`` on the desk fake (``desk_wire.py``) and the simulator. This
compares messages and setpoints; the fake arm is our model, so it says nothing about whether the jaws arrive.
Before a vendor Home every gripper call is refused and sends nothing.
"""

import json
import tempfile
import threading
import unittest
from pathlib import Path

from scorbot import ScorbotError, vendor_gripper as vg, vendor_home as vh
from scorbot.robot import GripperMove, MotionStopped
from scorbot.simulated import SimulatedScorbot
from scorbot.vendor_home_source import VendorHomeSource
from scripts import make_gripper_fixture as reduce
from tests.vendor_home_plant import Plant, run

try:
    from tests.desk_wire import record_gripper
except ImportError:                                     # plain `unittest discover -s tests`
    from desk_wire import record_gripper

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "dll_lab_gripper.json").read_text(encoding="utf-8"))
GOLDEN = FIXTURE["calls"]
SHAPE_TOLERANCE_COUNTS = 8      # the fitted profile against the DLL's setpoints, at every message
CONFIRMED = dict(operator_at_stop=True, jaws_empty=True, wrist_clear=True)
REFUSAL = "Home the arm before moving the gripper"


def reply(counts, sequence=1):
    return vh.Reply(counts=tuple(counts), switches=0, now=0.0, echo=sequence)


def drive(generator, first, *, stop_at=None):
    """Run the generator against an arm that holds still; ``stop_at`` throws the stop in at that message."""
    sent, current = [], first
    try:
        out = next(generator)
        while True:
            sent.append(out)
            if stop_at is not None and len(sent) == stop_at:
                out = generator.throw(vh.HomeError(vh.ERR_STOPPED, "a stop was requested"))
            else:
                out = generator.send(current)
    except StopIteration as done:
        return sent, done.value


def summary(outs):
    packets = [vh.encode_out(o, 1) for o in outs]
    return reduce.describe(packets)


class Planner(unittest.TestCase):
    def test_home_and_gripper_unused_slots_match_the_recording(self):
        expected = bytes.fromhex(FIXTURE["unused_slots_hex"])
        self.assertEqual(expected, bytes(8))
        home, _ = run(Plant())
        for outs in [home] + [drive(vg.vendor_gripper(reply([0] * 5 + [g["gripper"][0]]),
                                                     g["gripper"][-1]),
                                    reply([0] * 5 + [g["gripper"][0]]))[0] for g in GOLDEN]:
            self.assertEqual({vh.encode_out(out, 1)[36:44] for out in outs}, {expected})

    def test_open_and_close_match_the_dlls_calls(self):
        for golden in GOLDEN:
            start, target = golden["gripper"][0], golden["gripper"][-1]
            with self.subTest(golden=golden["direction"]):
                sent, result = drive(vg.vendor_gripper(reply([0, 0, 0, 0, 0, start]), target),
                                     reply([0, 0, 0, 0, 0, start]))
                ours = summary(sent)
                self.assertEqual(ours["commands"], golden["commands"])
                self.assertLessEqual(abs(ours["nop_count"] - golden["nop_count"]), 1)
                self.assertEqual((ours["gripper"][0], ours["gripper"][-1]), (start, target))
                self.assertEqual(ours["arm"], [(0, 0, 0, 0, 0)])
                self.assertTrue(result.sent and not result.stopped)

    def test_the_ramp_is_the_dlls_s_curve(self):
        for golden in GOLDEN[:2]:                # calls 3 and 4 repeat them, one shifted by a 0D of DLL timing
            start, target = golden["gripper"][0], golden["gripper"][-1]
            sent, _ = drive(vg.vendor_gripper(reply([0] * 5 + [start]), target), reply([0] * 5 + [start]))
            ours = summary(sent)["gripper"]
            with self.subTest(golden=golden["direction"]):
                self.assertEqual(len(ours), len(golden["gripper"]))
                worst = max(abs(a - b) for a, b in zip(ours, golden["gripper"]))
                self.assertLessEqual(worst, SHAPE_TOLERANCE_COUNTS)
                steps = [b - a for a, b in zip(ours, ours[1:])]
                sign = 1 if target > start else -1
                self.assertTrue(all(sign * s >= 0 for s in steps), "monotonic")
                middle = max(abs(s) for s in steps)
                self.assertLess(abs(steps[3]), middle / 3, "slow start")
                self.assertLess(abs(steps[-4]), middle / 3, "slow end")

    def test_the_arm_setpoints_hold_the_measured_counts(self):
        measured = [120, -340, 55, 7, -9, 0]
        sent, _ = drive(vg.vendor_gripper(reply(measured), vg.RANGE_ENC), reply(measured))
        self.assertEqual({o.setpoints[:5] for o in sent}, {tuple(measured[:5])})

    def test_already_at_the_target_sends_nothing(self):
        for count, target in ((vg.RANGE_ENC, vg.RANGE_ENC), (vg.RANGE_ENC - vg.AT_TARGET_COUNTS, vg.RANGE_ENC),
                              (0, 0), (vg.AT_TARGET_COUNTS, 0)):
            with self.subTest(count=count, target=target):
                sent, result = drive(vg.vendor_gripper(reply([0] * 5 + [count]), target), reply([0] * 5 + [count]))
                self.assertEqual(sent, [])
                self.assertFalse(result.sent)

    def test_a_stop_switches_the_gripper_off_where_it_is(self):
        first = reply([0] * 6)
        sent, result = drive(vg.vendor_gripper(first, vg.RANGE_ENC), first, stop_at=10)
        self.assertTrue(result.stopped)
        tail = [vh.encode_out(o, 1)[4:7].hex() for o in sent[-2:]]
        self.assertEqual(tail, ["732000", "422000"])
        self.assertEqual({o.setpoints[5] for o in sent[-2:]}, {0}, "held at the measured count")

    def test_an_emergency_at_the_target_is_a_fault_not_a_no_op(self):
        first = vh.Reply(counts=(0,) * 6, switches=0, now=0.0, echo=1, emergency=True)
        with self.assertRaises(vh.HomeError) as caught:
            drive(vg.vendor_gripper(first, 0), first)
        self.assertEqual(caught.exception.code, vh.ERR_EMERGENCY)

    def test_an_emergency_during_the_stop_still_switches_off_then_faults(self):
        first = reply([0] * 6)
        alarm = vh.Reply(counts=(0,) * 6, switches=0, now=0.0, echo=1, emergency=True)
        generator = vg.vendor_gripper(first, vg.RANGE_ENC)
        sent = [next(generator)]
        for _ in range(8):
            sent.append(generator.send(first))
        sent.append(generator.throw(vh.HomeError(vh.ERR_STOPPED, "a stop was requested")))   # 73 20
        sent.append(generator.send(alarm))                                                    # 42 20 00
        with self.assertRaises(vh.HomeError) as caught:
            generator.send(alarm)
        self.assertEqual(caught.exception.code, vh.ERR_EMERGENCY)
        self.assertEqual([vh.encode_out(o, 1)[4:6].hex() for o in sent[-2:]], ["7320", "4220"])

    def test_an_unreadable_reply_during_the_stop_is_a_fault(self):
        good = bytearray(64)
        for n in range(6):
            good[19 + 5 * n:22 + 5 * n] = vh.ZERO24.to_bytes(3, "little")
        stop = threading.Event()
        source = VendorHomeSource(start=lambda first: vg.vendor_gripper(first, vg.RANGE_ENC), stop_event=stop)
        sequence = 1
        for _ in range(10):
            good[0] = sequence
            sequence += 1
            self.assertEqual(source(bytes(good), sequence)[0], "send")
        stop.set()
        steps = []
        for packet in (bytes(good[:10]), bytes(good[:10]), bytes(good[:10])):   # too short to decode
            sequence += 1
            steps.append(source(packet, sequence)[0])
        self.assertIn("stop", steps)
        self.assertFalse(source.ok)

    def test_targets_come_from_range_enc_and_range_mm(self):
        self.assertEqual(vg.target_counts("open"), vg.RANGE_ENC)
        self.assertEqual(vg.target_counts("close"), 0)
        self.assertEqual((vg.RANGE_ENC, vg.RANGE_MM), (5000, 70))
        with self.assertRaises(ValueError):
            vg.target_counts("squeeze")

    def test_jaw_metric_targets_match_the_lab_dll(self):
        # JawMetric(40) went to 2857 counts and JawMetric(10) to 714 on the desk (LEDGER.md PL71).
        self.assertEqual(vg.metric_target_counts(40), 2857)
        self.assertEqual(vg.metric_target_counts(10), 714)
        self.assertEqual(vg.metric_target_counts(0), 0)
        self.assertEqual(vg.metric_target_counts(vg.RANGE_MM), vg.RANGE_ENC)
        for bad in (-1, vg.RANGE_MM + 1, 12.5, True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                vg.metric_target_counts(bad)

    def test_the_source_ends_without_sending_when_at_target(self):
        source = VendorHomeSource(start=lambda first: vg.vendor_gripper(first, 0))
        packet = bytearray(64)                         # an idle reply echoing ID 1, every count 0
        packet[0] = 1
        for n in range(6):
            packet[19 + 5 * n:22 + 5 * n] = vh.ZERO24.to_bytes(3, "little")
        self.assertEqual(source(packet, 2), ("end",))
        self.assertFalse(source.result.sent)


class Desk(unittest.TestCase):
    """The real ``Scorbot`` and legacy workers on the desk fake."""

    def test_post_home_open_and_close_send_the_dlls_messages(self):
        calls = record_gripper([("open", 0, lambda r: r.open_gripper()),
                                ("close", vg.RANGE_ENC, lambda r: r.close_gripper())])
        for name, golden in (("open", GOLDEN[0]), ("close", GOLDEN[1])):
            packets, outcome = calls[name]
            with self.subTest(name=name):
                self.assertIsInstance(outcome, GripperMove, outcome)
                found = reduce.calls(packets)
                self.assertEqual(len(found), 1)
                ours = reduce.describe(found[0])
                self.assertEqual(ours["commands"], golden["commands"])
                self.assertLessEqual(abs(ours["nop_count"] - golden["nop_count"]), 1)
                self.assertEqual((ours["gripper"][0], ours["gripper"][-1]),
                                 (golden["gripper"][0], golden["gripper"][-1]))
                self.assertEqual(ours["arm"], [(0, 0, 0, 0, 0)])
                self.assertEqual(len(ours["gripper"]), len(golden["gripper"]))
                worst = max(abs(a - b) for a, b in zip(ours["gripper"], golden["gripper"]))
                self.assertLessEqual(worst, SHAPE_TOLERANCE_COUNTS)
                sign = 1 if name == "open" else -1
                steps = [b - a for a, b in zip(ours["gripper"], ours["gripper"][1:])]
                self.assertTrue(all(sign * s >= 0 for s in steps), "monotonic")

    def test_unhomed_open_and_close_refuse_and_send_nothing(self):
        for homed, gripper_homed in ((False, False), (True, False)):    # no Home; a legacy or inch Home
            calls = record_gripper([("open", 0, lambda r: r.open_gripper()),
                                    ("close", 0, lambda r: r.close_gripper())],
                                   homed=homed, gripper_homed=gripper_homed)
            for name in ("open", "close"):
                packets, outcome = calls[name]
                with self.subTest(homed=homed, name=name):
                    self.assertIsInstance(outcome, ScorbotError)
                    self.assertIn(REFUSAL, str(outcome))
                    self.assertEqual([p[4:6].hex() for p in packets if p[4] != vh.NOP], [])


class Simulated(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.robot = SimulatedScorbot(log_path=Path(folder.name) / "run.jsonl").connect()
        self.addCleanup(self._close)
        self.robot.enable()

    def _close(self):
        try:
            self.robot.disconnect()
        except Exception:
            pass

    def test_after_a_vendor_home_open_then_close_reach_the_targets(self):
        self.robot.vendor_home(**CONFIRMED)
        opened = self.robot.open_gripper()
        self.assertTrue(opened.full_travel)
        self.assertGreater(opened.moved_counts, vg.RANGE_ENC - vg.AT_TARGET_COUNTS)
        again = self.robot.open_gripper()
        self.assertEqual(again.moved_counts, 0)
        closed = self.robot.close_gripper()
        self.assertTrue(closed.full_travel)
        self.assertLess(closed.moved_counts, -(vg.RANGE_ENC - vg.AT_TARGET_COUNTS))

    def test_before_a_home_and_after_the_legacy_home_it_refuses(self):
        for direction in ("open", "close"):
            with self.assertRaisesRegex(ScorbotError, REFUSAL):
                self.robot.move_gripper(direction)
        self.robot.home(start_position_confirmed=True)
        with self.assertRaisesRegex(ScorbotError, REFUSAL):
            self.robot.open_gripper()

    def test_jaw_to_a_width_in_mm(self):
        self.robot.vendor_home(**CONFIRMED)
        half = self.robot.move_gripper_mm(40)
        self.assertEqual(half.direction, "open")
        self.assertAlmostEqual(half.planned_counts, 2857, delta=1)   # from where the jaw is, not from 0
        self.assertTrue(half.full_travel)
        self.assertEqual(self.robot.sim.vendor_packets[-1][32:36], (2857).to_bytes(4, "little", signed=True))
        narrower = self.robot.move_gripper_mm(10)
        self.assertEqual(narrower.direction, "close")
        self.assertAlmostEqual(narrower.planned_counts, 2857 - 714, delta=1)

    def test_a_width_queued_behind_another_move_picks_its_direction_after_it(self):
        self.robot.vendor_home(**CONFIRMED)
        result = {}

        def queued():
            try:
                result["move"] = self.robot.move_gripper_mm(10)
            except Exception as exc:      # the thread's failure is the test's
                result["error"] = exc

        with self.robot._motion_lock:     # another move holds the lock, the jaw still at 0
            waiting = threading.Thread(target=queued)
            waiting.start()
            waiting.join(0.3)
            self.robot.open_gripper()     # crosses the queued target: 0 -> 5000
        waiting.join(30)
        self.assertNotIn("error", result)
        self.assertEqual(result["move"].direction, "close")
        self.assertIsNone(self.robot._fault)

    def test_jaw_to_a_width_needs_a_vendor_home(self):
        with self.assertRaisesRegex(ScorbotError, REFUSAL):
            self.robot.move_gripper_mm(40)

    def test_a_fault_clears_the_gripper_home(self):
        self.robot.vendor_home(**CONFIRMED)
        self.robot._latch_fault("test")
        with self.assertRaises(ScorbotError):
            self.robot.open_gripper()
        self.assertFalse(self.robot._homed and self.robot._gripper_homed)

    def test_a_stop_request_ends_it_with_motion_stopped(self):
        self.robot.vendor_home(**CONFIRMED)
        self.robot.sim.vendor_hook = lambda count, plant: count == 8 and self.robot.request_stop()
        with self.assertRaises(MotionStopped):
            self.robot.open_gripper()
        self.robot.sim.vendor_hook = None
        self.assertIsNone(self.robot._fault)


if __name__ == "__main__":
    unittest.main()
