"""Append-only experiment recorder writing one crash-safe MCAP file per session.

Messages are written unchunked and flushed one at a time, so a crash loses at
most the record being written. Streams are aligned with the host monotonic
clock; the raw values travel in each payload's ``_rec`` block.
"""

from __future__ import annotations

import base64
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import hashlib
from importlib import metadata as importlib_metadata
import json
import math
import os
from pathlib import Path
import platform
import secrets
import subprocess
import sys
import threading
import time

from mcap.writer import Writer

from . import schemas

IMAGE_FORMATS = ("jpeg", "png", "webp", "avif")
_REPO_ROOT = Path(__file__).resolve().parents[2]

NOTES_TEMPLATE = """# Session {session_id} observation sheet

Fill in by hand during or right after the run. Keep this file with the session.

- Date / time:
- Operators present (who held the physical emergency stop):
- Emergency stop reachable and tested before start (yes/no):
- Start pose (describe; photo file name):
- What moved, which direction, anything unexpected:
- Controller LEDs / sounds:
- How the run ended (normal return / e-stop / error / other):
- Discrepancies between this sheet and the software log:
- Reviewed by / date:
"""


class SessionError(RuntimeError):
    """The recorder refused an event or is not writable."""


class SessionWriter:
    """Record one experiment. Safe to share between threads."""

    def __init__(self, path: Path, metadata: dict, stream, writer: Writer):
        self.path = path
        self.mcap_path = path / "session.mcap"
        self.metadata = metadata
        self._stream = stream
        self._writer = writer
        self._lock = threading.Lock()
        self._seq = 0
        self._next_command = 1
        self._schema_ids: dict[str, int] = {}
        self._channel_ids: dict[str, int] = {}
        self._closed = False
        self._broken: str | None = None
        self._last_fault: str | None = None
        self._clock = metadata["clock"]

    @classmethod
    def create(cls, root, *, data_source: str, robot_id: str,
               controller_id: str = "unknown", operator: str = "", task: str = "",
               start_pose_note: str = "unmeasured", camera_ids=(),
               calibration_path=None, usb_driver: str = "unknown") -> "SessionWriter":
        if data_source not in schemas.DATA_SOURCES:
            raise ValueError(f"data_source must be one of {schemas.DATA_SOURCES}, "
                             f"not {data_source!r}")
        if not str(robot_id).strip():
            raise ValueError("robot_id must contain a label")
        for camera_id in camera_ids:
            schemas.camera_topic(camera_id, "image")

        started_epoch_ns = time.time_ns()
        started_monotonic_ns = time.monotonic_ns()
        session_id = (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(started_epoch_ns / 1e9))
                      + "-" + secrets.token_hex(3))
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        path = root / session_id
        path.mkdir()

        metadata = {
            "schema_version": schemas.SCHEMA_VERSION,
            "session_id": session_id,
            "data_source": data_source,
            "robot_id": str(robot_id).strip(),
            "controller_id": controller_id,
            "operator": operator,
            "task": task,
            "start_pose_note": start_pose_note,
            "camera_ids": list(camera_ids),
            "calibration": _calibration(calibration_path),
            "code": _code_identity(),
            "environment": _environment(usb_driver),
            "clock": _clock_info(started_monotonic_ns, started_epoch_ns),
            "started_utc": datetime.fromtimestamp(started_epoch_ns / 1e9,
                                                  timezone.utc).isoformat(),
        }
        _write_json_atomic(path / "metadata.json", metadata)
        (path / "notes.md").write_text(NOTES_TEMPLATE.format(session_id=session_id),
                                       encoding="utf-8")

        stream = open(path / "session.mcap", "xb")
        # The data-section CRC lets replay detect silent edits in closed sessions.
        writer = Writer(stream, use_chunking=False, enable_data_crcs=True)
        writer.start(profile="", library=f"scorbot-session/{schemas.SCHEMA_VERSION}")
        writer.add_metadata("scorbot.session", {"json": json.dumps(metadata)})
        stream.flush()
        return cls(path, metadata, stream, writer)

    # -- public logging API -------------------------------------------------

    def log_state(self, state, *, raw_packet: bytes | None = None,
                  observed_monotonic_ns: int | None = None) -> int:
        if isinstance(state, dict):
            payload = dict(state)
        elif is_dataclass(state):
            payload = asdict(state)
        else:
            payload = dict(vars(state))
        flag = payload.get("simulated")
        if flag is not None:
            session_is_simulated = self.metadata["data_source"] == "simulated"
            if bool(flag) != session_is_simulated:
                raise SessionError(
                    f"Refusing a {'simulated' if flag else 'real'} robot state in a "
                    f"{self.metadata['data_source']!r} session; real and simulated data "
                    "must never share a session")
        if raw_packet is not None:
            payload["raw_packet_hex"] = bytes(raw_packet).hex()
        if observed_monotonic_ns is None:
            observed_monotonic_ns = payload.get("host_monotonic_ns")
        return self._emit("/robot/state", payload, observed_monotonic_ns)

    def log_command(self, kind: str, params: dict) -> str:
        # Reserve the ID atomically; a rejected command leaves a harmless ID gap.
        with self._lock:
            command_id = f"cmd-{self._next_command:04d}"
            self._next_command += 1
        self._emit("/robot/command",
                   {"command_id": command_id, "kind": kind, "params": params}, None)
        return command_id

    def log_command_result(self, command_id: str, status: str, *,
                           completion_source: str | None = None,
                           detail: str | None = None) -> int:
        if status not in schemas.COMMAND_STATUSES:
            raise ValueError(f"status must be one of {schemas.COMMAND_STATUSES}")
        return self._emit("/robot/command_result",
                          {"command_id": command_id, "status": status,
                           "completion_source": completion_source, "detail": detail}, None)

    def log_frame(self, camera_id: str, frame_number: int, data: bytes, *, format: str,
                  width: int, height: int, observed_monotonic_ns: int | None = None,
                  camera_config_id: str | None = None, mount_id: str | None = None) -> int:
        if format not in IMAGE_FORMATS:
            raise ValueError(f"format must be one of {IMAGE_FORMATS}")
        if int(width) <= 0 or int(height) <= 0:
            raise ValueError("width and height must be positive")
        topic = schemas.camera_topic(camera_id, "image")
        stamp = self._epoch(observed_monotonic_ns if observed_monotonic_ns is not None
                            else time.monotonic_ns())
        payload = {
            "timestamp": {"sec": stamp // 1_000_000_000, "nsec": stamp % 1_000_000_000},
            "frame_id": camera_id,
            "data": base64.b64encode(bytes(data)).decode("ascii"),
            "format": format,
        }
        extra = {"camera_id": camera_id, "frame_number": int(frame_number),
                 "width": int(width), "height": int(height),
                 "camera_config_id": camera_config_id, "mount_id": mount_id}
        return self._emit(topic, payload, observed_monotonic_ns, rec_extra=extra)

    def log_detection(self, camera_id: str, frame_number: int, label: str, bbox_xyxy,
                      confidence: float, model_id: str, *, operator_correction=None,
                      observed_monotonic_ns: int | None = None) -> int:
        bbox = [float(value) for value in bbox_xyxy]
        if len(bbox) != 4:
            raise ValueError("bbox_xyxy must have four values")
        if not 0.0 <= float(confidence) <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        return self._emit(schemas.camera_topic(camera_id, "detections"),
                          {"camera_id": camera_id, "frame_number": int(frame_number),
                           "label": label, "bbox_xyxy": bbox,
                           "confidence": float(confidence), "model_id": model_id,
                           "operator_correction": operator_correction},
                          observed_monotonic_ns)

    def log_decision(self, choice, *, refers_to_seq: int | None = None,
                     reason: str | None = None) -> int:
        return self._emit("/operator/decision", {"choice": choice,
                                                 "refers_to_seq": refers_to_seq,
                                                 "reason": reason}, None)

    def log_fault(self, message: str, *, command_id: str | None = None) -> int:
        self._last_fault = message
        return self._emit("/session/fault", {"message": message,
                                             "command_id": command_id}, None)

    def log_note(self, text: str) -> int:
        return self._emit("/session/note", {"text": text}, None)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                # After a failed write the file may end in a partial record;
                # appending a summary would bury it, so leave it for replay.
                if self._broken is None:
                    self._writer.finish()
                    self._stream.flush()
                    os.fsync(self._stream.fileno())
            finally:
                self._stream.close()
            event_count = self._seq
        self.metadata.update({
            "ended_utc": datetime.now(timezone.utc).isoformat(),
            "event_count": event_count,
            "closed_cleanly": self._broken is None,
        })
        if self._broken is not None:
            self.metadata["write_error"] = self._broken
        _write_json_atomic(self.path / "metadata.json", self.metadata)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, _traceback):
        if exc_type is None:
            self.close()
            return False
        # Something already went wrong: record it, close as well as possible, and
        # never let a secondary close failure replace the caller's exception.
        try:
            message = f"{exc_type.__name__}: {exc}"
            # The caller may already have logged this exception with more context.
            if not self._closed and message != self._last_fault:
                self.log_fault(message)
        except Exception:
            pass
        try:
            self.close()
        except Exception:
            pass
        return False

    # -- internals ----------------------------------------------------------

    def _epoch(self, monotonic_ns: int) -> int:
        return self._clock["started_epoch_ns"] + (int(monotonic_ns)
                                                  - self._clock["started_monotonic_ns"])

    def _emit(self, topic: str, payload: dict, observed_monotonic_ns,
              rec_extra: dict | None = None) -> int:
        schema_name = schemas.schema_name_for_topic(topic)
        missing = [key for key in schemas.REQUIRED[schema_name] if key not in payload]
        if missing:
            raise SessionError(f"{topic} payload is missing {missing}")
        if observed_monotonic_ns is not None:
            observed_monotonic_ns = int(observed_monotonic_ns)
        # Serialize the (possibly large) payload before taking the lock; only the
        # small _rec block is encoded while other threads wait.
        try:
            body = json.dumps(payload, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise SessionError(f"{topic} payload is not strict JSON: {error}") from None
        with self._lock:
            if self._closed:
                raise SessionError("Session is closed")
            if self._broken is not None:
                raise SessionError(f"Recording stopped after a write failure: {self._broken}")
            logged = time.monotonic_ns()
            rec = {"seq": self._seq, "logged_monotonic_ns": logged,
                   "observed_monotonic_ns": observed_monotonic_ns}
            if rec_extra:
                rec.update(rec_extra)
            try:
                rec_json = json.dumps(rec, allow_nan=False)
            except (TypeError, ValueError) as error:
                raise SessionError(f"{topic} record fields are not strict JSON: {error}") from None
            # body is a non-empty JSON object (required keys exist); splice _rec in.
            data = (body[:-1] + ', "_rec": ' + rec_json + "}").encode()
            # Consume seq before writing: an interrupt (Ctrl-C) landing after the
            # bytes reach disk must not let the next event reuse this number.
            seq = self._seq
            self._seq += 1
            log_time = self._epoch(logged)
            publish_time = (self._epoch(observed_monotonic_ns)
                            if observed_monotonic_ns is not None else log_time)
            try:
                channel_id = self._channel(topic, schema_name)
                self._writer.add_message(channel_id=channel_id, log_time=log_time,
                                         publish_time=max(0, publish_time),
                                         sequence=seq, data=data)
                self._stream.flush()
            except Exception as error:
                self._broken = f"{type(error).__name__}: {error}"
                raise SessionError(f"Recording failed and has stopped: {self._broken}") from error
            return seq

    def _channel(self, topic: str, schema_name: str) -> int:
        if topic not in self._channel_ids:
            if schema_name not in self._schema_ids:
                self._schema_ids[schema_name] = self._writer.register_schema(
                    name=schema_name, encoding="jsonschema",
                    data=schemas.schema_json(schema_name))
            self._channel_ids[topic] = self._writer.register_channel(
                topic=topic, message_encoding="json",
                schema_id=self._schema_ids[schema_name])
        return self._channel_ids[topic]


class BestEffortRecorder:
    """Wrap a SessionWriter so a recording failure never interrupts the caller.

    Lab scripts treat the MCAP session as secondary evidence: after the robot
    has connected, a disk or recorder error must not skip the primary JSONL
    record, the operator prompts, or the motor-disable step. The first failure
    prints one warning; later ``log_*`` calls are dropped and return None.
    """

    def __init__(self, writer: SessionWriter, warn=print):
        self._writer = writer
        self._warn = warn
        self.failure: Exception | None = None

    def __getattr__(self, name):
        attribute = getattr(self._writer, name)
        if not name.startswith("log_"):
            return attribute

        def call(*args, **kwargs):
            if self.failure is not None:
                return None
            try:
                return attribute(*args, **kwargs)
            except Exception as error:
                self.failure = error
                self._warn(f"WARNING: MCAP recording stopped ({error}). The JSONL record "
                           "and the procedure continue.")
                return None
        return call


def _clock_info(started_monotonic_ns: int, started_epoch_ns: int) -> dict:
    # Windows on Python < 3.13 uses GetTickCount64 (about 15.6 ms steps); record it.
    info = time.get_clock_info("monotonic")
    return {"started_monotonic_ns": started_monotonic_ns,
            "started_epoch_ns": started_epoch_ns,
            "monotonic_implementation": info.implementation,
            "monotonic_resolution_s": info.resolution}


def _write_json_atomic(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with open(temporary, "w", encoding="utf-8") as stream:
        stream.write(json.dumps(value, indent=2, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _calibration(path) -> dict | None:
    if path is None:
        return None
    path = Path(path)
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _git(*args: str) -> str | None:
    try:
        result = subprocess.run(["git", *args], cwd=_REPO_ROOT, capture_output=True,
                                text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _code_identity() -> dict:
    commit = _git("rev-parse", "HEAD")
    status = _git("status", "--porcelain") if commit else None
    digest = hashlib.sha256()
    for package in ("scorbot", "openScorbot"):
        for source in sorted((_REPO_ROOT / package).rglob("*.py")):
            digest.update(source.relative_to(_REPO_ROOT).as_posix().encode())
            digest.update(source.read_bytes())
    return {"git_commit": commit, "git_dirty": bool(status) if commit else None,
            "source_fingerprint": digest.hexdigest()}


def _environment(usb_driver: str) -> dict:
    versions = {}
    for name in ("mcap", "pyusb", "numpy"):
        try:
            versions[name] = importlib_metadata.version(name)
        except importlib_metadata.PackageNotFoundError:
            versions[name] = None
    return {"python": sys.version.split()[0], "platform": platform.platform(),
            "packages": versions, "usb_driver": usb_driver}
