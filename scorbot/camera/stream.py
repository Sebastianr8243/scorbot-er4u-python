"""One camera's frames in their own MCAP file, sharing nothing with the robot recording.

``camera-<id>.mcap`` holds only ``/camera/<id>/image`` messages; the
``camera-<id>.json`` sidecar is written once, when the stream closes. The
stream copies the parent session's identity (id, data source, clock) at
creation and keeps no reference to the parent, so a camera fault can never
stop or delay the robot recording. See
docs/superpowers/specs/2026-10-01-camera-capture-design.md.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from mcap.records import Message
from mcap.stream_reader import StreamReader
from mcap.writer import Writer

from ..session import replay, schemas
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
    capture_failure: str | None = None  # set by the recorder before close()

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
        if self.capture_failure is not None:
            sidecar["capture_failure"] = self.capture_failure
        _write_json_atomic(self.sidecar_path, sidecar)


# -- reading -----------------------------------------------------------------

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
        if embedded is not None and (sidecar.get("session_id") != embedded.get("session_id")
                                     or sidecar.get("camera_id") != camera_id):
            findings.append(replay.Finding(
                "error", f"{name}.json sidecar belongs to a different stream "
                         "(session or camera id differs)"))
        if sidecar.get("capture_failure"):
            findings.append(replay.Finding(
                "error", f"Camera capture failed: {sidecar['capture_failure']}; frames "
                         "may stop early or have gaps"))
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
    parent = _parent_identity(folder, []) or {}
    return sorted(c for c in parent.get("camera_ids", [])
                  if not (folder / f"{stream_name(c)}.mcap").is_file())
