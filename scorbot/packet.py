"""Timestamp complete USB responses without changing legacy packet construction."""

from dataclasses import dataclass
import threading
import time

from .state import PACKET_MIN_LENGTH


@dataclass(frozen=True)
class PacketSnapshot:
    data: bytes
    index: int
    host_monotonic_ns: int


MAX_TRACE_PACKETS = 4000


class PacketTrace:
    """Copies of USB packets in both directions, kept only while started.

    Recording copies bytes the legacy code already built or read; it never
    changes a packet or the legacy sleeps. Bounded: packets past ``limit`` are
    counted as dropped, not stored.
    """

    def __init__(self, limit: int = MAX_TRACE_PACKETS):
        self.limit = limit
        self._lock = threading.Lock()
        self._packets = None
        self._dropped = 0

    def start(self) -> None:
        with self._lock:
            self._packets = []
            self._dropped = 0

    def stop(self) -> tuple[list[dict], int]:
        """Stop recording; return the packets and how many were dropped."""
        with self._lock:
            packets, dropped = self._packets or [], self._dropped
            self._packets = None
            return packets, dropped

    def add(self, direction: str, data, host_monotonic_ns: int | None = None) -> None:
        with self._lock:
            if self._packets is None:
                return
            if len(self._packets) >= self.limit:
                self._dropped += 1
                return
            self._packets.append({
                "direction": direction,
                "host_monotonic_ns": host_monotonic_ns or time.monotonic_ns(),
                "hex": bytes(data).hex()})


class TrackedOutputEndpoint:
    """Endpoint proxy that copies each written packet into a ``PacketTrace``."""

    def __init__(self, endpoint, trace: PacketTrace):
        self._endpoint = endpoint
        self._trace = trace

    def __getattr__(self, name):
        return getattr(self._endpoint, name)

    def write(self, data, timeout=None):
        result = self._endpoint.write(data, timeout)
        self._trace.add("out", data)
        return result


class TrackedInputEndpoint:
    """Endpoint proxy shared by the legacy sync and command workers."""

    def __init__(self, endpoint, trace: PacketTrace | None = None):
        self._endpoint = endpoint
        self._trace = trace
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
            latest = self._latest
        if self._trace is not None:
            self._trace.add("in", latest.data, latest.host_monotonic_ns)
        return result

    def snapshot(self, *, after_index=None, timeout=2.0, max_age=2.0):
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                sample = self._latest
                if (sample is not None and len(sample.data) >= PACKET_MIN_LENGTH
                        and (after_index is None or sample.index > after_index)
                        and time.monotonic_ns() - sample.host_monotonic_ns
                        <= int(max_age * 1e9)):
                    return sample
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("No fresh full-length USB controller response")
                self._condition.wait(remaining)
