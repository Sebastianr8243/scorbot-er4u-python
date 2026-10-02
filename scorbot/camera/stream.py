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
