# Keyboard Teleop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a teleop mode to `python -m scorbot.lab`: arm once, then one key press = one 1 degree step with no per-step questions, with episodes, intent logging, the full action vector in every jog command, and optional camera recording with liveness checks.

**Architecture:** The operator layer gains a release gate (`wait_for_release`, Windows `GetAsyncKeyState` via ctypes) and a polled key read (`key_or_tick`). A new `scorbot/lab/teleop.py` runs the teleop loop through the session's existing `_prepare`/`_execute` gates. A new `scorbot/lab/camera.py` wraps `CameraRecorder` with a liveness check. Episodes go to JSONL rows and a new MCAP topic `/session/episode`.

**Tech Stack:** Python 3.10+, standard library (`ctypes`), existing `scorbot.camera`, `unittest`.

**Spec:** `docs/superpowers/specs/2026-10-02-keyboard-teleop-design.md`

## Global Constraints

- No change to `Scorbot`, `SimulatedScorbot`, `openScorbot/`, packet bytes, sleeps, the 10 degree `TRAVEL_CAP_DEG`, the wrist block, the 5 degree SDK ceiling or the speed range.
- Teleop jogs go only through `LabSession._prepare` and `LabSession._execute(..., observe=False)`.
- Teleop idle disarm 15 s (`TELEOP_IDLE_DISARM_S = 15.0`); key poll tick 0.2 s; camera live = no failure and last frame written under 1 s ago; release warning after 3 s held.
- Teleop refused without single-key input or without key-state reading.
- A camera fault never disarms and never latches the robot fault.
- JSONL row written before the matching MCAP call. Strict JSON.
- No new dependencies. Run with `.venv/Scripts/python.exe`; lint with `.venv/Scripts/ruff.exe check .`.
- Commits: imperative sentence-case subject, body explains why, no AI attribution.

## Review Focus

- Operator presses `t` when not armed: refused with a message, no teleop (Task 4 test `test_teleop_needs_arming`).
- Operator types `n` while an episode is open: refused, task unchanged (Task 4 test `test_new_task_refused_during_episode`).
- Empty task text at the first `r`: no episode starts (Task 4 test `test_empty_task_refuses_episode`).
- Camera factory raises on open: session continues, `camera_unavailable` logged, episodes record `camera: false` (Task 5 test `test_camera_open_failure_continues_without_video`).
- `x` pressed during an open episode: episode aborted with reason `finish`, session finishes normally (Task 4 test `test_finish_aborts_open_episode`).

---

### Task 1: Release gate and polled key read in the operator layer

**Files:**
- Modify: `scorbot/lab/operator.py` (protocol, `ScriptedOperator`, `TICK`)
- Modify: `scorbot/lab/terminal.py` (`TerminalOperator`)
- Test: `tests/test_lab_terminal.py`, `tests/test_lab_operator.py`

**Interfaces:**
- Produces: `operator.TICK = "<tick>"`; protocol methods `can_wait_for_release() -> bool`, `wait_for_release(key: str) -> int` (number of queued keys discarded), `key_or_tick(prompt: str, tick_s: float) -> str | None` (None = tick with no key).
- Produces: `TerminalOperator(getwch=None, isatty=None, kbhit=None, key_down=None, sleep=time.sleep, clock=time.monotonic)`; `key_down(char) -> bool`.
- Produces: `ScriptedOperator.releases: list[str]`, `ScriptedOperator.release_gate: bool = True`; scripted answer `TICK` makes `key_or_tick` return None.

- [ ] **Step 1: Failing tests** — append to `TerminalOperatorTests` in `tests/test_lab_terminal.py`:

```python
    def held(self, polls_down, repeats_at=()):
        """Key physically down for `polls_down` polls; repeats queue at given polls."""
        state = {"polls": 0}
        pending = []

        def key_down(char):
            state["polls"] += 1
            if state["polls"] in repeats_at:
                pending.append(char)
            return state["polls"] <= polls_down
        op = TerminalOperator(getwch=lambda: pending.pop(0), isatty=lambda: True,
                              kbhit=lambda: bool(pending), key_down=key_down,
                              sleep=lambda s: None)
        return op, pending, state

    def test_wait_for_release_waits_for_key_up_and_drops_repeats(self):
        op, pending, state = self.held(polls_down=40, repeats_at=range(20, 40))
        with contextlib.redirect_stdout(io.StringIO()):
            discarded = op.wait_for_release("q")
        self.assertEqual(discarded, 20)
        self.assertEqual(pending, [])
        self.assertEqual(state["polls"], 41)

    def test_long_repeat_delay_still_one_step(self):
        # No repeat has arrived yet when the gate starts, but the key is still down.
        op, pending, _ = self.held(polls_down=60, repeats_at=(55, 58))
        with contextlib.redirect_stdout(io.StringIO()):
            op.wait_for_release("q")
            self.assertIsNone(op.key_or_tick("k: ", 0.0))
        self.assertEqual(pending, [])

    def test_release_gate_needs_key_state(self):
        op = TerminalOperator(getwch=lambda: "q", isatty=lambda: True, kbhit=lambda: False,
                              key_down=None)
        op._key_down = None
        self.assertFalse(op.can_wait_for_release())

    def test_key_or_tick_returns_none_without_a_key(self):
        now = iter([0.0, 0.1, 0.3])
        op = TerminalOperator(getwch=lambda: "q", isatty=lambda: True, kbhit=lambda: False,
                              key_down=lambda c: False, sleep=lambda s: None,
                              clock=lambda: next(now))
        self.assertIsNone(op.key_or_tick("k: ", 0.2))

    def test_key_or_tick_returns_a_waiting_key(self):
        pending = ["Q"]
        op = TerminalOperator(getwch=lambda: pending.pop(0), isatty=lambda: True,
                              kbhit=lambda: bool(pending), key_down=lambda c: False,
                              sleep=lambda s: None)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(op.key_or_tick("k: ", 0.2), "q")

    def test_virtual_key_codes(self):
        from scorbot.lab.terminal import _virtual_key
        self.assertEqual((_virtual_key("q"), _virtual_key("3"), _virtual_key("?")),
                         (ord("Q"), ord("3"), None))
```

