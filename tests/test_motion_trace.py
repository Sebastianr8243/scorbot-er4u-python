"""In-SDK packet trace during jogs: recording, logging and the usb_trace export."""

import json
from pathlib import Path
import tempfile
import unittest

from scorbot.packet import PacketTrace, TrackedInputEndpoint, TrackedOutputEndpoint
from scorbot.simulated import SimulatedController, SimulatedScorbot, encode_packet
from scripts.usb_trace import rows_from_controller_log, setpoint_stats


class _Out:
    def __init__(self):
        self.written = []

    def write(self, data, timeout=None):
        self.written.append(bytes(data))
        return len(data)


class _In:
    def __init__(self, packet):
        self.packet = packet
        self.wMaxPacketSize = 64

    def read(self, buffer, timeout=None):
        buffer[:len(self.packet)] = self.packet
        return len(self.packet)


class PacketTraceTests(unittest.TestCase):
    def test_records_both_directions_only_while_started(self):
        trace = PacketTrace()
        out, inp = TrackedOutputEndpoint(_Out(), trace), TrackedInputEndpoint(_In(bytes(64)), trace)
        out.write(b"\x01\x02")
        inp.read(bytearray(64))
        self.assertEqual(trace.stop(), ([], 0))
        trace.start()
        out.write(b"\x01\x02")
        inp.read(bytearray(64))
        packets, dropped = trace.stop()
        self.assertEqual([p["direction"] for p in packets], ["out", "in"])
        self.assertEqual(packets[0]["hex"], "0102")
        self.assertEqual(packets[1]["hex"], "00" * 64)
        self.assertEqual(dropped, 0)
        out.write(b"\x03")
        self.assertEqual(trace.stop(), ([], 0))

    def test_output_proxy_passes_writes_through_unchanged(self):
        raw = _Out()
        out = TrackedOutputEndpoint(raw, PacketTrace())
        self.assertEqual(out.write(b"\xaa" * 64, 5), 64)
        self.assertEqual(raw.written, [b"\xaa" * 64])

    def test_limit_counts_dropped_packets(self):
        trace = PacketTrace(limit=2)
        trace.start()
        for _ in range(5):
            trace.add("out", b"\x00")
        packets, dropped = trace.stop()
        self.assertEqual((len(packets), dropped), (2, 3))


class _TracingController(SimulatedController):
    """Adds one OUT and one IN packet per jog to the robot's trace, like the real proxies."""

    trace = None

    def _apply(self, payload):
        code = super()._apply(payload)
        if self.trace is not None and payload[0] in range(4, 14):
            self.trace.add("out", bytes(64))
            self.trace.add("in", encode_packet(self.counts))
        return code


class JogTraceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.log = Path(self._tmp.name) / "events.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def events(self, name):
        rows = [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]
        return [row for row in rows if row["event"] == name]

    def robot(self):
        controller = _TracingController()
        robot = SimulatedScorbot(controller=controller, log_path=self.log).connect()
        robot._trace = controller.trace = PacketTrace()
        robot.enable()
        robot.home(start_position_confirmed=True)
        return robot

    def test_jog_logs_the_packets_it_exchanged(self):
        robot = self.robot()
        try:
            robot.jog_joint("base", 1.0)
        finally:
            robot.disconnect()
        traces = self.events("motion_trace")
        self.assertEqual(len(traces), 1)
        self.assertEqual(traces[0]["joint"], "base")
        self.assertEqual([p["direction"] for p in traces[0]["packets"]], ["out", "in"])
        self.assertEqual(traces[0]["dropped_packets"], 0)

    def test_failed_jog_still_logs_its_trace(self):
        from scorbot import ScorbotError
        robot = self.robot()
        try:
            robot.sim.inject("controller_error")
            with self.assertRaises(ScorbotError):
                robot.jog_joint("base", 1.0)
        finally:
            robot.disconnect()
        self.assertEqual(len(self.events("motion_trace")), 1)

    def test_simulator_without_trace_logs_none(self):
        robot = SimulatedScorbot(log_path=self.log).connect()
        try:
            robot.enable()
            robot.home(start_position_confirmed=True)
            robot.jog_joint("base", 1.0)
        finally:
            robot.disconnect()
        self.assertEqual(self.events("motion_trace"), [])

    def test_usb_trace_reads_the_log_into_export_rows(self):
        robot = self.robot()
        try:
            robot.jog_joint("base", 1.0)
            robot.jog_joint("base", -1.0)
        finally:
            robot.disconnect()
        rows = rows_from_controller_log(self.log)
        self.assertEqual([r["direction"] for r in rows], ["out", "in", "out", "in"])
        self.assertEqual(rows[0]["t_s"], 0.0)
        self.assertIn("encoder_counts", rows[1])
        self.assertIn("byte0", rows[0])
        setpoint_stats(rows)  # the existing analysis accepts these rows


if __name__ == "__main__":
    unittest.main()
