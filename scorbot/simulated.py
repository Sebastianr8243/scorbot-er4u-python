"""A fake ER-4U controller behind the real ``Scorbot`` facade. No USB, no motion.

Only ``connect`` and ``disconnect`` are replaced. Commands still pass through
``Scorbot._command`` (timeouts, error codes, fault latch) and feedback through
``get_state`` / ``decode_state``, so the simulator exercises the same safety
code as the lab. The motion model is deliberately simple: a jog changes the
motor counts by exactly the legacy plan; there is no timing, dynamics,
backlash, gravity or collision model. Every state is marked ``simulated``.

A stop request (``Scorbot.request_stop``) ends a jog after the step in
progress, keeps the counts reached so far and answers the legacy "stopped"
code, like the legacy loop. That the real arm stops there is a hypothesis: it
will coast, and the controller's reaction to the stop sequence is unverified.

``SimulatorProfile`` adds optional realism for rehearsals (BACKLOG #40): a
homing sequence that presses each home switch in the legacy order, rest
jitter, and the front-panel LEDs. Every profile value is a modeled
hypothesis taken from the manuals, vendor files or other projects, never a
measurement of our arm. The default profile keeps the simple model above.

``motors_dropped`` models the controller cutting motor power by itself (e-stop,
over-current, communication time-out). Nothing in the state packet changes, as
on hardware no decoded byte is known to show motor power, so the facade still
believes the motors are on. The next motion command then returns an error
code. That a real controller answers this way is an assumption (unverified).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
import queue
import random
import threading
import time
from types import MappingProxyType

from .packet import PacketSnapshot
from .robot import Scorbot, ScorbotError
from .state import ENCODER_OFFSETS, JOINTS

FAULT_KINDS = ("timeout", "late_answer", "controller_error", "worker_crash",
               "stale_feedback", "corrupt_packet", "motors_dropped")
MAX_COUNT = 65535
_EXIT, _MOTORS_OFF, _MOTORS_ON, _HOME = 528, 16, 17, 18
_JOG_ORDERS = set(range(4, 14))
_ERROR_UNKNOWN_ORDER, _ERROR_INJECTED, _ERROR_COUNT_RANGE, _ERROR_MOTORS_OFF = 1, 3, 4, 5
_ERROR_HOME_SEARCH = 2  # what openScorbot/setHome.py puts in the queue on a search time-out
_STOPPED = 14  # openScorbot/libcomm.py:STOPPED, a jog ended by a stop request
ARM_MOTORS = ("base", "shoulder", "elbow", "wrist_motor_1", "wrist_motor_2")


@dataclass(frozen=True)
class SimulatorProfile:
    """Optional simulator realism. Every default is a modeled hypothesis, not measured.

    - ``homing_order``: the legacy ``setHome.py`` sequence (shoulder, elbow,
      pitch, roll, base); the vendor INI order differs and is not imposed.
      Pitch and roll are modeled on wrist motors 1 and 2 alone, a
      simplification of the two-motor differential.
    - ``switch_bits``: ``state.HOME_SWITCH_BITS`` (legacy byte 5 decode; polarity
      unverified).
    - ``switch_width_counts``: about 200 counts pressed, from
      github.com/steveturbek/scorbot_controller (a replacement controller, so a
      weak prior).
    - ``home_offsets``: vendor INI counts from the switch to home (shoulder -190,
      elbow +45, pitch +850, roll -690; none given for the base).
    - ``rest_jitter_counts``: our choice; +/- counts added to every reading at
      rest, never to the stored position.
    - ``fail_home_motor``: that motor's switch is never found and homing fails
      with the legacy search time-out code.
    """

    model_homing: bool = False
    homing_duration_s: float = 0.0
    homing_steps_per_motor: int = 8
    homing_order: tuple[str, ...] = ("shoulder", "elbow", "wrist_motor_1", "wrist_motor_2",
                                     "base")
    switch_bits: MappingProxyType = field(default_factory=lambda: MappingProxyType({
        "base": 1, "shoulder": 2, "elbow": 4, "wrist_motor_1": 8, "wrist_motor_2": 16}))
    switch_width_counts: int = 200
    home_offsets: MappingProxyType = field(default_factory=lambda: MappingProxyType({
        "base": 0, "shoulder": -190, "elbow": 45, "wrist_motor_1": 850,
        "wrist_motor_2": -690}))
    rest_jitter_counts: int = 0
    seed: int = 0
    fail_home_motor: str | None = None
    status: str = "modeled hypotheses from manuals and other projects, not measured"

    def __post_init__(self):
        if set(self.homing_order) != set(self.switch_bits) or \
                set(self.home_offsets) != set(self.switch_bits):
            raise ValueError("homing_order, switch_bits and home_offsets must name the same motors")
        if self.fail_home_motor is not None and self.fail_home_motor not in self.switch_bits:
            raise ValueError(f"Unknown motor {self.fail_home_motor!r}")
        if self.homing_duration_s < 0 or self.homing_steps_per_motor < 1 \
                or self.rest_jitter_counts < 0 or self.switch_width_counts < 1:
            raise ValueError("Durations, steps, jitter and switch width must be non-negative")


# Rehearsals (python -m scorbot.lab --simulate): each switch visibly pressed in
# turn, about 3 s in all, and +/-1 count of rest noise (two readings then differ by
# at most 2, inside the lab idle check's STABLE_COUNTS). Still modeled, not measured.
REHEARSAL_PROFILE = SimulatorProfile(model_homing=True, homing_duration_s=3.0,
                                     rest_jitter_counts=1)


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
                 start_counts: dict[str, int] | None = None, step_delay_s: float = 0.0,
                 drop_motors_after_home: bool = False,
                 profile: SimulatorProfile | None = None):
        self.profile = profile or SimulatorProfile()
        self.homing_trace: list[tuple[str, int, dict[str, int]]] = []
        self.link_up = False
        self._moving = False
        self._rng = random.Random(self.profile.seed)
        self.home_counts = {name: 0 for name in JOINTS}
        self.home_counts.update(home_counts or {})
        self.counts = {name: 0 for name in JOINTS}
        self.counts.update(start_counts or {})
        self.step_delay_s = step_delay_s
        self.drop_motors_after_home = drop_motors_after_home
        self.late_answer_s = 0.5
        self.motors_on = False
        self.switch_bits = 0
        self.commands: list[list] = []
        self._pending: set[str] = set()
        self._lock = threading.Condition()
        self._index = 0
        self._plan_jog = None
        self._stop_event = None

    def inject(self, kind: str) -> None:
        """Arm a one-shot fault for the next command or feedback read.

        ``motors_dropped`` acts at once: motor power goes off silently.
        """
        if kind not in FAULT_KINDS:
            raise ValueError(f"Fault kind must be one of {FAULT_KINDS}")
        with self._lock:
            if kind == "motors_dropped":
                self.motors_on = False
            else:
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
            counts = self.counts
            jitter = self.profile.rest_jitter_counts
            if jitter and not self._moving:
                counts = dict(counts)
                for motor in ARM_MOTORS:
                    value = counts[motor] + self._rng.randint(-jitter, jitter)
                    counts[motor] = max(-MAX_COUNT, min(MAX_COUNT, value))
            data = encode_packet(counts, self.switch_bits, override)
            return PacketSnapshot(data, self._index, time.monotonic_ns())

    def leds(self) -> dict[str, str]:
        """Front-panel LEDs as the manual describes them (pp. 10-11), modeled.

        POWER is green while the PC link is up and orange otherwise (the
        datasheet says red; unverified). MOTORS is lit only while motor power
        is on, so a silent ``motors_dropped`` shows here although no state
        byte reveals it.
        """
        with self._lock:
            return {"motors": "lit" if self.motors_on and self.link_up else "off",
                    "power": "green" if self.link_up else "orange"}

    # -- command worker seam (Scorbot._commands / Scorbot._results) --------

    def worker(self, commands: queue.Queue, results: queue.Queue) -> None:
        with self._lock:
            self.link_up = True
        while True:
            payload = commands.get()
            self.commands.append(list(payload))
            order = payload[0]
            if order == _EXIT:
                with self._lock:
                    self.motors_on = False
                    self.link_up = False
                results.put(0)
                return
            if self._take("worker_crash"):
                # Like Scorbot._run_command_worker: report the exception, then die.
                # A real thread is still alive for a moment after reporting. The
                # PC link is gone, so the modeled POWER LED goes orange.
                with self._lock:
                    self.link_up = False
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
            with self._lock:
                self.motors_on = False
        elif order == _MOTORS_ON:
            with self._lock:
                self.motors_on = True
        elif order == _HOME:
            if not self.motors_on:
                return _ERROR_MOTORS_OFF
            if self.profile.model_homing:
                code = self._model_homing()
                if code:
                    return code
            with self._lock:
                self.counts = dict(self.home_counts)
                if self.drop_motors_after_home:
                    self.drop_motors_after_home = False
                    self.motors_on = False
        elif order in _JOG_ORDERS:
            if not self.motors_on:
                return _ERROR_MOTORS_OFF
            plan = self._plan_jog(order, float(payload[2]), int(payload[1]))
            deltas = plan["motor_count_deltas"]
            with self._lock:
                targets = {name: self.counts[name] + delta for name, delta in deltas.items()}
                if any(abs(value) > MAX_COUNT for value in targets.values()):
                    return _ERROR_COUNT_RANGE
            increments = list(plan["increments"])
            total, done, stopped = sum(increments), 0, False
            for step in increments:
                if self.step_delay_s:
                    time.sleep(self.step_delay_s)
                done += step
                # Like libcomm.move_*: checked after each step, the first included.
                if self._stop_event is not None and self._stop_event.is_set():
                    stopped = True
                    break
            with self._lock:
                if done < total:
                    targets = {name: self.counts[name] + round(delta * done / total)
                               for name, delta in deltas.items()}
                self.counts.update(targets)
                if self.profile.model_homing:
                    self.switch_bits = self._pressed_switches()
            if stopped:
                return _STOPPED
        else:
            return _ERROR_UNKNOWN_ORDER
        return 0

    def _pressed_switches(self) -> int:
        """Switch byte for the current counts (call with ``_lock`` held)."""
        profile = self.profile
        half = profile.switch_width_counts // 2
        return sum(bit for motor, bit in profile.switch_bits.items()
                   if motor != profile.fail_home_motor
                   and abs(self.counts[motor] - (self.home_counts[motor]
                                                 - profile.home_offsets[motor])) <= half)

    def _model_homing(self) -> int:
        """Drive each motor onto its switch, then back off to home, in the legacy order.

        Uses the legacy axis order, not the legacy routine itself: the real
        ``setHome.py`` searches packet by packet and keeps moving for a while
        after the switch closes. This only rehearses what an operator sees.
        Stops with the motors-off code if motor power drops part-way.
        """
        profile = self.profile
        steps = profile.homing_steps_per_motor
        pause = profile.homing_duration_s / (len(profile.homing_order) * steps * 2)

        def step(motor, value) -> bool:
            with self._lock:
                if not self.motors_on:
                    return False
                if value is not None:
                    self.counts[motor] = round(value)
                self.switch_bits = self._pressed_switches()
                self.homing_trace.append((motor, self.switch_bits, dict(self.counts)))
            if pause:
                time.sleep(pause)
            return True

        self._moving = True
        try:
            for motor in profile.homing_order:
                if motor == profile.fail_home_motor:
                    for _ in range(steps * 2):
                        if not step(motor, None):
                            return _ERROR_MOTORS_OFF
                    return _ERROR_HOME_SEARCH
                start = self.counts[motor]
                target = self.home_counts[motor] - profile.home_offsets[motor]
                home = self.home_counts[motor]
                path = [start + (target - start) * i / steps for i in range(1, steps + 1)]
                path += [target + (home - target) * i / steps for i in range(1, steps + 1)]
                for value in path:
                    if not step(motor, value):
                        return _ERROR_MOTORS_OFF
        finally:
            self._moving = False
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
        self._stop_event.clear()
        self.sim._plan_jog = self._legacy("motion_profile").plan_jog
        self.sim._stop_event = self._stop_event
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

    def get_state_and_packet(self, *, after_index: int | None = None):
        state, packet = super().get_state_and_packet(after_index=after_index)
        return dataclasses.replace(state, simulated=True), packet

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
