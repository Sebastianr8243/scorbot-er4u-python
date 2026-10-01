# Lab Positions and Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add back to start (`b`), marked positions (`m`, `g`), a counts drift check before every motion, and plain-language fault guidance to the guided lab session (`python -m scorbot.lab`).

**Architecture:** Two new pure modules hold all the new logic that does not touch the robot: `scorbot/lab/moves.py` (step planning) and `scorbot/lab/faults.py` (error text to guidance). The engine `scorbot/lab/session.py` gets one shared motion path (`_prepare` then `_execute`) that keyed jogs and multi-step plans both use, so every step passes the same travel cap, drift check, SDK gates and logging. Multi-step plans move one joint at a time (elbow, shoulder, base) in ordinary 1 or 0.5 degree jogs.

**Tech Stack:** Python >= 3.10 standard library, existing `scorbot` SDK and `scorbot.session`, `unittest` (run with pytest).

**Spec:** [docs/superpowers/specs/2026-09-30-lab-positions-and-recovery-design.md](../specs/2026-09-30-lab-positions-and-recovery-design.md)

## Global Constraints

- No new controller commands. The only motion call added is more `Scorbot.jog_joint` steps for `base`, `shoulder`, `elbow`. No wrist, no gripper, no simultaneous joints.
- Step sizes are exactly 1.0 and 0.5 degrees. `TRAVEL_CAP_DEG = 10.0` still applies to every step.
- Multi-step joint order: `RETURN_ORDER = ("elbow", "shoulder", "base")`.
- `MAX_MARKS = 9`, names `P1`..`P9`. Marks live only in the session object; never saved to disk.
- Drift limit: `DRIFT_COUNTS = 20` (the legacy settle band, `openScorbot/libcomm.py:86`). Compared per motor with `scorbot.calibration.signed_count_delta`, never raw subtraction.
- A multi-step move needs the session armed and one typed confirmation: `BACK` or `GOTO P<n>` (trimmed, case-insensitive, via `operator.confirm`).
- Any key pending before a plan step stops the plan before that step and disarms. Screens call this a software pause and name the physical stop. Never call anything here an emergency stop.
- Texts say "probably" wherever a cause is inferred.
- `scorbot/lab/moves.py` and `scorbot/lab/faults.py` import nothing from `scorbot` except the standard library (`faults.py`) or nothing at all (`moves.py`). No robot, no I/O, no `print`.
- JSONL rows are written before the matching recorder call. Payloads stay strict JSON.
- Tests never open USB. Use `SimulatedScorbot` / `SimulatedController` and `ScriptedOperator`.
- Do not edit `openScorbot/`.
- Commit messages: imperative sentence-case subject, a body explaining why, **no AI attribution or co-author lines**.
- Commands use `.venv/Scripts/python.exe`. Per-file test runs: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider <file>`.

## Review Focus

1. **`g` then a mark number that does not exist** (for example `7` with two marks). Expected: "No mark chosen.", nothing moves, no plan rows. Test in Task 5.
2. **End of input at "Is the arm at start?"** Expected: answer recorded as `unsure`, `plan_complete` still written, session finishes normally. Test in Task 5.
3. **Drift on a motor no plan moves** (a wrist motor nudged 21 counts). Expected: the next jog is refused like any other drift. Test in Task 4.
4. **Back to start after 0.5 degree jogs.** Expected: the plan contains the matching 0.5 step regardless of the current step setting. Test in Task 5.
5. **`b` when already at start.** Expected: "Already at start.", no `BACK` prompt, nothing moves. Test in Task 5.

---

## System breakdown

### Components and ownership

Each task owns its files. **A task never edits a file another task owns.** If a task finds it needs a change outside its files, it stops and reports instead.

| Task | Component | Owns (create or modify) | Depends on |
|---|---|---|---|
| T1 | Step planner | `scorbot/lab/moves.py` (new), `tests/test_lab_moves.py` (new) | none |
| T2 | Fault guidance | `scorbot/lab/faults.py` (new), `tests/test_lab_faults.py` (new) | none |
| T3 | Operator test double | `scorbot/lab/operator.py`, `tests/test_lab_operator.py` (new) | none |
| T4 | Engine: shared motion path and drift check | `scorbot/lab/session.py`, `tests/test_lab_session.py` | none |
| T5 | Engine: back, mark, go to | `scorbot/lab/session.py`, `tests/test_lab_session.py` | T1, T3, T4 |
| T6 | Engine: fault guidance on screen | `scorbot/lab/session.py`, `tests/test_lab_session.py` | T2, T5 |
| T7 | Review, live view, docs | `scorbot/lab/review.py`, `scripts/watch_lab_log.py`, `tests/test_lab_log_review.py`, `tests/test_watch_lab_log.py`, `docs/LAB_SESSION.md`, `docs/SAFETY_CASE.md` | row names only (listed below) |

### Execution waves

| Wave | Tasks in parallel | Why this grouping |
|---|---|---|
| 1 | T1, T2, T3, T4 | Disjoint files; T4 uses plain `(joint, delta)` arguments, so it does not need T1 |
| 2 | T5, T7 | T5 needs T1, T3, T4. T7 only needs the row names below, and owns different files |
| 3 | T6 | Same file as T5 (`session.py`), so it waits for T5 |
| Final | Orchestrator | Full suite, ruff, simulated rehearsal, whole-branch review |

**Parallel-work rules for subagents:**
- Work on branch `feat/lab-positions` (created from `main` by the orchestrator before wave 1).
- **Implementers do not commit.** They leave changes in the working tree and report. The orchestrator reviews each task and commits it with the task's commit message. This avoids git index conflicts between parallel agents.
- Run only your own test files while others are working. The orchestrator runs the full suite between waves.

### Shared contracts (the only names tasks share)

**T1 `scorbot/lab/moves.py` exports:**
```python
RETURN_ORDER: tuple[str, ...] = ("elbow", "shoulder", "base")
MAX_MARKS: int = 9
STEP_DEG: float = 1.0
HALF_STEP_DEG: float = 0.5

@dataclass(frozen=True)
class Move:
    joint: str
    delta_deg: float
    label: str  # property: f"{joint.upper()} {delta_deg:+g}", e.g. "BASE -1", "ELBOW +0.5"

def plan_moves(current: Mapping[str, float], target: Mapping[str, float]) -> list[Move]
def apply(travel: Mapping[str, float], move: Move) -> dict[str, float]

@dataclass(frozen=True)
class MarkedPosition:
    name: str                      # "P1".."P9"
    travel: dict[str, float]       # session travel at marking, base/shoulder/elbow
    counts: dict[str, int]         # RobotState.encoder_counts at marking, all six motors
