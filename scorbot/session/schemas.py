"""Topic and payload contracts for experiment sessions.

``foxglove_CompressedImage.json`` is vendored unchanged from
https://github.com/foxglove/foxglove-sdk/blob/dcbc66776e8704f5b4f9aa0c6e3ef695ed4c297b/schemas/jsonschema/CompressedImage.json
so viewers such as Foxglove and Lichtblick display camera frames.
"""

from __future__ import annotations

from importlib import resources
import json
import re

from scorbot.state import JOINTS

SCHEMA_VERSION = 1
DATA_SOURCES = ("real", "simulated", "synthetic")
COMMAND_STATUSES = ("completed", "faulted", "timeout", "rejected")
IMAGE_SCHEMA = "foxglove.CompressedImage"

TOPICS = {
    "/robot/state": "scorbot.RobotState",
    "/robot/command": "scorbot.Command",
    "/robot/command_result": "scorbot.CommandResult",
    "/operator/decision": "scorbot.Decision",
    "/session/fault": "scorbot.Fault",
    "/session/note": "scorbot.Note",
}

REQUIRED = {
    "scorbot.RobotState": ("encoder_counts", "home_switch_bits", "connected"),
    "scorbot.Command": ("command_id", "kind", "params"),
    "scorbot.CommandResult": ("command_id", "status"),
    "scorbot.Detection": ("frame_number", "label", "bbox_xyxy", "confidence", "model_id"),
    "scorbot.Decision": ("choice",),
    "scorbot.Fault": ("message",),
    "scorbot.Note": ("text",),
    IMAGE_SCHEMA: ("timestamp", "frame_id", "data", "format"),
}

_CAMERA_TOPIC = re.compile(r"^/camera/([^/]+)/(image|detections)$")


def camera_topic(camera_id: str, kind: str) -> str:
    topic = f"/camera/{camera_id}/{kind}"
    if not _CAMERA_TOPIC.match(topic):
        raise ValueError(f"Invalid camera id: {camera_id!r}")
    return topic


def schema_name_for_topic(topic: str) -> str | None:
    if topic in TOPICS:
        return TOPICS[topic]
    match = _CAMERA_TOPIC.match(topic)
    if match:
        return IMAGE_SCHEMA if match.group(2) == "image" else "scorbot.Detection"
    return None


def _t(kind, *, nullable=False, **extra):
    return {"type": [kind, "null"] if nullable else kind, **extra}


def _joint_map(description):
    """Object keyed by joint name, so viewers can autocomplete ``.base`` etc."""
    return {"type": "object", "description": description, "additionalProperties": True,
            "properties": {joint: {"type": "integer"} for joint in JOINTS}}


# Every payload also carries a recorder-added "_rec" block (see record.py).
_REC = {"type": "object", "description": "Recorder bookkeeping: seq, logged/observed "
        "monotonic ns, and camera extras", "additionalProperties": True,
        "properties": {"seq": _t("integer"), "logged_monotonic_ns": _t("integer"),
                       "observed_monotonic_ns": _t("integer", nullable=True)}}

# "required" always comes from REQUIRED; properties here are descriptive and
# permissive (additionalProperties stays true) so older sessions still fit.
PROPERTIES = {
    "scorbot.RobotState": {
        "timestamp_utc": _t("string"),
        "encoder_counts": _joint_map("Raw unsigned 16-bit counts; wraps (sawtooth)"),
        "controller_error_counts": _joint_map("Controller position error counts"),
        "home_switch_bits": _t("integer"),
        "connected": _t("boolean"),
        "enabled": _t("boolean", nullable=True),
        "homed": _t("boolean"),
        "fault": _t("string", nullable=True),
        "packet_index": _t("integer", nullable=True),
        "host_monotonic_ns": _t("integer", nullable=True),
        "encoder_sign_bytes": {**_joint_map("Sign byte per joint: 127 or 128"),
                               "type": ["object", "null"]},
        "signed_encoder_counts": {**_joint_map("Unwrapped signed counts; plot these"),
                                  "type": ["object", "null"]},
        "simulated": _t("boolean"),
        "raw_packet_hex": _t("string"),
    },
    "scorbot.Command": {
        "command_id": _t("string"),
        "kind": _t("string"),
        "params": {"type": "object", "additionalProperties": True},
    },
    "scorbot.CommandResult": {
        "command_id": _t("string"),
        "status": _t("string", enum=list(COMMAND_STATUSES)),
        "completion_source": _t("string", nullable=True),
        "detail": _t("string", nullable=True),
    },
    "scorbot.Detection": {
        "camera_id": _t("string"),
        "frame_number": _t("integer"),
        "label": _t("string"),
        "bbox_xyxy": {"type": "array", "items": {"type": "number"},
                      "minItems": 4, "maxItems": 4},
        "confidence": _t("number"),
        "model_id": _t("string"),
        "operator_correction": {},
    },
    "scorbot.Decision": {
        "choice": _t("string"),
        "refers_to_seq": _t("integer", nullable=True),
        "reason": _t("string", nullable=True),
    },
    "scorbot.Fault": {
        "message": _t("string"),
        "command_id": _t("string", nullable=True),
    },
    "scorbot.Note": {"text": _t("string")},
}


def schema_json(name: str) -> bytes:
    if name == IMAGE_SCHEMA:
        return resources.files(__package__).joinpath("foxglove_CompressedImage.json").read_bytes()
    schema = {"title": name, "type": "object", "required": list(REQUIRED[name]),
              "properties": {**PROPERTIES[name], "_rec": _REC},
              "additionalProperties": True}
    return json.dumps(schema).encode()