Append to `tests/test_lab_operator.py` (create the class if the file has none suitable):

```python
class ScriptedTeleopTests(unittest.TestCase):
    def test_tick_and_release(self):
        from scorbot.lab.operator import TICK, ScriptedOperator
        op = ScriptedOperator([TICK, "Q"])
        self.assertIsNone(op.key_or_tick("k: ", 0.2))
        self.assertEqual(op.key_or_tick("k: ", 0.2), "q")
        op.pending_keys = 4
        self.assertEqual(op.wait_for_release("q"), 4)
        self.assertEqual(op.releases, ["q"])
        self.assertTrue(op.can_wait_for_release())
        op.release_gate = False
        self.assertFalse(op.can_wait_for_release())
```

- [ ] **Step 2: Run** `.venv/Scripts/python.exe -m unittest tests.test_lab_terminal tests.test_lab_operator` — Expected: FAIL (unexpected keyword `key_down`, missing `TICK`).

- [ ] **Step 3: Implement.** In `scorbot/lab/operator.py`: add `TICK = "<tick>"` after `ENTER`; add to the `Operator` protocol:

```python
    def can_wait_for_release(self) -> bool: ...
    def wait_for_release(self, key: str) -> int: ...
    def key_or_tick(self, prompt: str, tick_s: float) -> str | None: ...
```

In `ScriptedOperator.__init__` add `self.releases: list[str] = []` and `self.release_gate = True`; add methods:

```python
    def can_wait_for_release(self):
        return self.release_gate

    def wait_for_release(self, key):
        self.releases.append(key)
        return self.discard_pending_keys()

    def key_or_tick(self, prompt, tick_s):
        key = self.key(prompt)
        return None if key == TICK else key
```

In `scorbot/lab/terminal.py`: add `import time`, and

```python
RELEASE_POLL_S = 0.02
RELEASE_WARN_S = 3.0


def _virtual_key(char):
    """Windows virtual-key code for a letter or digit key; None for anything else."""
    upper = char.upper()
    if len(upper) == 1 and ("A" <= upper <= "Z" or "0" <= upper <= "9"):
        return ord(upper)
    return None


def _windows_key_down():
    """Physical key state through user32.GetAsyncKeyState; None off Windows.

    Only ever used to wait for a release (it delays motion, never causes it):
    motion still comes only from a character read from this console.
    """
    try:
        import ctypes
        user32 = ctypes.windll.user32
    except (ImportError, AttributeError, OSError):
        return None

    def key_down(char):
        code = _virtual_key(char)
        return code is not None and bool(user32.GetAsyncKeyState(code) & 0x8000)
    return key_down
```

`TerminalOperator.__init__` gains `key_down=None, sleep=time.sleep, clock=time.monotonic` and sets
`self._key_down = key_down if key_down is not None else _windows_key_down()`,
`self._sleep, self._clock = sleep, clock`. Add methods:

```python
    def can_wait_for_release(self):
        return bool(self._single_keys() and self._kbhit is not None
                    and self._key_down is not None)

    def wait_for_release(self, key):
        """Block until `key` is physically up, then drop every queued character."""
        if self._key_down is not None:
            started, warned = self._clock(), False
            while self._key_down(key):
                if not warned and self._clock() - started > RELEASE_WARN_S:
                    print("  Release the key to continue.", flush=True)
                    warned = True
                self._sleep(RELEASE_POLL_S)
        return self.discard_pending_keys()

    def key_or_tick(self, prompt, tick_s):
        """A key if one arrives within tick_s, else None (lets the caller poll)."""
        if not self._single_keys() or self._kbhit is None:
            return self.key(prompt)
        deadline = self._clock() + tick_s
        while True:
            if self._kbhit():
                return self.key(prompt)
            if self._clock() >= deadline:
                return None
            self._sleep(RELEASE_POLL_S)
```

Note `test_long_repeat_delay_still_one_step` relies on `key_or_tick(..., 0.0)` with `kbhit` false returning None: the deadline check runs after the `kbhit` check, so with nothing queued it returns None.

- [ ] **Step 4: Run** the same command — Expected: PASS. Then `.venv/Scripts/python.exe -m unittest tests.test_lab_session` — Expected: PASS (no behaviour change yet).

- [ ] **Step 5: Commit** — "Let the lab operator wait for a key release and poll for keys".

---

### Task 2: Episode topic and camera liveness signal

