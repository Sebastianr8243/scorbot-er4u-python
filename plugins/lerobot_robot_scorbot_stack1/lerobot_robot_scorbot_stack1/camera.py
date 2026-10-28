"""A fake LeRobot camera for rehearsing `lerobot-record` on a laptop with no webcam (`type: scorbot_fake`).

    --robot.cameras="{front: {type: scorbot_fake, width: 64, height: 64, fps: 10}}"

Each read returns a new frame (colour ramps with a moving blue bar), so a recorded video really changes. It is marked
`simulated`, and Stack1Robot refuses it next to a real arm: a fake image beside real arm state would pass as real data.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from lerobot.cameras import Camera, CameraConfig


@CameraConfig.register_subclass("scorbot_fake")
@dataclass
class ScorbotFakeCameraConfig(CameraConfig):
    fps: int | None = 10
    width: int | None = 64
    height: int | None = 64


class ScorbotFakeCamera(Camera):
    simulated = True

    def __init__(self, config: ScorbotFakeCameraConfig):
        super().__init__(config)
        self._connected, self._count = False, 0

    @property
    def is_connected(self) -> bool:
        return self._connected

    @staticmethod
    def find_cameras() -> list[dict]:
        return []

    def connect(self, warmup: bool = True) -> None:
        self._connected = True

    def _frame(self) -> np.ndarray:
        if not self._connected:
            raise ConnectionError("the fake camera is not connected")
        image = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        image[:, :, 0] = np.linspace(0, 255, self.width, dtype=np.uint8)[None, :]
        image[:, :, 1] = np.linspace(0, 255, self.height, dtype=np.uint8)[:, None]
        start = (self._count * 4) % self.width
        image[:, start:start + max(1, self.width // 8), 2] = 255
        self._count += 1
        return image

    def read(self) -> np.ndarray:
        return self._frame()

    def async_read(self, timeout_ms: float = 200) -> np.ndarray:
        return self._frame()

    def read_latest(self, max_age_ms: int = 500) -> np.ndarray:
        return self._frame()

    def disconnect(self) -> None:
        self._connected = False
