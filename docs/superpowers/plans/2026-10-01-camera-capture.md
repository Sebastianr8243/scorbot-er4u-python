# Camera Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record webcam frames into their own per-camera MCAP file next to a session, on the session clock, isolated from the robot recording, with health data and a camera check command.

**Architecture:** `CameraStream` subclasses `SessionWriter` to reuse its crash-safe MCAP writer, but writes `camera-<id>.mcap` plus a close-time sidecar and holds no reference to the parent. `CameraRecorder` runs a reader thread and a writer thread joined by a byte-bounded queue; the writer thread is the only user of the stream and closes it. `scan_stream`/`iter_frames` read streams without keeping image bytes.

**Tech Stack:** Python 3.10+, `mcap`, `numpy`, optional `opencv-python-headless`, `unittest`.

**Spec:** `docs/superpowers/specs/2026-10-01-camera-capture-design.md`

## Global Constraints

- Python >= 3.10; tests use `unittest`; optional deps skip when missing.
- Run everything with `.venv/Scripts/python.exe` (desk machine) and lint with `.venv/Scripts/ruff.exe check .`.
- Importing `scorbot.camera` must not import `cv2`.
- Strict JSON everywhere (`allow_nan=False`).
- The camera never writes `metadata.json` or `session.mcap` and never touches the robot or its fault latch.
- `stop()` returns within its timeout and never raises.
- Stream file: `camera-<camera_id>.mcap`, sidecar `camera-<camera_id>.json`, embedded metadata name `scorbot.stream`, `stream_version: 1`.
- Defaults: 640x480, 30 fps, JPEG quality 90, `queue_bytes` 64 MiB, `max_latency_s` 1.0, `stop(timeout_s=3.0)`.
- No hardware in tests; no wall-clock timing assertions in recorder tests.
- Commits: imperative sentence-case subject, body explains why, no AI attribution.

## Review Focus

- A camera id containing characters outside the topic pattern: refused before any file is created (Task 2 test `test_rejects_undeclared_or_invalid_camera_ids`).
- Settings dict containing NaN/inf from `cap.get`: stored as None so the stream metadata stays strict JSON (Task 5 test `test_non_finite_read_back_is_none`).
- `read()` returning `(False, None)` repeatedly (unplugged camera): recorder fails after 30 consecutive misses instead of spinning (Task 6 test `test_repeated_empty_reads_fail_the_recorder`).
- `stop()` called before `start()`: returns without raising (Task 6 test `test_stop_before_start_is_harmless`).
- Session folder copied elsewhere with only the camera file: `scan_stream` reports missing parent identity instead of crashing (Task 3 test `test_stream_without_parent_metadata_is_an_error`).

---

### Task 1: Require frame timestamps and let the reader handle other files

**Files:**
- Modify: `scorbot/session/record.py` (`log_frame`)
- Modify: `scorbot/session/replay.py` (`_read_mcap`)
- Modify: `tests/test_session.py:173`
- Test: `tests/test_session.py`

**Interfaces:**
- Produces: `SessionWriter.log_frame(...)` raises `SessionError` when `observed_monotonic_ns is None`.
- Produces: `replay._read_mcap(stream, findings, *, metadata_name="scorbot.session", drop_data=False)`; with `drop_data=True` each event's `payload` has no `"data"` key.

- [ ] **Step 1: Write the failing tests** (append to the main `SessionWriter` test class in `tests/test_session.py`)

```python
    def test_frame_without_capture_time_is_refused(self):
        from scorbot.session import SessionError
        with new_writer(self.root, camera_ids=["cam0"]) as writer:
            with self.assertRaises(SessionError):
                writer.log_frame("cam0", 0, b"x", format="png", width=1, height=1)

    def test_reader_can_drop_image_bytes(self):
        from scorbot.session.replay import _read_mcap
        with new_writer(self.root, camera_ids=["cam0"]) as writer:
            writer.log_frame("cam0", 0, b"abc", format="png", width=1, height=1,
                             observed_monotonic_ns=5)
        with open(writer.mcap_path, "rb") as stream:
            events, embedded, finished, failure = _read_mcap(stream, [], drop_data=True)
        self.assertNotIn("data", events[0]["payload"])
        self.assertEqual(embedded["session_id"], writer.metadata["session_id"])
        self.assertTrue(finished)
        self.assertIsNone(failure)
```

Also change the existing call at `tests/test_session.py:173` to pass `observed_monotonic_ns=1`.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m unittest tests.test_session -k frame_without -k drop_image`
Expected: FAIL (no SessionError raised; `drop_data` unexpected keyword).

- [ ] **Step 3: Implement**

In `record.py` `log_frame`, first lines of the body:

```python
        if observed_monotonic_ns is None:
            raise SessionError("A camera frame needs its capture time "
                               "(observed_monotonic_ns, taken right after read())")
```

and replace the stamp line with `stamp = self._epoch(observed_monotonic_ns)`.

In `replay.py`, change the signature and two branches of `_read_mcap`:

```python
def _read_mcap(stream, findings: list[Finding], *, metadata_name: str = "scorbot.session",
               drop_data: bool = False):
    ...
            elif isinstance(record, Message):
                event = _decode(record, channels, schema_names, last_good, findings)
                if event is not None:
                    if drop_data:
                        event["payload"].pop("data", None)
                    events.append(event)
            elif isinstance(record, Metadata) and record.name == metadata_name:
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python.exe -m unittest tests.test_session tests.test_analysis`
Expected: PASS. Also run `.venv/Scripts/python.exe examples/make_synthetic_session.py --root <scratch dir>`: exits 0 (it already passes the stamp).

- [ ] **Step 5: Commit**

`git commit -m "Require a capture time for every recorded camera frame"` with a body explaining the second clock call it removes.

---

### Task 2: CameraStream writer

**Files:**
- Create: `scorbot/camera/__init__.py`, `scorbot/camera/stream.py`
- Modify: `pyproject.toml` (packages list adds `"scorbot.camera"`)
- Test: `tests/test_camera_stream.py`

**Interfaces:**
- Consumes: `SessionWriter` (`path`, `metadata`, `_lock`, `_seq`, `_broken`, `_closed`, `_writer`, `_stream`, `log_frame`), `record._write_json_atomic`, `schemas.camera_topic`.
- Produces: `stream_name(camera_id) -> str`; `CameraStream.create(session, camera_id, settings=None) -> CameraStream`; `CameraStream.log_frame(frame_number, data, *, width, height, observed_monotonic_ns, format="jpeg") -> int`; `CameraStream.close()`; attributes `camera_id`, `mcap_path`, `sidecar_path`, `health_summary` (dict or None, written into the sidecar).

- [ ] **Step 1: Write the failing tests** (`tests/test_camera_stream.py`)

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m unittest tests.test_camera_stream`
Expected: FAIL (`No module named 'scorbot.camera'`).

- [ ] **Step 3: Implement**

`scorbot/camera/__init__.py`:

```python
"""Webcam capture into sessions. Importing this package does not import OpenCV."""
```

`scorbot/camera/stream.py`:

```python
"""One camera's frames in their own MCAP file, sharing nothing with the robot recording.

``camera-<id>.mcap`` holds only ``/camera/<id>/image`` messages; the
``camera-<id>.json`` sidecar is written once, when the stream closes. The
stream copies the parent session's identity (id, data source, clock) at
creation and keeps no reference to the parent, so a camera fault can never
stop or delay the robot recording. See
docs/superpowers/specs/2026-10-01-camera-capture-design.md.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path

from mcap.writer import Writer

from ..session import schemas
from ..session.record import SessionError, SessionWriter, _write_json_atomic

STREAM_VERSION = 1
STREAM_METADATA = "scorbot.stream"


def stream_name(camera_id: str) -> str:
    if not isinstance(camera_id, str) or not camera_id:
        raise ValueError("camera_id must be a non-empty string")
    schemas.camera_topic(camera_id, "image")  # raises ValueError for bad ids
    return f"camera-{camera_id}"


def _robot_only(name):
    def refuse(self, *args, **kwargs):
        raise SessionError(f"{name} is not allowed on a camera stream; robot data "
                           "belongs in session.mcap")
    return refuse


class CameraStream(SessionWriter):
    """Append-only frames for one camera. Safe to share between threads."""

    camera_id: str
    sidecar_path: Path
    health_summary: dict | None = None

    @classmethod
    def create(cls, session: SessionWriter, camera_id: str,
               settings: dict | None = None) -> "CameraStream":
        name = stream_name(camera_id)
        parent = session.metadata
        if camera_id not in parent.get("camera_ids", []):
            raise ValueError(f"Camera {camera_id!r} was not declared when the session "
                             f"was created (camera_ids={parent.get('camera_ids')})")
        identity = {"schema_version": schemas.SCHEMA_VERSION,
                    "stream_version": STREAM_VERSION,
                    "session_id": parent["session_id"], "camera_id": camera_id,
                    "data_source": parent["data_source"], "clock": dict(parent["clock"]),
                    "camera": dict(settings or {})}
        try:
            encoded = json.dumps(identity, allow_nan=False)
        except ValueError as error:
            raise ValueError(f"Camera settings are not strict JSON: {error}") from None
        mcap_path = Path(session.path) / f"{name}.mcap"
        stream = open(mcap_path, "xb")
        writer = Writer(stream, use_chunking=False, enable_data_crcs=True)
        writer.start(profile="", library=f"scorbot-camera-stream/{STREAM_VERSION}")
        writer.add_metadata(STREAM_METADATA, {"json": encoded})
        stream.flush()
        self = cls(Path(session.path), identity, stream, writer)
        self.camera_id = camera_id
        self.mcap_path = mcap_path
        self.sidecar_path = Path(session.path) / f"{name}.json"
        return self

    def log_frame(self, frame_number: int, data: bytes, *, width: int, height: int,
                  observed_monotonic_ns: int | None, format: str = "jpeg") -> int:
        return super().log_frame(self.camera_id, frame_number, data, format=format,
                                 width=width, height=height,
                                 observed_monotonic_ns=observed_monotonic_ns)

    log_state = _robot_only("log_state")
    log_command = _robot_only("log_command")
    log_command_result = _robot_only("log_command_result")
    log_detection = _robot_only("log_detection")
    log_decision = _robot_only("log_decision")
    log_fault = _robot_only("log_fault")
    log_note = _robot_only("log_note")

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            finish_error = None
            try:
                if self._broken is None:
                    self._writer.finish()
                    self._stream.flush()
                    os.fsync(self._stream.fileno())
            except Exception as error:
                finish_error = f"{type(error).__name__}: {error}"
            finally:
                try:
                    self._stream.close()
                except Exception as error:
                    finish_error = finish_error or f"{type(error).__name__}: {error}"
            event_count = self._seq
        error = self._broken or finish_error
        sidecar = {"stream_version": STREAM_VERSION,
                   "session_id": self.metadata["session_id"], "camera_id": self.camera_id,
                   "closed_cleanly": error is None, "event_count": event_count,
                   "ended_utc": datetime.now(timezone.utc).isoformat()}
        if error is not None:
            sidecar["write_error"] = error
        if self.health_summary is not None:
            sidecar["health_summary"] = self.health_summary
        _write_json_atomic(self.sidecar_path, sidecar)
```

`pyproject.toml`: packages list becomes `["scorbot", "scorbot.session", "scorbot.lab", "scorbot.transport", "scorbot.camera", "openScorbot"]`.

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python.exe -m unittest tests.test_camera_stream tests.test_session`
Expected: PASS.

- [ ] **Step 5: Commit** — "Write each camera to its own stream file next to the session".

---

### Task 3: Stream reader (`scan_stream`, `iter_frames`)

**Files:**
- Modify: `scorbot/camera/stream.py`
- Test: `tests/test_camera_stream.py` (new class)

**Interfaces:**
- Consumes: `replay._read_mcap(..., metadata_name=, drop_data=)`, `replay._classify_tail`, `replay._ends_with_magic`, `replay._check_events`, `replay._read_sidecar`, `replay.Finding`.
- Produces: `StreamIndex` dataclass (`camera_id`, `metadata: dict`, `frames: list[dict]`, `findings: list[Finding]`, properties `errors`, `warnings`); `scan_stream(session_dir, camera_id) -> StreamIndex`; `iter_frames(session_dir, camera_id)` yielding `Frame(frame_number, observed_monotonic_ns, data: bytes, format)`; `missing_streams(session_dir) -> list[str]` (declared camera ids with no file).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_camera_stream.py`)

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m unittest tests.test_camera_stream.CameraStreamReaderTests`
Expected: FAIL (ImportError on `scan_stream`).

- [ ] **Step 3: Implement** (append to `scorbot/camera/stream.py`; add imports
`import base64`, `from dataclasses import dataclass, field`,
`from mcap.records import Channel, Message`, `from mcap.stream_reader import StreamReader`,
and `from ..session import replay`)

```python
_IDENTITY = ("session_id", "data_source", "clock")


@dataclass
class StreamIndex:
    camera_id: str
    metadata: dict
    frames: list[dict]
    findings: list = field(default_factory=list)

    @property
    def errors(self):
        return [f for f in self.findings if f.level == "error"]

    @property
    def warnings(self):
        return [f for f in self.findings if f.level == "warning"]


@dataclass(frozen=True)
class Frame:
    frame_number: int
    observed_monotonic_ns: int
    data: bytes
    format: str


def _parent_identity(folder: Path, findings) -> dict | None:
    sidecar = replay._read_sidecar(folder / "metadata.json", findings)
    if sidecar is not None:
        return sidecar
    mcap = folder / "session.mcap"
    if mcap.is_file():
        with open(mcap, "rb") as stream:
            _events, embedded, _finished, _failure = replay._read_mcap(stream, [],
                                                                      drop_data=True)
        return embedded
    return None


def _read_stream_sidecar(path: Path, findings) -> dict | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        findings.append(replay.Finding("error", f"{path.name} is not valid JSON: {error}"))
        return None
    return value if isinstance(value, dict) else None