**Files:**
- Modify: `scorbot/session/schemas.py` (topic, REQUIRED, PROPERTIES)
- Modify: `scorbot/session/record.py` (`log_episode`)
- Modify: `scorbot/camera/recorder.py` (`last_write_ns`)
- Test: `tests/test_session.py`, `tests/test_camera_recorder.py`

**Interfaces:**
- Produces: topic `/session/episode` → schema `scorbot.Episode`, required `("episode", "event")`; `SessionWriter.log_episode(episode: int, event: str, *, task: str | None = None, status: str | None = None, reason: str | None = None) -> int`; `EPISODE_EVENTS = ("start", "end")`, `EPISODE_STATUSES = ("completed", "aborted")` in `schemas`.
- Produces: `CameraRecorder.last_write_ns: int | None` (clock value when the last frame was written).

- [ ] **Step 1: Failing tests.** Append to `WriterTests` in `tests/test_session.py`:

```python
    def test_episode_events_round_trip(self):
        from scorbot.session.replay import load_session
        with new_writer(self.root) as writer:
            writer.log_episode(1, "start", task="reach left")
            writer.log_episode(1, "end", task="reach left", status="completed")
            with self.assertRaises(ValueError):
                writer.log_episode(2, "pause")
            with self.assertRaises(ValueError):
                writer.log_episode(2, "end", status="maybe")
        session = load_session(writer.path)
        self.assertEqual(session.findings, [])
        episodes = [e["payload"] for e in session.events if e["topic"] == "/session/episode"]
        self.assertEqual([(p["episode"], p["event"], p["status"]) for p in episodes],
                         [(1, "start", None), (1, "end", "completed")])
```

Append to `RecorderTests` in `tests/test_camera_recorder.py`:

```python
    def test_last_write_time_is_tracked(self):
        recorder = self.recorder(FakeSource())
        self.assertIsNone(recorder.last_write_ns)
        recorder.start()
        self.assertTrue(wait_for(lambda: recorder.last_write_ns is not None))
        recorder.stop()
```

- [ ] **Step 2: Run** `.venv/Scripts/python.exe -m unittest tests.test_session.WriterTests tests.test_camera_recorder` — Expected: FAIL (`log_episode` missing, `last_write_ns` missing).

- [ ] **Step 3: Implement.** `schemas.py`: add `"/session/episode": "scorbot.Episode",` to `TOPICS`; `"scorbot.Episode": ("episode", "event"),` to `REQUIRED`; constants

```python
EPISODE_EVENTS = ("start", "end")
EPISODE_STATUSES = ("completed", "aborted")
```

near `COMMAND_STATUSES`, and in `PROPERTIES`:

```python
    "scorbot.Episode": {
        "episode": _t("integer"),
        "event": _t("string", enum=list(EPISODE_EVENTS)),
        "task": _t("string", nullable=True),
        "status": _t("string", nullable=True),
        "reason": _t("string", nullable=True),
    },
```

`record.py`, after `log_note`:

```python
    def log_episode(self, episode: int, event: str, *, task: str | None = None,
                    status: str | None = None, reason: str | None = None) -> int:
        """Episode boundaries for datasets; the lab JSONL holds the same rows first."""
        if event not in schemas.EPISODE_EVENTS:
            raise ValueError(f"event must be one of {schemas.EPISODE_EVENTS}")
        if status is not None and status not in schemas.EPISODE_STATUSES:
            raise ValueError(f"status must be one of {schemas.EPISODE_STATUSES}")
        return self._emit("/session/episode", {"episode": int(episode), "event": event,
                                               "task": task, "status": status,
                                               "reason": reason}, None)
```

Add `log_episode = _robot_only("log_episode")` to `CameraStream` in `scorbot/camera/stream.py`.

`recorder.py`: in `__init__` add `self.last_write_ns: int | None = None`; in `_write_loop`, inside the `with self._cond:` block after a frame is written, add `self.last_write_ns = self.clock()` (set even when `stream is None`, so measuring-only recorders report liveness too).

- [ ] **Step 4: Run** the same command plus `tests.test_camera_stream` — Expected: PASS.

- [ ] **Step 5: Commit** — "Record episode boundaries and when the camera last wrote a frame".

---

### Task 3: Full action vector on every jog command

**Files:**
- Modify: `scorbot/lab/session.py` (`_execute`)
- Test: `tests/test_lab_session.py`

**Interfaces:**
- Produces: every MCAP `/robot/command` with `kind == "jog_joint"` has `params["target_signed_counts"]`: dict over `ARM_MOTORS = ("base", "shoulder", "elbow", "wrist_motor_1", "wrist_motor_2")` = `before.signed_encoder_counts[m] + plan["motor_count_deltas"].get(m, 0)`.

- [ ] **Step 1: Failing test** (append to `LabSessionTests`):

