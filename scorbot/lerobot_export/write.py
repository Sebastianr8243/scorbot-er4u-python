"""Build a LeRobot dataset from checked, resampled episodes. Imports lerobot.

Built in ``<out>.partial-<hex>``, verified by reopening, then renamed to
``<out>``; any failure removes the partial folder. Never uploads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil

import numpy as np

from .load import CAMERA_ID, MOTORS

EXPORTER_VERSION = 1
UNITS = "uncalibrated encoder counts from session home"


@dataclass
class ExportPlan:
    sessions: list
    episodes: list = field(default_factory=list)   # (LabSessionData, EpisodeFrames)
    refusals: list = field(default_factory=list)


def _features(video: bool, size):
    features = {"observation.state": {"dtype": "float32", "shape": (5,), "names": list(MOTORS)},
                "action": {"dtype": "float32", "shape": (5,), "names": list(MOTORS)}}
    if video:
        width, height = size
        features[f"observation.images.{CAMERA_ID}"] = {
            "dtype": "video", "shape": (height, width, 3),
            "names": ["height", "width", "channels"]}
    return features


def _episode_images(data, frame_seq):
    """One RGB image per tick, decoded as the stream is read: memory stays one frame.

    ``frame_seq`` never decreases (latest frame at or before each tick), so one
    pass over the camera file is enough; a frame reused by several ticks is
    decoded once.
    """
    import cv2
    from ..camera import stream as camera_stream
    frames = enumerate(camera_stream.iter_frames(data.mcap_folder, CAMERA_ID))
    seq, image = -1, None
    for wanted in frame_seq:
        while seq < wanted:
            seq, frame = next(frames)
            if seq == wanted:
                bgr = cv2.imdecode(np.frombuffer(frame.data, np.uint8), cv2.IMREAD_COLOR)
                image = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        yield image


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_dataset(plan: ExportPlan, out: Path, repo_id: str, fps: int, video: bool) -> Path:
    from lerobot.datasets.lerobot_dataset import LeRobotDataset
    out = Path(out)
    if out.exists():
        raise FileExistsError(f"{out} already exists; choose a new --out")
    partial = out.with_name(f"{out.name}.partial-{secrets.token_hex(4)}")
    try:
        size = None
        if video:
            data, _ = plan.episodes[0]
            size = (data.camera.frames[0]["width"], data.camera.frames[0]["height"])
        dataset = LeRobotDataset.create(repo_id, fps, _features(video, size), root=partial,
                                        robot_type="scorbot_er4u", use_videos=video)
        exported = []
        for index, (data, frames) in enumerate(plan.episodes):
            images = (_episode_images(data, frames.frame_seq) if video
                      else iter(lambda: None, object()))
            for k, image in zip(range(len(frames.times_ns)), images):
                frame = {"observation.state": np.asarray(frames.state[k], np.float32),
                         "action": np.asarray(frames.action[k], np.float32),
                         "task": frames.task}
                if video:
                    frame[f"observation.images.{CAMERA_ID}"] = image
                dataset.add_frame(frame)
            dataset.save_episode()
            exported.append({"dataset_episode": index, "session": data.name,
                             "source_episode": frames.episode, "task": frames.task,
                             "frames": len(frames.times_ns)})
        dataset.finalize()
        _write_provenance(partial, plan, exported, fps, video)
        reopened = LeRobotDataset(repo_id, root=partial)
        expected = sum(len(f.times_ns) for _, f in plan.episodes)
        if reopened.num_episodes != len(plan.episodes) or reopened.num_frames != expected:
            raise RuntimeError(f"verification failed: {reopened.num_episodes} episodes, "
                               f"{reopened.num_frames} frames; expected "
                               f"{len(plan.episodes)}, {expected}")
        del reopened, dataset
        os.replace(partial, out)
        return out
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise


def _write_provenance(folder: Path, plan: ExportPlan, exported, fps, video):
    sessions = []
    for data in plan.sessions:
        session_row = next((r for r in data.rows if r.get("type") == "session"), {})
        sessions.append({"lab_jsonl": str(data.jsonl_path), "sha256": _sha256(data.jsonl_path),
                         "mcap_session": data.mcap_folder.name if data.mcap_folder else None,
                         "motion_source_sha256": session_row.get("motion_source_sha256"),
                         "software_commit": session_row.get("software_commit"),
                         "camera_frames": len(data.camera.frames) if data.camera else 0})
    source = plan.sessions[0].data_source if plan.sessions else None
    provenance = {"exporter_version": EXPORTER_VERSION,
                  "created_utc": datetime.now(timezone.utc).isoformat(),
                  "fps": fps, "units": UNITS, "calibration": "uncalibrated",
                  "image_latency": "unmeasured" if video else None,
                  "data_source": source, "simulated": source != "real",
                  "video": video, "sessions": sessions, "episodes": exported,
                  "refused": [vars(r) for r in plan.refusals]}
    (folder / "scorbot_provenance.json").write_text(
        json.dumps(provenance, indent=2, allow_nan=False) + "\n", encoding="utf-8")
