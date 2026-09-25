import json
from pathlib import Path
import unittest


class SchemaTests(unittest.TestCase):
    def test_every_scorbot_schema_lists_its_required_keys(self):
        from scorbot.session import schemas
        for name, keys in schemas.REQUIRED.items():
            schema = json.loads(schemas.schema_json(name))
            self.assertEqual(schema["title"], name)
            for key in keys:
                self.assertIn(key, schema["required"])

    def test_foxglove_image_schema_is_the_vendored_official_one(self):
        from scorbot.session import schemas
        schema = json.loads(schemas.schema_json("foxglove.CompressedImage"))
        self.assertEqual(schema["required"], ["timestamp", "frame_id", "data", "format"])
        self.assertEqual(schema["properties"]["data"]["contentEncoding"], "base64")

    def test_topic_to_schema_mapping(self):
        from scorbot.session.schemas import schema_name_for_topic
        self.assertEqual(schema_name_for_topic("/robot/state"), "scorbot.RobotState")
        self.assertEqual(schema_name_for_topic("/camera/cam0/image"), "foxglove.CompressedImage")
        self.assertEqual(schema_name_for_topic("/camera/cam0/detections"), "scorbot.Detection")
        self.assertIsNone(schema_name_for_topic("/unknown"))
        self.assertIsNone(schema_name_for_topic("/camera//image"))


def raw_messages(mcap_path):
    """Decode every message with the plain mcap stream reader (independent of replay.py)."""
    from mcap.records import Channel, Message
    from mcap.stream_reader import StreamReader
    channels, out = {}, []
    with open(mcap_path, "rb") as stream:
        try:
            for record in StreamReader(stream).records:
                if isinstance(record, Channel):
                    channels[record.id] = record.topic
                elif isinstance(record, Message):
                    out.append((channels[record.channel_id], record,
                                json.loads(record.data)))
        except Exception:
            pass
    return out


def new_writer(root, **overrides):
    from scorbot.session.record import SessionWriter
    options = dict(data_source="synthetic", robot_id="test-arm")
    options.update(overrides)
    return SessionWriter.create(root, **options)


class WriterTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_rejects_unknown_data_source(self):
        with self.assertRaises(ValueError):
            new_writer(self.root, data_source="maybe")

    def test_creates_session_files_and_finalizes_metadata(self):
        writer = new_writer(self.root)
        meta = json.loads((writer.path / "metadata.json").read_text())
        self.assertEqual(meta["data_source"], "synthetic")
        self.assertEqual(meta["schema_version"], 1)
        self.assertNotIn("closed_cleanly", meta)
        self.assertIn("started_epoch_ns", meta["clock"])
        self.assertTrue((writer.path / "notes.md").exists())
        writer.log_note("hello")
        writer.close()
        meta = json.loads((writer.path / "metadata.json").read_text())
        self.assertTrue(meta["closed_cleanly"])
        self.assertEqual(meta["event_count"], 1)

    def test_creates_missing_nested_root_with_spaces(self):
        root = self.root / "lab data" / "week 1"
        with new_writer(root) as writer:
            writer.log_note("x")
        self.assertTrue((writer.path / "session.mcap").exists())

    def test_rejects_nan_missing_keys_and_writes_after_close(self):
        from scorbot.session.record import SessionError
        writer = new_writer(self.root)
        with self.assertRaises(SessionError):
            writer.log_command("jog_joint", {"delta": float("nan")})
        with self.assertRaises(SessionError):
            writer.log_state({"encoder_counts": {}})
        with self.assertRaises(ValueError):
            writer.log_command_result("cmd-0001", "finished")
        writer.close()
        with self.assertRaises(SessionError):
            writer.log_note("late")

    def test_unserializable_payload_does_not_consume_seq(self):
        from scorbot.session.record import SessionError
        with new_writer(self.root) as writer:
            writer.log_note("first")
            with self.assertRaises(SessionError):
                writer.log_command("jog_joint", {"x": object()})
            writer.log_note("second")
        seqs = [payload["_rec"]["seq"] for _, _, payload in raw_messages(writer.mcap_path)]
        self.assertEqual(seqs, [0, 1])

    def test_exception_inside_with_block_is_logged_as_fault(self):
        with self.assertRaises(RuntimeError):
            with new_writer(self.root) as writer:
                raise RuntimeError("operator pressed e-stop")
        topics = [(topic, payload) for topic, _, payload in raw_messages(writer.mcap_path)]
        self.assertEqual(topics[-1][0], "/session/fault")
        self.assertIn("operator pressed e-stop", topics[-1][1]["message"])
        meta = json.loads((writer.path / "metadata.json").read_text())
        self.assertTrue(meta["closed_cleanly"])

    def test_concurrent_writers_get_contiguous_seq(self):
        import threading
        with new_writer(self.root) as writer:
            def work(tag):
                for i in range(500):
                    writer.log_note(f"{tag}{i}")
            threads = [threading.Thread(target=work, args=(t,)) for t in "ab"]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        messages = raw_messages(writer.mcap_path)
        self.assertEqual([p["_rec"]["seq"] for _, _, p in messages], list(range(1000)))
        logged = [p["_rec"]["logged_monotonic_ns"] for _, _, p in messages]
        self.assertEqual(logged, sorted(logged))
        self.assertEqual([m.sequence for _, m, _ in messages], list(range(1000)))

    def test_state_round_trip_with_raw_packet_and_observed_time(self):
        from dataclasses import asdict
        from scorbot.state import decode_state
        packet = bytearray(64)
        packet[19:21] = (1234).to_bytes(2, "little")
        state = decode_state(bytes(packet), connected=True, enabled=False,
                             homed=False, fault=None)
        with new_writer(self.root) as writer:
            writer.log_state(state, raw_packet=bytes(packet))
            writer.log_state(asdict(state), observed_monotonic_ns=5)
        (_, first, p1), (_, second, p2) = raw_messages(writer.mcap_path)
        self.assertEqual(p1["encoder_counts"]["base"], 1234)
        self.assertEqual(p1["raw_packet_hex"], bytes(packet).hex())
        self.assertIsNone(p1["_rec"]["observed_monotonic_ns"])
        self.assertEqual(first.publish_time, first.log_time)
        self.assertEqual(p2["_rec"]["observed_monotonic_ns"], 5)
        self.assertNotEqual(second.publish_time, second.log_time)

    def test_observed_time_taken_from_state_host_monotonic_ns(self):
        from types import SimpleNamespace
        state = {"encoder_counts": {}, "home_switch_bits": 0, "connected": True,
                 "host_monotonic_ns": 777}
        with new_writer(self.root) as writer:
            writer.log_state(state)
            writer.log_state(SimpleNamespace(**state))
        payloads = [p for _, _, p in raw_messages(writer.mcap_path)]
        self.assertEqual([p["_rec"]["observed_monotonic_ns"] for p in payloads], [777, 777])

    def test_image_message_matches_foxglove_schema_and_round_trips_bytes(self):
        import base64
        from scorbot.session import schemas
        data = b"\x89PNG fake image bytes"
        with new_writer(self.root, camera_ids=["cam0"]) as writer:
            writer.log_frame("cam0", 7, data, format="png", width=4, height=3)
        [(topic, _, payload)] = raw_messages(writer.mcap_path)
        self.assertEqual(topic, "/camera/cam0/image")
        for key in schemas.REQUIRED["foxglove.CompressedImage"]:
            self.assertIn(key, payload)
        self.assertEqual(base64.b64decode(payload["data"]), data)
        self.assertEqual(payload["_rec"]["frame_number"], 7)
        self.assertEqual(payload["frame_id"], "cam0")

    def test_concurrent_commands_get_unique_ids(self):
        import threading
        ids = []
        with new_writer(self.root) as writer:
            def work():
                for _ in range(200):
                    ids.append(writer.log_command("jog_joint", {}))
            threads = [threading.Thread(target=work) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(len(set(ids)), 800)

    def test_command_ids_are_generated_and_results_link_back(self):
        with new_writer(self.root) as writer:
            cid = writer.log_command("jog_joint", {"joint": "base"})
            writer.log_command_result(cid, "completed", completion_source="test")
        (_, _, cmd), (_, _, result) = raw_messages(writer.mcap_path)
        self.assertEqual(cid, "cmd-0001")
        self.assertEqual(cmd["command_id"], cid)
        self.assertEqual(result["command_id"], cid)


REPO_ROOT = Path(__file__).resolve().parents[1]

CRASH_SCRIPT = """
import os, sys
from scorbot.session.record import SessionWriter
writer = SessionWriter.create(sys.argv[1], data_source="synthetic", robot_id="crash")
for i in range(25):
    writer.log_note(f"n{i}")
print(writer.path, flush=True)
os._exit(1)
"""


class ReplayTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def crashed_session(self):
        import subprocess
        import sys
        result = subprocess.run([sys.executable, "-c", CRASH_SCRIPT, str(self.root)],
                                cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        return Path(result.stdout.strip())

    def test_round_trip_reproduces_timeline(self):
        from scorbot.session.replay import load_session
        with new_writer(self.root) as writer:
            writer.log_state({"encoder_counts": {"base": 1}, "home_switch_bits": 0,
                              "connected": True})
            cid = writer.log_command("jog_joint", {"joint": "base"})
            writer.log_command_result(cid, "completed")
            writer.log_note("done")
        session = load_session(writer.path)
        self.assertEqual(session.errors, [])
        self.assertEqual(session.warnings, [])
        self.assertEqual([e["seq"] for e in session.events], [0, 1, 2, 3])
        self.assertEqual([e["topic"] for e in session.events],
                         ["/robot/state", "/robot/command", "/robot/command_result",
                          "/session/note"])
        self.assertEqual(session.events[1]["payload"]["params"], {"joint": "base"})
        self.assertEqual(session.metadata["data_source"], "synthetic")

    def test_accepts_mcap_file_path(self):
        from scorbot.session.replay import load_session
        with new_writer(self.root) as writer:
            writer.log_note("x")
        self.assertEqual(len(load_session(writer.mcap_path).events), 1)

    def test_hard_crash_keeps_every_event_with_warning_only(self):
        from scorbot.session.replay import load_session
        session = load_session(self.crashed_session())
        self.assertEqual(len(session.events), 25)
        self.assertEqual(session.errors, [])
        self.assertTrue(any("not closed" in f.message for f in session.warnings))

    def test_truncated_tail_is_a_warning(self):
        from scorbot.session.replay import load_session
        path = self.crashed_session()
        mcap = path / "session.mcap"
        mcap.write_bytes(mcap.read_bytes()[:-5])
        session = load_session(path)
        self.assertEqual(len(session.events), 24)
        self.assertEqual(session.errors, [])
        self.assertTrue(any("truncated" in f.message for f in session.warnings))

    def test_mid_file_corruption_is_an_error(self):
        from scorbot.session.replay import load_session
        path = self.crashed_session()
        mcap = path / "session.mcap"
        data = bytearray(mcap.read_bytes())
        middle = len(data) // 2
        data[middle:middle + 32] = b"\xff" * 32
        mcap.write_bytes(bytes(data))
        session = load_session(path)
        self.assertNotEqual(session.errors, [])

    def test_silent_byte_change_in_closed_session_fails_checksum(self):
        from scorbot.session.replay import load_session
        with new_writer(self.root) as writer:
            writer.log_note("reading was 1234")
        mcap = writer.mcap_path
        data = mcap.read_bytes()
        mcap.write_bytes(data.replace(b"1234", b"1284", 1))
        session = load_session(writer.path)
        self.assertTrue(any("checksum" in f.message for f in session.errors),
                        [f.message for f in session.findings])

    def test_seq_gap_is_an_error(self):
        from scorbot.session.replay import load_session
        with new_writer(self.root) as writer:
            writer.log_note("a")
            writer._seq += 1  # simulate a lost record
            writer.log_note("b")
        session = load_session(writer.path)
        self.assertTrue(any("seq" in f.message for f in session.errors))

    def test_logged_time_going_backwards_is_an_error(self):
        from unittest.mock import patch
        from scorbot.session.replay import load_session
        writer = new_writer(self.root)
        start = writer.metadata["clock"]["started_monotonic_ns"]
        with patch("scorbot.session.record.time.monotonic_ns",
                   side_effect=[start + 2000, start + 1000]):
            writer.log_note("a")
            writer.log_note("b")
        writer.close()
        session = load_session(writer.path)
        self.assertTrue(any("backwards" in f.message for f in session.errors))

    def test_out_of_order_observed_times_are_not_errors(self):
        from scorbot.session.replay import load_session
        with new_writer(self.root, camera_ids=["cam0"]) as writer:
            writer.log_state({"encoder_counts": {}, "home_switch_bits": 0,
                              "connected": True}, observed_monotonic_ns=2_000)
            writer.log_frame("cam0", 0, b"x", format="png", width=1, height=1,
                             observed_monotonic_ns=1_000)
        self.assertEqual(load_session(writer.path).errors, [])

    def test_missing_command_result_is_a_warning(self):
        from scorbot.session.replay import load_session
        with new_writer(self.root) as writer:
            writer.log_command("jog_joint", {})
        session = load_session(writer.path)
        self.assertEqual(session.errors, [])
        self.assertTrue(any("cmd-0001" in f.message for f in session.warnings))

    def test_newer_schema_version_is_an_error(self):
        from scorbot.session.replay import load_session
        with new_writer(self.root) as writer:
            writer.log_note("x")
        meta_path = writer.path / "metadata.json"
        meta = json.loads(meta_path.read_text())
        meta["schema_version"] = 99
        meta_path.write_text(json.dumps(meta))
        self.assertTrue(any("schema_version" in f.message
                            for f in load_session(writer.path).errors))

    def test_nearest_prefers_observed_time_and_reports_signed_difference(self):
        from scorbot.session.replay import nearest
        events = [
            {"topic": "/robot/state", "payload": {"_rec": {
                "logged_monotonic_ns": 100, "observed_monotonic_ns": 10}}},
            {"topic": "/robot/state", "payload": {"_rec": {
                "logged_monotonic_ns": 110, "observed_monotonic_ns": None}}},
            {"topic": "/session/note", "payload": {"_rec": {
                "logged_monotonic_ns": 50, "observed_monotonic_ns": None}}},
        ]
        event, delta = nearest(events, 40)
        self.assertIs(event, events[0])
        self.assertEqual(delta, -30)
        event, delta = nearest(events, 108)
        self.assertIs(event, events[1])
        self.assertEqual(delta, 2)
        self.assertIsNone(nearest(events, 0, topic="/camera/cam0/image"))


def load_example():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "make_synthetic_session", REPO_ROOT / "examples" / "make_synthetic_session.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_cli(*args):
    import subprocess
    import sys
    return subprocess.run([sys.executable, "-m", "scorbot.session", *map(str, args)],
                          cwd=REPO_ROOT, capture_output=True, text=True)


class CliAndExampleTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_synthetic_session_covers_every_topic_kind_without_errors(self):
        from scorbot.session.replay import load_session
        path = load_example().write_synthetic_session(self.root)
        session = load_session(path)
        self.assertEqual(session.errors, [])
        topics = {e["topic"] for e in session.events}
        for topic in ("/robot/state", "/robot/command", "/robot/command_result",
                      "/camera/cam0/image", "/camera/cam0/detections",
                      "/operator/decision", "/session/fault", "/session/note"):
            self.assertIn(topic, topics)
        image = next(e for e in session.events if e["topic"] == "/camera/cam0/image")
        import base64
        self.assertTrue(base64.b64decode(image["payload"]["data"]).startswith(b"\x89PNG"))

    def test_cli_prints_data_source_banner_timeline_and_viewer_hint(self):
        path = load_example().write_synthetic_session(self.root)
        result = run_cli(path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SYNTHETIC", result.stdout)
        self.assertIn("/robot/command", result.stdout)
        self.assertIn("Foxglove", result.stdout)

    def test_cli_limit_caps_timeline_rows(self):
        path = load_example().write_synthetic_session(self.root)
        result = run_cli(path, "--limit", "2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("more events not shown", result.stdout)

    def test_cli_exits_1_on_integrity_errors(self):
        with new_writer(self.root) as writer:
            writer.log_note("a")
            writer._seq += 1
            writer.log_note("b")
        result = run_cli(writer.path)
        self.assertEqual(result.returncode, 1)
        self.assertIn("ERROR", result.stdout)

    def test_cli_missing_path_exits_2_without_traceback(self):
        result = run_cli(self.root / "nope")
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("Traceback", result.stderr + result.stdout)

    def test_package_exports_the_documented_names(self):
        from scorbot.session import SessionError, SessionWriter, load_session, nearest
        self.assertTrue(all((SessionError, SessionWriter, load_session, nearest)))

    def test_importing_session_package_loads_no_hardware_code(self):
        import subprocess
        import sys
        code = ("import sys, scorbot.session, scorbot.session.record, scorbot.session.replay;"
                "print('usb' in sys.modules or 'openScorbot' in sys.modules)")
        result = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT,
                                capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), "False", result.stderr)


class FlushProxy:
    """Wrap the writer's file so the Nth flush raises (after the bytes were written)."""

    def __init__(self, stream, fail_on, error):
        self._stream, self._fail_on, self._error, self.calls = stream, fail_on, error, 0

    def flush(self):
        self._stream.flush()
        self.calls += 1
        if self.calls == self._fail_on:
            raise self._error

    def __getattr__(self, name):
        return getattr(self._stream, name)


class ReviewFixTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_ctrl_c_after_a_write_does_not_duplicate_seq(self):
        from scorbot.session.replay import load_session
        with self.assertRaises(KeyboardInterrupt):
            with new_writer(self.root) as writer:
                writer._stream = FlushProxy(writer._stream, 3, KeyboardInterrupt())
                for i in range(10):
                    writer.log_note(f"n{i}")
        session = load_session(writer.path)
        self.assertEqual(session.errors, [], [f.message for f in session.errors])
        self.assertEqual(session.events[-1]["topic"], "/session/fault")

    def test_write_failure_breaks_the_writer(self):
        from scorbot.session.record import SessionError
        writer = new_writer(self.root)
        writer._stream = FlushProxy(writer._stream, 1, OSError("disk full"))
        with self.assertRaises(SessionError):
            writer.log_note("a")
        with self.assertRaises(SessionError):
            writer.log_note("b")
        writer.close()
        meta = json.loads((writer.path / "metadata.json").read_text())
        self.assertFalse(meta["closed_cleanly"])

    def test_relabelled_sidecar_data_source_is_an_error_and_ignored(self):
        from scorbot.session.replay import load_session
        with new_writer(self.root, data_source="simulated") as writer:
            writer.log_note("x")
        meta_path = writer.path / "metadata.json"
        meta = json.loads(meta_path.read_text())
        meta["data_source"] = "real"
        meta_path.write_text(json.dumps(meta))
        session = load_session(writer.path)
        self.assertEqual(session.metadata["data_source"], "simulated")
        self.assertTrue(any("data_source" in f.message for f in session.errors))

    def test_length_field_damage_in_closed_session_is_an_error(self):
        from scorbot.session.replay import load_session
        with new_writer(self.root) as writer:
            for i in range(40):
                writer.log_note(f"note {i:03d}")
        mcap = writer.mcap_path
        data = bytearray(mcap.read_bytes())
        marker = data.find(b"note 010")
        # Walk back to this message record's opcode (0x05) and flip a bit in its length.
        start = data.rfind(b"\x05", 0, marker - 30)
        while int.from_bytes(data[start + 1:start + 9], "little") > 4096:
            start = data.rfind(b"\x05", 0, start)
        data[start + 4] ^= 0x01
        mcap.write_bytes(bytes(data))
        session = load_session(writer.path)
        self.assertNotEqual(session.errors, [], [f.message for f in session.findings])

    def test_lone_finished_mcap_is_not_reported_as_unclosed(self):
        from scorbot.session.replay import load_session
        with new_writer(self.root) as writer:
            writer.log_note("x")
        (writer.path / "metadata.json").unlink()
        session = load_session(writer.mcap_path)
        self.assertEqual(session.findings, [], [f.message for f in session.findings])

    def test_cli_output_survives_non_ascii_text_when_piped(self):
        import os
        import subprocess
        import sys
        with new_writer(self.root) as writer:
            writer.log_note("base Δ=+30° → ok ✓")
        env = {k: v for k, v in os.environ.items()
               if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
        result = subprocess.run([sys.executable, "-m", "scorbot.session", str(writer.path)],
                                cwd=REPO_ROOT, capture_output=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))

    def test_metadata_records_clock_resolution(self):
        with new_writer(self.root) as writer:
            clock = writer.metadata["clock"]
        self.assertIn("monotonic_implementation", clock)
        self.assertGreater(clock["monotonic_resolution_s"], 0)


if __name__ == "__main__":
    unittest.main()