```

**T2 `scorbot/lab/faults.py` exports:**
```python
@dataclass(frozen=True)
class Guidance:
    key: str
    title: str
    meaning: str
    steps: tuple[str, ...]

COMMON_STEPS: tuple[str, ...]
def guidance_for(error: str) -> Guidance
def format_guidance(guidance: Guidance) -> str
```
Keys: `timeout`, `error_too_large`, `not_settled`, `feedback`, `worker_crash`, `led_gate`, `counts_drift`, `faulted`, `unknown`.

**T3 `ScriptedOperator` gains:** attribute `pending_keys: int` (default 0). `discard_pending_keys()` returns it and resets it to 0 (still increments `discards`).

**T4 `LabSession` gains (private, used by T5 and T6):**
```python
DRIFT_COUNTS = 20                                  # module constant
self.home_counts: dict[str, int] | None            # counts at home_complete
self.last_counts: dict[str, int] | None            # counts after the last completed step
def _drift_ok(self, state) -> bool                 # False => wrote counts_drift, alarmed, disarmed
def _prepare(self, joint: str, delta: float) -> tuple[int, RobotState, dict] | None
def _execute(self, joint: str, delta: float, how: str, n: int, before, plan, *,
             observe: bool = True) -> bool
```

**JSONL row types (T7 relies on these names):**

| Row | Written by | Fields |
|---|---|---|
| `counts_drift` | T4 | `differences` (dict motor -> int), `limit` (int) |
| `position_marked` | T5 | `name`, `travel`, `counts` |
| `mark_refused` | T5 | `reason` |
| `plan_shown` | T5 | `name`, `moves` (list of labels) |
| `plan_declined` | T5 | `name` |
| `plan_stopped` | T5 | `name`, `reason`, `steps_done` |
| `plan_complete` | T5 | `name`, `answer`, `steps`, `count_differences` |
| `fault_guidance` | T6 | `key`, `title` |
| `jog_confirmed` | existing | `how` now also `back` or `goto` |

---

### Task 1: Step planner (`scorbot/lab/moves.py`)

**Files:**
- Create: `scorbot/lab/moves.py`
- Test: `tests/test_lab_moves.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `RETURN_ORDER`, `MAX_MARKS`, `STEP_DEG`, `HALF_STEP_DEG`, `Move`, `plan_moves`, `apply`, `MarkedPosition` exactly as in "Shared contracts".

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lab_moves.py`:
```python
"""Pure step planning for multi-step moves: no robot, no I/O."""

import dataclasses
import unittest

from scorbot.lab.moves import (MAX_MARKS, RETURN_ORDER, MarkedPosition, Move, apply,
                               plan_moves)

ZERO = {"base": 0.0, "shoulder": 0.0, "elbow": 0.0}


class PlanMovesTests(unittest.TestCase):
    def test_order_is_elbow_shoulder_base(self):
        self.assertEqual(RETURN_ORDER, ("elbow", "shoulder", "base"))
        moves = plan_moves({"base": -2.0, "shoulder": 1.0, "elbow": -1.0}, ZERO)
        self.assertEqual([m.label for m in moves],
                         ["ELBOW +1", "SHOULDER -1", "BASE +1", "BASE +1"])

    def test_whole_steps_then_one_half_step(self):
        self.assertEqual([m.delta_deg for m in plan_moves({"base": 0.0}, {"base": 2.5})],
                         [1.0, 1.0, 0.5])
        self.assertEqual([m.label for m in plan_moves({"elbow": 0.5}, {"elbow": -1.0})],
                         ["ELBOW -1", "ELBOW -0.5"])

    def test_already_there_is_empty(self):
        self.assertEqual(plan_moves({"base": 1.0}, {"base": 1.0}), [])
        self.assertEqual(plan_moves({}, {}), [])

    def test_missing_joint_counts_as_zero(self):
        self.assertEqual(plan_moves({"base": 1.0}, {}), [Move("base", -1.0)])

    def test_float_noise_is_tolerated(self):
        self.assertEqual(plan_moves({"base": 0.1 + 0.2 - 0.3 + 1.0}, {}), [Move("base", -1.0)])

    def test_rejects_bad_input(self):
        for current, target in (({"base": 0.3}, {}), ({"wrist_pitch": 1.0}, {}),
                                ({}, {"gripper": 1.0}), ({"base": float("nan")}, {}),
                                ({"base": float("inf")}, {})):
            with self.subTest(current=current, target=target):
                with self.assertRaises(ValueError):
                    plan_moves(current, target)


