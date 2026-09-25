"""Read a recorded session without hardware and report integrity problems.

The MCAP file is streamed record by record, so crashed (unfinished) sessions
load too. A damaged final record is a crash symptom and only a warning;
unreadable data before the end is corruption and an error.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
import json
from pathlib import Path

from mcap.records import Channel, Footer, Message, Metadata, Schema
from mcap.stream_reader import CRCValidationError, StreamReader

from . import schemas

_KNOWN_OPCODES = set(range(0x01, 0x10))  # MCAP v0 record opcodes
_MAGIC = b"\x89MCAP0\r\n"
# Fields written once at session start; both metadata copies must agree on them.
_IDENTITY_KEYS = ("session_id", "data_source", "schema_version", "robot_id", "clock")
_MAX_RECORD = 2 ** 32


@dataclass(frozen=True)
class Finding:
    level: str  # "error" or "warning"
    message: str


@dataclass
class Session:
    path: Path
    metadata: dict
    events: list[dict]
    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "warning"]


def load_session(path) -> Session:
    path = Path(path)
    folder, mcap_path = (path.parent, path) if path.suffix == ".mcap" else (path, path / "session.mcap")
    if not mcap_path.is_file():
        raise FileNotFoundError(f"No session.mcap found at {mcap_path}")
    findings: list[Finding] = []
    data = mcap_path.read_bytes()
    events, embedded, finished, failure = _read_mcap(data, findings)
    sidecar = _read_sidecar(folder / "metadata.json", findings)

    if failure is not None:
        # A closed file (magic at the end, or sidecar says so) has no crash tail:
        # any unreadable record in it is damage, never a harmless truncation.
        claims_closed = data.endswith(_MAGIC) or (sidecar or {}).get("closed_cleanly") is True
        _classify_tail(data, *failure, claims_closed, findings)

    metadata = _merge_metadata(sidecar, embedded, findings)
    _check_metadata(metadata, findings)
    closed = finished and (sidecar is None or sidecar.get("closed_cleanly") is True)
    if not closed:
        findings.append(Finding("warning", "Session was not closed cleanly "
                                           "(crash, e-stop, or still recording)"))
    if sidecar is not None and isinstance(sidecar.get("event_count"), int)             and sidecar["event_count"] != len(events):
        findings.append(Finding("error", f"metadata.json records {sidecar['event_count']} "
                                         f"events but {len(events)} could be read"))
    _check_events(events, findings)
    return Session(folder, metadata, events, findings)


def nearest(events, t_ns: int, topic: str = "/robot/state"):
    """Closest event on ``topic`` to monotonic time ``t_ns``, and event_time - t_ns."""
    best = None
    for event in events:
        if event["topic"] != topic:
            continue
        rec = event["payload"]["_rec"]
        when = rec["observed_monotonic_ns"]
        if when is None:
            when = rec["logged_monotonic_ns"]
        if best is None or abs(when - t_ns) < abs(best[1]):
            best = (event, when - t_ns)
    return best


def _read_sidecar(path: Path, findings: list[Finding]) -> dict | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        findings.append(Finding("error", f"metadata.json is not valid JSON: {error}"))
        return None
    if not isinstance(value, dict):
        findings.append(Finding("error", "metadata.json does not contain a JSON object"))
        return None
    return value


def _merge_metadata(sidecar, embedded, findings: list[Finding]) -> dict:
    if sidecar is None and embedded is None:
        findings.append(Finding("error", "No session metadata found"))
        return {}
    if sidecar is None or embedded is None:
        return dict(sidecar or embedded)
    differing = [key for key in _IDENTITY_KEYS if sidecar.get(key) != embedded.get(key)]
    if differing:
        findings.append(Finding("error", "metadata.json disagrees with the metadata recorded "
                                         f"in session.mcap on {', '.join(differing)}; "
                                         "showing the recorded values"))
    # The copy inside the MCAP file is CRC-protected; it wins on identity fields.
    return {**sidecar, **{key: embedded.get(key) for key in _IDENTITY_KEYS}}


def _read_mcap(data: bytes, findings: list[Finding]):
    stream = BytesIO(data)
    channels: dict[int, Channel] = {}
    schema_names: dict[int, str] = {}
    events: list[dict] = []
    embedded_metadata = None
    finished = False
    last_good = 0
    try:
        for record in StreamReader(stream, validate_crcs=True).records:
            if isinstance(record, Channel):
                channels[record.id] = record
            elif isinstance(record, Schema):
                schema_names[record.id] = record.name
            elif isinstance(record, Message):
                event = _decode(record, channels, schema_names, last_good, findings)
                if event is not None:
                    events.append(event)
            elif isinstance(record, Metadata) and record.name == "scorbot.session":
                try:
                    embedded_metadata = json.loads(record.metadata["json"])
                except (KeyError, ValueError):
                    findings.append(Finding("error", "Embedded session metadata is unreadable"))
            elif isinstance(record, Footer):
                finished = True
            last_good = stream.tell()
    except Exception as error:  # the mcap reader raises several types at a bad record
        return events, embedded_metadata, False, (last_good, error)
    return events, embedded_metadata, finished, None


def _decode(record: Message, channels, schema_names, offset, findings):
    channel = channels.get(record.channel_id)
    if channel is None:
        findings.append(Finding("error", f"Message at byte {offset} uses an unknown channel"))
        return None
    try:
        payload = json.loads(record.data)
        seq = payload["_rec"]["seq"]
    except (ValueError, KeyError, TypeError):
        findings.append(Finding("error", f"Message at byte {offset} on {channel.topic} "
                                         "is not a valid session payload"))
        return None
    return {"seq": seq, "topic": channel.topic,
            "schema": schema_names.get(channel.schema_id), "log_time": record.log_time,
            "publish_time": record.publish_time, "payload": payload}


def _classify_tail(data: bytes, offset: int, error: Exception, claims_closed: bool,
                   findings: list[Finding]):
    if isinstance(error, CRCValidationError):
        findings.append(Finding("error", "Data checksum mismatch: the file changed after "
                                         "recording or is damaged"))
        return
    remaining = len(data) - offset
    if claims_closed:
        findings.append(Finding("error", f"Damaged record at byte {offset} in a closed "
                                         f"session; later events are unreadable "
                                         f"({type(error).__name__})"))
        return
    if remaining == 0:
        return  # stopped exactly at a record boundary: a crash, reported as "not closed"
    if remaining < 9:
        findings.append(Finding("warning", f"Final record is truncated ({remaining} bytes)"))
        return
    opcode = data[offset]
    length = int.from_bytes(data[offset + 1:offset + 9], "little")
    if opcode in _KNOWN_OPCODES and length < _MAX_RECORD and 9 + length > remaining:
        findings.append(Finding("warning", f"Final record is truncated "
                                           f"({remaining} of {9 + length} bytes)"))
        return
    findings.append(Finding("error", f"Corrupted data at byte {offset}; {remaining} bytes "
                                     f"unreadable ({type(error).__name__})"))


def _check_metadata(metadata: dict, findings: list[Finding]):
    if not metadata:
        return
    version = metadata.get("schema_version")
    if not isinstance(version, int) or version > schemas.SCHEMA_VERSION:
        findings.append(Finding("error", f"Unsupported schema_version {version!r}; this "
                                         f"reader supports {schemas.SCHEMA_VERSION}"))
    if metadata.get("data_source") not in schemas.DATA_SOURCES:
        findings.append(Finding("error", f"data_source must be one of {schemas.DATA_SOURCES}, "
                                         f"found {metadata.get('data_source')!r}"))


def _check_events(events: list[dict], findings: list[Finding]):
    for index, event in enumerate(events):
        if event["seq"] != index:
            findings.append(Finding("error", f"seq gap or duplicate: event {index} has "
                                             f"seq {event['seq']} (events lost or reordered)"))
            break
    previous = None
    for event in events:
        logged = event["payload"]["_rec"].get("logged_monotonic_ns")
        if previous is not None and logged is not None and logged < previous:
            findings.append(Finding("error", f"Logged time goes backwards at seq {event['seq']}"))
            break
        previous = logged
    unknown = sorted({e["topic"] for e in events if schemas.schema_name_for_topic(e["topic"]) is None})
    for topic in unknown:
        findings.append(Finding("warning", f"Unknown topic {topic} (kept)"))
    answered = {e["payload"].get("command_id") for e in events
                if e["topic"] == "/robot/command_result"}
    for event in events:
        if event["topic"] == "/robot/command":
            command_id = event["payload"].get("command_id")
            if command_id not in answered:
                findings.append(Finding("warning", f"Command {command_id} has no result"))
