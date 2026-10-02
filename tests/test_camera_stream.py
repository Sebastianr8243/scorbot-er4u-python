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


def record(root, frames=3, close=True, data_source="simulated"):
    from scorbot.camera.stream import CameraStream
    session = new_session(root, data_source=data_source)
    stream = CameraStream.create(session, "wrist")
    for n in range(frames):
        stream.log_frame(n, bytes([n]) * 4, width=2, height=2,
                         observed_monotonic_ns=1_000 + n)
    if close:
        stream.close()
    session.close()
    return session.path, stream


class CameraStreamReaderTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_clean_stream_scans_without_image_bytes(self):
        from scorbot.camera.stream import scan_stream
        folder, _ = record(self.root)
        index = scan_stream(folder, "wrist")
        self.assertEqual(index.findings, [])
        self.assertEqual([f["frame_number"] for f in index.frames], [0, 1, 2])
        self.assertEqual([f["observed_monotonic_ns"] for f in index.frames],
                         [1000, 1001, 1002])
        self.assertTrue(all("data" not in f for f in index.frames))

    def test_iter_frames_yields_bytes_in_order(self):
        from scorbot.camera.stream import iter_frames
        folder, _ = record(self.root)
        frames = list(iter_frames(folder, "wrist"))
        self.assertEqual([f.data for f in frames], [b"\x00" * 4, b"\x01" * 4, b"\x02" * 4])
        self.assertEqual(frames[1].observed_monotonic_ns, 1001)

    def test_no_sidecar_is_not_closed_cleanly(self):
        from scorbot.camera.stream import scan_stream
        folder, stream = record(self.root, close=False)
        stream._stream.close()  # crash: no footer, no sidecar
        index = scan_stream(folder, "wrist")
        self.assertEqual(index.errors, [])
        self.assertTrue(any("not closed cleanly" in f.message for f in index.warnings))
        self.assertEqual(len(index.frames), 3)

    def test_write_error_and_count_mismatch_are_errors(self):
        from scorbot.camera.stream import scan_stream
        folder, stream = record(self.root)
        sidecar = json.loads(stream.sidecar_path.read_text(encoding="utf-8"))
        sidecar.update(closed_cleanly=False, write_error="disk full", event_count=5)
        stream.sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
        messages = [f.message for f in scan_stream(folder, "wrist").errors]
        self.assertTrue(any("disk full" in m for m in messages))
        self.assertTrue(any("5" in m and "3" in m for m in messages))

    def test_foreign_stream_is_an_error(self):
        import shutil
        from scorbot.camera.stream import scan_stream
        first, _ = record(self.root / "a")
        second, _ = record(self.root / "b")
        for suffix in (".mcap", ".json"):
            shutil.copy(first / f"camera-wrist{suffix}", second / f"camera-wrist{suffix}")
        errors = scan_stream(second, "wrist").errors
        self.assertTrue(any("different session" in f.message for f in errors))

    def test_sidecar_from_another_stream_is_an_error(self):
        import shutil
        from scorbot.camera.stream import scan_stream
        first, _ = record(self.root / "a")
        second, _ = record(self.root / "b")
        shutil.copy(first / "camera-wrist.json", second / "camera-wrist.json")
        errors = scan_stream(second, "wrist").errors
        self.assertTrue(any("sidecar" in f.message for f in errors))

    def test_stream_without_parent_metadata_is_an_error(self):
        from scorbot.camera.stream import scan_stream
        folder, _ = record(self.root)
        (folder / "metadata.json").unlink()
        (folder / "session.mcap").unlink()
        errors = scan_stream(folder, "wrist").errors
        self.assertTrue(any("parent session" in f.message for f in errors))

    def test_parent_identity_falls_back_to_session_mcap(self):
        from scorbot.camera.stream import scan_stream
        folder, _ = record(self.root)
        (folder / "metadata.json").unlink()
        self.assertEqual(scan_stream(folder, "wrist").errors, [])

    def test_declared_camera_without_file_is_listed(self):
        from scorbot.camera.stream import missing_streams
        session = new_session(self.root, camera_ids=("wrist", "top"))
        session.close()
        self.assertEqual(missing_streams(session.path), ["top", "wrist"])


class StreamListingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_camera_streams_are_not_strays(self):
        from scorbot.session.analysis import find_sessions
        folder, _ = record(self.root)
        (folder / "export.mcap").write_bytes(b"")
        search = find_sessions([self.root])
        self.assertEqual([p.name for p in search.not_sessions], ["export.mcap"])
        self.assertEqual(len(search.sessions), 1)

    def test_list_shows_each_camera_under_its_session(self):
        import contextlib
        import io
        from scorbot.session.__main__ import main
        folder, _ = record(self.root)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["list", str(self.root)])
        self.assertEqual(code, 0, out.getvalue())
        lines = out.getvalue().splitlines()
        session_line = next(i for i, line in enumerate(lines) if folder.name in line)
        self.assertIn("camera wrist", lines[session_line + 1])
        self.assertIn(" 3 ", lines[session_line + 1])
        self.assertNotIn("NOT A SESSION", out.getvalue())

    def test_undeclared_camera_file_is_flagged_in_list(self):
        import contextlib
        import io
        import shutil
        from scorbot.session.__main__ import main
        folder, _ = record(self.root)
        shutil.copy(folder / "camera-wrist.mcap", folder / "camera-stray.mcap")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["list", str(self.root)])
        self.assertIn("camera stray", out.getvalue())
        self.assertIn("not declared", out.getvalue())
        self.assertEqual(code, 1)

    def test_frames_inside_session_mcap_are_not_a_missing_stream(self):
        import contextlib
        import io
        from scorbot.session.__main__ import main
        with new_session(self.root, camera_ids=("cam0",)) as session:
            session.log_frame("cam0", 0, b"x", format="png", width=1, height=1,
                              observed_monotonic_ns=1)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(["list", str(self.root)])
        self.assertNotIn("no stream file", out.getvalue())


if __name__ == "__main__":
    unittest.main()
