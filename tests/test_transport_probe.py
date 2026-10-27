"""Full-handshake probe packet pins; connecting is not motion-free."""

import contextlib
from hashlib import sha256
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scorbot.lab.dry_link import DryScorbot
from scorbot.transport import codec, probe
from scorbot.transport.device_file import DeviceLinkError


CONNECT_OPCODES = {
    0x0D: "idle/setpoint carrier", 0x42: "legacy control flag B",
    0x47: "move open G", 0x4F: "axis mode O", 0x53: "handshake parameter table S",
    0x5A: "handshake initial Z", 0x61: "legacy mode a", 0x64: "legacy mode d",
    0x72: "legacy mode r", 0x73: "legacy control flag s",
}
HANDSHAKE_SHA256 = "76786a8fa135fc205778b0c8cbdd2d534c5a6eb8c2d0d79642c6d0c547fdf975"


def connection_commands():
    commands = [*codec.GET_MSG1.values(), *codec.MOTORS_ON.values(),
                *codec.MOTORS_OFF.values(), *codec.SCORBOT_OFF.values(), codec.IDLE,
                codec.PKT2_SETUP_1, codec.PKT2_SETUP_2]
    commands += [codec.pkt2_mode_command(mode) for mode in codec.PKT2_MODES]
    commands += [codec.pkt2_table_command(n) for n in range(1, 80)]
    return {codec.build_out(1, c)[1:12] for c in commands} | {
        codec.build_out(1, codec.PKT2_END_COMMAND, header=codec.PKT2_END_HEADER)[1:12]}


class ProbeTests(unittest.TestCase):
    def test_probe_has_exact_connect_command_set_and_pinned_full_handshake(self):
        sets = []
        for use_probe in (False, True):
            robot = DryScorbot()
            sync = robot._legacy("libsync")
            original = sync.msg_start
            handshake = []

            def capture(*args, original=original, handshake=handshake, robot=robot):
                result = original(*args)
                handshake.extend(robot.dry_link.sent)
                return result

            with mock.patch.object(sync, "msg_start", side_effect=capture):
                try:
                    if use_probe:
                        state, packet = probe.read_status(robot)
                        self.assertTrue(state.simulated)
                        self.assertEqual(state.home_switch_bits, packet[5])
                        self.assertEqual(state.encoder_counts["base"], int.from_bytes(packet[19:21], "little"))
                    else:
                        robot.connect()
                finally:
                    robot.disconnect()
            self.assertEqual(len(handshake), 145)
            self.assertEqual(sha256(b"".join(handshake)).hexdigest(), HANDSHAKE_SHA256)
            sent = robot.dry_link.sent
            self.assertEqual({p[4] for p in sent}, set(CONNECT_OPCODES))
            # Sequence and echoed encoder fields vary with background idle polls.
            commands = {p[1:12] for p in sent}
            self.assertEqual(commands, connection_commands())
            self.assertTrue(all(len(p) == 64 and p[36:] == bytes(28) for p in sent))
            sets.append(commands)
        self.assertEqual(*sets)

    def test_cli_labels_raw_counts_and_connection_warning(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(probe.main(["--simulate", "--latency-ms", "1"]), 0)
        lines = output.getvalue().splitlines()
        self.assertTrue(all(line.startswith("SIMULATED ") for line in lines))
        self.assertIn("energises then releases", lines[0])
        self.assertIn("raw ", lines[1])
        self.assertIn("switches", lines[2])

    def test_summarise_pins_gaps_and_echo_lag_on_known_rows(self):
        def row(direction, ms, first):
            return {"direction": direction, "host_monotonic_ns": ms * 1_000_000, "hex": bytes([first] + [0] * 63).hex()}

        rows = [row("out", 0, 10), row("in", 8, 10), row("out", 9, 11), row("in", 18, 9),
                row("out", 20, 12), row("in", 40, 0)]
        result = probe.summarise(rows)
        self.assertEqual(result["replies"], 3)
        self.assertEqual(result["lag"], {0: 1, 2: 1, None: 1})        # echo 0 is unreadable
        self.assertEqual(result["gap_ms"], {"min": 10.0, "median": 16.0, "p95": 10.0, "max": 22.0})
        self.assertEqual(probe.summarise([row("in", 5, 3)]), {"replies": 1, "lag": {None: 1}})

    def test_reads_times_idle_replies_and_logs_to_a_new_file_only(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "reads.jsonl"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(probe.main(["--simulate", "--latency-ms", "1", "--reads", "5",
                                             "--log", str(path)]), 0)
            lines = output.getvalue().splitlines()
            self.assertTrue(all(line.startswith("SIMULATED ") for line in lines))
            self.assertIn("timing", lines[-1])
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertGreaterEqual(sum(r["direction"] == "in" for r in rows), 5)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(probe.main(["--simulate", "--reads", "1", "--log", str(path)]), 1)
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            probe.main(["--simulate", "--log", "x.jsonl"])

    def test_real_refusals_open_nothing_and_device_errors_are_plain(self):
        with mock.patch.object(probe, "Scorbot") as factory, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(probe.main([]), 1)
            factory.assert_not_called()
            factory.return_value._device = None
            factory.return_value.connect.side_effect = DeviceLinkError("Close SCORBASE first")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(probe.main(["--online"]), 1)
            self.assertIn("Close SCORBASE first", output.getvalue())
            factory.return_value.disconnect.assert_called_once()
