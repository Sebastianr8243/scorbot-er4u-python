"""Episode recorder: LeRobot's column names as JSON lines, standard library only. Convert to a LeRobotDataset later.

    <root>/info.json                  {"data_source", "fps"}   written once; a root holds one data source
    <root>/episode_000000.jsonl       one line per frame, created with "x" so an episode is never overwritten

Each frame: episode_index, frame_index, timestamp, task, observation.state (dict keyed by the robot's feature names),
action (same), next.done, data_source. Real and simulated frames never share a root (MixedDataError).
"""

from __future__ import annotations

import json
from pathlib import Path

SOURCES = ("real", "simulated")


class MixedDataError(ValueError):
    """A frame or a dataset root with a different data_source than the dataset's."""


class DatasetWriter:
    def __init__(self, root, data_source: str, fps: float):
        if data_source not in SOURCES:
            raise ValueError(f"data_source must be one of {SOURCES}, got {data_source!r}")
        self.root, self.data_source, self.fps = Path(root), data_source, fps
        info = self.root / "info.json"
        if info.exists():
            existing = json.loads(info.read_text(encoding="utf-8")).get("data_source")
            if existing != data_source:
                raise MixedDataError(f"{self.root} holds {existing} data; refusing to add {data_source} data to it")
        else:
            self.root.mkdir(parents=True, exist_ok=True)
            with open(info, "x", encoding="utf-8") as file:
                json.dump({"data_source": data_source, "fps": fps}, file, allow_nan=False)
        self._file = self._pending = None

    def start_episode(self, task: str, index: int | None = None) -> Path:
        """Open the next episode file (or `index`, which must be new). Returns its path."""
        if self._file is not None:
            self.end_episode()
        self.index = len(list(self.root.glob("episode_*.jsonl"))) if index is None else index
        path = self.root / f"episode_{self.index:06d}.jsonl"
        self._file, self._task, self._frames, self._pending = open(path, "x", encoding="utf-8"), task, 0, None
        return path

    def _write(self, record, done):
        self._file.write(json.dumps({**record, "next.done": done}, allow_nan=False) + "\n")
        self._file.flush()

    def add_frame(self, observation: dict, action: dict, data_source: str) -> None:
        """One step: the observation read before the action, and the action that was sent."""
        if self._file is None:
            raise RuntimeError("no episode open: call start_episode first")
        if data_source != self.data_source:
            raise MixedDataError(f"frame is {data_source} but this dataset is {self.data_source}")
        if self._pending is not None:       # the last frame is written at end_episode, when done is known
            self._write(self._pending, False)
        self._pending = {"episode_index": self.index, "frame_index": self._frames, "timestamp": round(self._frames / self.fps, 6),
                         "task": self._task, "observation.state": observation, "action": action, "data_source": data_source}
        self._frames += 1

    def end_episode(self) -> None:
        if self._file is not None:
            if self._pending is not None:
                self._write(self._pending, True)
            self._file.close()
        self._file = self._pending = None


def read_episode(path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]
