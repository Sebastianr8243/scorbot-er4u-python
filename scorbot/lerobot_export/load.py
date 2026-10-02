"""Read one lab session into plain data: the lab JSONL (primary), the SDK's
controller events (what each jog actually sent, and its USB packets), the
MCAP session and the camera index. Never imports lerobot or USB code."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path

from ..calibration import signed_count_delta
from ..camera.stream import StreamIndex, scan_stream
from ..session.replay import Session, load_session
from ..state import decode_state

MOTORS = ("base", "shoulder", "elbow", "wrist_motor_1", "wrist_motor_2")
CAMERA_ID = "main"


@dataclass
class Jog:
    index: int
    joint: str
    command_ns: int | None = None
    result_ns: int | None = None
    sdk_start_raw: dict = field(default_factory=dict)
    deltas: dict = field(default_factory=dict)
    sdk_target_signed: dict = field(default_factory=dict)
    lab_target_signed: dict | None = None
    trace: list = field(default_factory=list)
    trace_dropped: int | None = None


@dataclass
class Episode:
    number: int
    task: str
    status: str
    reason: str | None
    start_ns: int
    end_ns: int | None
    camera: bool


@dataclass
class LabSessionData:
    jsonl_path: Path
    name: str
    rows: list
    data_source: str | None = None
    mcap_folder: Path | None = None
    mcap: Session | None = None
    home_raw: dict | None = None
    states: list = field(default_factory=list)
    jogs: list = field(default_factory=list)
    lab_command_count: int = 0
    episodes: list = field(default_factory=list)
    mcap_episodes: list = field(default_factory=list)
    camera: StreamIndex | None = None
    clock_resolution_s: float | None = None
    load_errors: list = field(default_factory=list)


def rel(raw: dict, home: dict) -> list[int]:
    """Counts from the session home for each motor, across the 0/65535 wrap."""
    return [signed_count_delta(raw[m], home[m]) for m in MOTORS]


def _read_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def _decode_trace(packets) -> list:
    """(time, raw encoder counts) for every decodable controller-to-PC packet."""
    samples = []
    for packet in packets:
        if packet.get("direction") != "in":
            continue
        try:
            state = decode_state(bytes.fromhex(packet["hex"]), connected=True, enabled=True,
                                 homed=True, fault=None)
        except (ValueError, KeyError, TypeError):
            continue
        samples.append((int(packet["host_monotonic_ns"]), dict(state.encoder_counts)))
    return samples


def _jogs_from_controller(rows) -> list[Jog]:
    jogs: list[Jog] = []
    for row in rows:
        event = row.get("event")
        if event == "motion_preview":
            plan = row.get("plan", {})
            jogs.append(Jog(index=len(jogs), joint=plan.get("joint"),
                            sdk_start_raw=dict(row["state"]["encoder_counts"]),
                            deltas=dict(plan.get("motor_count_deltas", {})),
                            sdk_target_signed=dict(plan.get("target_signed_counts", {}))))
        elif not jogs:
            continue
        elif event == "motion_start":
            jogs[-1].command_ns = row["host_monotonic_ns"]
        elif event == "motion_trace":
            jogs[-1].trace = _decode_trace(row.get("packets", []))
            jogs[-1].trace_dropped = row.get("dropped_packets", 0)
        elif event == "motion_complete":
            jogs[-1].result_ns = row["host_monotonic_ns"]
    return jogs


def _episodes(rows) -> list[Episode]:
    starts, ends = {}, {}
    for row in rows:
        if row["type"] == "episode_start":
            starts[row["episode"]] = row
        elif row["type"] == "episode_end":
            ends[row["episode"]] = row
    episodes = []
    for number, start in sorted(starts.items()):
        end = ends.get(number)
        episodes.append(Episode(number, start["task"],
                                end["status"] if end else "missing end",
                                end.get("reason") if end else "no episode_end row",
                                start["host_monotonic_ns"],
                                end["host_monotonic_ns"] if end else None,
                                bool(start.get("camera"))))
    return episodes


def load_lab_session(jsonl_path) -> LabSessionData:
    path = Path(jsonl_path)
    rows = _read_jsonl(path)
    data = LabSessionData(path, path.stem, rows)
    session_row = next((r for r in rows if r["type"] == "session"), None)
    recorder = next((r for r in rows if r["type"] == "recorder"), None)
    homes = [r for r in rows if r["type"] == "home_complete"]
    data.data_source = session_row.get("data_source") if session_row else None
    data.home_raw = dict(homes[-1]["state"]["encoder_counts"]) if homes else None
    data.episodes = _episodes(rows)
    if session_row and session_row.get("controller_event_log"):
        events_path = path.parent / session_row["controller_event_log"]
        if events_path.is_file():
            data.jogs = _jogs_from_controller(_read_jsonl(events_path))
        else:
            data.load_errors.append(f"SDK controller event log not found: {events_path}")
    if recorder is None:
        data.load_errors.append("no recorder row: the MCAP session is unknown")
        return data
    folder = path.parent / "sessions" / recorder["mcap_session"]
    if not (folder / "session.mcap").is_file():
        data.load_errors.append(f"MCAP session folder not found: {folder}")
        return data
    data.mcap_folder = folder
    data.mcap = load_session(folder)
    # The recorder row only names a folder; prove the MCAP is this session's.
    for key in ("data_source", "robot_id"):
        lab_value = (session_row or {}).get(key)
        mcap_value = data.mcap.metadata.get(key)
        if lab_value != mcap_value:
            data.load_errors.append(f"MCAP {key} {mcap_value!r} differs from the lab log's "
                                    f"{lab_value!r}: wrong session folder")
    data.clock_resolution_s = (data.mcap.metadata.get("clock") or {}).get(
        "monotonic_resolution_s")
    lab_targets, lab_joints = [], []
    for event in data.mcap.events:
        payload = event["payload"]
        if event["topic"] == "/robot/state":
            t = payload["_rec"].get("observed_monotonic_ns") or payload.get("host_monotonic_ns")
            if t is not None:
                data.states.append((int(t), dict(payload["encoder_counts"])))
        elif event["topic"] == "/robot/command" and payload.get("kind") == "jog_joint":
            lab_targets.append(payload["params"].get("target_signed_counts"))
            lab_joints.append(payload["params"].get("joint"))
        elif event["topic"] == "/session/episode":
            data.mcap_episodes.append(payload)
    data.states.sort(key=lambda sample: sample[0])
    data.lab_command_count = len(lab_targets)
    if len(lab_targets) != len(data.jogs):
        # A missing SDK record would leave a real move labelled "stay still".
        data.load_errors.append(f"{len(lab_targets)} jog commands in the MCAP but "
                                f"{len(data.jogs)} SDK jog records: jogs cannot be matched")
    for jog, target, joint in zip(data.jogs, lab_targets, lab_joints):
        jog.lab_target_signed = target
        if joint != jog.joint:
            data.load_errors.append(f"jog {jog.index}: MCAP command moves {joint!r} but the "
                                    f"SDK record moves {jog.joint!r}")
    if (folder / f"camera-{CAMERA_ID}.mcap").is_file():
        data.camera = scan_stream(folder, CAMERA_ID)
    return data