def scan_stream(session_dir, camera_id: str) -> StreamIndex:
    """Check one camera stream and index its frames without keeping image bytes."""
    folder = Path(session_dir)
    name = stream_name(camera_id)
    path = folder / f"{name}.mcap"
    if not path.is_file():
        raise FileNotFoundError(f"No {name}.mcap in {folder}")
    findings: list = []
    sidecar = _read_stream_sidecar(folder / f"{name}.json", findings)
    with open(path, "rb") as stream:
        events, embedded, finished, failure = replay._read_mcap(
            stream, findings, metadata_name=STREAM_METADATA, drop_data=True)
        if failure is not None:
            claims_closed = (replay._ends_with_magic(stream)
                             or (sidecar or {}).get("closed_cleanly") is True)
            replay._classify_tail(stream, *failure, claims_closed, findings)
    metadata = embedded or {}
    if embedded is None:
        findings.append(replay.Finding("error", "No embedded stream metadata"))
    elif embedded.get("stream_version") != STREAM_VERSION:
        findings.append(replay.Finding(
            "error", f"Unsupported stream_version {embedded.get('stream_version')!r}"))
    elif embedded.get("camera_id") != camera_id:
        findings.append(replay.Finding("error", "Stream metadata names camera "
                                                f"{embedded.get('camera_id')!r}"))
    parent = _parent_identity(folder, findings)
    if parent is None:
        findings.append(replay.Finding("error", "No parent session metadata to check "
                                                "this stream against"))
    elif embedded is not None:
        differing = [k for k in _IDENTITY if parent.get(k) != embedded.get(k)]
        if differing:
            findings.append(replay.Finding(
                "error", f"Stream belongs to a different session (differs on "
                         f"{', '.join(differing)}); frames must not be used"))
    topic = schemas.camera_topic(camera_id, "image")
    stray = sorted({e["topic"] for e in events if e["topic"] != topic})
    if stray:
        findings.append(replay.Finding("error", f"Unexpected topics in stream: {stray}"))
    replay._check_events(events, findings)
    if sidecar is None or not finished:
        findings.append(replay.Finding("warning", "Camera stream was not closed cleanly "
                                                  "(crash or stuck stop)"))
    if sidecar is not None:
        if sidecar.get("closed_cleanly") is False:
            findings.append(replay.Finding(
                "error", f"Camera stream write failed: {sidecar.get('write_error')}"))
        count = sidecar.get("event_count")
        if isinstance(count, int) and count != len(events):
            findings.append(replay.Finding(
                "error", f"{name}.json records {count} frames but {len(events)} "
                         "could be read"))
    frames = [{"seq": e["seq"],
               "frame_number": e["payload"]["_rec"].get("frame_number"),
               "observed_monotonic_ns": e["payload"]["_rec"].get("observed_monotonic_ns"),
               "logged_monotonic_ns": e["payload"]["_rec"].get("logged_monotonic_ns"),
               "width": e["payload"]["_rec"].get("width"),
               "height": e["payload"]["_rec"].get("height")}
              for e in events if e["topic"] == topic]
    return StreamIndex(camera_id, metadata, frames, findings)


def iter_frames(session_dir, camera_id: str):
    """Yield each frame's bytes in file order; stops quietly at a damaged tail.

    Run ``scan_stream`` first: it reports the damage this generator skips.
    """
    path = Path(session_dir) / f"{stream_name(camera_id)}.mcap"
    with open(path, "rb") as stream:
        try:
            for record in StreamReader(stream, validate_crcs=True).records:
                if isinstance(record, Message):
                    payload = json.loads(record.data)
                    rec = payload.get("_rec", {})
                    yield Frame(rec.get("frame_number"), rec.get("observed_monotonic_ns"),
                                base64.b64decode(payload["data"]), payload.get("format"))
        except Exception:
            return


def missing_streams(session_dir) -> list[str]:
    """Camera ids the session declared that have no stream file."""
    folder = Path(session_dir)
    findings: list = []
    parent = _parent_identity(folder, findings) or {}
    return sorted(c for c in parent.get("camera_ids", [])
                  if not (folder / f"{stream_name(c)}.mcap").is_file())
```

Remove the unused `Channel` import if ruff flags it.

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python.exe -m unittest tests.test_camera_stream`
Expected: PASS.

- [ ] **Step 5: Commit** — "Read camera streams without loading their images".

---

### Task 4: List streams with their session

**Files:**
- Modify: `scorbot/session/analysis.py` (`find_sessions`)
- Modify: `scorbot/session/__main__.py` (`_list`)
- Test: `tests/test_analysis.py` (or the file holding `find_sessions` tests; grep `find_sessions`)

**Interfaces:**
- Consumes: `scorbot.camera.stream.scan_stream`, `missing_streams` (imported lazily inside `_list`).
- Produces: `find_sessions` no longer reports `camera-*.mcap` next to a `session.mcap` as `not_sessions`.

- [ ] **Step 1: Write the failing test**

```python
    def test_camera_streams_are_not_strays(self):
        from scorbot.camera.stream import CameraStream
        from scorbot.session import SessionWriter
        from scorbot.session.analysis import find_sessions
        session = SessionWriter.create(self.root, data_source="simulated",
                                       robot_id="arm-1", camera_ids=["wrist"])
        CameraStream.create(session, "wrist").close()
        session.close()
        (session.path / "export.mcap").write_bytes(b"")
        search = find_sessions([self.root])
        self.assertEqual([p.name for p in search.not_sessions], ["export.mcap"])
        self.assertEqual(len(search.sessions), 1)
```

(Place it in the class that tests `find_sessions`; give it a `self.root` temp dir like its neighbours.)

- [ ] **Step 2: Run to verify failure** — Expected: `camera-wrist.mcap` appears in `not_sessions`.

- [ ] **Step 3: Implement**

In `find_sessions`:

```python
            strays = sorted(p for p in path.rglob("*.mcap")
                            if p.name != "session.mcap"
                            and not (p.name.startswith("camera-")
                                     and (p.parent / "session.mcap").is_file()))
```

Update its docstring: camera stream files next to a `session.mcap` belong to that session.

In `_list`, after appending each session's row, add one row per stream:

```python
        from ..camera.stream import missing_streams, scan_stream
        for camera_id in meta.get("camera_ids", []):
            stream_file = Path(session.path) / f"camera-{camera_id}.mcap"
            if not stream_file.is_file():
                continue
            index = scan_stream(session.path, camera_id)
            stream_status = (f"ERROR {len(index.errors)}" if index.errors else
                             f"WARN {len(index.warnings)}" if index.warnings else "OK")
            broken += bool(index.errors)
            rows.append((stream_status, str(meta.get("data_source", "?")).upper(),
                         f"  camera {camera_id}", meta.get("started_utc") or "-",
                         meta.get("robot_id") or "-", len(index.frames), "-", "frames"))
        for camera_id in missing_streams(session.path):
            rows.append(("WARN 1", str(meta.get("data_source", "?")).upper(),
                         f"  camera {camera_id}", meta.get("started_utc") or "-",
                         meta.get("robot_id") or "-", 0, "-", "declared, no stream file"))
```

Rows are sorted by start time; stream rows share their session's start time so they stay next to it (use a stable sort key `(row[3], row[2].startswith("  "))` with `reverse=True` replaced by sorting sessions first: change `rows.sort(key=lambda row: row[3], reverse=True)` to `rows.sort(key=lambda row: (row[3], not row[2].startswith("  ")), reverse=True)`).

