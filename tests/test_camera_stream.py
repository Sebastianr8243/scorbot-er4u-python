"""Per-camera stream files (scorbot.camera.stream). No camera hardware."""

import json
from pathlib import Path
import tempfile
import unittest

from scorbot.session import SessionError, SessionWriter


def new_session(root, camera_ids=("wrist",), data_source="simulated"):
    return SessionWriter.create(root, data_source=data_source, robot_id="arm-1",
                                camera_ids=list(camera_ids))


class CameraStreamWriterTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_creates_its_own_file_with_parent_identity(self):
        from scorbot.camera.stream import CameraStream
        with new_session(self.root) as session:
            stream = CameraStream.create(session, "wrist", {"backend": "fake"})
            stream.log_frame(0, b"jpg", width=2, height=1, observed_monotonic_ns=10)
            stream.close()
            self.assertEqual(stream.mcap_path, session.path / "camera-wrist.mcap")
            sidecar = json.loads(stream.sidecar_path.read_text(encoding="utf-8"))
            self.assertEqual((sidecar["closed_cleanly"], sidecar["event_count"]), (True, 1))
            self.assertEqual(stream.metadata["session_id"], session.metadata["session_id"])
            self.assertEqual(stream.metadata["clock"], session.metadata["clock"])
            self.assertEqual(stream.metadata["data_source"], "simulated")
            self.assertEqual(stream.metadata["camera"], {"backend": "fake"})
            self.assertEqual(stream.metadata["stream_version"], 1)
        meta = json.loads((session.path / "metadata.json").read_text(encoding="utf-8"))
        self.assertNotIn("streams", meta)

    def test_rejects_undeclared_or_invalid_camera_ids(self):
        from scorbot.camera.stream import CameraStream
        with new_session(self.root) as session:
            for bad in ("other", "a/b", ""):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    CameraStream.create(session, bad)
            self.assertEqual(sorted(p.name for p in session.path.glob("camera-*")), [])

    def test_never_overwrites(self):
        from scorbot.camera.stream import CameraStream
        with new_session(self.root) as session:
            CameraStream.create(session, "wrist").close()
            with self.assertRaises(FileExistsError):
                CameraStream.create(session, "wrist")

    def test_only_frames_are_accepted(self):
        from scorbot.camera.stream import CameraStream
        with new_session(self.root) as session:
            stream = CameraStream.create(session, "wrist")
            for call in (lambda: stream.log_state({}), lambda: stream.log_note("x"),
                         lambda: stream.log_command("jog", {}),
                         lambda: stream.log_fault("x")):
                with self.assertRaises(SessionError):
                    call()
            with self.assertRaises(SessionError):
                stream.log_frame(0, b"x", width=1, height=1, observed_monotonic_ns=None)
            stream.close()

    def test_non_json_settings_create_no_file(self):
        from scorbot.camera.stream import CameraStream
        with new_session(self.root) as session:
            with self.assertRaises(ValueError):
                CameraStream.create(session, "wrist", {"exposure": float("nan")})
            self.assertFalse((session.path / "camera-wrist.mcap").exists())

    def test_stream_failure_leaves_robot_recording_working(self):
        from scorbot.camera.stream import CameraStream
        from scorbot.session.replay import load_session
        with new_session(self.root) as session:
            stream = CameraStream.create(session, "wrist")
            stream._stream.close()  # simulate the disk failing under the camera
            with self.assertRaises(SessionError):
                stream.log_frame(0, b"x", width=1, height=1, observed_monotonic_ns=1)
            stream.close()
            session.log_note("robot recording continues")
        sidecar = json.loads(stream.sidecar_path.read_text(encoding="utf-8"))
        self.assertFalse(sidecar["closed_cleanly"])
        self.assertIn("write_error", sidecar)
        self.assertEqual(load_session(session.path).errors, [])

    def test_close_twice_is_harmless(self):
        from scorbot.camera.stream import CameraStream
        with new_session(self.root) as session:
            stream = CameraStream.create(session, "wrist")
            stream.close()
            stream.close()


if __name__ == "__main__":
    unittest.main()