```python
    def test_jog_command_carries_the_full_target_vector(self):
        from scorbot.session.replay import load_session
        self.run_session(TO_LOOP + ARM + ["q", "BASE -1"] + OBS + FINISH)
        [folder] = [p for p in (self.root / "sessions").iterdir() if p.is_dir()]
        commands = [e["payload"]["params"] for e in load_session(folder).events
                    if e["topic"] == "/robot/command"
                    and e["payload"]["kind"] == "jog_joint"]
        [params] = commands
        target = params["target_signed_counts"]
        self.assertEqual(set(target), {"base", "shoulder", "elbow", "wrist_motor_1",
                                       "wrist_motor_2"})
        [preview] = self.of("jog_preview")
        self.assertEqual(target["base"], preview["plan"]["target_signed_counts"]["base"])
        self.assertEqual(target["elbow"],
                         preview["plan"]["starting_signed_counts"].get("elbow", target["elbow"]))
```

- [ ] **Step 2: Run** `.venv/Scripts/python.exe -m unittest tests.test_lab_session -k full_target` — Expected: FAIL (`KeyError: 'target_signed_counts'`).

- [ ] **Step 3: Implement.** In `session.py` add near the constants:

```python
ARM_MOTORS = ("base", "shoulder", "elbow", "wrist_motor_1", "wrist_motor_2")
```

and in `_execute` replace the `log_command` call with:

```python
        deltas = plan["motor_count_deltas"]
        target = {motor: before.signed_encoder_counts[motor] + deltas.get(motor, 0)
                  for motor in ARM_MOTORS}
        command = self.rec.log_command("jog_joint", {"joint": joint, "delta_degrees": delta,
                                                     "speed": self.profile.speed,
                                                     "target_signed_counts": target})
```

(the action for datasets, contract C2 of the roadmap: moving motors get the previewed target, the rest keep their current count).

- [ ] **Step 4: Run** `.venv/Scripts/python.exe -m unittest tests.test_lab_session` — Expected: PASS.

- [ ] **Step 5: Commit** — "Log the full commanded target with every jog".

---

### Task 4: Teleop mode and episodes

**Files:**
- Create: `scorbot/lab/teleop.py`
- Modify: `scorbot/lab/session.py` (`t` key in `_jog_loop`, `HELP`, `self.camera = None` in `__init__`)
- Test: `tests/test_lab_teleop.py`

**Interfaces:**
- Consumes: Task 1 operator methods and `TICK`; Task 2 `log_episode`; `LabSession._prepare`, `_execute`, `_disarm`, `_write`, `_status`, `rec`, `op`, `clock`, `armed`, `fault`, `step`; `JOG_KEYS`.
- Produces: `Teleop(session, camera=None).run() -> str` returning `"left"` or `"finish"`; constants `TELEOP_IDLE_DISARM_S = 15.0`, `TICK_S = 0.2`. Camera duck type used: `live() -> bool`, `problem() -> str`, `frames_written() -> int`, `drain() -> list[dict]`.

- [ ] **Step 1: Failing tests** (`tests/test_lab_teleop.py`):

