"""SIMULATED packet link for desk tests of the real legacy workers; never opens USB.

DryScorbot overrides only the existing _open_link seam. Scorbot.connect,
msg_start, syncro and the command worker run unchanged. Plant answers plain
status polls too. Latency is a model, never controller timing evidence.
"""

from array import array
from dataclasses import replace
import math
import queue
import time

from ..robot import Scorbot
from ..transport.device_file import DeviceLinkError, PACKET_SIZE
from ..vendor_home_model import Plant


class DryLink:
    """SIMULATED pyusb-shaped endpoints, with latency from OUT to available IN."""

    def __init__(self, *, latency_ms=0, plant=None):
        if not math.isfinite(latency_ms) or latency_ms < 0:
            raise ValueError("SIMULATED latency_ms must be finite and non-negative")
        self.latency_ms = latency_ms
        self.plant = plant if plant is not None else Plant()
        self.sent = []
        self.closed = False
        self._replies = queue.Queue()
        self.endpoint_out = _OutEndpoint(self)
        self.endpoint_in = _InEndpoint(self)

    def _live(self):
        if self.closed:
            raise DeviceLinkError("SIMULATED link is closed")

    def close(self):
        self.closed = True


class _OutEndpoint:
    wMaxPacketSize = PACKET_SIZE

    def __init__(self, link):
        self.link = link

    def write(self, data, timeout=None):
        self.link._live()
        if len(data) != PACKET_SIZE:
            raise ValueError("SIMULATED link takes 64-byte packets")
        packet = bytes(data)
        self.link.sent.append(packet)
        self.link._replies.put((time.monotonic() + self.link.latency_ms / 1000,
                                self.link.plant.exchange(packet)))
        return PACKET_SIZE


class _InEndpoint:
    wMaxPacketSize = PACKET_SIZE

    def __init__(self, link):
        self.link = link
        self._pending = None

    def read(self, buffer, timeout=None):
        self.link._live()
        deadline = time.monotonic() + timeout / 1000 if timeout and timeout > 0 else None
        try:
            ready, data = (self._pending if self._pending is not None else
                           self.link._replies.get(timeout=timeout / 1000 if timeout else None))
        except queue.Empty as exc:
            raise TimeoutError("SIMULATED no pending reply") from exc
        delay = max(0, ready - time.monotonic())
        if deadline is not None and ready > deadline:
            self._pending = (ready, data)
            time.sleep(max(0, deadline - time.monotonic()))
            raise TimeoutError("SIMULATED reply latency exceeded timeout")
        time.sleep(delay)
        self._pending = None
        buffer[:PACKET_SIZE] = array("B", data) if isinstance(buffer, array) else data
        return PACKET_SIZE


class DryScorbot(Scorbot):
    """SIMULATED injection seam; all connection and worker logic is inherited."""

    def __init__(self, *, link=None, latency_ms=0, **kwargs):
        super().__init__(transport="device_file", **kwargs)
        self.dry_link = link if link is not None else DryLink(latency_ms=latency_ms)

    def _open_link(self):
        self.transport_used = "SIMULATED"
        return self.dry_link

    def _record(self, event, **fields):
        super()._record(event, data_source="SIMULATED", simulated=True, **fields)

    def get_state_and_packet(self, *, after_index=None):
        state, packet = super().get_state_and_packet(after_index=after_index)
        return replace(state, simulated=True), packet
