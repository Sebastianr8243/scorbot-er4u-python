"""The lab session's camera: one recorder for the whole session, with liveness.

Live means the recorder has not failed and wrote a frame less than
``stale_s`` ago; a stalled reader can keep reporting "ok" health rows with no
frames, so health rows alone are not enough. The camera never disarms the
arm or latches a robot fault: it records training data, it is not the
operator's view.
"""

from __future__ import annotations

import time

from ..camera.recorder import CameraRecorder
from ..camera.stream import CameraStream

CAMERA_ID = "main"


class LabCamera:
    def __init__(self, source_factory, *, stale_s: float = 1.0, clock=time.monotonic_ns):
        self.source_factory = source_factory
        self.stale_ns, self.clock = int(stale_s * 1e9), clock
        self.recorder: CameraRecorder | None = None
        self.settings: dict = {}

    def start(self, writer) -> None:
        source = self.source_factory()
        source.open()
        stream = None
        try:
            self.settings = source.settings()
            stream = CameraStream.create(writer, CAMERA_ID, self.settings)
            recorder = CameraRecorder(source, stream, clock=self.clock)
            recorder.start()
        except Exception:
            # Nothing else holds these yet, so nothing else would close them.
            source.close()
            if stream is not None:
                stream.close()
            raise
        self.recorder = recorder

    def live(self) -> bool:
        recorder = self.recorder
        return (recorder is not None and recorder.failure is None
                and recorder.last_write_ns is not None
                and self.clock() - recorder.last_write_ns < self.stale_ns)

    def problem(self) -> str:
        recorder = self.recorder
        if recorder is None:
            return "camera not started"
        if recorder.failure is not None:
            return f"camera failed: {recorder.failure}"
        return "camera stalled"

    def frames_written(self) -> int:
        return 0 if self.recorder is None else self.recorder.counts["written"]

    def drain(self) -> list[dict]:
        return [] if self.recorder is None else self.recorder.drain_health()

    def stop(self) -> str:
        return "not started" if self.recorder is None else self.recorder.stop()
