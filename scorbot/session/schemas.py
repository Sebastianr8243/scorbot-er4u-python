"""Topic and payload contracts for experiment sessions.

``foxglove_CompressedImage.json`` is vendored unchanged from
https://github.com/foxglove/foxglove-sdk/blob/dcbc66776e8704f5b4f9aa0c6e3ef695ed4c297b/schemas/jsonschema/CompressedImage.json
so viewers such as Foxglove and Lichtblick display camera frames.
"""

from __future__ import annotations

from importlib import resources
import json
import re

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


def schema_json(name: str) -> bytes:
    if name == IMAGE_SCHEMA:
        return resources.files(__package__).joinpath("foxglove_CompressedImage.json").read_bytes()
    schema = {"title": name, "type": "object", "required": list(REQUIRED[name]),
              "properties": {}}
    return json.dumps(schema).encode()