class MoveTests(unittest.TestCase):
    def test_labels_match_the_typed_confirmation_format(self):
        self.assertEqual(Move("base", -1.0).label, "BASE -1")
        self.assertEqual(Move("elbow", 0.5).label, "ELBOW +0.5")

    def test_apply_returns_a_new_dict(self):
        travel = dict(ZERO)
        new = apply(travel, Move("base", -1.0))
        self.assertEqual(new["base"], -1.0)
        self.assertEqual(travel["base"], 0.0)
        with self.assertRaises(ValueError):
            apply(travel, Move("gripper", 1.0))

    def test_marked_position_is_frozen(self):
        mark = MarkedPosition("P1", dict(ZERO), {"base": 5})
        with self.assertRaises(dataclasses.FrozenInstanceError):
            mark.name = "P2"
        self.assertEqual(MAX_MARKS, 9)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_lab_moves.py`
Expected: FAIL, `ModuleNotFoundError: No module named 'scorbot.lab.moves'`.

- [ ] **Step 3: Implement**

Create `scorbot/lab/moves.py`:
```python
"""Step planning for multi-step moves in the guided session. Pure: no robot, no I/O.

A plan is a list of ordinary 1 or 0.5 degree jogs, one joint at a time, in
RETURN_ORDER (retract the elbow and shoulder before swinging the base).
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping

RETURN_ORDER = ("elbow", "shoulder", "base")
MAX_MARKS = 9
STEP_DEG = 1.0
HALF_STEP_DEG = 0.5
_TOLERANCE = 1e-9


@dataclass(frozen=True)
class Move:
    joint: str
    delta_deg: float

    @property
    def label(self) -> str:
        return f"{self.joint.upper()} {self.delta_deg:+g}"


@dataclass(frozen=True)
class MarkedPosition:
    """A pose marked in this session. Never saved: home is re-found each session."""

    name: str
    travel: dict
    counts: dict


def _check_joint(joint: str) -> None:
    if joint not in RETURN_ORDER:
        raise ValueError(f"Unknown joint for a multi-step move: {joint!r}")


def plan_moves(current: Mapping[str, float], target: Mapping[str, float]) -> list[Move]:
    """Whole steps toward the target per joint, then one half step if needed."""
    for joint in (*current, *target):
        _check_joint(joint)
    moves: list[Move] = []
    for joint in RETURN_ORDER:
        difference = float(target.get(joint, 0.0)) - float(current.get(joint, 0.0))
        if not math.isfinite(difference):
            raise ValueError(f"{joint}: travel must be finite")
        halves = round(difference / HALF_STEP_DEG)
        if abs(difference - halves * HALF_STEP_DEG) > _TOLERANCE:
            raise ValueError(f"{joint}: {difference:g} degrees is not a multiple of 0.5")
        sign = 1.0 if halves > 0 else -1.0
        whole, half = divmod(abs(halves), 2)
        moves += [Move(joint, sign * STEP_DEG)] * whole
        if half:
            moves.append(Move(joint, sign * HALF_STEP_DEG))
    return moves


def apply(travel: Mapping[str, float], move: Move) -> dict[str, float]:
    """The travel dict after ``move``; the input is not changed."""
    _check_joint(move.joint)
    new = dict(travel)
    new[move.joint] = new.get(move.joint, 0.0) + move.delta_deg
    return new
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_lab_moves.py`
Expected: all pass.

- [ ] **Step 5: Hand back (orchestrator commits)**

Commit message:
```
Add pure step planning for lab multi-step moves

Back to start and go to mark are built from the same bounded jogs as keyed
moves. Keeping the plan pure (no robot, no I/O) lets the joint order, step
sizes and input checks be tested on their own.
```

---

### Task 2: Fault guidance (`scorbot/lab/faults.py`)

**Files:**
- Create: `scorbot/lab/faults.py`
- Test: `tests/test_lab_faults.py`

**Interfaces:**
- Consumes: nothing. Matches these exact SDK and session texts (from `scorbot/robot.py` and `scorbot/lab/session.py`):
  - `"Command timed out; physical stop may be required"`
  - `"Legacy controller returned error code {n}"`
  - `"Controller feedback is unavailable: {detail}"`
  - `"USB {sync|command} worker stopped: {exc}"`, `"USB sync worker is not running: {fault}"`
  - `"LED check {step} not confirmed: {details}"`
  - `"Controller is faulted: {fault}"`
  - `"counts drift"` (passed by T6 for a drift refusal)
- Produces: `Guidance`, `COMMON_STEPS`, `guidance_for`, `format_guidance` as in "Shared contracts".

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lab_faults.py`:
```python
"""Error text to plain-language guidance: pure, no robot."""

import unittest

from scorbot.lab.faults import COMMON_STEPS, Guidance, format_guidance, guidance_for

CASES = {
    "Command timed out; physical stop may be required": "timeout",
    "Controller is faulted: Command timed out; physical stop may be required": "timeout",
    "Legacy controller returned error code 1": "error_too_large",
    "Legacy controller returned error code 2": "not_settled",
    "Legacy controller returned error code 10": "unknown",
    "Controller feedback is unavailable: Simulated stale feedback: no fresh controller "
    "response": "feedback",
    "USB command worker stopped: USB write failed": "worker_crash",
    "USB sync worker is not running: USB sync worker stopped: boom": "worker_crash",
    "LED check before_arm not confirmed: motors=off (expected lit)": "led_gate",
    "counts drift": "counts_drift",
    "Controller is faulted: Legacy controller returned error code 3": "faulted",
    "something new": "unknown",
    "": "unknown",
}


class GuidanceTests(unittest.TestCase):
    def test_known_texts_map_to_their_keys(self):
        for text, key in CASES.items():
            with self.subTest(text=text):
                self.assertEqual(guidance_for(text).key, key)

    def test_every_guidance_ends_with_the_common_steps(self):
        for text in CASES:
            guidance = guidance_for(text)
            self.assertIsInstance(guidance, Guidance)
            self.assertEqual(guidance.steps, COMMON_STEPS)

    def test_common_steps_name_the_stop_the_start_pose_and_holding(self):
        joined = " ".join(COMMON_STEPS)
        self.assertIn("physical stop", joined)
        self.assertIn("start pose", joined)
        self.assertIn("Connecting turns the motors on", joined)
        self.assertIn("unverified", joined)

    def test_inferred_causes_say_probably(self):
        for text in ("Legacy controller returned error code 1",
                     "Legacy controller returned error code 2",
                     "USB command worker stopped: x", "LED check after_enable not confirmed",
                     "counts drift"):
            with self.subTest(text=text):
                self.assertIn("probably", guidance_for(text).meaning.lower())

    def test_error_too_large_lists_motors_off_as_a_cause(self):
        self.assertIn("motor power", guidance_for("Legacy controller returned error code 1").meaning)

    def test_format_numbers_the_steps(self):
        text = format_guidance(guidance_for("Legacy controller returned error code 1"))
        self.assertTrue(text.startswith("Joint error too large: "))
        self.assertIn("\n  1. ", text)
        self.assertIn(f"\n  {len(COMMON_STEPS)}. ", text)
        self.assertNotIn("emergency stop", text.lower().replace("not an emergency stop", ""))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_lab_faults.py`
Expected: FAIL, `ModuleNotFoundError: No module named 'scorbot.lab.faults'`.

- [ ] **Step 3: Implement**

Create `scorbot/lab/faults.py`:
```python
"""Turn an SDK or session error into what it probably means and what to do. Pure.

Rules are checked in order; the first match wins, so a latched "Controller is
faulted: <cause>" still reports its root cause when that cause is known.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Guidance:
    key: str
    title: str
    meaning: str
    steps: tuple[str, ...]


COMMON_STEPS = (
    "If anything is still moving, use the physical stop.",
    "Do not retry in this session.",
    "Note the pose, the LEDs and any sounds; take a photo.",
    "Check the MOTORS LED: the controller may have cut motor power.",
    "Keep hands and objects clear below the arm: whether it holds its pose with "
    "motors off is unverified.",
    "To continue, start a new session. Connecting turns the motors on at the current "
    "pose, so first get the arm back to the known start pose per the lab procedure, "
    "then home again.",
)

_RULES = (
    ("timeout", r"timed out", "Command timed out",
     "The controller did not answer in time. The motion state is unknown; the arm may "
     "still be moving."),
    ("error_too_large", r"error code 1\b", "Joint error too large",
     "Probably a stall, a collision or an impact, or the controller cut motor power "
     "(e-stop, over-current or communication time-out)."),
    ("not_settled", r"error code 2\b", "Joint did not settle",
     "The joint probably did not reach its target within the legacy settle loop; it "
     "may be blocked or overloaded."),
    ("feedback", r"feedback is unavailable|no fresh|stale", "Controller feedback lost",
     "No fresh state arrived from the controller, so the arm's position is unknown."),
    ("worker_crash", r"worker stopped|worker is not running", "USB link lost",
     "A USB worker thread stopped. The controller probably times out and cuts motor "
     "power; when is unverified."),
    ("led_gate", r"led check", "LED check failed",
     "The LEDs did not show the expected state. The motors are probably off or the "
     "controller is not communicating."),
    ("counts_drift", r"counts drift", "Counts moved with nothing commanded",
     "Something probably moved the arm or the readings (electrical noise, a push, or "
     "sagging with motors off), so the logged travel no longer describes the pose."),
    ("faulted", r"controller is faulted", "Session already faulted",
     "An earlier fault latched the session; nothing will move again in this session."),
)
_UNKNOWN = ("unknown", "Unexpected error", "The cause is not recognised.")


