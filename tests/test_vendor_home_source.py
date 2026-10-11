"""scorbot.vendor_home_source: the worker-side adapter. No USB, no threads (events are set by hand)."""

import json
import threading
import unittest
from pathlib import Path

from scorbot import vendor_home as vh
from scorbot.vendor_home_source import TOTAL_SECONDS, VENDOR_HOME_ORDER, VendorHomeSource
from tests.vendor_home_plant import Plant, run_generator

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "vendor_home_trace04.json").read_text(encoding="utf-8"))


def drive(source, plant, *, on_message=None, limit=100000):
    """What the legacy worker does: exchange a packet, ask the source what is next. Returns (step, sent)."""
    reply = plant.exchange(vh.encode_out(vh.Out(vh.NOP), 1))
    sequence, sent = 1, []
    for _ in range(limit):
        sequence = sequence % 255 + 1
        step = source(reply, sequence)
        if step[0] != "send":
            return step, sent
        sent.append(step[1])
        if on_message:
            on_message(len(sent), plant)
        reply = plant.exchange(step[1])
    raise AssertionError("the source never ended")


def non_nop(packets):
    items = [[p[4], p[5], int.from_bytes(p[6:8], "little"), int.from_bytes(p[8:12], "little", signed=True)]
             for p in packets if p[4] != vh.NOP]
    return [item for i, item in enumerate(items) if i == 0 or item != items[i - 1]]


def abort_messages(source, plant, *, start_stop_at=30, garble_from=None):
    """Run ``start_stop_at`` messages, interrupt, and return (final step, every packet sent after that).

    ``garble_from=n`` makes every reply from the n-th message after the interrupt unreadable."""
    reply = plant.exchange(vh.encode_out(vh.Out(vh.NOP), 1))
    sequence, sent, interrupted_at = 1, [], None
    for _ in range(100000):
        sequence = sequence % 255 + 1
        if interrupted_at is None and len(sent) == start_stop_at:
            source.interrupt(vh.ERR_BAD_REPLY, "the USB link failed")
            interrupted_at = len(sent)
        bad = garble_from is not None and interrupted_at is not None and len(sent) >= interrupted_at + garble_from
        step = source(b"\x00" * 10 if bad else reply, sequence)
        if step[0] != "send":
            return step, sent[interrupted_at:]
        sent.append(step[1])
        reply = plant.exchange(step[1])
    raise AssertionError("the source never ended")


def natural_abort(plant_factory, *, garble=False, stop=False):
    """Run until the home faults on its own and starts its abort; return (step, abort packets, source).

    ``garble`` makes every reply from the first abort message on unreadable; ``stop`` sets the stop event
    while the abort runs. Where the abort starts is read from the generator itself (``Out.aborting``),
    not from the source, so the test still injects its fault if the source stops tracking it."""
    try:
        run_generator(lambda first: vh.vendor_home(first), plant_factory(), max_cycles=20000)
    except vh.HomeError as error:
        abort_index = next(i for i, out in enumerate(error.outs) if out.aborting)
    else:
        raise AssertionError("the home was meant to fault")
    stop_event = threading.Event()
    source = VendorHomeSource(stop_event=stop_event)
    plant = plant_factory()
    reply = plant.exchange(vh.encode_out(vh.Out(vh.NOP), 1))
    sequence, sent = 1, []
    for _ in range(100000):
        sequence = sequence % 255 + 1
        if stop and len(sent) == abort_index + 1:
            stop_event.set()
        bad = garble and len(sent) > abort_index
        step = source(b"\x00" * 10 if bad else reply, sequence)
        if step[0] != "send":
            return step, sent[abort_index:], source
        sent.append(step[1])
        reply = plant.exchange(step[1])
    raise AssertionError("the source never ended")