```python
"""Teleop mode of the guided session: simulated controller, scripted keys."""

import json
from pathlib import Path
import tempfile
import unittest

from scorbot import SimulatedScorbot
from scorbot.lab.operator import TICK, ScriptedOperator
from scorbot.lab.profile import LabProfile
from scorbot.lab.session import EXIT_OK, LabSession
from scorbot.simulated import SimulatedController

PROFILE = LabProfile("lab-er4u-1", "A-12", "C-3", "WinUSB", "SR")
TO_LOOP = ["y"] * 4 + ["n", "g", "pose matches photo", "HOME", "y", "g", "all axes homed", "y"]
ARM = ["a", "door", "y", "g", "ARM"]
FINISH = ["x", "n", "g"]
JOG_ORDERS = set(range(4, 14))


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class TeleopTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.ctrl = SimulatedController()
        self.clock = Clock()

    def tearDown(self):
        self._tmp.cleanup()

    def run_session(self, answers, *, release_gate=True, camera_factory=None):
        self.op = ScriptedOperator(answers)
        self.op.release_gate = release_gate
        self.session = LabSession(
            profile=PROFILE, operator=self.op,
            robot_factory=lambda **kw: SimulatedScorbot(controller=self.ctrl, **kw),
            data_source="simulated", log_path=self.root / "s.jsonl",
            session_root=self.root / "sessions", clock=self.clock, sleep=lambda s: None,
            camera_factory=camera_factory)
        code = self.session.run()
        self.rows = [json.loads(line) for line in
                     (self.root / "s.jsonl").read_text(encoding="utf-8").splitlines()]
        return code

    def of(self, kind):
        return [row for row in self.rows if row["type"] == kind]

    def jogs(self):
        return [c for c in self.ctrl.commands if c and c[0] in JOG_ORDERS]

    def mcap_episodes(self):
        from scorbot.session.replay import load_session
        [folder] = [p for p in (self.root / "sessions").iterdir() if p.is_dir()]
        return [e["payload"] for e in load_session(folder).events
                if e["topic"] == "/session/episode"]

    def test_press_moves_without_questions(self):
        code = self.run_session(TO_LOOP + ARM + ["t", "q", "q", "t"] + FINISH)
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(len(self.jogs()), 2)
        self.assertEqual(self.of("jog_observation"), [])
        self.assertEqual([r["how"] for r in self.of("jog_confirmed")], ["teleop", "teleop"])
        self.assertEqual(self.op.releases, ["t", "q", "q", "t"])
        self.assertTrue(all(r["accepted"] for r in self.of("teleop_intent")
                            if r["action"] == "jog"))
        self.assertEqual(len(self.of("teleop_start")), 1)

    def test_teleop_needs_arming(self):
        self.run_session(TO_LOOP + ["t"] + FINISH)
        self.assertEqual(self.of("teleop_start"), [])
        self.assertTrue(any("press a" in m for m in self.op.shown))

    def test_refused_without_release_gate(self):
        self.run_session(TO_LOOP + ARM + ["t"] + FINISH, release_gate=False)
        self.assertEqual(len(self.of("teleop_refused")), 1)
        self.assertEqual(self.jogs(), [])

    def test_repeats_queued_during_a_step_are_discarded_and_logged(self):
        def press_with_repeats():
            self.op.pending_keys = 7
            return "q"
        self.run_session(TO_LOOP + ARM + ["t", press_with_repeats, "t"] + FINISH)
        self.assertEqual(len(self.jogs()), 1)
        [intent] = [r for r in self.of("teleop_intent") if r["action"] == "jog"]
        self.assertEqual(intent["discarded_keys"], 7)

    def test_cap_refusal_disarms_and_leaves_teleop(self):
        self.run_session(TO_LOOP + ARM + ["t"] + ["q"] * 11 + FINISH)
        self.assertEqual(len(self.jogs()), 10)
        self.assertEqual(self.of("disarmed")[-1]["reason"], "travel cap")
        self.assertFalse(self.of("teleop_intent")[-1]["accepted"])
        self.assertEqual(len(self.of("teleop_end")), 1)

    def test_unknown_key_disarms(self):
        self.run_session(TO_LOOP + ARM + ["t", "z"] + FINISH)
        self.assertTrue(self.of("disarmed")[-1]["reason"].startswith("unknown key"))
        self.assertEqual(self.of("teleop_intent")[-1]["key"], "z")

    def test_idle_disarms_after_fifteen_seconds(self):
        def tick_later():
            self.clock.t += 16.0
            return TICK
        self.run_session(TO_LOOP + ARM + ["t", tick_later] + FINISH)
        self.assertIn("idle", self.of("disarmed")[-1]["reason"])

    def test_episode_start_and_end(self):
        self.run_session(TO_LOOP + ARM + ["t", "r", "reach left", "q", "r", "t"] + FINISH)
        [start] = self.of("episode_start")
        [end] = self.of("episode_end")
        self.assertEqual((start["episode"], start["task"], start["camera"]),
                         (1, "reach left", False))
        self.assertEqual((end["status"], end["jogs"]), ("completed", 1))
        self.assertEqual([(p["event"], p["status"]) for p in self.mcap_episodes()],
                         [("start", None), ("end", "completed")])
        self.assertEqual(self.op.releases.count("r"), 2)

    def test_task_asked_once_and_new_task_key(self):
        self.run_session(TO_LOOP + ARM + ["t", "r", "first", "r", "r", "r", "n", "second",
                                          "r", "r", "t"] + FINISH)
        self.assertEqual([r["task"] for r in self.of("episode_start")],
                         ["first", "first", "second"])

    def test_new_task_refused_during_episode(self):
        self.run_session(TO_LOOP + ARM + ["t", "r", "first", "n", "r", "t"] + FINISH)
        self.assertEqual([r["task"] for r in self.of("episode_start")], ["first"])
        self.assertTrue(any("close the episode" in m for m in self.op.shown))

    def test_empty_task_refuses_episode(self):
        self.run_session(TO_LOOP + ARM + ["t", "r", "", "t"] + FINISH)
        self.assertEqual(self.of("episode_start"), [])

    def test_disarm_aborts_open_episode(self):
        self.run_session(TO_LOOP + ARM + ["t", "r", "task", "z"] + FINISH)
        [end] = self.of("episode_end")
        self.assertEqual((end["status"], end["reason"]), ("aborted", "disarmed"))

    def test_finish_aborts_open_episode(self):
        code = self.run_session(TO_LOOP + ARM + ["t", "r", "task", "x", "n", "g"])
        self.assertEqual(code, EXIT_OK)
        [end] = self.of("episode_end")
        self.assertEqual((end["status"], end["reason"]), ("aborted", "finish"))
        self.assertEqual(len(self.of("summary")), 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run** `.venv/Scripts/python.exe -m unittest tests.test_lab_teleop` — Expected: errors (`camera_factory` unexpected keyword, no teleop).

- [ ] **Step 3: Implement.**

`scorbot/lab/teleop.py`:

```python
"""Teleop mode of the guided lab session: arm once, then one press = one step.

Every step goes through the session's own gates (_prepare: travel cap, drift
check, preview; _execute: jog_joint and logging); teleop only removes the
typed confirmation and the questions between steps. After each step the
operator layer waits until the key is physically released and drops the
queued auto-repeats, so a held key gives exactly one step. Episodes mark
spans of a session for datasets. The physical stop is the stop.
"""

from __future__ import annotations

TELEOP_IDLE_DISARM_S = 15.0
TICK_S = 0.2
HELP = ("TELEOP: 1/q base +/-  2/w shoulder +/-  3/e elbow +/-  r start/stop episode  "
        "n new task  t leave teleop  ? help  x finish.  One press = one step; release "
        "the key before the next. Any other key disarms. The physical stop is the stop.")