def guidance_for(error: str) -> Guidance:
    text = (error or "").lower()
    for key, pattern, title, meaning in _RULES:
        if re.search(pattern, text):
            return Guidance(key, title, meaning, COMMON_STEPS)
    return Guidance(*_UNKNOWN, COMMON_STEPS)


def format_guidance(guidance: Guidance) -> str:
    lines = [f"{guidance.title}: {guidance.meaning}"]
    lines += [f"  {number}. {step}" for number, step in enumerate(guidance.steps, start=1)]
    return "\n".join(lines)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_lab_faults.py`
Expected: all pass.

- [ ] **Step 5: Hand back (orchestrator commits)**

Commit message:
```
Add plain-language guidance for lab session faults

An error string alone does not tell the operator what probably happened or
what is safe to do next. The guidance names likely causes, including the
controller cutting motor power, and always ends with the same safe steps,
including that reconnecting energises the motors at the current pose.
```

---

### Task 3: Operator test double (`ScriptedOperator.pending_keys`)

**Files:**
- Modify: `scorbot/lab/operator.py` (class `ScriptedOperator`: `__init__` and `discard_pending_keys`)
- Test: `tests/test_lab_operator.py` (new)

**Interfaces:**
- Consumes: nothing.
- Produces: `ScriptedOperator.pending_keys: int`; `discard_pending_keys()` returns it once and resets it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_lab_operator.py`:
```python
"""The scripted operator can simulate keys pressed while the arm moves."""

import unittest

from scorbot.lab.operator import ScriptedOperator


class ScriptedOperatorTests(unittest.TestCase):
    def test_pending_keys_are_returned_once_by_discard(self):
        op = ScriptedOperator([])
        self.assertEqual(op.discard_pending_keys(), 0)
        op.pending_keys = 2
        self.assertEqual(op.discard_pending_keys(), 2)
        self.assertEqual(op.discard_pending_keys(), 0)
        self.assertEqual(op.discards, 3)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_lab_operator.py`
Expected: FAIL (`assertEqual(0, 2)`: `discard_pending_keys` ignores `pending_keys`).

- [ ] **Step 3: Implement**

In `scorbot/lab/operator.py`, `ScriptedOperator.__init__`, after `self.discards = 0` add:
```python
        self.pending_keys = 0
```
Replace `ScriptedOperator.discard_pending_keys` with:
```python
    def discard_pending_keys(self):
        self.discards += 1
        count, self.pending_keys = self.pending_keys, 0
        return count
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_lab_operator.py tests/test_lab_session.py`
Expected: all pass.

- [ ] **Step 5: Hand back (orchestrator commits)**

Commit message:
```
Let the scripted operator simulate keys pressed during motion

Multi-step moves stop when a key is pressed between steps. Tests need to
press a key at a chosen moment without a terminal.
```

---

### Task 4: Engine shared motion path and drift check

**Files:**
- Modify: `scorbot/lab/session.py` (module constants, `LabSession.__init__`, `_home`, `_jog`; add `_drift_ok`, `_prepare`, `_execute`)
- Test: `tests/test_lab_session.py`

**Interfaces:**
- Consumes: existing `signed_count_delta`, `JOINTS`, `self.robot.get_state()`, `preview_jog`, `jog_joint`.
- Produces: `DRIFT_COUNTS`, `self.home_counts`, `self.last_counts`, `_drift_ok`, `_prepare`, `_execute` exactly as in "Shared contracts". Row `counts_drift`. All existing row types and their order stay the same (`jog_preview`, `jog_confirmed`, `before_jog`, `after_jog`, `jog_observation`, `jog_result`).

- [ ] **Step 1: Write the failing tests**

Add to class `LabSessionTests` in `tests/test_lab_session.py`:
```python
    def nudge(self, motor, counts, then):
        def answer():
            self.ctrl.counts[motor] += counts
            return then
        return answer

    def test_counts_drift_beyond_the_settle_band_refuses_the_next_jog(self):
        code = self.run_session(TO_LOOP + ARM + ["q", "BASE -1"] + OBS
                                + [self.nudge("base", 21, "q")] + FINISH)
        self.assertEqual(code, EXIT_OK)
        drift = self.of("counts_drift")
        self.assertEqual(len(drift), 1)
        self.assertEqual(drift[0]["differences"]["base"], 21)
        self.assertEqual(drift[0]["limit"], 20)
        self.assertEqual(len(self.jog_commands()), 1)
        self.assertEqual(self.of("disarmed")[-1]["reason"], "counts drift")

    def test_counts_within_the_settle_band_do_not_refuse(self):
        self.run_session(TO_LOOP + ARM + ["q", "BASE -1"] + OBS
                         + [self.nudge("base", 20, "q")] + OBS + FINISH)
        self.assertFalse(self.of("counts_drift"))
        self.assertEqual(len(self.jog_commands()), 2)

    def test_drift_after_homing_refuses_the_first_jog(self):
        self.run_session(TO_LOOP + ARM + [self.nudge("elbow", -25, "q")] + FINISH)
        self.assertEqual(len(self.of("counts_drift")), 1)
        self.assertFalse(self.jog_commands())

    def test_drift_on_a_motor_no_plan_moves_also_refuses(self):
        self.run_session(TO_LOOP + ARM + [self.nudge("wrist_motor_1", 21, "q")] + FINISH)
        self.assertEqual(self.of("counts_drift")[0]["differences"]["wrist_motor_1"], 21)
        self.assertFalse(self.jog_commands())
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_lab_session.py -k drift`
Expected: FAIL (no `counts_drift` rows; the jog runs).

- [ ] **Step 3: Implement**

In `scorbot/lab/session.py`:

After `STABLE_COUNTS = 2` add:
```python
# A legacy jog ends once the joint is within 20 counts of its target
# (openScorbot/libcomm.py settle loop), so counts may keep settling that far.
DRIFT_COUNTS = 20
```

In `LabSession.__init__`, after `self.jogs = 0` add:
```python
        self.home_counts = self.last_counts = None
```

In `_home`, replace the line `self._state("home_complete")` with:
```python
        state = self._state("home_complete")
        self.home_counts = dict(state.encoder_counts)
        self.last_counts = dict(state.encoder_counts)
```

