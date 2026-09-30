"""Show a recorded session in Rerun (optional: pip install -e ".[viz]").

session_items() is pure and needs no Rerun, so CI tests it everywhere and a
later live source can produce the same Items. send() and view() are the only
code that touches the Rerun SDK. Nothing here opens USB or writes a session.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import math
import numbers
import uuid

from ..state import JOINTS
from .analysis import event_summary

APPLICATION_ID = "scorbot_session"
_LOG_TOPICS = ("/robot/command", "/robot/command_result", "/operator/decision",
               "/session/note", "/session/fault")


class RerunUnavailable(RuntimeError):
    """rerun-sdk is not installed."""


@dataclass(frozen=True)
class Item:
    """One thing to log: where, what kind, the value, and when (None = static)."""

    path: str
    kind: str   # scalar | text_log | image | boxes | clear | document
    value: object
    time_s: float | None = None
    seq: int | None = None
    level: str | None = None


def require_rerun():
    try:
        import rerun
    except ImportError as error:
        raise RerunUnavailable('Viewing needs Rerun: pip install -e ".[viz]"') from error
    return rerun


def _number(value) -> bool:
    return (isinstance(value, numbers.Real) and not isinstance(value, bool)
            and math.isfinite(value))


def info_markdown(session) -> str:
    meta = session.metadata
    source = str(meta.get("data_source") or "unknown").upper()
    code = meta.get("code") or {}
    lines = [f"# {source} session", ""]
    if source != "REAL":
        lines += [f"**{source} data: not evidence from the physical arm.**", ""]
    for label, key in (("Session", "session_id"), ("Robot", "robot_id"),
                       ("Operator", "operator"), ("Task", "task"),
                       ("Start pose", "start_pose_note"), ("Started", "started_utc")):
        lines.append(f"- {label}: {meta.get(key) or '-'}")
    dirty = " (uncommitted changes)" if code.get("git_dirty") else ""
    lines.append(f"- Code commit: {code.get('git_commit') or '-'}{dirty}")
    lines += ["", "## Integrity", ""]
    lines += ([f"- {f.level.upper()}: {f.message}" for f in session.findings]
              or ["- No findings."])
    return "\n".join(lines)


def _state_items(payload, t, seq):
    counts = payload.get("signed_encoder_counts")
    if not isinstance(counts, dict):
        counts = payload.get("encoder_counts")
    for group, values in (("counts", counts),
                          ("controller_error", payload.get("controller_error_counts"))):
        if isinstance(values, dict):
            for joint in JOINTS:
                if _number(values.get(joint)):
                    yield Item(f"state/{group}/{joint}", "scalar", float(values[joint]), t, seq)
    if _number(payload.get("home_switch_bits")):
        yield Item("state/home_switch_bits", "scalar", float(payload["home_switch_bits"]), t, seq)


def _log_item(event, t, seq):
    topic, payload = event["topic"], event["payload"]
    try:
        text = payload["text"] if topic == "/session/note" else event_summary(event)
    except (KeyError, TypeError, ValueError):
        text = "(unreadable payload)"
    if topic == "/session/fault":
        level = "ERROR"
    elif topic == "/robot/command_result" and payload.get("status") != "completed":
        level = "WARN"
    else:
        level = "INFO"
    return Item("events", "text_log", f"{topic}: {text}", t, seq, level)


def _image_item(camera_id, payload, t, seq):
    try:
        # binascii.Error and the non-ASCII error are both ValueError.
        contents = base64.b64decode(payload["data"], validate=True)
    except (KeyError, TypeError, ValueError):
        return None
    return Item(f"camera/{camera_id}/image", "image",
                {"contents": contents, "media_type": f"image/{payload.get('format')}"}, t, seq)


def _detection(payload):
    """(xyxy, label) for one detection payload, or None if its box is unusable."""
    box = payload.get("bbox_xyxy")
    if not (isinstance(box, list) and len(box) == 4 and all(_number(v) for v in box)):
        return None
    confidence = payload.get("confidence")
    label = str(payload.get("label", ""))
    if _number(confidence):
        label = f"{label} {confidence:.2f}"
    return [float(v) for v in box], label


def iter_items(session):
    """Yield everything to show for one session, in recorded order, lazily.

    Streaming keeps decoded images out of memory on long sessions. Detections
    of one frame become one box set at the first detection's time; a camera's
    pending box sets are emitted just before its next image, which then clears
    them, so the viewer never shows stale or partial detections.
    """
    yield Item("session/info", "document", info_markdown(session))
    start = min((e["publish_time"] for e in session.events), default=0)
    pending = {}   # camera_id -> {frame_number: (t, seq, xyxy list, label list)}

    def flush(camera_id):
        for t, seq, xyxy, labels in pending.pop(camera_id, {}).values():
            yield Item(f"camera/{camera_id}/image/detections", "boxes",
                       {"xyxy": xyxy, "labels": labels}, t, seq)

    for event in session.events:
        topic, payload = event["topic"], event["payload"]
        t, seq = (event["publish_time"] - start) / 1e9, event["seq"]
        if topic == "/robot/state":
            yield from _state_items(payload, t, seq)
        elif topic in _LOG_TOPICS:
            yield _log_item(event, t, seq)
        elif topic.startswith("/camera/") and topic.count("/") == 3:
            _, _, camera_id, kind = topic.split("/", 3)
            if kind == "image":
                yield from flush(camera_id)
                yield Item(f"camera/{camera_id}/image/detections", "clear", None, t, seq)
                image = _image_item(camera_id, payload, t, seq)
                if image is not None:
                    yield image
            elif kind == "detections" and (found := _detection(payload)) is not None:
                group = pending.setdefault(camera_id, {}).setdefault(
                    payload.get("frame_number"), (t, seq, [], []))
                group[2].append(found[0])
                group[3].append(found[1])
    for camera_id in list(pending):
        yield from flush(camera_id)


def session_items(session) -> list[Item]:
    """iter_items as a list (for tests and small sessions)."""
    return list(iter_items(session))


def recording_id(session) -> str:
    """Session id plus a per-run suffix, so viewing twice never merges two runs."""
    base = session.metadata.get("session_id") or str(session.path)
    return f"{base}-{uuid.uuid4().hex[:8]}"


def _archetype(rr, item: Item):
    value = item.value
    if item.kind == "scalar":
        return rr.Scalars(value)
    if item.kind == "text_log":
        return rr.TextLog(value, level=item.level)
    if item.kind == "image":
        return rr.EncodedImage(contents=value["contents"], media_type=value["media_type"])
    if item.kind == "boxes":
        return rr.Boxes2D(array=value["xyxy"], array_format=rr.Box2DFormat.XYXY,
                          labels=value["labels"])
    if item.kind == "clear":
        return rr.Clear(recursive=False)
    if item.kind == "document":
        return rr.TextDocument(value, media_type=rr.MediaType.MARKDOWN)
    raise ValueError(f"Unknown item kind: {item.kind}")


def send(items, recording) -> None:
    """Log Items to a Rerun RecordingStream (the only Rerun logging call site)."""
    rr = require_rerun()
    for item in items:
        static = item.time_s is None
        if not static:
            recording.set_time("session_time", duration=item.time_s)
            if item.seq is not None:
                recording.set_time("seq", sequence=item.seq)
        recording.log(item.path, _archetype(rr, item), static=static)


def view(session, save_path=None) -> None:
    """Open one session in the Rerun viewer, or write it to save_path (.rrd)."""
    rr = require_rerun()
    recording = rr.RecordingStream(APPLICATION_ID, recording_id=recording_id(session))
    if save_path is not None:
        recording.save(str(save_path))
    else:
        recording.spawn()
    send(iter_items(session), recording)
    recording.flush()
