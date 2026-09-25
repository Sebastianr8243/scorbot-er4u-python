"""A fake ER-4U controller behind the real ``Scorbot`` facade. No USB, no motion.

Only ``connect`` and ``disconnect`` are replaced. Commands still pass through
``Scorbot._command`` (timeouts, error codes, fault latch) and feedback through
``get_state`` / ``decode_state``, so the simulator exercises the same safety
code as the lab. The motion model is deliberately simple: a jog changes the
motor counts by exactly the legacy plan; there is no timing, dynamics,
backlash, gravity or collision model. Every state is marked ``simulated``.
"""

from __future__ import annotations

import dataclasses
import queue
import threading
import time

from .packet import PacketSnapshot
from .robot import Scorbot, ScorbotError
from .state import ENCODER_OFFSETS, JOINTS

FAULT_KINDS = ("timeout", "late_answer", "controller_error", "worker_crash",
               "stale_feedback", "corrupt_packet")
MAX_COUNT = 65535
_EXIT, _MOTORS_OFF, _MOTORS_ON, _HOME = 528, 16, 17, 18
_JOG_ORDERS = set(range(4, 14))
_ERROR_UNKNOWN_ORDER, _ERROR_INJECTED, _ERROR_COUNT_RANGE = 1, 3, 4


def encode_packet(signed_counts: dict[str, int], switch_bits: int = 0,
                  sign_override: dict[str, int] | None = None) -> bytes:
    """Build a 64-byte controller response that ``decode_state`` parses back."""
    packet = bytearray(64)
    packet[5] = switch_bits & 0xFF
    for name, offset in zip(JOINTS, ENCODER_OFFSETS):
        value = int(signed_counts.get(name, 0))
        if not -MAX_COUNT <= value <= MAX_COUNT:
            raise ValueError(f"Simulated count for {name} out of range: {value}")
        raw, sign = (value, 128) if value >= 0 else (value + MAX_COUNT, 127)
        packet[offset:offset + 2] = raw.to_bytes(2, "little")
        packet[offset + 2] = sign
    for name, sign in (sign_override or {}).items():
        packet[ENCODER_OFFSETS[JOINTS.index(name)] + 2] = sign
    return bytes(packet)


class SimulatedController:
    """In-memory controller: answers the legacy command queue and serves packets."""

    def __init__(self, *, home_counts: dict[str, int] | None = None,
                 start_counts: dict[str, int] | None = None, step_delay_s: float = 0.0):
        self.home_counts = {name: 0 for name in JOINTS}
        self.home_counts.update(home_counts or {})
        self.counts = {name: 0 for name in JOINTS}
        self.counts.update(start_counts or {})
        self.step_delay_s = step_delay_s
        self.late_answer_s = 0.5
        self.motors_on = False
        self.switch_bits = 0
        self.commands: list[list] = []
        self._pending: set[str] = set()
        self._lock = threading.Condition()
        self._index = 0
        self._plan_jog = None

    def inject(self, kind: str) -> None:
        """Arm a one-shot fault for the next command or feedback read."""
        if kind not in FAULT_KINDS:
            raise ValueError(f"Fault kind must be one of {FAULT_KINDS}")
        with self._lock:
            self._pending.add(kind)

    def _take(self, kind: str) -> bool:
        with self._lock:
            if kind in self._pending:
                self._pending.discard(kind)
                return True
            return False

    # -- input endpoint seam (Scorbot.get_state -> self._input.snapshot) ----

    def snapshot(self, *, after_index=None, timeout=2.0, max_age=2.0) -> PacketSnapshot:
        if self._take("stale_feedback"):
            raise TimeoutError("Simulated stale feedback: no fresh controller response")
        override = {"base": 0} if self._take("corrupt_packet") else None
        with self._lock:
            self._index += 1
            data = encode_packet(self.counts, self.switch_bits, override)
            return PacketSnapshot(data, self._index, time.monotonic_ns())

    # -- command worker seam (Scorbot._commands / Scorbot._results) --------

    def worker(self, commands: queue.Queue, results: queue.Queue) -> None:
        while True:
            payload = commands.get()
            self.commands.append(list(payload))
            order = payload[0]
            if order == _EXIT:
                self.motors_on = False
                results.put(0)
                return
            if self._take("worker_crash"):
                # Like Scorbot._run_command_worker: report the exception, then die.
                # A real thread is still alive for a moment after reporting.
                results.put(RuntimeError("Simulated command worker crash"))
                time.sleep(0.3)
                return
            if self._take("timeout"):
                continue  # never answer; the facade's timeout must fire
            if self._take("late_answer"):
                # Answer, but only after the facade has given up: the stale 0 then
                # sits in the result queue, as it can on hardware.
                threading.Timer(self.late_answer_s, results.put, args=(0,)).start()
                continue
            if self._take("controller_error"):
                results.put(_ERROR_INJECTED)
                results.put(0)
                continue
            code = self._apply(payload)
            if code:
                results.put(code)
            results.put(0)

    def _apply(self, payload) -> int:
        order = payload[0]
        if order == _MOTORS_OFF:
            self.motors_on = False
        elif order == _MOTORS_ON:
            self.motors_on = True
        elif order == _HOME:
            with self._lock:
                self.counts = dict(self.home_counts)
        elif order in _JOG_ORDERS:
            plan = self._plan_jog(order, float(payload[2]), int(payload[1]))
            deltas = plan["motor_count_deltas"]
            with self._lock:
                targets = {name: self.counts[name] + delta for name, delta in deltas.items()}
                if any(abs(value) > MAX_COUNT for value in targets.values()):
                    return _ERROR_COUNT_RANGE
            for _ in plan["increments"]:
                if self.step_delay_s:
                    time.sleep(self.step_delay_s)
            with self._lock:
                self.counts.update(targets)
        else:
            return _ERROR_UNKNOWN_ORDER
        return 0