Replace the whole `_jog` method with these four methods:
```python
    def _drift_ok(self, state) -> bool:
        """False (row, alarm, disarm) if counts moved since the last step unprompted."""
        if self.last_counts is None:
            return True
        differences = {motor: signed_count_delta(state.encoder_counts[motor],
                                                 self.last_counts[motor])
                       for motor in JOINTS}
        drift = {motor: d for motor, d in differences.items() if abs(d) > DRIFT_COUNTS}
        if not drift:
            return True
        self._write("counts_drift", differences=differences, limit=DRIFT_COUNTS)
        self.op.show(f"Counts moved by {drift} since the last step with nothing commanded. "
                     "The logged travel no longer describes the pose. Finish (x) and home "
                     "again in a new session.", "alarm")
        self._disarm("counts drift")
        return False

    def _prepare(self, joint, delta):
        """Travel cap, drift check and preview for one step; None if refused or failed."""
        self.joint = joint
        if abs(self.travel[joint] + delta) > TRAVEL_CAP_DEG + 1e-9:
            reason = (f"{joint} would be {self.travel[joint] + delta:+g} degrees from home; "
                      f"the session cap is {TRAVEL_CAP_DEG:g}")
            self._write("jog_refused", joint=joint, delta_deg=delta, reason=reason)
            self.op.show(f"Refused: {reason}.", "alarm")
            self._disarm("travel cap")
            return None
        n = self.jogs + 1
        try:
            before = self.robot.get_state()
        except Exception as error:
            self._jog_failed(n, error)
            return None
        if not self._drift_ok(before):
            return None
        try:
            plan = self.robot.preview_jog(joint, delta, speed=self.profile.speed,
                                          starting_signed_counts=before.signed_encoder_counts)
        except Exception as error:
            self._jog_failed(n, error)
            return None
        self._write("jog_preview", n=n, joint=joint, delta_deg=delta, plan=plan)
        return n, before, plan

    def _execute(self, joint, delta, how, n, before, plan, *, observe=True) -> bool:
        """Run one prepared step through jog_joint; False if it failed (session latched).

        With observe=False (plan steps) no questions are asked and pending keys
        are left for the plan loop, which stops on them.
        """
        move = f"{joint.upper()} {delta:+g}"
        self._write("jog_confirmed", n=n, how=how, move=move)
        self._write("before_jog", n=n, state=asdict(before))
        self.rec.log_state(before)
        command = self.rec.log_command("jog_joint", {"joint": joint, "delta_degrees": delta,
                                                     "speed": self.profile.speed})
        try:
            after = self.robot.jog_joint(joint, delta, speed=self.profile.speed)
        except Exception as error:
            self.rec.log_command_result(command, "faulted", detail=str(error))
            self._jog_failed(n, error)
            return False
        self.jogs = n
        self.travel[joint] += delta
        self.last_counts = dict(after.encoder_counts)
        self._write("after_jog", n=n, state=asdict(after))
        self.rec.log_command_result(command, "completed", completion_source="jog_joint() returned")
        self.rec.log_state(after)
        if observe:
            ignored = self.op.discard_pending_keys()
            if ignored:
                self.op.show(f"Ignored {ignored} key(s) pressed while the arm was moving.",
                             "warn")
            direction = self.op.choose(f"Which way did {joint} move relative to "
                                       f"{self.landmark}? [t toward / a away / n none / "
                                       "u unsure] ", DIRECTION_KEYS)
            other = self.op.choose("Did any other joint move? [y/n/u] ", YES_NO_UNSURE)
            note = self.op.text("Note (Enter to skip): ")
            self._write("jog_observation", n=n, direction=direction, other_joint_moved=other,
                        note=note)
        measured = {}
        for motor in JOINTS:
            try:
                measured[motor] = signed_count_delta(after.encoder_counts[motor],
                                                     before.encoder_counts[motor])
            except (KeyError, ValueError):
                measured[motor] = None
        self._write("jog_result", n=n, planned=plan["motor_count_deltas"], measured=measured)
        self.op.show(f"Planned {plan['motor_count_deltas']}, measured "
                     f"{ {m: v for m, v in measured.items() if v} or 'no change'}.")
        return True

    def _jog(self, joint, sign):
        delta = sign * self.step
        prepared = self._prepare(joint, delta)
        if prepared is None:
            return
        n, before, plan = prepared
        move = f"{joint.upper()} {delta:+g}"
        move_key = (joint, sign, self.step)
        if move_key in self.confirmed:
            how = "repeat"
            self.op.show(f"Repeat {move}.")
        else:
            self.op.show(f"Plan {move}: motor counts {plan['motor_count_deltas']} in "
                         f"{len(plan['increments'])} steps (legacy scale, not measured).")
            if not self.op.confirm(f"Type {move} to move: ", move):
                self._write("jog_declined", n=n, move=move)
                self._disarm("confirmation declined")
                return
            self.confirmed.add(move_key)
            how = "typed"
        self._execute(joint, delta, how, n, before, plan)
```

- [ ] **Step 4: Run to verify everything passes**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_lab_session.py tests/test_lab_terminal.py tests/test_lab_log_review.py`
Expected: all pass, including every pre-existing test (this task is a refactor plus the drift check).

- [ ] **Step 5: Hand back (orchestrator commits)**

Commit message:
```
Share one motion path in the lab session and check counts drift