class Teleop:
    def __init__(self, session, camera=None):
        self.s, self.camera = session, camera
        self.task: str | None = None
        self.episode: dict | None = None
        self.count = 0

    # -- loop -----------------------------------------------------------------

    def run(self) -> str:
        s = self.s
        if not s.op.can_wait_for_release():
            reason = "needs single-key input with key-release detection (Windows console)"
            s._write("teleop_refused", reason=reason)
            s.op.show(f"Teleop {reason}.", "warn")
            return "left"
        s._write("teleop_start", idle_disarm_s=TELEOP_IDLE_DISARM_S)
        s.op.show(HELP)
        s.op.wait_for_release("t")
        result, last = "left", s.clock()
        try:
            while s.fault is None and s.armed:
                s._status()
                key = s.op.key_or_tick("teleop key: ", TICK_S)
                self._check_camera()
                if key is None:
                    if s.clock() - last > TELEOP_IDLE_DISARM_S:
                        s._disarm(f"teleop idle for more than {TELEOP_IDLE_DISARM_S:g} s")
                    continue
                outcome = self._handle(key)
                if outcome is not None:
                    result = outcome
                    break
                last = s.clock()
        finally:
            reason = ("fault" if s.fault else "disarmed" if not s.armed else
                      "finish" if result == "finish" else "left teleop")
            self._end_episode("aborted", reason)
            s._write("teleop_end", result=result)
        return result

    def _intent(self, key, action, accepted, reason=None, **extra):
        self.s._write("teleop_intent", key=key, action=action, accepted=accepted,
                      reason=reason, **extra)

    def _handle(self, key):
        from .session import JOG_KEYS
        s = self.s
        if key in ("", "x"):
            self._intent(key, "finish", True)
            return "finish"
        if key == "t":
            self._intent(key, "leave", True)
            s.op.wait_for_release(key)
            return "left"
        if key == "?":
            self._intent(key, "help", True)
            s.op.show(HELP)
        elif key == "r":
            self._toggle_episode()
            s.op.wait_for_release(key)
        elif key == "n":
            self._new_task()
        elif key in JOG_KEYS:
            self._jog(key, *JOG_KEYS[key])
        else:
            self._intent(key, "unknown", False, "unknown key disarms")
            s._disarm(f"unknown key {key!r}")
        return None

    def _jog(self, key, joint, sign):
        s = self.s
        delta = sign * s.step
        prepared = s._prepare(joint, delta)
        accepted = False
        if prepared is not None:
            n, before, plan = prepared
            accepted = s._execute(joint, delta, "teleop", n, before, plan, observe=False)
            if accepted and self.episode is not None:
                self.episode["jogs"] += 1
        discarded = s.op.wait_for_release(key)
        self._intent(key, "jog", accepted, None if accepted else "refused or failed",
                     joint=joint, delta_deg=delta, discarded_keys=discarded)

    # -- episodes ---------------------------------------------------------------

    def _new_task(self):
        s = self.s
        if self.episode is not None:
            self._intent("n", "new_task", False, "episode open")
            s.op.show("Stop the episode first (r) to close the episode, then change the task.")
            return
        task = s.op.text("Task for the next episodes (e.g. reach left block): ")
        if not task:
            self._intent("n", "new_task", False, "empty task")
            return
        self.task = task
        self._intent("n", "new_task", True)

    def _toggle_episode(self):
        if self.episode is not None:
            self._end_episode("completed")
            return
        s = self.s
        if self.camera is not None and not self.camera.live():
            reason = self.camera.problem()
            self._intent("r", "episode_start", False, reason)
            s.op.show(f"Episode not started: {reason}.", "warn")
            return
        if self.task is None:
            task = s.op.text("Task for these episodes (e.g. reach left block): ")
            if not task:
                self._intent("r", "episode_start", False, "empty task")
                s.op.show("Episode not started: a task is needed.", "warn")
                return
            self.task = task
        self.count += 1
        self.episode = {"episode": self.count, "task": self.task, "jogs": 0,
                        "frames_at_start": (self.camera.frames_written()
                                            if self.camera is not None else 0)}
        self._intent("r", "episode_start", True)
        s._write("episode_start", episode=self.count, task=self.task,
                 camera=self.camera is not None)
        s.rec.log_episode(self.count, "start", task=self.task)
        s.op.show(f"EPISODE {self.count} RECORDING: {self.task}. Press r to stop.")

    def _end_episode(self, status, reason=None):
        episode = self.episode
        if episode is None:
            return
        if status == "completed" and self.camera is not None and not self.camera.live():
            status, reason = "aborted", self.camera.problem()
        self.episode = None
        frames = (self.camera.frames_written() - episode["frames_at_start"]
                  if self.camera is not None else None)
        s = self.s
        s._write("episode_end", episode=episode["episode"], status=status, reason=reason,
                 jogs=episode["jogs"], frames=frames)
        s.rec.log_episode(episode["episode"], "end", task=episode["task"], status=status,
                          reason=reason)
        level = "info" if status == "completed" else "alarm"
        s.op.show(f"EPISODE {episode['episode']} {status.upper()}"
                  + (f": {reason}" if reason else "") + ".", level)

    # -- camera -----------------------------------------------------------------

    def _check_camera(self):
        if self.camera is None:
            return
        for row in self.camera.drain():
            self.s._write("camera_health", **row)
        if self.episode is not None and not self.camera.live():
            self._end_episode("aborted", self.camera.problem())