class NaturalAbortTests(unittest.TestCase):
    """A fault the home found itself (here a stall) starts an abort the source did not ask for."""

    def test_a_natural_fault_runs_the_whole_abort_and_keeps_its_own_error(self):
        step, abort, source = natural_abort(lambda: Plant(gain=0.0))
        self.assertEqual(step, ("stop",))
        self.assertEqual(source.error.code, vh.ERR_STALL)
        self.assertTrue(any(p[4] == vh.CLEAR for p in abort))
        self.assertGreaterEqual(sum(1 for p in abort if p[4] == vh.MOVE and p[8:12] == bytes(4)), 6)

    def test_unreadable_replies_during_a_natural_abort_do_not_cut_it_short(self):
        clean = natural_abort(lambda: Plant(gain=0.0))[1]
        step, garbled, source = natural_abort(lambda: Plant(gain=0.0), garble=True)
        self.assertEqual(step, ("stop",))
        self.assertEqual([p[4:12] for p in garbled], [p[4:12] for p in clean])
        self.assertEqual(source.error.code, vh.ERR_STALL)

    def test_a_stop_request_during_a_natural_abort_does_not_cut_it_short(self):
        clean = natural_abort(lambda: Plant(gain=0.0))[1]
        step, stopped, source = natural_abort(lambda: Plant(gain=0.0), stop=True)
        self.assertEqual(step, ("stop",))
        self.assertEqual([p[4:12] for p in stopped], [p[4:12] for p in clean])
        self.assertEqual(source.error.code, vh.ERR_STALL)


class AbortFromOutsideTests(unittest.TestCase):
    def test_an_outside_interrupt_runs_the_abort_messages_then_stops_with_that_error(self):
        source = VendorHomeSource()
        step, abort = abort_messages(source, Plant())
        self.assertEqual(step, ("stop",))
        self.assertEqual(source.error.code, vh.ERR_BAD_REPLY)
        self.assertTrue(any(p[4] == vh.CLEAR for p in abort))
        self.assertGreaterEqual(sum(1 for p in abort if p[4] == vh.MOVE and p[8:12] == bytes(4)), 6)

    def test_unreadable_replies_while_aborting_do_not_cut_the_abort_short(self):
        clean = abort_messages(VendorHomeSource(), Plant())[1]
        garbled_source = VendorHomeSource()
        step, garbled = abort_messages(garbled_source, Plant(), garble_from=0)
        self.assertEqual(step, ("stop",))
        self.assertEqual([p[4:12] for p in garbled], [p[4:12] for p in clean])
        self.assertEqual(garbled_source.error.code, vh.ERR_BAD_REPLY)


class FullRunTests(unittest.TestCase):
    def test_a_whole_home_through_the_source_sends_the_vendors_commands(self):
        source = VendorHomeSource()
        step, sent = drive(source, Plant())
        self.assertEqual(step, ("end",))
        self.assertTrue(source.ok)
        self.assertEqual(source.result.axes_done, vh.HOMING_SEQUENCE)
        self.assertEqual(non_nop(sent), [list(s) for s in FIXTURE["steps"]])

    def test_every_packet_carries_the_sequence_the_worker_gave_it(self):
        source = VendorHomeSource()
        seen = []
        reply = Plant().exchange(vh.encode_out(vh.Out(vh.NOP), 1))
        for sequence in (7, 8, 9, 10, 11):
            step = source(reply, sequence)
            self.assertEqual(step[0], "send")
            self.assertEqual(step[1][0], sequence)
            self.assertEqual(len(step[1]), 64)
            seen.append(step[1])

    def test_the_order_code_is_the_one_libcomm_dispatches_on(self):
        self.assertEqual(VENDOR_HOME_ORDER, 22)
        self.assertNotIn(VENDOR_HOME_ORDER, (4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 528))