Keyed jogs and the coming multi-step moves must pass the same travel cap,
SDK gates and logging, so the jog is split into prepare and execute. Before
every step the counts are compared with the last completed step: beyond the
20-count legacy settle band something moved the arm or the readings, so the
step is refused and the session disarms.
```

---

### Task 5: Engine back, mark and go to

**Files:**
- Modify: `scorbot/lab/session.py` (imports, `HELP`, `LabSession.__init__`, `_jog_loop`; add `_mark`, `_goto`, `_back`, `_run_plan`, `_stop_plan`)
- Test: `tests/test_lab_session.py`

**Interfaces:**
- Consumes: T1 `plan_moves`, `MarkedPosition`, `MAX_MARKS`, `Move.label`, `Move.joint`, `Move.delta_deg`; T3 `ScriptedOperator.pending_keys`; T4 `_drift_ok`, `_prepare`, `_execute`, `self.home_counts`.
- Produces: keys `b`, `m`, `g`; rows `position_marked`, `mark_refused`, `plan_shown`, `plan_declined`, `plan_stopped`, `plan_complete`; `jog_confirmed.how` values `back` and `goto`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_lab_session.py` at module level, after `FINISH`:
```python
MIXED = ["q", "BASE -1"] + OBS + ["q"] + OBS + ["e", "ELBOW -1"] + OBS  # base -2, elbow -1
```
Add to class `LabSessionTests`:
```python
    def plan_moves_run(self):
        return [r["move"] for r in self.of("jog_confirmed") if r["how"] in ("back", "goto")]

    def test_back_to_start_after_mixed_jogs(self):
        code = self.run_session(TO_LOOP + ARM + MIXED + ["b", "BACK", "y"] + FINISH)
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(self.of("plan_shown")[0]["moves"], ["ELBOW +1", "BASE +1", "BASE +1"])
        self.assertEqual(self.plan_moves_run(), ["ELBOW +1", "BASE +1", "BASE +1"])
        complete = self.of("plan_complete")[0]
        self.assertEqual((complete["name"], complete["answer"], complete["steps"]),
                         ("start", "yes", 3))
        self.assertEqual(set(complete["count_differences"].values()), {0})
        self.assertEqual(len(self.of("jog_observation")), 3)  # plan steps ask nothing

    def test_declined_back_moves_nothing_and_disarms(self):
        self.run_session(TO_LOOP + ARM + ["q", "BASE -1"] + OBS + ["b", "NO"] + FINISH)
        self.assertEqual(len(self.of("plan_declined")), 1)
        self.assertEqual(len(self.jog_commands()), 1)
        self.assertEqual(self.of("disarmed")[-1]["reason"], "confirmation declined")

    def test_key_during_a_plan_stops_it_after_the_current_step(self):
        original, seen = self.ctrl._apply, []

        def apply(payload):
            code = original(payload)
            if payload[0] in JOG_ORDERS:
                seen.append(payload[0])
                if len(seen) == 4:          # 3 keyed jogs, then the first plan step
                    self.op.pending_keys = 1
            return code
        self.ctrl._apply = apply
        self.run_session(TO_LOOP + ARM + MIXED + ["b", "BACK"] + FINISH)
        stopped = self.of("plan_stopped")[0]
        self.assertEqual((stopped["steps_done"], stopped["reason"]),
                         (1, "stopped by a key press"))
        self.assertEqual(len(self.jog_commands()), 4)
        self.assertEqual(self.of("disarmed")[-1]["reason"], "plan stopped")
        self.assertTrue(any("not an emergency stop" in m for m in self.op.shown))

    def test_mark_then_go_to_it(self):
        self.run_session(TO_LOOP + ARM + ["q", "BASE -1"] + OBS + ["m", "q"] + OBS
                         + ["g", "1", "GOTO P1", "y"] + FINISH)
        mark = self.of("position_marked")[0]
        self.assertEqual((mark["name"], mark["travel"]["base"]), ("P1", -1.0))
        self.assertEqual(self.plan_moves_run(), ["BASE +1"])
        self.assertEqual(set(self.of("plan_complete")[0]["count_differences"].values()), {0})

    def test_go_to_with_no_marks_and_a_tenth_mark_are_refused(self):
        self.run_session(TO_LOOP + ARM + ["g"] + ["m"] * 10 + FINISH)
        self.assertFalse(self.of("plan_shown"))
        self.assertEqual(len(self.of("position_marked")), 9)
        self.assertEqual(len(self.of("mark_refused")), 1)

    def test_invalid_mark_number_moves_nothing(self):
        self.run_session(TO_LOOP + ARM + ["m", "m", "g", "7", "7", "7"] + FINISH)
        self.assertFalse(self.of("plan_shown"))
        self.assertTrue(any("No mark chosen" in m for m in self.op.shown))

    def test_back_while_disarmed_does_nothing(self):
        self.run_session(TO_LOOP + ["b"] + FINISH)
        self.assertFalse(self.of("plan_shown"))
        self.assertTrue(any("press a to arm" in m for m in self.op.shown))

    def test_back_when_already_at_start_asks_nothing(self):
        self.run_session(TO_LOOP + ARM + ["b"] + FINISH)
        self.assertEqual(self.of("plan_shown")[0]["moves"], [])
        self.assertFalse(any("Type BACK" in p for p in self.op.prompts))
        self.assertFalse(self.jog_commands())

    def test_back_after_half_steps_uses_a_half_step(self):
        self.run_session(TO_LOOP + ARM + ["s", "q", "BASE -0.5"] + OBS + ["s", "b", "BACK", "y"]
                         + FINISH)
        self.assertEqual(self.plan_moves_run(), ["BASE +0.5"])

    def test_end_of_input_at_the_arrival_question_still_completes(self):
        code = self.run_session(TO_LOOP + ARM + ["q", "BASE -1"] + OBS + ["b", "BACK"])
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(self.of("plan_complete")[0]["answer"], "unsure")
        self.assertEqual(len(self.of("summary")), 1)

    def test_fault_in_a_plan_latches_and_still_finishes(self):
        def inject_then_type():
            self.ctrl.inject("controller_error")
            return "BACK"
        code = self.run_session(TO_LOOP + ARM + ["q", "BASE -1"] + OBS
                                + ["b", inject_then_type, "n", "g"])
        self.assertEqual(code, EXIT_FAILED)
        self.assertEqual(self.of("plan_stopped")[0]["steps_done"], 0)
        self.assertEqual(len(self.of("jog_failed")), 1)
        self.assertEqual(len(self.of("summary")), 1)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_lab_session.py`
Expected: the new tests FAIL (keys `b`, `m`, `g` are unknown; no plan rows).

- [ ] **Step 3: Implement**

In `scorbot/lab/session.py`:

Add the import after `from .operator import ENTER, StatusLine`:
```python
from .moves import MAX_MARKS, MarkedPosition, plan_moves
```

Replace `HELP` with:
```python
HELP = ("Keys: 1/q base +/-   2/w shoulder +/-   3/e elbow +/-   s step size   "
        "a arm   d disarm   b back to start   m mark pose   g go to mark   ? help   "
        "x finish.  One press = one step. The physical stop is the stop.")
```

In `LabSession.__init__`, after `self.home_counts = self.last_counts = None` add:
```python
        self.marks: list[MarkedPosition] = []
```

In `_jog_loop`, insert before `elif key in JOG_KEYS:`:
```python
            elif key == "m":
                self._mark()
            elif key == "g":
                self._goto()
            elif key == "b":
                self._back()
```

