"""HTTP multipart MJPEG camera; no OpenCV capture backend or device property writes."""

from dataclasses import dataclass
import math
import threading
import time
from urllib.parse import urlsplit
from urllib.request import urlopen

import cv2
import numpy as np
from lerobot.cameras import Camera, CameraConfig
from lerobot.cameras.configs import ColorMode


@CameraConfig.register_subclass("mjpeg")
@dataclass(kw_only=True)
class MJPEGCameraConfig(CameraConfig):
    url: str
    fps: int = 30
    color_mode: ColorMode = ColorMode.RGB
    timeout_s: float = 2.0

    def __post_init__(self):
        address = urlsplit(self.url)
        if address.scheme not in ("http", "https") or not address.hostname:
            raise ValueError("url must be an HTTP or HTTPS stream address")
        self.color_mode = ColorMode(self.color_mode)
        for name in ("fps", "width", "height", "timeout_s"):
            value = getattr(self, name)
            if value is not None and (not math.isfinite(value) or value <= 0):
                raise ValueError(f"{name} must be positive and finite")


class MJPEGCamera(Camera):
    """Keep one latest decoded frame; async_read consumes each frame at most once."""

    def __init__(self, config: MJPEGCameraConfig):
        super().__init__(config)
        self.config = config
        self._condition = threading.Condition()
        self._stop = threading.Event()
        self._response = None
        self._thread = None
        self._frame = None
        self._frame_at = 0.0
        self._sequence = self._consumed = 0
        self._error = None

    @property
    def is_connected(self) -> bool:
        return self._response is not None

    @staticmethod
    def find_cameras() -> list[dict]:
        return []  # Network stream addresses must be supplied explicitly.

    def connect(self, warmup: bool = True) -> None:
        if self.is_connected:
            raise ConnectionError("MJPEG camera is already connected")
        self._stop.clear()
        self._frame, self._frame_at, self._error = None, 0.0, None
        self._sequence = self._consumed = 0
        try:
            self._response = urlopen(self.config.url, timeout=self.config.timeout_s)
            self._thread = threading.Thread(target=self._capture, name="mjpeg-camera", daemon=True)
            self._thread.start()
            if warmup:
                self.read()
        except Exception:
            self.disconnect()
            raise

    def _capture(self) -> None:
        response = self._response
        buffer = bytearray()
        try:
            while not self._stop.is_set():
                chunk = response.read1(65536)
                if not chunk:
                    raise EOFError("MJPEG stream ended")
                buffer.extend(chunk)
                while True:
                    start = buffer.find(b"\xff\xd8")
                    if start < 0:
                        buffer[:] = buffer[-1:]  # Preserve a split SOI marker.
                        break
                    if start:
                        del buffer[:start]
                    end = buffer.find(b"\xff\xd9", 2)
                    if end < 0:
                        if len(buffer) > 16 * 1024 * 1024:
                            raise ValueError("MJPEG frame exceeds 16 MiB")
                        break
                    jpeg = bytes(buffer[:end + 2])
                    del buffer[:end + 2]
                    frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
                    if frame is None:
                        continue
                    height, width = frame.shape[:2]
                    size = (self.width or width, self.height or height)
                    if size != (width, height):
                        frame = cv2.resize(frame, size)
                    if self.config.color_mode == ColorMode.RGB:
                        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    with self._condition:
                        self._frame, self._frame_at = frame, time.monotonic()
                        self._sequence += 1
                        self._condition.notify_all()
        except Exception as error:
            with self._condition:
                self._error = error
                self._condition.notify_all()

    def read(self):
        return self.async_read(timeout_ms=self.config.timeout_s * 1000)

    def async_read(self, timeout_ms: float = 200):
        if not math.isfinite(timeout_ms) or timeout_ms < 0:
            raise ValueError("timeout_ms must be nonnegative and finite")
        deadline = time.monotonic() + timeout_ms / 1000
        with self._condition:
            while True:
                if not self.is_connected or self._stop.is_set():
                    raise ConnectionError("MJPEG camera is not connected")
                if self._error is not None:
                    raise TimeoutError("MJPEG stream stopped delivering frames") from self._error
                if (self._frame is not None and self._sequence > self._consumed
                        and time.monotonic() - self._frame_at <= self.config.timeout_s):
                    self._consumed = self._sequence
                    return self._frame.copy()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Timed out waiting for a new MJPEG frame")
                self._condition.wait(remaining)

    def read_latest(self, max_age_ms: int = 500):
        with self._condition:
            if not self.is_connected or self._stop.is_set():
                raise ConnectionError("MJPEG camera is not connected")
            if self._frame is None:
                raise RuntimeError("MJPEG camera has not captured a frame")
            if self._error is not None or (time.monotonic() - self._frame_at) * 1000 > max_age_ms:
                raise TimeoutError("Latest MJPEG frame is stale")
            return self._frame.copy()

    def disconnect(self) -> None:
        self._stop.set()
        with self._condition:
            self._condition.notify_all()
        # Socket reads have timeout_s set by urlopen; join before closing the buffered reader.
        if self._thread is not None:
            self._thread.join(self.config.timeout_s + 1)
        response, self._response = self._response, None
        if response is not None:
            response.close()