- [ ] **Step 4: Run tests** — `.venv/Scripts/python.exe -m unittest tests.test_analysis tests.test_session tests.test_camera_stream`; also run `python -m scorbot.session list <dir>` on a folder from Task 3's helper and check the camera row appears under its session.

- [ ] **Step 5: Commit** — "List camera streams under their session".

---

### Task 5: Frame sources and the camera extra

**Files:**
- Create: `scorbot/camera/source.py`
- Modify: `pyproject.toml` (`camera` extra, `dev` gets `opencv-python-headless>=4.9,<5`), `uv.lock` (`.venv/Scripts/uv.exe lock`)
- Test: `tests/test_camera_source.py`

**Interfaces:**
- Produces: `FrameSource` protocol; `FakeSource(width=64, height=48, fps=30.0, pace=False, fail_at=None, block_at=None, release_on_close=True)` with `open()`, `read() -> (bool, numpy.ndarray | None)`, `settings() -> dict`, `close()`; `decode_frame_number(image) -> int`; `OpenCVSource(index_or_path=0, *, width=640, height=480, fps=30.0, focus=None, exposure=None, warmup_s=1.0, cv2_module=None)` with the same methods; `settings()` shape: `{"backend": str, "requested": {...}, "set_ok": {...}, "read_back": {...}, "actual": {"width", "height", "measured_fps"}}`.

- [ ] **Step 1: Write the failing tests** (`tests/test_camera_source.py`)

```python
"""Frame sources. No camera hardware: OpenCV is replaced by a fake module."""

import json
import math
import threading
import unittest

import numpy as np

from scorbot.camera.source import FakeSource, OpenCVSource, decode_frame_number


class FakeCapture:
    def __init__(self, index, backend, module):
        self.module, self.props, self.opened = module, {}, True
        module.opened_with = (index, backend)

    def isOpened(self):
        return self.opened

    def set(self, prop, value):
        self.props[prop] = value
        return prop in self.module.accepted

    def get(self, prop):
        return self.module.read_back.get(prop, self.props.get(prop, 0.0))

    def read(self):
        return True, np.zeros((self.module.frame_h, self.module.frame_w, 3), np.uint8)

    def release(self):
        self.opened = False


class FakeCV2:
    CAP_DSHOW, CAP_ANY = 700, 0
    CAP_PROP_FRAME_WIDTH, CAP_PROP_FRAME_HEIGHT, CAP_PROP_FPS = 3, 4, 5
    CAP_PROP_AUTOFOCUS, CAP_PROP_FOCUS = 39, 28
    CAP_PROP_AUTO_EXPOSURE, CAP_PROP_EXPOSURE = 21, 15

    def __init__(self, frame_w=320, frame_h=240, accepted=(3, 4, 5), read_back=None):
        self.frame_w, self.frame_h = frame_w, frame_h
        self.accepted, self.read_back = set(accepted), dict(read_back or {})

    def VideoCapture(self, index, backend):  # noqa: N802 (OpenCV's name)
        return FakeCapture(index, backend, self)


class FakeSourceTests(unittest.TestCase):
    def test_frames_carry_their_number(self):
        source = FakeSource()
        source.open()
        numbers = [decode_frame_number(source.read()[1]) for _ in range(3)]
        self.assertEqual(numbers, [0, 1, 2])
        self.assertEqual(source.settings()["backend"], "fake")

    def test_fail_at_raises(self):
        source = FakeSource(fail_at=1)
        source.open()
        source.read()
        with self.assertRaises(RuntimeError):
            source.read()

    def test_block_at_is_released_by_close(self):
        source = FakeSource(block_at=0)
        source.open()
        result = []
        thread = threading.Thread(target=lambda: result.append(source.read()))
        thread.start()
        source.close()
        thread.join(2)
        self.assertEqual(result, [(False, None)])


class OpenCVSourceTests(unittest.TestCase):
    def test_settings_record_request_set_result_and_raw_read_back(self):
        cv2 = FakeCV2(read_back={39: 2.0, 21: -1.0})
        source = OpenCVSource(1, width=320, height=240, fps=30.0, focus=10.0,
                              exposure=-6.0, warmup_s=0.0, cv2_module=cv2)
        source.open()
        settings = source.settings()
        self.assertEqual(settings["requested"]["width"], 320)
        self.assertTrue(settings["set_ok"]["width"])
        self.assertFalse(settings["set_ok"]["exposure"])
        self.assertEqual(settings["read_back"]["autofocus"], 2.0)  # raw, not interpreted
        self.assertEqual(settings["actual"]["width"], 320)
        self.assertNotIn("accepted", json.dumps(settings))
        json.dumps(settings, allow_nan=False)
        source.close()

    def test_actual_size_is_verified_from_frames(self):
        cv2 = FakeCV2(frame_w=160, frame_h=120)
        source = OpenCVSource(0, width=640, height=480, warmup_s=0.0, cv2_module=cv2)
        source.open()
        self.assertEqual((source.settings()["actual"]["width"],
                          source.settings()["actual"]["height"]), (160, 120))

    def test_non_finite_read_back_is_none(self):
        cv2 = FakeCV2(read_back={5: math.nan})
        source = OpenCVSource(0, warmup_s=0.0, cv2_module=cv2)
        source.open()
        self.assertIsNone(source.settings()["read_back"]["fps"])

    def test_unopened_camera_raises(self):
        cv2 = FakeCV2()
        original = cv2.VideoCapture

        def closed(index, backend):
            capture = original(index, backend)
            capture.opened = False
            return capture
        cv2.VideoCapture = closed
        with self.assertRaises(RuntimeError):
            OpenCVSource(0, warmup_s=0.0, cv2_module=cv2).open()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure** — `.venv/Scripts/python.exe -m unittest tests.test_camera_source` → ImportError.

- [ ] **Step 3: Implement** `scorbot/camera/source.py`

```python
"""Where frames come from: a real webcam through OpenCV, or a deterministic fake.

``OpenCVSource`` follows LeRobot's ``OpenCVCamera`` design (config names,
warm-up) without depending on it. It records what was requested, whether each
``set()`` succeeded and the raw ``get()`` value, and never interprets the raw
value as "accepted": OpenCV's DirectShow getters return backend flags (for
example autofocus) or -1 (auto exposure). What it verifies instead is the
frame size actually delivered and the frame rate measured during warm-up.
"""

from __future__ import annotations

import math
import sys
import threading
import time
from typing import Protocol

import numpy as np


class FrameSource(Protocol):
    def open(self) -> None: ...
    def read(self) -> tuple[bool, np.ndarray | None]: ...
    def settings(self) -> dict: ...
    def close(self) -> None: ...


_BLOCK = 4  # pixels per encoded bit block


def _encode_number(image: np.ndarray, number: int) -> None:
    for bit in range(16):
        value = 255 if (number >> bit) & 1 else 0
        image[0:_BLOCK, bit * _BLOCK:(bit + 1) * _BLOCK, :] = value