```

In `session.py`:
- `__init__` gains keyword `camera_factory=None`; store `self.camera_factory = camera_factory` and `self.camera = None`.
- `HELP` becomes:

```python
HELP = ("Keys: 1/q base +/-   2/w shoulder +/-   3/e elbow +/-   s step size   "
        "a arm   d disarm   t teleop   b back to start   m mark pose   g go to mark   "
        "? help   x finish.  One press = one step. The physical stop is the stop.")
```

- In `_jog_loop`, before `elif key in JOG_KEYS:` add:

```python
            elif key == "t":
                if not self.armed:
                    self.op.show("Teleop needs the arm armed: press a.")
                else:
                    from .teleop import Teleop
                    if Teleop(self, self.camera).run() == "finish":
                        break
```

- [ ] **Step 4: Run** `.venv/Scripts/python.exe -m unittest tests.test_lab_teleop tests.test_lab_session` — Expected: PASS. (`camera_factory` is accepted and unused until Task 5.)

- [ ] **Step 5: Commit** — "Add a teleop mode with episodes to the guided lab session".

---

### Task 5: Camera in the lab session

**Files:**
- Create: `scorbot/lab/camera.py`
- Modify: `scorbot/lab/session.py` (`run`, `_steps`, `_finish`), `scorbot/lab/__main__.py` (`--camera`)
- Test: `tests/test_lab_teleop.py` (camera class), `tests/test_lab_terminal.py` (CLI flag)

**Interfaces:**
- Consumes: `CameraStream.create`, `CameraRecorder` (`last_write_ns`, `failure`, `counts`, `drain_health`, `stop`), `FakeSource`, `OpenCVSource`.
- Produces: `LabCamera(source_factory, *, stale_s=1.0, clock=time.monotonic_ns)` with `start(writer)`, `live()`, `problem()`, `frames_written()`, `drain()`, `stop() -> str`, `settings: dict`; `CAMERA_ID = "main"`.

- [ ] **Step 1: Failing tests.** Append to `tests/test_lab_teleop.py`:

```python
def needs_cv2(test):
    try:
        import cv2  # noqa: F401
    except ImportError:
        return unittest.skip("opencv not installed (pip install .[camera])")(test)
    return test


class TeleopCameraTests(TeleopTests):
    def wait_live(self, then):
        def answer():
            import time
            for _ in range(300):
                if self.session.camera is not None and self.session.camera.live():
                    break
                time.sleep(0.01)
            return then
        return answer

    def pause(self, seconds, then=TICK):
        def answer():
            import time
            time.sleep(seconds)
            return then
        return answer

    @needs_cv2
    def test_episode_with_camera_records_frames(self):
        from scorbot.camera.source import FakeSource
        from scorbot.camera.stream import scan_stream
        self.run_session(TO_LOOP + ARM + ["t", self.wait_live("r"), "task", "q",
                                          self.pause(0.3, "r"), "t"] + FINISH,
                         camera_factory=lambda: FakeSource(pace=True))
        [end] = self.of("episode_end")
        self.assertEqual(end["status"], "completed")
        self.assertGreater(end["frames"], 0)
        self.assertTrue(self.of("episode_start")[0]["camera"])
        self.assertTrue(self.of("camera_health"))
        [folder] = [p for p in (self.root / "sessions").iterdir() if p.is_dir()]
        self.assertEqual(scan_stream(folder, "main").errors, [])

    @needs_cv2
    def test_stalled_camera_aborts_episode_without_a_key_and_stays_armed(self):
        from scorbot.camera.source import FakeSource
        self.run_session(TO_LOOP + ARM + ["t", self.wait_live("r"), "task"]
                         + [self.pause(0.3)] * 6 + ["t"] + FINISH,
                         camera_factory=lambda: FakeSource(pace=True, block_at=15))
        [end] = self.of("episode_end")
        self.assertEqual((end["status"], end["reason"]), ("aborted", "camera stalled"))
        self.assertNotIn("disarmed", [r["type"] for r in self.rows
                                      if r["type"] == "disarmed"
                                      and "camera" in r.get("reason", "")])

    @needs_cv2
    def test_failed_camera_refuses_episode(self):
        from scorbot.camera.source import FakeSource
        self.run_session(TO_LOOP + ARM + ["t", self.pause(0.3, "r"), "t"] + FINISH,
                         camera_factory=lambda: FakeSource(pace=True, fail_at=2))
        self.assertEqual(self.of("episode_start"), [])
        refused = [r for r in self.of("teleop_intent") if r["action"] == "episode_start"]
        self.assertIn("camera failed", refused[0]["reason"])

    def test_camera_open_failure_continues_without_video(self):
        def broken():
            raise RuntimeError("no camera at index 0")
        code = self.run_session(TO_LOOP + ARM + ["t", "r", "task", "r", "t"] + FINISH,
                                camera_factory=broken)
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(len(self.of("camera_unavailable")), 1)
        self.assertFalse(self.of("episode_start")[0]["camera"])