Add these methods after `_jog`:
```python
    # -- multi-step moves -----------------------------------------------------

    def _mark(self):
        if len(self.marks) >= MAX_MARKS:
            reason = f"at most {MAX_MARKS} marks per session"
            self._write("mark_refused", reason=reason)
            self.op.show(f"Not marked: {reason}.", "warn")
            return
        state = self.robot.get_state()
        mark = MarkedPosition(f"P{len(self.marks) + 1}", dict(self.travel),
                              dict(state.encoder_counts))
        self.marks.append(mark)
        self._write("position_marked", name=mark.name, travel=mark.travel, counts=mark.counts)
        self.op.show(f"Marked {mark.name}: {mark.travel} (degrees from home, legacy scale).")

    def _goto(self):
        if not self.armed:
            self.op.show("DISARMED: press a to arm.")
            return
        if not self.marks:
            self.op.show("No marked positions yet; press m to mark one.")
            return
        names = {str(i): mark.name for i, mark in enumerate(self.marks, start=1)}
        choice = self.op.choose(f"Go to which mark? [1-{len(self.marks)}] ", names)
        mark = next((m for m in self.marks if m.name == choice), None)
        if mark is None:
            self.op.show("No mark chosen.")
            return
        self._run_plan(mark.name, plan_moves(self.travel, mark.travel), "goto",
                       mark.counts, f"GOTO {mark.name}")

    def _back(self):
        if not self.armed:
            self.op.show("DISARMED: press a to arm.")
            return
        start = {joint: 0.0 for joint in self.travel}
        self._run_plan("start", plan_moves(self.travel, start), "back",
                       self.home_counts, "BACK")

    def _stop_plan(self, name, reason, done, total):
        self._write("plan_stopped", name=name, reason=reason, steps_done=done)
        self.op.show(f"Move to {name} stopped after {done} of {total} steps ({reason}). "
                     "This is a software pause, not an emergency stop; the physical "
                     "stop is the stop.", "warn")
        self._disarm("plan stopped")

    def _run_plan(self, name, moves, how, target_counts, confirm_text):
        """Show a multi-step move, confirm once, run it step by step, then check arrival."""
        try:
            state = self.robot.get_state()
        except Exception as error:
            self._jog_failed(self.jogs + 1, error)
            return
        if not self._drift_ok(state):
            return
        labels = [move.label for move in moves]
        self._write("plan_shown", name=name, moves=labels)
        if not moves:
            self.op.show(f"Already at {name}.")
            return
        self.op.show(f"Move to {name}, one joint at a time: {', '.join(labels)}. Any key "
                     "during the move stops it after the current step (a software pause, "
                     "not an emergency stop; the physical stop is the stop).")
        if not self.op.confirm(f"Type {confirm_text} to run it: ", confirm_text):
            self._write("plan_declined", name=name)
            self._disarm("confirmation declined")
            return
        for done, move in enumerate(moves):
            if self.op.discard_pending_keys() > 0:
                self._stop_plan(name, "stopped by a key press", done, len(moves))
                return
            prepared = self._prepare(move.joint, move.delta_deg)
            if prepared is None or not self._execute(move.joint, move.delta_deg, how,
                                                     *prepared, observe=False):
                self._stop_plan(name, "step refused or failed", done, len(moves))
                return
        answer = self.op.choose(f"Is the arm at {name}? [y/n/u] ", YES_NO_UNSURE)
        after = self.robot.get_state()
        differences = {motor: signed_count_delta(after.encoder_counts[motor],
                                                 target_counts[motor])
                       for motor in JOINTS}
        self._write("plan_complete", name=name, answer=answer, steps=len(moves),
                    count_differences=differences)
        self.op.show(f"At {name}: count differences from the target "
                     f"{ {m: d for m, d in differences.items() if d} or 'none'}.")
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_lab_session.py tests/test_lab_operator.py tests/test_lab_moves.py`
Expected: all pass. If `test_back_to_start_after_mixed_jogs` shows non-zero count differences, the simulator's `plan_jog` is not symmetric between +1 and -1; report it rather than loosening the test.

- [ ] **Step 5: Hand back (orchestrator commits)**

Commit message:
```
Add back to start, marked positions and go to mark in the lab session

The legacy code has no Go Home or position table, but the proven jogs can
return the arm in bounded steps. Plans move one joint at a time (elbow,
shoulder, base), need one typed confirmation, stop on any key between
steps, and end with an arrival question and the count difference from the
target. Marks live only for the session because home is found again each
session.
```

---

### Task 6: Engine fault guidance on screen

**Files:**
- Modify: `scorbot/lab/session.py` (imports, remove `RECOVERY_TEXT`, add `_guide`, edit `run`, `_jog_failed`, `_drift_ok`)
- Test: `tests/test_lab_session.py`

**Interfaces:**
- Consumes: T2 `guidance_for`, `format_guidance`; T4 `_drift_ok`; T5 nothing new.
- Produces: row `fault_guidance` (`key`, `title`), shown on every jog failure, on `session_failed` after enable, and on a drift refusal.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_lab_session.py` at module level, after the imports:
```python
class _ErrorOneController(SimulatedController):
    """Answers every jog with legacy result 1 (joint error word too large)."""

    def _apply(self, payload):
        return 1 if payload[0] in JOG_ORDERS else super()._apply(payload)
```
Add to class `LabSessionTests`:
```python
    def test_jog_failure_shows_guidance_naming_motors_off(self):
        self.ctrl = _ErrorOneController()
        code = self.run_session(TO_LOOP + ARM + ["q", "BASE -1", "n", "g"])
        self.assertEqual(code, EXIT_FAILED)
        self.assertEqual(self.of("fault_guidance")[0]["key"], "error_too_large")
        shown = "\n".join(self.op.shown)
        self.assertIn("motor power", shown)
        self.assertIn("start pose", shown)

    def test_led_gate_at_arming_shows_led_guidance(self):
        self.run_session(TO_LOOP + ["a", "door", "n", "g", "n", "g"])
        self.assertEqual(self.of("fault_guidance")[0]["key"], "led_gate")

    def test_drift_refusal_shows_drift_guidance(self):
        self.run_session(TO_LOOP + ARM + [self.nudge("base", 21, "q")] + FINISH)
        self.assertEqual(self.of("fault_guidance")[0]["key"], "counts_drift")

    def test_declined_before_enable_shows_no_guidance(self):
        self.run_session(["y", "n"])
        self.assertFalse(self.of("fault_guidance"))
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_lab_session.py -k guidance`
Expected: FAIL (no `fault_guidance` rows).

- [ ] **Step 3: Implement**

In `scorbot/lab/session.py`:

Add the import after the `.moves` import:
```python
from .faults import format_guidance, guidance_for
```

Delete the `RECOVERY_TEXT = (...)` constant (its text now lives in `faults.COMMON_STEPS`).

Add this method in the plumbing section, after `_state`:
```python
    def _guide(self, error_text):
        guidance = guidance_for(error_text)
        self._write("fault_guidance", key=guidance.key, title=guidance.title)
        self.op.show(format_guidance(guidance), "warn")
```

In `run`, replace:
```python
                    if self.enabled:
                        self.op.show(RECOVERY_TEXT, "warn")
```
with:
```python
                    if self.enabled:
                        self._guide(str(error))
```

In `_jog_failed`, replace `self.op.show(RECOVERY_TEXT, "warn")` with:
```python
        self._guide(self.fault)
