"""Capture frames on one thread, write them on another, never block the owner.

The reader thread stamps each frame with the session clock right after
``read()`` returns (read-return time, not exposure time) and puts it on a
queue bounded by bytes; when full, the oldest frame is dropped and counted.
The writer thread drops frames older than ``max_latency_s``, JPEG-encodes the
rest and writes them; it is the only thread that writes the stream. ``stop()``
closes the stream once the writer has finished, recording any capture failure
(including a stuck reader) in the sidecar so the file never reads as clean
after a failed capture. Health rows go to a bounded deque that the owner
drains on its own thread. ``stop()`` is bounded and never raises: closing the
camera runs on a helper thread, and a thread that will not stop is left behind
(daemon) and reported as stuck.
"""

from __future__ import annotations

from collections import deque
import threading
import time

EMPTY_READS_BEFORE_FAILURE = 30
HEALTH_ROWS_KEPT = 600


def encode_jpeg(image, quality: int) -> bytes:
    import cv2  # optional dependency: pip install .[camera]
    ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        raise RuntimeError("JPEG encoding failed")
    return encoded.tobytes()


class CameraRecorder:
    def __init__(self, source, stream=None, *, fps: float = 30.0,
                 queue_bytes: int = 64 * 2 ** 20, max_latency_s: float = 1.0,
                 jpeg_quality: int = 90, clock=time.monotonic_ns, encoder=None,
                 health_interval_s: float = 1.0):
        self.source, self.stream, self.fps = source, stream, fps
        self.queue_bytes, self.max_latency_ns = queue_bytes, int(max_latency_s * 1e9)
        self.clock = clock
        self.encoder = encoder or (lambda image: encode_jpeg(image, jpeg_quality))
        self.health_interval_ns = int(health_interval_s * 1e9)
        self._queue: deque = deque()
        self._queued_bytes = 0
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._health: deque = deque(maxlen=HEALTH_ROWS_KEPT)
        self._threads: list[threading.Thread] = []
        self._state = "idle"
        self._result: str | None = None
        self.failure: str | None = None
        self.last_write_ns: int | None = None  # clock() when the last frame was written
        self.counts = {"frames": 0, "written": 0, "dropped_queue": 0, "dropped_late": 0}
        self._window = self._new_window()
        self._window_started = None
        self._last_stamp = None

    # -- owner API ------------------------------------------------------------

    @property
    def status(self) -> str:
        if self.failure:
            return f"failed: {self.failure}"
        return self._state

    def start(self) -> None:
        """Start capturing. The source must already be open."""
        if self._state != "idle":
            raise RuntimeError("A recorder starts once")
        self._state = "running"
        self._window_started = self.clock()
        for name, target in (("camera-reader", self._read_loop),
                             ("camera-writer", self._write_loop)):
            thread = threading.Thread(target=target, name=name, daemon=True)
            self._threads.append(thread)
            thread.start()

    def stop(self, timeout_s: float = 3.0) -> str:
        """Stop within ``timeout_s``; never raises. Returns the final status."""
        if self._result is not None:
            return self._result
        if self._state == "idle":
            self._result = "idle"
            return self._result
        deadline = time.monotonic() + timeout_s
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        # Closing the camera unblocks most blocked read() calls, but a driver can
        # also hang in release(), so it runs on its own thread inside the deadline.
        closer = threading.Thread(target=self._close_source, name="camera-close",
                                  daemon=True)
        closer.start()
        reader, writer = self._threads
        for thread in (reader, writer, closer):
            thread.join(max(0.0, deadline - time.monotonic()))
        stuck = [t.name for t in (reader, writer, closer) if t.is_alive()]
        with self._cond:
            if stuck and not self.failure:
                self.failure = f"stuck {', '.join(stuck)}"
        if not self.failure:
            self._state = "stopped"
        if self.stream is not None and not writer.is_alive():
            self.stream.health_summary = dict(self.counts)
            self.stream.capture_failure = self.failure
            try:
                self.stream.close()
            except Exception as error:
                with self._cond:
                    self.failure = self.failure or f"closing the stream: {error}"
        self._emit_health(force=True)
        self._result = self.status
        return self._result

    def _close_source(self) -> None:
        try:
            self.source.close()
        except Exception:
            pass

    def drain_health(self) -> list[dict]:
        """Health rows since the last call, oldest first. Call from the owner's thread."""
        rows = []
        while self._health:
            rows.append(self._health.popleft())
        return rows

    # -- threads --------------------------------------------------------------

    def _fail(self, reason: str) -> None:
        with self._cond:
            if self.failure is None:
                self.failure = reason
            self._stop.set()
            self._cond.notify_all()

    def _read_loop(self) -> None:
        number, empty = 0, 0
        try:
            while not self._stop.is_set():
                ok, image = self.source.read()
                stamp = self.clock()
                if self._stop.is_set():
                    return
                if not ok or image is None:
                    empty += 1
                    if empty >= EMPTY_READS_BEFORE_FAILURE:
                        self._fail(f"camera returned no frame {empty} times in a row")
                        return
                    continue
                empty = 0
                with self._cond:
                    self.counts["frames"] += 1
                    self._window["frames"] += 1
                    if self._last_stamp is not None:
                        gap = stamp - self._last_stamp
                        self._window["max_gap_ns"] = max(self._window["max_gap_ns"], gap)
                    self._last_stamp = stamp
                    self._queue.append((number, stamp, image))
                    self._queued_bytes += image.nbytes
                    while self._queued_bytes > self.queue_bytes and len(self._queue) > 1:
                        _, _, old = self._queue.popleft()
                        self._queued_bytes -= old.nbytes
                        self.counts["dropped_queue"] += 1
                        self._window["dropped"] += 1
                    self._cond.notify()
                number += 1
        except Exception as error:
            self._fail(f"{type(error).__name__}: {error}")

    def _write_loop(self) -> None:
        try:
            while True:
                with self._cond:
                    while not self._queue and not self._stop.is_set():
                        self._cond.wait(0.1)
                        self._emit_health_locked()
                    if self._stop.is_set():
                        return
                    number, stamp, image = self._queue.popleft()
                    self._queued_bytes -= image.nbytes
                latency = self.clock() - stamp
                if latency > self.max_latency_ns:
                    with self._cond:
                        self.counts["dropped_late"] += 1
                        self._window["dropped"] += 1
                    continue
                if self.stream is not None:
                    data = self.encoder(image)
                    self.stream.log_frame(number, data, width=int(image.shape[1]),
                                          height=int(image.shape[0]),
                                          observed_monotonic_ns=stamp)
                with self._cond:
                    self.counts["written"] += 1
                    self.last_write_ns = self.clock()
                    self._window["max_latency_ns"] = max(self._window["max_latency_ns"],
                                                         latency)
                    self._emit_health_locked()
        except Exception as error:
            self._fail(f"{type(error).__name__}: {error}")

    # -- health ---------------------------------------------------------------

    @staticmethod
    def _new_window() -> dict:
        return {"frames": 0, "dropped": 0, "max_gap_ns": 0, "max_latency_ns": 0}

    def _emit_health(self, force: bool = False) -> None:
        with self._cond:
            self._emit_health_locked(force)

    def _emit_health_locked(self, force: bool = False) -> None:
        now = self.clock()
        started = self._window_started if self._window_started is not None else now
        elapsed = now - started
        if not force and elapsed < self.health_interval_ns:
            return
        window = self._window
        period_ns = 1e9 / self.fps
        if self.failure:
            status = "failed"
        elif window["dropped"] or window["max_gap_ns"] > 2.5 * period_ns:
            status = "degraded"
        else:
            status = "ok"
        self._health.append({
            "frames": window["frames"], "dropped": window["dropped"],
            "dropped_queue": self.counts["dropped_queue"],
            "dropped_late": self.counts["dropped_late"],
            "max_gap_ms": round(window["max_gap_ns"] / 1e6, 3),
            "max_latency_ms": round(window["max_latency_ns"] / 1e6, 3),
            "measured_fps": (round(window["frames"] / (elapsed / 1e9), 2)
                             if elapsed > 0 else None),
            "queue_bytes": self._queued_bytes, "status": status,
            "failure": self.failure})
        self._window = self._new_window()
        self._window_started = now