def decode_frame_number(image: np.ndarray) -> int:
    return sum(1 << bit for bit in range(16)
               if image[_BLOCK // 2, bit * _BLOCK + _BLOCK // 2, 0] > 127)


class FakeSource:
    """Deterministic frames with their number drawn in the top-left corner."""

    def __init__(self, *, width: int = 64, height: int = 48, fps: float = 30.0,
                 pace: bool = False, fail_at: int | None = None,
                 block_at: int | None = None, release_on_close: bool = True):
        if width < 16 * _BLOCK or height < _BLOCK:
            raise ValueError(f"Fake frames need at least {16 * _BLOCK}x{_BLOCK} pixels")
        self.width, self.height, self.fps = width, height, fps
        self.pace, self.fail_at, self.block_at = pace, fail_at, block_at
        self.release_on_close = release_on_close
        self._next = 0
        self._closed = threading.Event()

    def open(self) -> None:
        self._next = 0
        self._closed.clear()

    def read(self):
        if self._closed.is_set():
            return False, None
        number = self._next
        self._next += 1
        if number == self.fail_at:
            raise RuntimeError(f"Fake camera failure at frame {number}")
        if number == self.block_at:
            if self.release_on_close:
                self._closed.wait()
            else:
                threading.Event().wait()  # never released
            return False, None
        if self.pace:
            time.sleep(1.0 / self.fps)
        image = np.full((self.height, self.width, 3), 64, np.uint8)
        _encode_number(image, number)
        return True, image

    def settings(self) -> dict:
        return {"backend": "fake", "requested": {"width": self.width, "height": self.height,
                                                 "fps": self.fps},
                "actual": {"width": self.width, "height": self.height,
                           "measured_fps": None}}

    def close(self) -> None:
        self._closed.set()


def _finite_or_none(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


class OpenCVSource:
    """A webcam via OpenCV; DirectShow on Windows (MSMF starts slowly)."""

    _PROPS = (("width", "CAP_PROP_FRAME_WIDTH"), ("height", "CAP_PROP_FRAME_HEIGHT"),
              ("fps", "CAP_PROP_FPS"), ("autofocus", "CAP_PROP_AUTOFOCUS"),
              ("focus", "CAP_PROP_FOCUS"), ("auto_exposure", "CAP_PROP_AUTO_EXPOSURE"),
              ("exposure", "CAP_PROP_EXPOSURE"))

    def __init__(self, index_or_path=0, *, width: int = 640, height: int = 480,
                 fps: float = 30.0, focus: float | None = None,
                 exposure: float | None = None, warmup_s: float = 1.0, cv2_module=None):
        self.index_or_path, self.width, self.height, self.fps = index_or_path, width, height, fps
        self.focus, self.exposure, self.warmup_s = focus, exposure, warmup_s
        self._cv2 = cv2_module
        self._capture = None
        self._settings: dict = {}

    def _module(self):
        if self._cv2 is None:
            import cv2  # optional dependency: pip install .[camera]
            self._cv2 = cv2
        return self._cv2

    def open(self) -> None:
        cv2 = self._module()
        backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        capture = cv2.VideoCapture(self.index_or_path, backend)
        if not capture.isOpened():
            raise RuntimeError(f"Could not open camera {self.index_or_path!r}")
        requested = {"width": self.width, "height": self.height, "fps": self.fps,
                     "autofocus": 0}
        if self.focus is not None:
            requested["focus"] = self.focus
        if self.exposure is not None:
            # Manual exposure: DirectShow treats a value rounding to 0 as manual,
            # V4L2 uses 1 (OpenCV backend sources; unverified with our camera).
            requested["auto_exposure"] = 0 if sys.platform == "win32" else 1
            requested["exposure"] = self.exposure
        names = dict(self._PROPS)
        set_ok = {key: bool(capture.set(getattr(cv2, names[key]), value))
                  for key, value in requested.items()}
        read_back = {key: _finite_or_none(capture.get(getattr(cv2, prop)))
                     for key, prop in self._PROPS}
        self._capture = capture
        actual = self._warm_up()
        self._settings = {"backend": "directshow" if sys.platform == "win32" else "default",
                          "index_or_path": str(self.index_or_path),
                          "requested": requested, "set_ok": set_ok,
                          "read_back": read_back, "actual": actual,
                          "warmup_s": self.warmup_s}

    def _warm_up(self) -> dict:
        ok, image = self._capture.read()
        if not ok or image is None:
            raise RuntimeError("Camera opened but returned no frame")
        height, width = image.shape[:2]
        frames, started = 0, time.monotonic()
        while time.monotonic() - started < self.warmup_s:
            ok, _ = self._capture.read()
            frames += bool(ok)
        elapsed = time.monotonic() - started
        measured = round(frames / elapsed, 2) if elapsed > 0 and frames else None
        return {"width": int(width), "height": int(height), "measured_fps": measured}

    def read(self):
        if self._capture is None:
            return False, None
        return self._capture.read()

    def settings(self) -> dict:
        return dict(self._settings)

    def close(self) -> None:
        capture, self._capture = self._capture, None
        if capture is not None:
            capture.release()
```

`pyproject.toml`: add after `planning`:

```toml
# Webcam capture (scorbot.camera.OpenCVSource and JPEG encoding).
camera = ["opencv-python-headless>=4.9,<5"]
```

and add `"opencv-python-headless>=4.9,<5",` to `dev`. Then `.venv/Scripts/uv.exe lock` and check `uv.lock` lists cp310 and cp313 Windows and manylinux wheels for `opencv-python-headless` (it ships abi3 wheels).

- [ ] **Step 4: Run tests** — `.venv/Scripts/python.exe -m unittest tests.test_camera_source`; also `.venv/Scripts/python.exe -c "import scorbot.camera, sys; assert 'cv2' not in sys.modules"`.

- [ ] **Step 5: Commit** — "Add webcam and fake frame sources".

---

### Task 6: CameraRecorder

**Files:**
- Create: `scorbot/camera/recorder.py`
- Test: `tests/test_camera_recorder.py`

**Interfaces:**
- Consumes: `FrameSource` (Task 5), `CameraStream` (Task 2; optional, `None` measures only).
- Produces: `CameraRecorder(source, stream=None, *, fps=30.0, queue_bytes=64 * 2**20, max_latency_s=1.0, jpeg_quality=90, clock=time.monotonic_ns, encoder=None, health_interval_s=1.0)`; `start()` (source must already be open); `stop(timeout_s=3.0) -> str`; `drain_health() -> list[dict]`; properties `status` (`"idle"`, `"running"`, `"stopped"`, `"failed: <reason>"`), `failure` (str or None), `counts` (dict).

- [ ] **Step 1: Write the failing tests** (`tests/test_camera_recorder.py`)

```python
"""Camera recorder threads. FakeSource, injected clock and encoder, no sleeps-as-asserts."""

import itertools
from pathlib import Path
import tempfile
import threading
import unittest

from scorbot.camera.recorder import CameraRecorder
from scorbot.camera.source import FakeSource
from scorbot.camera.stream import CameraStream, scan_stream
from scorbot.session import SessionWriter


def fake_jpeg(image):
    return b"JPEG" + bytes(image[0, :8, 0])


class StepClock:
    """Each call advances 33 ms, like a 30 fps camera."""

    def __init__(self, step_ns=33_000_000):
        self._counter = itertools.count()
        self.step_ns = step_ns

    def __call__(self):
        return next(self._counter) * self.step_ns


class RecorderTests(unittest.TestCase):
    def setUp(self):
        # A stuck-writer test leaves its file open in an abandoned thread.
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.session = SessionWriter.create(Path(self._tmp.name), data_source="simulated",
                                            robot_id="arm-1", camera_ids=["wrist"])
        self.stream = CameraStream.create(self.session, "wrist")

    def tearDown(self):
        self.session.close()
        self._tmp.cleanup()

    def recorder(self, source, **kwargs):
        kwargs.setdefault("clock", StepClock())
        kwargs.setdefault("encoder", fake_jpeg)
        kwargs.setdefault("max_latency_s", 1e9)
        source.open()
        return CameraRecorder(source, self.stream, **kwargs)

    def test_frames_written_in_order_with_their_stamps(self):
        source = FakeSource(fail_at=None)
        recorder = self.recorder(source)
        done = threading.Event()
        original = self.stream.log_frame

        def counting(*args, **kwargs):
            result = original(*args, **kwargs)
            if args[0] >= 20:
                done.set()
            return result
        self.stream.log_frame = counting
        recorder.start()
        self.assertTrue(done.wait(5))
        recorder.stop()
        index = scan_stream(self.session.path, "wrist")
        self.assertEqual(index.errors, [])
        numbers = [f["frame_number"] for f in index.frames]
        self.assertEqual(numbers, sorted(numbers))
        stamps = [f["observed_monotonic_ns"] for f in index.frames]
        self.assertEqual(stamps, sorted(stamps))
        self.assertEqual(recorder.status, "stopped")
        health = recorder.drain_health()
        self.assertTrue(health)
        # Thread interleaving can stretch gaps between stamps, so "degraded" is
        # possible here; "failed" never is.
        self.assertNotIn("failed", {row["status"] for row in health})

    def test_blocked_writer_drops_oldest_by_bytes(self):
        gate = threading.Event()
        original = self.stream.log_frame

        def blocked(*args, **kwargs):
            gate.wait(5)
            return original(*args, **kwargs)
        self.stream.log_frame = blocked
        source = FakeSource()
        one_frame = 64 * 48 * 3
        recorder = self.recorder(source, queue_bytes=4 * one_frame)
        recorder.start()
        deadline = threading.Event()
        for _ in range(200):
            if recorder.counts["dropped_queue"] >= 10:
                break
            deadline.wait(0.01)
        gate.set()
        recorder.stop()
        self.assertGreaterEqual(recorder.counts["dropped_queue"], 10)
        self.assertTrue(any(row["status"] == "degraded" for row in recorder.drain_health()))

    def test_stale_frames_are_dropped_not_written_late(self):
        source = FakeSource()
        recorder = self.recorder(source, clock=StepClock(step_ns=2_000_000_000),
                                 max_latency_s=1.0)
        recorder.start()
        for _ in range(200):
            if recorder.counts["dropped_late"] >= 3:
                break
            threading.Event().wait(0.01)
        recorder.stop()
        self.assertGreaterEqual(recorder.counts["dropped_late"], 3)

    def test_source_failure_fails_closes_stream_and_spares_the_robot(self):
        source = FakeSource(fail_at=5)
        recorder = self.recorder(source)
        recorder.start()
        for _ in range(500):
            if recorder.failure:
                break
            threading.Event().wait(0.01)
        self.session.log_note("robot recording continues")
        status = recorder.stop()
        self.assertTrue(status.startswith("failed"))
        self.assertIn("frame 5", recorder.failure)
        self.assertTrue(self.stream.sidecar_path.is_file())
        self.assertTrue(any(row["status"] == "failed" for row in recorder.drain_health()))

    def test_write_failure_fails_the_recorder(self):
        def broken(*args, **kwargs):
            raise OSError("disk full")
        self.stream.log_frame = broken
        recorder = self.recorder(FakeSource())
        recorder.start()
        for _ in range(500):
            if recorder.failure:
                break
            threading.Event().wait(0.01)
        recorder.stop()
        self.assertIn("disk full", recorder.failure)

    def test_repeated_empty_reads_fail_the_recorder(self):
        class Empty(FakeSource):
            def read(self):
                return False, None
        recorder = self.recorder(Empty())
        recorder.start()
        for _ in range(500):
            if recorder.failure:
                break
            threading.Event().wait(0.01)
        recorder.stop()
        self.assertIn("no frame", recorder.failure)

    def test_stuck_read_does_not_block_stop(self):
        source = FakeSource(block_at=3, release_on_close=False)
        recorder = self.recorder(source)
        recorder.start()
        status = recorder.stop(timeout_s=0.5)
        self.assertIn("stuck", status)

    def test_stuck_writer_leaves_no_sidecar_and_stop_returns(self):
        never = threading.Event()

        def stuck(*args, **kwargs):
            never.wait()
        self.stream.log_frame = stuck
        recorder = self.recorder(FakeSource())
        recorder.start()
        status = recorder.stop(timeout_s=0.5)
        self.assertIn("stuck", status)
        self.assertFalse(self.stream.sidecar_path.exists())

    def test_stop_twice_and_before_start(self):
        idle = CameraRecorder(FakeSource(), None, encoder=fake_jpeg)
        self.assertEqual(idle.stop(), "idle")
        recorder = self.recorder(FakeSource())
        recorder.start()
        first = recorder.stop()
        self.assertEqual(recorder.stop(), first)

    def test_stop_is_harmless_after_failure(self):
        recorder = self.recorder(FakeSource(fail_at=0))
        recorder.start()
        for _ in range(500):
            if recorder.failure:
                break
            threading.Event().wait(0.01)
        self.assertTrue(recorder.stop().startswith("failed"))

    def test_without_a_stream_frames_are_only_measured(self):
        source = FakeSource()
        source.open()
        recorder = CameraRecorder(source, None, encoder=fake_jpeg, clock=StepClock())
        recorder.start()
        for _ in range(500):
            if recorder.counts["frames"] >= 10:
                break
            threading.Event().wait(0.01)
        self.assertEqual(recorder.stop(), "stopped")


class RealEncoderTests(unittest.TestCase):
    def test_default_encoder_makes_a_jpeg(self):
        try:
            import cv2  # noqa: F401
        except ImportError:
            self.skipTest("opencv not installed (pip install .[camera])")
        from scorbot.camera.recorder import encode_jpeg
        source = FakeSource()
        source.open()
        data = encode_jpeg(source.read()[1], 90)
        self.assertEqual(data[:2], b"\xff\xd8")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure** — ImportError on `scorbot.camera.recorder`.

- [ ] **Step 3: Implement** `scorbot/camera/recorder.py`

```python
"""Capture frames on one thread, write them on another, never block the owner.

The reader thread stamps each frame with the session clock right after
``read()`` returns (read-return time, not exposure time) and puts it on a
queue bounded by bytes; when full, the oldest frame is dropped and counted.
The writer thread drops frames older than ``max_latency_s``, JPEG-encodes the
rest and writes them; it is the only user of the stream and closes it when it
exits. Health rows go to a bounded deque that the owner drains on its own
thread. ``stop()`` is bounded and never raises; a thread that will not stop
is left behind (daemon) and reported as stuck.
"""

from __future__ import annotations

from collections import deque
import threading
import time

EMPTY_READS_BEFORE_FAILURE = 30
HEALTH_ROWS_KEPT = 600


def encode_jpeg(image, quality: int) -> bytes:
    import cv2  # optional dependency: pip install .[camera]
    ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        raise RuntimeError("JPEG encoding failed")
    return encoded.tobytes()


class CameraRecorder:
    def __init__(self, source, stream=None, *, fps: float = 30.0,
                 queue_bytes: int = 64 * 2 ** 20, max_latency_s: float = 1.0,
                 jpeg_quality: int = 90, clock=time.monotonic_ns, encoder=None,
                 health_interval_s: float = 1.0):
        self.source, self.stream, self.fps = source, stream, fps
        self.queue_bytes, self.max_latency_ns = queue_bytes, int(max_latency_s * 1e9)
        self.clock = clock
        self.encoder = encoder or (lambda image: encode_jpeg(image, jpeg_quality))
        self.health_interval_ns = int(health_interval_s * 1e9)
        self._queue: deque = deque()
        self._queued_bytes = 0
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._health: deque = deque(maxlen=HEALTH_ROWS_KEPT)
        self._threads: list[threading.Thread] = []
        self._state = "idle"
        self._result: str | None = None
        self.failure: str | None = None
        self.counts = {"frames": 0, "written": 0, "dropped_queue": 0, "dropped_late": 0}
        self._window = self._new_window()
        self._last_stamp = None

    # -- owner API ------------------------------------------------------------

    @property
    def status(self) -> str:
        if self.failure:
            return f"failed: {self.failure}"
        return self._state

    def start(self) -> None:
        if self._state != "idle":
            raise RuntimeError("A recorder starts once")
        self._state = "running"
        self._window_started = self.clock()
        for name, target in (("camera-reader", self._read_loop),
                             ("camera-writer", self._write_loop)):
            thread = threading.Thread(target=target, name=name, daemon=True)
            self._threads.append(thread)
            thread.start()

    def stop(self, timeout_s: float = 3.0) -> str:
        if self._result is not None:
            return self._result
        if self._state == "idle":
            self._result = "idle"
            return self._result
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        try:
            self.source.close()
        except Exception:
            pass
        reader, writer = self._threads
        deadline = time.monotonic() + timeout_s
        reader.join(max(0.0, timeout_s / 2))
        writer.join(max(0.0, deadline - time.monotonic()))
        stuck = [t.name for t in self._threads if t.is_alive()]
        if stuck and not self.failure:
            self.failure = f"stuck {', '.join(stuck)}"
        if not self.failure:
            self._state = "stopped"
        self._emit_health(force=True)
        self._result = self.status
        return self._result

    def drain_health(self) -> list[dict]:
        rows = []
        while self._health:
            rows.append(self._health.popleft())
        return rows

    # -- threads --------------------------------------------------------------

    def _fail(self, reason: str) -> None:
        with self._cond:
            if self.failure is None:
                self.failure = reason
            self._stop.set()
            self._cond.notify_all()

    def _read_loop(self) -> None:
        number, empty = 0, 0
        try:
            while not self._stop.is_set():
                ok, image = self.source.read()
                stamp = self.clock()
                if self._stop.is_set():
                    return
                if not ok or image is None:
                    empty += 1
                    if empty >= EMPTY_READS_BEFORE_FAILURE:
                        self._fail(f"camera returned no frame {empty} times in a row")
                        return
                    continue
                empty = 0
                with self._cond:
                    self.counts["frames"] += 1
                    self._window["frames"] += 1
                    if self._last_stamp is not None:
                        gap = stamp - self._last_stamp
                        self._window["max_gap_ns"] = max(self._window["max_gap_ns"], gap)
                    self._last_stamp = stamp
                    self._queue.append((number, stamp, image))
                    self._queued_bytes += image.nbytes
                    while self._queued_bytes > self.queue_bytes and len(self._queue) > 1:
                        _, _, old = self._queue.popleft()
                        self._queued_bytes -= old.nbytes
                        self.counts["dropped_queue"] += 1
                        self._window["dropped"] += 1
                    self._cond.notify()
                number += 1
        except Exception as error:
            self._fail(f"{type(error).__name__}: {error}")

    def _write_loop(self) -> None:
        try:
            while True:
                with self._cond:
                    while not self._queue and not self._stop.is_set():
                        self._cond.wait(0.1)
                        self._emit_health_locked()
                    if self._stop.is_set():
                        return
                    number, stamp, image = self._queue.popleft()
                    self._queued_bytes -= image.nbytes
                latency = self.clock() - stamp
                if latency > self.max_latency_ns:
                    with self._cond:
                        self.counts["dropped_late"] += 1
                        self._window["dropped"] += 1
                    continue
                if self.stream is not None:
                    data = self.encoder(image)
                    self.stream.log_frame(number, data, width=int(image.shape[1]),
                                          height=int(image.shape[0]),
                                          observed_monotonic_ns=stamp)
                with self._cond:
                    self.counts["written"] += 1
                    self._window["max_latency_ns"] = max(self._window["max_latency_ns"],
                                                         latency)
                    self._emit_health_locked()
        except Exception as error:
            self._fail(f"{type(error).__name__}: {error}")
        finally:
            if self.stream is not None:
                self.stream.health_summary = dict(self.counts, status=self.status)
                try:
                    self.stream.close()
                except Exception as error:
                    self._fail(f"closing the stream: {error}")

    # -- health ---------------------------------------------------------------

    @staticmethod
    def _new_window() -> dict:
        return {"frames": 0, "dropped": 0, "max_gap_ns": 0, "max_latency_ns": 0}

    def _emit_health(self, force: bool = False) -> None:
        with self._cond:
            self._emit_health_locked(force)

    def _emit_health_locked(self, force: bool = False) -> None:
        now = self.clock()
        elapsed = now - getattr(self, "_window_started", now)
        if not force and elapsed < self.health_interval_ns:
            return
        window = self._window
        period_ns = 1e9 / self.fps
        if self.failure:
            status = "failed"
        elif window["dropped"] or window["max_gap_ns"] > 2.5 * period_ns:
            status = "degraded"
        else:
            status = "ok"
        self._health.append({
            "frames": window["frames"], "dropped": window["dropped"],
            "dropped_queue": self.counts["dropped_queue"],
            "dropped_late": self.counts["dropped_late"],
            "max_gap_ms": round(window["max_gap_ns"] / 1e6, 3),
            "max_latency_ms": round(window["max_latency_ns"] / 1e6, 3),
            "measured_fps": round(window["frames"] / (elapsed / 1e9), 2) if elapsed > 0 else None,
            "queue_bytes": self._queued_bytes, "status": status,
            "failure": self.failure})
        self._window = self._new_window()
        self._window_started = now
```

Note for the implementer: `_emit_health_locked` reads the injected clock, so with `StepClock` every call advances time; that is intended (health rows appear quickly in tests).

- [ ] **Step 4: Run tests** — `.venv/Scripts/python.exe -m unittest tests.test_camera_recorder -v`. Run it three times in a row to catch flakiness.

- [ ] **Step 5: Commit** — "Record camera frames on their own threads with bounded stop".

---

### Task 7: Camera check command and docs

**Files:**
- Create: `scorbot/camera/__main__.py`
- Modify: `docs/EXPERIMENT_RECORDING.md`, `docs/PROJECT_LOG.md`, `docs/superpowers/specs/2026-10-01-m1-roadmap-design.md` (status line: step 3 done), `CLAUDE.md` repo map row for `scorbot/` (mention `camera/`)
- Test: `tests/test_camera_cli.py`

**Interfaces:**
- Consumes: `FakeSource`, `OpenCVSource`, `CameraRecorder`, `CameraStream`, `scan_stream`, `SessionWriter`.
- Produces: `main(argv=None) -> int` (0 ok, 1 failed).

- [ ] **Step 1: Write the failing test** (`tests/test_camera_cli.py`)

```python
import contextlib
import io
from pathlib import Path
import tempfile
import unittest


class CameraCheckTests(unittest.TestCase):
    def test_fake_check_records_a_clean_stream(self):
        from scorbot.camera.__main__ import main
        from scorbot.camera.stream import scan_stream
        with tempfile.TemporaryDirectory() as folder:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["check", "--fake", "--seconds", "0.5", "--record", folder])
            self.assertEqual(code, 0, out.getvalue())
            self.assertIn("requested", out.getvalue())
            [session] = [p for p in Path(folder).iterdir() if p.is_dir()]
            index = scan_stream(session, "check")
            self.assertEqual(index.errors, [])
            self.assertGreater(len(index.frames), 0)

    def test_fake_check_without_record(self):
        from scorbot.camera.__main__ import main
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["check", "--fake", "--seconds", "0.3"]), 0)


if __name__ == "__main__":
    unittest.main()
```

The fake check needs a real JPEG encoder only when recording; `--fake` without OpenCV uses a raw-bytes encoder and records `format="jpeg"` falsely, so instead: when `--fake` and OpenCV is missing, encode with `encode_jpeg` if importable, else skip recording with a message. In the test, skip `test_fake_check_records_a_clean_stream` if `cv2` is missing.

- [ ] **Step 2: Run to verify failure** — ImportError.

- [ ] **Step 3: Implement** `scorbot/camera/__main__.py`

```python
"""python -m scorbot.camera check: open a camera, show its settings and timing.

Opens no USB to the arm. With --record DIR, writes a camera-only session so the
stream file contract is exercised on the lab PC.
"""

from __future__ import annotations

import argparse
import sys
import time

from ..session import SessionWriter
from .recorder import CameraRecorder
from .source import FakeSource, OpenCVSource
from .stream import CameraStream, scan_stream


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m scorbot.camera")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="open a camera and report settings and timing")
    check.add_argument("--index", type=int, default=0)
    check.add_argument("--fake", action="store_true", help="synthetic frames, no camera")
    check.add_argument("--seconds", type=float, default=10.0)
    check.add_argument("--width", type=int, default=640)
    check.add_argument("--height", type=int, default=480)
    check.add_argument("--fps", type=float, default=30.0)
    check.add_argument("--record", metavar="DIR", help="write a camera-only session here")
    return parser


def _print_settings(settings: dict) -> None:
    print(f"backend: {settings.get('backend')}")
    requested = settings.get("requested", {})
    set_ok = settings.get("set_ok", {})
    read_back = settings.get("read_back", {})
    print(f"{'property':<14} {'requested':>10} {'set ok':>7} {'raw read-back':>14}")
    for key in sorted(set(requested) | set(read_back)):
        print(f"{key:<14} {str(requested.get(key, '-')):>10} "
              f"{str(set_ok.get(key, '-')):>7} {str(read_back.get(key, '-')):>14}")
    actual = settings.get("actual", {})
    print(f"actual frames: {actual.get('width')}x{actual.get('height')}, "
          f"measured fps {actual.get('measured_fps')}")
    print("Raw read-back values are backend flags, not proof a setting took effect.")


def _check(args) -> int:
    resolution = time.get_clock_info("monotonic").resolution
    print(f"clock: monotonic resolution {resolution * 1e3:.4f} ms")
    if resolution > 1e-3:
        print("WARNING: clock coarser than 1 ms; frame times will be coarse "
              "(use Python 3.13 on Windows).")
    source = (FakeSource(width=args.width, height=args.height, fps=args.fps, pace=True)
              if args.fake else OpenCVSource(args.index, width=args.width,
                                             height=args.height, fps=args.fps))
    source.open()
    settings = source.settings()
    _print_settings(settings)
    session = stream = None
    if args.record:
        session = SessionWriter.create(args.record,
                                       data_source="simulated" if args.fake else "real",
                                       robot_id="camera-check", task="camera check",
                                       camera_ids=["check"])
        stream = CameraStream.create(session, "check", settings)
    recorder = CameraRecorder(source, stream, fps=args.fps)
    recorder.start()
    deadline = time.monotonic() + args.seconds
    while time.monotonic() < deadline and not recorder.failure:
        time.sleep(0.2)
        for row in recorder.drain_health():
            print(f"health: {row['status']:<9} fps {row['measured_fps']} "
                  f"max gap {row['max_gap_ms']} ms dropped {row['dropped']}")
    status = recorder.stop()
    print(f"recorder: {status}; counts {recorder.counts}")
    if session is not None:
        session.close()
        index = scan_stream(session.path, "check")
        print(f"recorded {len(index.frames)} frames to {session.path}; "
              f"{len(index.errors)} errors, {len(index.warnings)} warnings")
        for finding in index.findings:
            print(f"  {finding.level}: {finding.message}")
        if index.errors:
            return 1
    return 1 if status.startswith("failed") else 0


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "check":
        return _check(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
```

Docs:
- `docs/EXPERIMENT_RECORDING.md`: new section "Camera streams" with the file layout, the crash table from the spec, `scan_stream`/`iter_frames` usage, read-return timestamps, and that an older checkout lists `camera-*.mcap` as "NOT A SESSION".
- `docs/PROJECT_LOG.md`: entry "Camera capture (M1 step 3)".
- Roadmap status line: "Steps 0-3 done."
- `CLAUDE.md` repo map: add a row `scorbot/camera/` — "Webcam capture: per-camera stream files, recorder threads, `python -m scorbot.camera check`. Never imports USB".

- [ ] **Step 4: Run tests** — `.venv/Scripts/python.exe -m unittest tests.test_camera_cli`, then the full suite once: `.venv/Scripts/python.exe -m unittest discover -s tests`, and `.venv/Scripts/ruff.exe check .`.

- [ ] **Step 5: Commit** — "Add a camera check command and document camera streams".

---

## Self-review notes

- Spec coverage: isolation (Tasks 2, 6), file contract and crash table (Tasks 2, 3), reader and listing (Tasks 3, 4), timestamps (Task 1, 6), OpenCV settings semantics (Task 5), bounded queue by bytes and latency (Task 6), owner-drained health (Task 6), bounded stop (Task 6), CLI (Task 7), deps and packages (Tasks 2, 5), docs (Task 7).
- `color_mode` from the spec is dropped (YAGNI): frames stay BGR, which is what `cv2.imencode` expects; note it in the source docstring.
