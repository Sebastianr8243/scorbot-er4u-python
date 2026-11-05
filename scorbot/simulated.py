"""A fake ER-4U controller behind the real ``Scorbot`` facade. No USB, no motion.

Only ``connect`` and ``disconnect`` are replaced. Commands still pass through
``Scorbot._command`` (timeouts, error codes, fault latch) and feedback through
``get_state`` / ``decode_state``, so the simulator exercises the same safety
code as the lab. The motion model is deliberately simple: a jog changes the
motor counts by exactly the legacy plan; there is no timing, dynamics,
backlash, gravity or collision model. Every state is marked ``simulated``.

A stop request (``Scorbot.request_stop``) ends a jog after the step in
progress, keeps the counts reached so far and answers the legacy "stopped"
code, like the legacy loop. A request during the last step still answers
"stopped" although the full travel was reached, as it can on the arm. That
the real arm stops there is a hypothesis: it will coast, there is no lag
model here, and the controller's reaction to the stop sequence is unverified.

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

from . import vendor_home as vendor_home_port
from .packet import PacketSnapshot
from .robot import Scorbot, ScorbotError
from .state import COUNT_MAX, COUNT_MIN, COUNT_ZERO, ENCODER_OFFSETS, ERROR_OFFSETS, JOINTS
from .streaming import MOTORS as STREAM_MOTORS
from .streaming import STREAM_ORDER
from .vendor_home_model import Plant
from .vendor_gripper import GRIPPER_ORDER as VENDOR_GRIPPER_ORDER
from .vendor_io import IO_ORDER as VENDOR_IO_ORDER
from .vendor_manual import MANUAL_ORDER as VENDOR_MANUAL_ORDER
from .vendor_torque import SHOW_TORQUE, TORQUE_ORDER as VENDOR_TORQUE_ORDER
from .vendor_home_source import VENDOR_HOME_ORDER

FAULT_KINDS = ("timeout", "late_answer", "controller_error", "worker_crash",
               "stale_feedback", "corrupt_packet", "motors_dropped")
# The controller's 24-bit count range (scorbot.state), so the fake reports what the arm can.
MIN_COUNT, MAX_COUNT = COUNT_MIN, COUNT_MAX
_EXIT, _MOTORS_OFF, _MOTORS_ON, _HOME = 528, 16, 17, 18
_JOG_ORDERS = set(range(4, 14))
_ERROR_UNKNOWN_ORDER, _ERROR_INJECTED, _ERROR_COUNT_RANGE, _ERROR_MOTORS_OFF = 1, 3, 4, 5
_ERROR_HOME_SEARCH = 2  # what openScorbot/setHome.py puts in the queue on a search time-out
_STOPPED = 14  # openScorbot/libcomm.py:STOPPED, a jog ended by a stop request
_GRIPPER_OPEN, _GRIPPER_CLOSE = 14, 15   # the order codes; unrelated to the result 14 above
_ERROR_NO_SETTLE = 2    # what libcomm.clamp reports for a gripper still moving at the end
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
        if not MIN_COUNT <= value <= MAX_COUNT:
            raise ValueError(f"Simulated count for {name} out of range: {value}")
        # raw24 = count + 0x7FFFFF: the low word, then the top byte decode_state calls the sign byte
        packet[offset:offset + 3] = (value + COUNT_ZERO).to_bytes(3, "little")
    for name, sign in (sign_override or {}).items():
        packet[ENCODER_OFFSETS[JOINTS.index(name)] + 2] = sign
    return bytes(packet)


class SimulatedController:
    """In-memory controller: answers the legacy command queue and serves packets."""

    MANUAL_MESSAGE_S = 0.01
    """Velocity jog pacing: one message per 10 ms, about the legacy worker's rate (ours, not measured)."""

    def __init__(self, *, home_counts: dict[str, int] | None = None,
                 start_counts: dict[str, int] | None = None, step_delay_s: float = 0.0,
                 drop_motors_after_home: bool = False,
                 profile: SimulatorProfile | None = None):
        self.profile = profile or SimulatorProfile()
        self.homing_trace: list[tuple[str, int, dict[str, int]]] = []
        self.link_up = False
        self._moving = False
        self._rng = random.Random(self.profile.seed)
        self.home_counts = dict.fromkeys(JOINTS, 0)
        self.home_counts.update(home_counts or {})
        self.counts = dict.fromkeys(JOINTS, 0)
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
        self._gripper_increments = None
        self._stop_event = None
        # Gripper model (modeled, not measured). A close stops at
        # gripper_blocked_at if set: an object in the jaws. gripper_never_settles
        # makes the move report that the gripper was still moving at the end.
        # gripper_disturbs adds counts to arm motors during the move.
        self.gripper_blocked_at: int | None = None
        self.gripper_never_settles = False
        self.gripper_reversed = False      # open and close swapped, as a wrong inherited mapping
        self.gripper_disturbs: dict[str, int] = {}
        # Streaming model (modeled, not measured): each step the arm covers
        # this share of the distance to the setpoint; stuck means it does not move.
        self.stream_follow = 1.0
        self.stream_stuck = False
        self.stream_error_word = 0
        self.stream_wrist_error_word = 0      # the same, on the two wrist motors' error words
        self.stream_emergency = False
        self.stream_collision_bits = 0        # IN byte 59 during a stream, one bit per motor
        self.stream_sign_override: dict[str, int] = {}   # top bytes a stream reply reports instead (corrupt)
        self.stream_sent: list[tuple] = []
        # Vendor-home model (modeled, not measured): the home runs scorbot.vendor_home against
        # scorbot.vendor_home_model.Plant, packet by packet, as libcomm.vendor_home_run would.
        # ``vendor_plant`` replaces the default arm; ``vendor_hook(n, plant)`` runs before message n
        # so a test can inject an emergency, an impact or a stuck axis; ``vendor_packets`` keeps the
        # OUT packets of the last home.
        self.vendor_plant: Plant | None = None
        self.vendor_hook = None
        self.vendor_packets: list[bytes] = []
        # Torque readout model (modeled, not measured): a reply to ``57`` carries ``torque_raw`` in bytes
        # 11-14, except the first ``torque_lag`` replies, which still echo ``0D`` (a controller behind).
        self.torque_raw = 0
        self.torque_lag = 0

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
        # A corrupt reply is cut short: every sign (top) byte is a valid 24-bit count since 2026-11-04
        # (docs/requirements/count-frame.md), so a bad one is no longer a decode error.
        corrupt = self._take("corrupt_packet")
        with self._lock:
            self._index += 1
            counts = self.counts
            jitter = self.profile.rest_jitter_counts
            if jitter and not self._moving:
                counts = dict(counts)
                for motor in ARM_MOTORS:
                    value = counts[motor] + self._rng.randint(-jitter, jitter)
                    counts[motor] = max(MIN_COUNT, min(MAX_COUNT, value))
            data = encode_packet(counts, self.switch_bits)
            if corrupt:
                data = data[:40]
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
        elif order == STREAM_ORDER:
            if not self.motors_on:
                return _ERROR_MOTORS_OFF
            return self._stream(payload[1])
        elif order == VENDOR_HOME_ORDER:
            if not self.motors_on:
                return _ERROR_MOTORS_OFF
            return self._vendor_home(payload[1])
        elif order == VENDOR_MANUAL_ORDER:
            if not self.motors_on:
                return _ERROR_MOTORS_OFF
            return self._vendor_manual(payload[1])
        elif order == VENDOR_IO_ORDER:
            # Output writes need no motors; their setpoints hold the counts, so the gripper model stays put.
            return self._vendor_gripper(payload[1])
        elif order == VENDOR_TORQUE_ORDER:
            # A reading needs no motors; its setpoints hold the counts, so nothing moves.
            return self._vendor_torque(payload[1])
        elif order == VENDOR_GRIPPER_ORDER:
            if not self.motors_on:
                return _ERROR_MOTORS_OFF
            return self._vendor_gripper(payload[1])
        elif order in (_GRIPPER_OPEN, _GRIPPER_CLOSE):
            if not self.motors_on:
                return _ERROR_MOTORS_OFF
            return self._gripper(1 if order == _GRIPPER_OPEN else -1)
        elif order in _JOG_ORDERS:
            if not self.motors_on:
                return _ERROR_MOTORS_OFF
            plan = self._plan_jog(order, float(payload[2]), int(payload[1]))
            deltas = plan["motor_count_deltas"]
            with self._lock:
                targets = {name: self.counts[name] + delta for name, delta in deltas.items()}
                if any(not MIN_COUNT <= value <= MAX_COUNT for value in targets.values()):
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

    def _vendor_home_plant(self) -> Plant:
        """The default arm for a vendor home: where the controller is now, switches around that pose."""
        with self._lock:
            start = [self.counts[name] for name in JOINTS]
        shift = Plant(start=start).joints()
        bands = {n: (lo + shift[n], hi + shift[n]) for n, (lo, hi) in Plant().bands.items()}
        return Plant(bands=bands, start=start)

    def _vendor_home(self, source) -> int:
        """Run the vendor home against the arm model, the way ``libcomm.vendor_home_run`` does.

        Success leaves the counts where the model ends (zero at home after the ``48`` sweep) and the
        new home pose; a failure switches the motors off and answers 1, like the legacy home.
        """
        plant = self.vendor_plant or self._vendor_home_plant()
        self.vendor_packets = []
        reply = plant.exchange(vendor_home_port.encode_out(vendor_home_port.Out(vendor_home_port.NOP), 1))
        sequence, count, succeeded = 1, 0, True
        while True:
            sequence = sequence % 255 + 1
            step = source(reply, sequence)
            if step[0] == "end":
                break
            if step[0] != "send":
                succeeded = False
                break
            count += 1
            self.vendor_packets.append(step[1])
            if self.vendor_hook is not None:
                self.vendor_hook(count, plant)
            reply = plant.exchange(step[1])
        with self._lock:
            self.counts = {name: plant.reported(index) for index, name in enumerate(JOINTS)}
            self.switch_bits = plant.switch_bits()
            if succeeded:
                self.home_counts = dict(self.counts)
            else:
                self.motors_on = False
        return 0 if succeeded else 1

    def _vendor_gripper(self, source) -> int:
        """Run the post-Home gripper move the way ``libcomm.vendor_home_run`` does (order 23).

        The gripper count follows the gripper setpoint (OUT bytes 32-35) at once, stopping at
        ``gripper_blocked_at`` when closing, or mirrored about its start with ``gripper_reversed``;
        ``gripper_disturbs`` is added to the arm at the end; ``stream_emergency`` sets the emergency bit.
        ``vendor_hook(n, None)`` runs before message n. A failure switches the motors off and answers 1.
        """
        self.vendor_packets = []
        sequence, count = 1, 0
        with self._lock:
            start = self.counts["gripper"]
        while True:
            with self._lock:
                reply = bytearray(encode_packet(self.counts, self.switch_bits))
                reply[2] |= 1 if self.stream_emergency else 0
            reply[0] = sequence
            sequence = sequence % 255 + 1
            step = source(bytes(reply), sequence)
            if step[0] == "end":
                break
            if step[0] != "send":
                with self._lock:
                    self.motors_on = False
                return 1
            count += 1
            self.vendor_packets.append(step[1])
            if self.vendor_hook is not None:
                self.vendor_hook(count, None)
            setpoint = int.from_bytes(step[1][32:36], "little", signed=True)
            value = setpoint     # setpoints and ``counts`` are both controller counts
            if self.gripper_reversed:
                value = 2 * start - value
            with self._lock:
                if self.gripper_blocked_at is not None and value < self.counts["gripper"]:
                    value = max(value, self.gripper_blocked_at)
                self.counts["gripper"] = value
        with self._lock:
            for motor, counts in self.gripper_disturbs.items():
                self.counts[motor] += counts
        return 0

    def _vendor_manual(self, source) -> int:
        """Run the velocity jog the way ``libcomm.vendor_home_run`` does (order 26), one message every
        ``MANUAL_MESSAGE_S``. The arm motors follow their setpoints (OUT bytes 12-31) at once; ``stream_emergency``
        sets the emergency bit. A failure switches the motors off and answers 1."""
        self.vendor_packets = []
        sequence = 1
        while True:
            with self._lock:
                reply = bytearray(encode_packet(self.counts, self.switch_bits))
                reply[2] |= 1 if self.stream_emergency else 0
            reply[0] = sequence
            sequence = sequence % 255 + 1
            step = source(bytes(reply), sequence)
            if step[0] == "end":
                return 0
            if step[0] != "send":
                with self._lock:
                    self.motors_on = False
                return 1
            self.vendor_packets.append(step[1])
            time.sleep(self.MANUAL_MESSAGE_S)
            with self._lock:
                for index, motor in enumerate(ARM_MOTORS):
                    setpoint = int.from_bytes(step[1][12 + 4 * index:16 + 4 * index], "little", signed=True)
                    self.counts[motor] = setpoint

    def _vendor_torque(self, source) -> int:
        """Run the torque readout the way ``libcomm.vendor_home_run`` does (order 25). Each reply echoes the
        last message's ID and command; nothing moves. A failure switches the motors off and answers 1."""
        self.vendor_packets = []
        sequence, command, answered = 1, 0x0D, 0
        while True:
            with self._lock:
                reply = bytearray(encode_packet(self.counts, self.switch_bits))
            reply[0] = sequence
            if command == SHOW_TORQUE:
                answered += 1
                if answered > self.torque_lag:
                    reply[1] = SHOW_TORQUE
                    reply[11:15] = int(self.torque_raw).to_bytes(4, "little", signed=True)
                else:
                    reply[1] = 0x0D
            sequence = sequence % 255 + 1
            step = source(bytes(reply), sequence)
            if step[0] == "end":
                return 0
            if step[0] != "send":
                with self._lock:
                    self.motors_on = False
                return 1
            self.vendor_packets.append(step[1])
            command = step[1][4]

    def _gripper(self, sign: int) -> int:
        """Ramp the gripper count like openScorbot/libcomm.py:clamp, with an object model."""
        stopped = False
        if self.gripper_reversed:
            sign = -sign
        for step in self._gripper_increments():
            if self.step_delay_s:
                time.sleep(self.step_delay_s)
            with self._lock:
                target = self.counts["gripper"] + sign * step
                if not MIN_COUNT <= target <= MAX_COUNT:
                    return _ERROR_COUNT_RANGE
                if sign < 0 and self.gripper_blocked_at is not None:
                    target = max(target, self.gripper_blocked_at)   # closed on the object
                self.counts["gripper"] = target
            if self._stop_event is not None and self._stop_event.is_set():
                stopped = True
                break
        with self._lock:
            for motor, counts in self.gripper_disturbs.items():
                self.counts[motor] += counts
        if stopped:
            return _STOPPED
        return _ERROR_NO_SETTLE if self.gripper_never_settles else 0

    def _stream(self, source) -> int:
        """Follow a stream like openScorbot/libcomm.py:stream_targets, with an arm model."""
        self._moving = True
        try:
            while True:
                with self._lock:
                    reply = bytearray(encode_packet(self.counts, self.switch_bits, self.stream_sign_override))
                    reply[2] |= 1 if self.stream_emergency else 0
                    reply[59] = self.stream_collision_bits
                    for offset in ERROR_OFFSETS[:len(STREAM_MOTORS)]:
                        reply[offset:offset + 2] = self.stream_error_word.to_bytes(2, "little")
                    for offset in ERROR_OFFSETS[len(STREAM_MOTORS):len(STREAM_MOTORS) + 2]:
                        reply[offset:offset + 2] = self.stream_wrist_error_word.to_bytes(2, "little")
                step = source(bytes(reply))
                if step[0] == "end":
                    return 0
                if step[0] == "stop":
                    return _STOPPED
                if self.step_delay_s:
                    time.sleep(self.step_delay_s)
                if step[0] != "send":
                    return _STOPPED       # as the legacy loop: anything else stops
                self.stream_sent.append(tuple(step[1]))
                with self._lock:
                    if not self.stream_stuck:
                        # Three setpoints (base, shoulder, elbow) or five (and both wrist motors).
                        for motor, target in zip(JOINTS, step[1]):
                            move = round((target - self.counts[motor]) * self.stream_follow)
                            self.counts[motor] += move
        finally:
            self._moving = False

    def _switch_position(self, motor: str) -> int:
        """Where a joint's switch sees the joint, in counts (call with ``_lock`` held).

        The elbow joint's angle follows the shoulder's motor as well as its own (the motors are
        coupled: source model, modeled not measured), so a coupled move that keeps the elbow
        joint still keeps its switch where it was.
        """
        position = self.counts[motor]
        if motor == "elbow":
            position += self.counts["shoulder"] - self.home_counts["shoulder"]
        return position

    def _pressed_switches(self) -> int:
        """Switch byte for the current counts (call with ``_lock`` held)."""
        profile = self.profile
        half = profile.switch_width_counts // 2
        return sum(bit for motor, bit in profile.switch_bits.items()
                   if motor != profile.fail_home_motor
                   and abs(self._switch_position(motor) - (self.home_counts[motor]
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
                    # Like setHome.search_must_stop: a stop request ends the search.
                    if self._stop_event is not None and self._stop_event.is_set():
                        return _STOPPED
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
        self.sim._gripper_increments = self._gripper_plan
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

    def _gripper_plan(self) -> list[int]:
        conf = self._legacy("conf")
        return self._legacy("motion_profile").gripper_increments(
            conf.readData("pinza", "vel"), conf.readData("pinza", "ite_clamp"))

    def get_state_and_packet(self, *, after_index: int | None = None):
        state, packet = super().get_state_and_packet(after_index=after_index)
        return dataclasses.replace(state, simulated=True), packet

    def disconnect(self):
        if self._device is None:
            return
        self._abandon_stream()
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
