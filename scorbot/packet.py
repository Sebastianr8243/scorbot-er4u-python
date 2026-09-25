"""Timestamp complete USB responses without changing legacy packet construction."""

from dataclasses import dataclass
import threading
import time


@dataclass(frozen=True)
class PacketSnapshot:
    data: bytes
    index: int
    host_monotonic_ns: int


class TrackedInputEndpoint:
    """Endpoint proxy shared by the legacy sync and command workers."""

    def __init__(self, endpoint):
        self._endpoint = endpoint
        self._condition = threading.Condition()
        self._latest = None
        self._index = 0

    def __getattr__(self, name):
        return getattr(self._endpoint, name)

    def read(self, buffer, timeout=None):
        result = self._endpoint.read(buffer, timeout)
        size = result if isinstance(result, int) else len(result)
        if size < 0 or size > len(buffer):
            raise ValueError("Invalid USB response length")
        # Copy immediately. The legacy code reuses this mutable buffer.
        with self._condition:
            self._index += 1
            self._latest = PacketSnapshot(
                bytes(buffer[:size]), self._index, time.monotonic_ns())
            self._condition.notify_all()
        return result

    def snapshot(self, *, after_index=None, timeout=2.0, max_age=2.0):
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                sample = self._latest
                if (sample is not None and len(sample.data) >= 49
                        and (after_index is None or sample.index > after_index)
                        and time.monotonic_ns() - sample.host_monotonic_ns
                        <= int(max_age * 1e9)):
                    return sample
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("No fresh full-length USB controller response")
                self._condition.wait(remaining)
