"""Error branches of command handling, streams, stop settling and disconnect.

Every test here drives code that already exists; none of it needs USB. Each one names the line it
protects, so a mutation of that line (see the commit message) must turn exactly its test red.
"""

import json
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from scorbot import Scorbot, ScorbotError
from scorbot.packet import PacketTrace
from scorbot.state import ENCODER_OFFSETS, JOINTS, decode_state

try:
    from tests.sim_support import SimulatedRobotCase
except ImportError:
    from sim_support import SimulatedRobotCase


class CommandErrorPaths(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.robot = Scorbot(command_timeout=0.05, log_path=Path(folder.name) / "run.controller.jsonl")
        self.robot._device = object()
        self.robot._buffer = bytearray(64)

    def rows(self):
        return [json.loads(line) for line in self.robot.log_path.read_text(encoding="utf-8").splitlines()]

    def alive_link(self):
        """Two live threads, so ``_link_alive()`` is true and a disable is attempted after an error."""
        release = threading.Event()
        self.addCleanup(release.set)
        for name in ("_command_thread", "_sync_thread"):
            thread = threading.Thread(target=release.wait, daemon=True)
            thread.start()
            setattr(self.robot, name, thread)

    def test_a_command_during_a_stream_is_refused_before_it_is_queued(self):
        self.robot._stream = object()
        with self.assertRaisesRegex(ScorbotError, "A stream is active"):
            self.robot._command([17, 1, 1])
        self.assertTrue(self.robot._commands.empty())

    def test_an_error_code_latches_and_a_silent_worker_is_logged_as_a_disable_timeout(self):
        self.alive_link()
        self.robot._results.put(5)           # the error, then the worker's closing 0
        self.robot._results.put(0)
        with self.assertRaisesRegex(ScorbotError, "error code 5"):
            self.robot._command([17, 1, 1])
        self.assertIn("error code 5", self.robot._fault)
        self.assertEqual([r["result"] for r in self.rows() if r["event"] == "disable_after_error"], ["timeout"])
        self.assertIn([16, 1, 1], list(self.robot._commands.queue))   # the best-effort disable was queued

    def test_a_timeout_latches_a_fault_and_queues_a_disable(self):
        with self.assertRaisesRegex(ScorbotError, "timed out"):
            self.robot._command([17, 1, 1])
        self.assertIsNotNone(self.robot._fault)
        self.assertTrue(self.robot._cancel_event.is_set())
        self.assertIn([16, 1, 1], list(self.robot._commands.queue))

    def test_keyboard_interrupt_latches_cancels_queues_a_disable_and_reraises(self):
        with mock.patch.object(self.robot, "_next_result", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.robot._command([17, 1, 1])
        self.assertIn("interrupted", self.robot._fault)
        self.assertTrue(self.robot._cancel_event.is_set())
        self.assertIn([16, 1, 1], list(self.robot._commands.queue))
        self.assertIn("command_interrupted", [r["event"] for r in self.rows()])

    def test_disable_best_effort_swallows_a_refusal(self):
        with mock.patch.object(self.robot, "_command", side_effect=ScorbotError("faulted")) as command:
            self.robot._disable_best_effort()           # must not raise: it runs while a fault is being handled
        command.assert_called_once_with([16, 1, 1])

    def test_abandoning_a_stream_that_cannot_stop_carries_on(self):
        stream = types.SimpleNamespace(stop=mock.Mock(side_effect=ScorbotError("latched")))
        self.robot._stream = stream
        self.robot._abandon_stream()                    # disconnect must still go on
        stream.stop.assert_called_once()


class SettleErrorPaths(SimulatedRobotCase):
    def test_counts_that_stay_ambiguous_never_count_as_settled(self):
        robot = self.robot()
        robot.STOP_SETTLE_TIMEOUT_S = 0.08
        robot.STOP_SETTLE_INTERVAL_S = 0.01
        index = {"n": 0}
        clock = {"t": 1000.0}                           # a virtual clock: no real waiting, no race with a slow runner
        fake_time = types.SimpleNamespace(
            monotonic=lambda: clock["t"], monotonic_ns=lambda: int(clock["t"] * 1e9),
            sleep=lambda seconds: clock.__setitem__("t", clock["t"] + seconds))

        def state(after_index=None):
            # each reading is half the counter range from the last: signed_count_delta refuses to guess
            index["n"] += 1
            count = 0 if index["n"] % 2 else 32767
            packet = bytearray(64)
            for offset in ENCODER_OFFSETS:
                packet[offset:offset + 2] = count.to_bytes(2, "little")
                packet[offset + 2] = 128
            return decode_state(bytes(packet), connected=True, enabled=True, homed=True, fault=None,
                                packet_index=index["n"])

        with mock.patch.object(robot, "_motion_state", side_effect=state), \
                mock.patch("scorbot.robot.time", fake_time):
            with self.assertRaisesRegex(ScorbotError, "still moving"):
                robot._settled_after_stop(state())
        self.assertGreaterEqual(index["n"], 8)          # eight 10 ms comparisons fit in the 80 ms window, all ambiguous
        self.assertIsNotNone(robot._fault)
        self.assertIn("stop_settle_failed", self.events())


class StreamErrorPaths(SimulatedRobotCase):
    CONTROLLER_DEFAULTS = {"step_delay_s": 0.001}

    def idle(self, robot, seconds=2.0):
        deadline = time.monotonic() + seconds
        while robot.sim._moving and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(robot.sim._moving, "the worker must leave the stream")

    def test_ending_a_stream_the_session_no_longer_holds_is_refused(self):
        robot = self.robot()
        stream = robot.start_stream()
        robot._stream = None            # as if another path had already ended it
        stream.final_state = None
        with self.assertRaisesRegex(ScorbotError, "already ended"):
            robot._end_stream(stream)
        robot._stream = stream
        stream.close()

    def test_a_refusal_from_the_worker_while_ending_latches_the_session(self):
        robot = self.robot()
        stream = robot.start_stream()
        with mock.patch.object(robot, "_next_result", side_effect=ScorbotError("worker said no")):
            with self.assertRaisesRegex(ScorbotError, "worker said no"):
                stream.stop()
        self.idle(robot)
        self.assertIn("worker said no", robot._fault)
        self.assertEqual(self.events().count("stream_fault"), 1)

    def test_an_error_code_from_the_stream_latches_the_session(self):
        robot = self.robot()
        stream = robot.start_stream()
        with mock.patch.object(robot, "_next_result", side_effect=[5, 0]):
            with self.assertRaisesRegex(ScorbotError, "error code 5"):
                stream.stop()
        self.idle(robot)
        self.assertIn("error code 5", robot._fault)

    def test_a_stream_record_that_cannot_be_written_is_not_a_clean_end(self):
        robot = self.robot()
        stream = robot.start_stream()
        real = robot._record

        def failing(event, **fields):
            if event in ("stream_trace", "stream_fault"):
                raise OSError("disk full")
            return real(event, **fields)

        with mock.patch.object(robot, "_record", side_effect=failing):
            with self.assertRaises(ScorbotError):
                stream.close()
        self.idle(robot)
        self.assertIn("record could not be written", robot._fault)

    def test_a_stream_with_a_packet_trace_writes_it_when_it_ends(self):
        robot = self.robot()
        robot._trace = PacketTrace()
        stream = robot.start_stream()
        robot._trace.add("in", b"\x01\x02")             # a trace records only once start_stream has started it
        stream.close()
        rows = [r for r in self.rows() if r["event"] == "stream_trace"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]["packets"]), 1)

    def test_a_home_the_counts_cannot_be_compared_with_refuses_the_stream(self):
        robot = self.robot()
        robot._home_counts = {name: 32767 for name in JOINTS}      # half the counter range from the arm: ambiguous
        before = len(robot.sim.commands)
        with self.assertRaisesRegex(ScorbotError, "Cannot place the arm relative to home"):
            robot.start_stream()
        self.assertEqual(len(robot.sim.commands), before)

    def test_a_missing_planning_extra_refuses_the_stream_without_sending(self):
        robot = self.robot()
        before = len(robot.sim.commands)
        with mock.patch("scorbot.stream_control.StreamCore", side_effect=ImportError("no ruckig")):
            with self.assertRaisesRegex(ScorbotError, "planning extra"):
                robot.start_stream()
        self.assertEqual(len(robot.sim.commands), before)

    def test_a_log_failure_while_reporting_a_stream_fault_does_not_escape(self):
        robot = self.robot()
        stream = types.SimpleNamespace(core=types.SimpleNamespace(fault="test fault"))   # no live worker to race with
        with mock.patch.object(robot, "_record", side_effect=OSError("disk full")):
            robot._stream_faulted(stream)               # runs on the worker: it must never raise
        self.assertIn("test fault", robot._fault)


if __name__ == "__main__":
    unittest.main()
