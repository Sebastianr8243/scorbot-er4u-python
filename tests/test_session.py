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


if __name__ == "__main__":
    unittest.main()