```

In `_drift_ok`, after `self._disarm("counts drift")` and before `return False`, add:
```python
        self._guide("counts drift")
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_lab_session.py tests/test_lab_faults.py tests/test_lab_terminal.py`
Expected: all pass, including the existing tests that look for "start pose" and "physical stop" in `op.shown`.

- [ ] **Step 5: Hand back (orchestrator commits)**

Commit message:
```
Show fault guidance in the lab session

Every jog failure, a failure after enable and a drift refusal now say what
probably happened and the safe next steps, and log a fault_guidance row,
instead of only an error string.
```

---

### Task 7: Review, live view and docs

**Files:**
- Modify: `scorbot/lab/review.py` (`review_session_rows`)
- Modify: `scripts/watch_lab_log.py` (`ALARM_EVENTS`)
- Modify: `docs/LAB_SESSION.md`, `docs/SAFETY_CASE.md` (row HZ-22)
- Test: `tests/test_lab_log_review.py`, `tests/test_watch_lab_log.py`

**Interfaces:**
- Consumes: row names and fields from "Shared contracts" only. Does not import or edit `session.py`.
- Produces: `counts_drift` is a review problem and a live-view alarm; plan steps (`how` = `back`/`goto`) show in the review table.

- [ ] **Step 1: Write the failing tests**

Add to the review test class in `tests/test_lab_log_review.py` (the class with the `rows(self, ...)` helper):
```python
    def test_plan_steps_appear_in_the_table_without_problems(self):
        from scorbot.lab.review import format_session_review, review_session_rows
        report = review_session_rows(self.rows(extra=[
            {"type": "plan_shown", "name": "start", "moves": ["BASE +1"]},
            {"type": "jog_preview", "n": 2, "joint": "base",
             "plan": {"motor_count_deltas": {"base": 142}}},
            {"type": "jog_confirmed", "n": 2, "how": "back", "move": "BASE +1"},
            {"type": "before_jog", "n": 2, "state": {}},
            {"type": "after_jog", "n": 2, "state": {}},
            {"type": "jog_result", "n": 2, "planned": {"base": 142},
             "measured": {"base": 142, "shoulder": 0, "elbow": 0}},
            {"type": "plan_complete", "name": "start", "answer": "yes", "steps": 1,
             "count_differences": {"base": 0}}]))
        self.assertEqual(report["problems"], [])
        self.assertEqual(report["jogs"][1]["how"], "back")
        self.assertIn("back", format_session_review(report))

    def test_counts_drift_is_a_problem(self):
        from scorbot.lab.review import review_session_rows
        report = review_session_rows(self.rows(extra=[
            {"type": "counts_drift", "differences": {"base": 25}, "limit": 20}]))
        self.assertTrue(any(p.startswith("counts drift") for p in report["problems"]))
```
Add to `WatchLabLogTests` in `tests/test_watch_lab_log.py`:
```python
    def test_counts_drift_is_an_alarm(self):
        view = RunView()
        view.add({"type": "counts_drift", "differences": {"base": 25}, "limit": 20}, "session")
        self.assertIn("!!! counts_drift", view.render())
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_lab_log_review.py tests/test_watch_lab_log.py`
Expected: `test_counts_drift_is_a_problem` and `test_counts_drift_is_an_alarm` FAIL. `test_plan_steps_appear_in_the_table_without_problems` may already pass; keep it as a guard.

- [ ] **Step 3: Implement**

In `scorbot/lab/review.py`, `review_session_rows`, after the `led_gate_failed` branch add:
```python
        elif row.get("type") == "counts_drift":
            problems.append(f"counts drift before a step: {row.get('differences')}")
```

In `scripts/watch_lab_log.py`, add `"counts_drift"` to the `ALARM_EVENTS` set.

In `docs/LAB_SESSION.md`, add to the "Jog keys" table after the `a` / `d` row:
```markdown
| `b` | back to start: every joint returns to where it was after homing |
| `m` | mark the current pose as P1..P9 (this session only) |
| `g` | go to a mark: choose 1-9 |
```
and after the paragraph that starts "One press is one step", add:
```markdown
`b` and `g` show the whole move first and need one typed confirmation
(`BACK`, `GOTO P2`), and the session must be armed. Joints move one at a time:
elbow, then shoulder, then base. **Any key during the move stops it after the
current step** and disarms; that is a software pause, the physical stop is the
stop. At the end you say whether the arm is there, and the count difference
from the target is logged. Back to start is not a re-home.

Before every step the counts are compared with the last step. If any motor
moved more than 20 counts with nothing commanded (noise, a push, sagging), the
step is refused and the session disarms: finish and home again in a new
session. Marks are never kept between sessions.
```

In `docs/SAFETY_CASE.md`, row `HZ-22`, replace this exact text:
```text
| S: `Scorbot.disable`, faults and `disconnect` clear `_homed` and `_home_counts`. P: re-home each session | None for the noise case. Unverified on hardware |
```
with:
```text
| S: `Scorbot.disable`, faults and `disconnect` clear `_homed` and `_home_counts`; the guided session refuses any step when counts moved more than 20 (the legacy settle band) since the last step (`scorbot/lab/session.py:LabSession._drift_ok`). P: re-home each session; marks never outlive a session | `tests/test_lab_session.py::LabSessionTests::test_counts_drift_beyond_the_settle_band_refuses_the_next_jog`. A shift that moves home and counts together is not detected. Unverified on hardware |
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_lab_log_review.py tests/test_watch_lab_log.py`
Expected: all pass.

- [ ] **Step 5: Hand back (orchestrator commits)**

Commit message:
```
Report counts drift and plan steps in reviews, live view and docs

A drift refusal means the logged travel no longer describes the pose, so
the review counts it as a problem and the live view raises it as an alarm.
The lab session doc explains back to start, marks and the drift check.
```

---

### Final: orchestrator integration

- [ ] Run `.venv/Scripts/ruff.exe check .` — expected `All checks passed!`.
- [ ] Run `.venv/Scripts/python.exe -m pytest -n auto -q -p no:cacheprovider` — expected all pass (2 xfailed are the documented legacy bugs).
- [ ] Simulated rehearsal smoke run (no USB): pipe answers into `python -m scorbot.lab --simulate --profile <tmp>/lab.json --logs <tmp>` covering a jog, `m`, a jog, `g` `1` `GOTO P1` `y`, `b` `BACK` `y`, `x`; check the log has `position_marked`, two `plan_complete` rows and `LOG CHECK: 0 problems`.
- [ ] Whole-branch review (one fresh reviewer) against the spec and this plan.
- [ ] Update `docs/superpowers/specs/2026-09-30-lab-positions-and-recovery-design.md` status to "built" and `docs/LAB_PLATFORM_VISION.md` action item 13 to done.