```

(`TeleopCameraTests` inherits the harness; it re-runs the base tests too, which is harmless and cheap. To avoid that, move the harness into a `_Harness` base without tests: make `TeleopTests` and `TeleopCameraTests` both subclass `_Harness(unittest.TestCase)` holding `setUp`, `tearDown`, `run_session`, `of`, `jogs`, `mcap_episodes`.)

Append to `tests/test_lab_terminal.py`:

```python
class CameraFlagTests(unittest.TestCase):
    def test_fake_camera_needs_simulate(self):
        from scorbot.lab.__main__ import main
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(["--camera", "fake"])

    def test_camera_index_must_be_a_number(self):
        from scorbot.lab.__main__ import main
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(["--simulate", "--camera", "front"])
```

- [ ] **Step 2: Run** `.venv/Scripts/python.exe -m unittest tests.test_lab_teleop tests.test_lab_terminal` — Expected: camera tests fail (no camera started, no `--camera`).

- [ ] **Step 3: Implement.** `scorbot/lab/camera.py`:

```python
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
        self.source_factory, self.stale_ns, self.clock = source_factory, int(stale_s * 1e9), clock
        self.recorder: CameraRecorder | None = None
        self.settings: dict = {}

    def start(self, writer) -> None:
        source = self.source_factory()
        source.open()
        try:
            self.settings = source.settings()
            stream = CameraStream.create(writer, CAMERA_ID, self.settings)
        except Exception:
            source.close()
            raise
        self.recorder = CameraRecorder(source, stream, clock=self.clock)
        self.recorder.start()

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
```

`session.py`:
- In `run()`, pass `camera_ids=["main"] if self.camera_factory else ()` to `SessionWriter.create`, and right after `with writer:` add `self._writer = writer`.
- In `_steps`, wrap the `with self.robot_factory(...)` block body's motion part: after `self._home()` and before `self._jog_loop()`, call `self._start_camera()`. Wrap the whole `with self.robot_factory(...)` block in `try: ... finally: self._stop_camera()`.
- Add:

```python
    def _start_camera(self):
        if self.camera_factory is None:
            return
        from .camera import LabCamera
        camera = LabCamera(self.camera_factory)
        try:
            camera.start(self._writer)
        except Exception as error:
            self._write("camera_unavailable", error=f"{type(error).__name__}: {error}")
            self.op.show(f"Camera unavailable ({error}); continuing without video.", "warn")
            return
        self.camera = camera
        self._write("camera_started", settings=camera.settings)

    def _stop_camera(self):
        camera, self.camera = self.camera, None
        if camera is None:
            return
        status = camera.stop()
        for row in camera.drain():
            self._write("camera_health", **row)
        self._write("camera_stopped", status=status)
```

`__main__.py`: add argument

```python
    parser.add_argument("--camera", default=None,
                        help="Record a webcam during the session: an index (0, 1, ...), "
                             "or 'fake' with --simulate")
```

after `parse_args`:

```python
    camera_factory = None
    if args.camera is not None:
        if args.camera == "fake":
            if not args.simulate:
                parser.error("--camera fake needs --simulate")
            from ..camera.source import FakeSource
            camera_factory = lambda: FakeSource(width=640, height=480, pace=True)  # noqa: E731
        else:
            try:
                index = int(args.camera)
            except ValueError:
                parser.error("--camera must be a number or 'fake'")
            from ..camera.source import OpenCVSource
            camera_factory = lambda: OpenCVSource(index)  # noqa: E731
```

and pass `camera_factory=camera_factory` to `LabSession(...)`.

- [ ] **Step 4: Run** `.venv/Scripts/python.exe -m unittest tests.test_lab_teleop tests.test_lab_terminal tests.test_lab_session` three times — Expected: PASS each time.

- [ ] **Step 5: Commit** — "Record the webcam during lab sessions and guard episodes on it".

---

### Task 6: Docs

**Files:** `docs/LAB_SESSION.md`, `docs/OPERATOR_UX.md`, `docs/PROJECT_LOG.md`, `docs/superpowers/specs/2026-10-01-m1-roadmap-design.md` (progress line), `scorbot/CLAUDE.md` (lab module row, if it lists modules).

- [ ] **Step 1:** `LAB_SESSION.md`: a "Teleop and episodes" section: `t` (armed) enters/leaves teleop; one press = one step, release before the next; `r` start/stop episode, task asked once, `n` new task; any other key disarms; 15 s idle disarm; `--camera N` / `--camera fake --simulate`; episodes need live camera frames; camera faults abort the episode but keep the arm armed.
- [ ] **Step 2:** `OPERATOR_UX.md`: why a held key gives one step (console sees no key-up; the release gate reads the key state and only ever delays motion).
- [ ] **Step 3:** `PROJECT_LOG.md` entry "Keyboard teleop (M1 step 4)" including both reviews' findings and the rejected Gemini suggestion (disarm on camera fault) with the reason.
- [ ] **Step 4:** Roadmap progress line: steps 0-4 done (keyboard only).
- [ ] **Step 5:** Run the full suite once, `ruff check .`, `compileall`; commit — "Document teleop, episodes and the session camera".

## Self-review notes

- Spec coverage: section 1 (Tasks 1, 4), 2 (Tasks 3, 4), 3 (Tasks 2, 4), 4 (Tasks 2, 5), 6 tests (all tasks), 7 docs (Task 6).
- `JOG_KEYS` is imported lazily inside `Teleop._handle` to avoid a circular import (session imports teleop lazily too).