class SimulatedScorbot(Scorbot):
    """``Scorbot`` with a simulated controller. States and log rows say simulated."""

    def __init__(self, *, controller: SimulatedController | None = None, **kwargs):
        super().__init__(**kwargs)
        self.sim = controller or SimulatedController()

    def _record(self, event: str, **fields):
        super()._record(event, simulated=True, **fields)

    def connect(self):
        if self._device is not None:
            raise ScorbotError("Already connected")
        if self._fault is not None:
            raise ScorbotError("Create a new Scorbot instance after a fault")
        self._cancel_event.clear()
        self.sim._plan_jog = self._legacy("motion_profile").plan_jog
        self._device = "simulated-controller"
        self._input = self.sim
        self._command_thread = threading.Thread(
            target=self.sim.worker, args=(self._commands, self._results),
            daemon=True, name="scorbot-simulated-commands")
        self._command_thread.start()
        try:
            self._command([_MOTORS_OFF, 1, 1])
            self._enabled = False
            self._record("connect", state=dataclasses.asdict(self.get_state()))
        except Exception as exc:
            # Mirror Scorbot.connect: log, clean up, never let cleanup hide the cause.
            self._fault = str(exc)
            self._enabled = None
            self._record("connect_failed", error=self._fault)
            self._cancel_event.set()
            try:
                self.disconnect()
            except Exception as cleanup_error:
                self._record("connect_cleanup_failed", error=str(cleanup_error))
            raise ScorbotError(f"Simulated connection failed: {exc}") from exc
        return self

    def get_state(self, *, after_index: int | None = None):
        return dataclasses.replace(super().get_state(after_index=after_index), simulated=True)

    def disconnect(self):
        if self._device is None:
            return
        self._cancel_event.set()
        thread = self._command_thread
        if thread is not None and thread.is_alive():
            if self._fault is None:
                self._command([_EXIT, 1, 1])
            else:
                self._commands.put([_EXIT, 1, 1])
            thread.join(timeout=5)
            if thread.is_alive():
                raise ScorbotError("Simulated command worker did not stop")
        self._device = None
        self._input = None
        self._command_thread = None
        self._last_state_index = 0
        self._enabled = False
        self._homed = False
        self._home_counts = None
