"""Where frames come from: a real webcam through OpenCV, or a deterministic fake.

``OpenCVSource`` follows LeRobot's ``OpenCVCamera`` design (config names,
warm-up) without depending on it. It records what was requested, whether each
``set()`` succeeded and the raw ``get()`` value, and never interprets the raw
value as "accepted": OpenCV's DirectShow getters return backend flags (for
example autofocus) or -1 (auto exposure). What it verifies instead is the
frame size actually delivered and the frame rate measured during warm-up.
Frames stay BGR, which is what ``cv2.imencode`` expects.
"""

from __future__ import annotations

import math
import sys
import threading
import time
from typing import Protocol

import numpy as np


class FrameSource(Protocol):
    def open(self) -> None: ...
    def read(self) -> tuple[bool, np.ndarray | None]: ...
    def settings(self) -> dict: ...
    def close(self) -> None: ...


_BLOCK = 4  # pixels per encoded bit block


def _encode_number(image: np.ndarray, number: int) -> None:
    for bit in range(16):
        value = 255 if (number >> bit) & 1 else 0
        image[0:_BLOCK, bit * _BLOCK:(bit + 1) * _BLOCK, :] = value


def decode_frame_number(image: np.ndarray) -> int:
    return sum(1 << bit for bit in range(16)
               if image[_BLOCK // 2, bit * _BLOCK + _BLOCK // 2, 0] > 127)


class FakeSource:
    """Deterministic frames with their number drawn in the top-left corner."""

    def __init__(self, *, width: int = 64, height: int = 48, fps: float = 30.0,
                 pace: bool = False, fail_at: int | None = None,
                 block_at: int | None = None, release_on_close: bool = True):
        if width < 16 * _BLOCK or height < _BLOCK:
            raise ValueError(f"Fake frames need at least {16 * _BLOCK}x{_BLOCK} pixels")
        self.width, self.height, self.fps = width, height, fps
        self.pace, self.fail_at, self.block_at = pace, fail_at, block_at
        self.release_on_close = release_on_close
        self._next = 0
        self._closed = threading.Event()

    def open(self) -> None:
        self._next = 0
        self._closed.clear()

    def read(self):
        if self._closed.is_set():
            return False, None
        number = self._next
        self._next += 1
        if number == self.fail_at:
            raise RuntimeError(f"Fake camera failure at frame {number}")
        if number == self.block_at:
            if self.release_on_close:
                self._closed.wait()
            else:
                threading.Event().wait()  # never released
            return False, None
        if self.pace:
            time.sleep(1.0 / self.fps)
        image = np.full((self.height, self.width, 3), 64, np.uint8)
        _encode_number(image, number)
        return True, image

    def settings(self) -> dict:
        return {"backend": "fake",
                "requested": {"width": self.width, "height": self.height, "fps": self.fps},
                "actual": {"width": self.width, "height": self.height,
                           "measured_fps": None}}

    def close(self) -> None:
        self._closed.set()


def _finite_or_none(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


class OpenCVSource:
    """A webcam via OpenCV; DirectShow on Windows (MSMF starts slowly)."""

    _PROPS = (("width", "CAP_PROP_FRAME_WIDTH"), ("height", "CAP_PROP_FRAME_HEIGHT"),
              ("fps", "CAP_PROP_FPS"), ("autofocus", "CAP_PROP_AUTOFOCUS"),
              ("focus", "CAP_PROP_FOCUS"), ("auto_exposure", "CAP_PROP_AUTO_EXPOSURE"),
              ("exposure", "CAP_PROP_EXPOSURE"))

    def __init__(self, index_or_path=0, *, width: int = 640, height: int = 480,
                 fps: float = 30.0, focus: float | None = None,
                 exposure: float | None = None, warmup_s: float = 1.0, cv2_module=None):
        self.index_or_path, self.width, self.height, self.fps = index_or_path, width, height, fps
        self.focus, self.exposure, self.warmup_s = focus, exposure, warmup_s
        self._cv2 = cv2_module
        self._capture = None
        self._settings: dict = {}

    def _module(self):
        if self._cv2 is None:
            import cv2  # optional dependency: pip install .[camera]
            self._cv2 = cv2
        return self._cv2

    def open(self) -> None:
        cv2 = self._module()
        backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        capture = cv2.VideoCapture(self.index_or_path, backend)
        if not capture.isOpened():
            raise RuntimeError(f"Could not open camera {self.index_or_path!r}")
        requested = {"width": self.width, "height": self.height, "fps": self.fps,
                     "autofocus": 0}
        if self.focus is not None:
            requested["focus"] = self.focus
        if self.exposure is not None:
            # Manual exposure: DirectShow treats a value rounding to 0 as manual,
            # V4L2 uses 1 (OpenCV backend sources; unverified with our camera).
            requested["auto_exposure"] = 0 if sys.platform == "win32" else 1
            requested["exposure"] = self.exposure
        names = dict(self._PROPS)
        set_ok = {key: bool(capture.set(getattr(cv2, names[key]), value))
                  for key, value in requested.items()}
        read_back = {key: _finite_or_none(capture.get(getattr(cv2, prop)))
                     for key, prop in self._PROPS}
        self._capture = capture
        try:
            actual = self._warm_up()
        except Exception:
            self.close()
            raise
        self._settings = {"backend": "directshow" if sys.platform == "win32" else "default",
                          "index_or_path": str(self.index_or_path),
                          "requested": requested, "set_ok": set_ok,
                          "read_back": read_back, "actual": actual,
                          "warmup_s": self.warmup_s}

    def _warm_up(self) -> dict:
        ok, image = self._capture.read()
        if not ok or image is None:
            raise RuntimeError("Camera opened but returned no frame")
        height, width = image.shape[:2]
        frames, started = 0, time.monotonic()
        while time.monotonic() - started < self.warmup_s:
            ok, _ = self._capture.read()
            frames += bool(ok)
        elapsed = time.monotonic() - started
        measured = round(frames / elapsed, 2) if elapsed > 0 and frames else None
        return {"width": int(width), "height": int(height), "measured_fps": measured}

    def read(self):
        capture = self._capture
        if capture is None:
            return False, None
        return capture.read()

    def settings(self) -> dict:
        return dict(self._settings)

    def close(self) -> None:
        capture, self._capture = self._capture, None
        if capture is not None:
            capture.release()