class InterruptTests(unittest.TestCase):
    def assert_aborted(self, step, sent, source, code, after):
        self.assertEqual(step, ("stop",))
        self.assertFalse(source.ok)
        self.assertIsInstance(source.error, vh.HomeError)
        self.assertEqual(source.error.code, code)
        tail = non_nop(sent[after:])
        self.assertEqual([c[0] for c in tail][:1], [vh.CLEAR])                      # the abort stop was sent...
        self.assertEqual([c[1] for c in tail if c[0] == vh.MOVE][-6:], [1 << n for n in range(6)])
        self.assertEqual(tail[-1][:3], [vh.MODE, 0xFF, vh.MODE_S])                  # ...and ends in mode S

    def test_a_stop_request_is_thrown_in_and_the_abort_messages_are_still_sent(self):
        stop = threading.Event()
        source = VendorHomeSource(stop_event=stop)
        step, sent = drive(source, Plant(), on_message=lambda n, p: stop.set() if n == 300 else None)
        self.assert_aborted(step, sent, source, vh.ERR_STOPPED, 300)
        self.assertLess(len(sent), 330)                                              # it stopped promptly

    def test_a_cancel_is_the_same_as_a_stop(self):
        cancel = threading.Event()
        source = VendorHomeSource(cancel_event=cancel)
        step, sent = drive(source, Plant(), on_message=lambda n, p: cancel.set() if n == 200 else None)
        self.assert_aborted(step, sent, source, vh.ERR_STOPPED, 200)

    def test_the_total_deadline_aborts_a_home_that_runs_too_long(self):
        ticks = iter(range(10**6))
        source = VendorHomeSource(clock=lambda: next(ticks) * 0.1, total_seconds=5.0)   # no axis limit is near
        step, sent = drive(source, Plant())
        self.assertEqual(step, ("stop",))
        self.assertEqual(source.error.code, vh.ERR_TIMEOUT)
        self.assertEqual(source.interrupted[0], vh.ERR_TIMEOUT)                       # the source's own deadline fired
        self.assertIn("in all", source.interrupted[1])
        self.assertGreater(len(sent), 40)
        self.assertEqual(non_nop(sent)[-1][:3], [vh.MODE, 0xFF, vh.MODE_S])           # after the abort stop

    def test_an_emergency_reply_aborts_through_the_generator(self):
        source = VendorHomeSource()
        step, sent = drive(source, Plant(), on_message=lambda n, p: setattr(p, "emergency", n > 100))
        self.assert_aborted(step, sent, source, vh.ERR_EMERGENCY, 100)

    def test_an_unreadable_reply_aborts_instead_of_crashing(self):
        source = VendorHomeSource()
        plant = Plant()
        reply = plant.exchange(vh.encode_out(vh.Out(vh.NOP), 1))
        sequence = 1
        for count in range(60):
            sequence = sequence % 255 + 1
            step = source(reply if count < 40 else reply[:20], sequence)
            if step[0] != "send":
                break
            reply = plant.exchange(step[1])
        self.assertTrue(source.interrupted is not None and source.interrupted[0] == vh.ERR_BAD_REPLY)

    def test_a_stop_set_before_anything_is_sent_still_ends_cleanly(self):
        stop = threading.Event()
        stop.set()
        source = VendorHomeSource(stop_event=stop)
        step, sent = drive(source, Plant())
        self.assertEqual(step, ("stop",))
        self.assertEqual(source.error.code, vh.ERR_STOPPED)

    def test_the_total_deadline_covers_every_axis_time_limit(self):
        per_axis = sum(a.max_time_s for a in vh.AXES)
        self.assertGreater(TOTAL_SECONDS, per_axis + sum(vh.AXES[a].max_time_s for a in vh.HOMING_SEQUENCE[:4]))


class NeverRaisesTests(unittest.TestCase):
    def test_garbage_in_gives_stop_not_an_exception(self):
        for bad in (b"", b"\x00" * 10, None, object()):
            source = VendorHomeSource()
            step = source(bad, 1)
            self.assertEqual(step, ("stop",))
            self.assertIsNotNone(source.error)

    def test_a_generator_bug_is_reported_as_a_stop(self):
        source = VendorHomeSource()
        reply = Plant().exchange(vh.encode_out(vh.Out(vh.NOP), 1))
        self.assertEqual(source(reply, 2)[0], "send")
        source.generator = iter(())                           # no .send: any call fails
        self.assertEqual(source(reply, 3), ("stop",))
        self.assertIsInstance(source.error, AttributeError)


if __name__ == "__main__":
    unittest.main()
