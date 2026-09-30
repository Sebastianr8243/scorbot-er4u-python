# Guided Lab Session Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One command, `python -m scorbot.lab [--simulate]`, that guides the operator through profile, checklist, connect, LED checks, idle, home and an armed multi-jog loop. It reuses the SDK gates, and a review prints a readable per-jog table.

**Architecture:** `scorbot/lab/` holds a UI-free engine (`LabSession`). It talks to the person only through an `Operator` protocol. A standard-library terminal front end implements that protocol, and so does a scripted test double. The engine writes JSONL rows first and MCAP second, through the existing `SessionWriter`/`BestEffortRecorder`. A pure `scorbot/lab/review.py` is shared by the session's finish step and `scripts/review_lab_logs.py --session`.

**Tech Stack:** Python >= 3.10 standard library, existing `scorbot` SDK and `scorbot.session`, `unittest`.

**Spec:** [docs/superpowers/specs/2026-09-29-guided-lab-session-design.md](../specs/2026-09-29-guided-lab-session-design.md)

## Global Constraints

- No new motion capability. The only motion calls are `Scorbot.enable`, `Scorbot.home(start_position_confirmed=True)`, `Scorbot.jog_joint` for `base`, `shoulder` and `elbow`, and `Scorbot.disable`.
- Per-jog step choices are exactly `(1.0, 0.5)` degrees; `MAX_STEP_DEG = 1.0`.
- Net travel cap per joint per session: `TRAVEL_CAP_DEG = 10.0` legacy software degrees from home. Refuse before anything is queued.
- Idle disarm: `IDLE_DISARM_S = 60.0`.
- The expected LED state is never shown before the answer. `after_connect` (motors off, power green) and `after_enable` (motors lit, power green) are required. `after_disable` (motors off) only warns.
- A typed confirmation must equal the expected text (trimmed, case-insensitive), or the step declines. A decline never moves.
- `scorbot/lab/*` never imports `examples`, `scripts` or `usb` at import time, and never calls `print` or `input` except in `terminal.py`.
- JSONL rows are written before the matching recorder call. Outputs open with mode `"x"`.
- Exit codes: 0 completed, 1 failed (including after a fault), 3 declined.
- Real and simulated differ only in the robot factory (`Scorbot` vs `SimulatedScorbot`) and `data_source`. `--simulate` defaults logs to `rehearsal/`, real runs to `logs/`.
- Never call `disable()` an emergency stop; screens name the physical stop.
- Commit messages: imperative sentence-case subject, a body explaining why, **no AI attribution lines**.
- Commands use `.venv/Scripts/python.exe`.

## Review Focus

1. **End of input in the middle of the jog loop** (the operator closes stdin, or the scripted answers run out). Expected: it is treated as finish, so the arm is disabled and the LED is asked. It never hangs or raises. Test in Task 3 (`test_end_of_input_in_loop_finishes_safely`).
2. **A fault during a jog leaves the SDK latched, so `disable()` raises.** Expected: a `disable_failed` row, an alarm naming the physical stop, the summary still written, and exit 1. Test in Task 3.
3. **A second arming after a disarm must require the typed move again.** Expected: the repeat shortcut is cleared on every arm. Test in Task 3 (`test_rearming_requires_typed_move_again`).
4. **A step change followed by the same key** is a different move, so it needs a typed confirmation (for example `BASE -0.5`). Test in Task 3.
5. **The log file name already exists** (two sessions on one day, or a leftover controller log). Expected: the next free number is used and nothing is overwritten. Test in Task 4.

---

### Task 1: Profile and operator protocol

**Files:**
- Create: `scorbot/lab/__init__.py`, `scorbot/lab/profile.py`, `scorbot/lab/operator.py`
- Modify: `pyproject.toml` (`[tool.setuptools] packages` adds `"scorbot.lab"`)
- Test: `tests/test_lab_profile.py`

**Interfaces:**
- Produces:
  - `LabProfile(robot_id: str, arm_label: str, controller_label: str, driver: str, operator: str, speed: int = 10)`: frozen; the constructor raises `ProfileError(ValueError)`; it has `.summary() -> str`.
  - `load_profile(path) -> LabProfile | None`, `save_profile(profile, path) -> None`, `ensure_profile(path, operator) -> LabProfile`.
  - `FIELDS`, `LABELS`, `EXAMPLE_VALUES`.
  - `StatusLine(source: str, homed: bool, armed: bool, joint: str, step_deg: float, speed: int, fault: str | None)` with `.render() -> str`.
  - The `Operator` protocol: `confirm(prompt, expected) -> bool`, `choose(prompt, options: dict[str, str]) -> str`, `key(prompt) -> str`, `text(prompt) -> str`, `show(message, level="info") -> None`, `status(line: StatusLine) -> None`.
  - `ScriptedOperator(answers)`, with lists `.prompts`, `.shown` and `.statuses`. An answer may be a zero-argument callable returning the string.
  - Constant `ENTER = "enter"`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_lab_profile.py`:

```python
"""Lab profile and the scripted operator; no hardware."""

from pathlib import Path
import tempfile
import unittest

from scorbot.lab.operator import ENTER, ScriptedOperator, StatusLine
from scorbot.lab.profile import (LabProfile, ProfileError, ensure_profile, load_profile,
                                 save_profile)

GOOD = dict(robot_id="lab-er4u-1", arm_label="A-12", controller_label="C-3",
            driver="WinUSB", operator="SR")


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "lab.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_validation(self):
        LabProfile(**GOOD)
        for field, bad in (("robot_id", " "), ("arm_label", "Arm nameplate"),
                           ("operator", "your initials")):
            with self.subTest(field=field), self.assertRaises(ProfileError):
                LabProfile(**{**GOOD, field: bad})
        for speed in (0, 21, True, 5.0):
            with self.subTest(speed=speed), self.assertRaises(ProfileError):
                LabProfile(**GOOD, speed=speed)

    def test_save_and_load_round_trip_without_temp_leftovers(self):
        save_profile(LabProfile(**GOOD, speed=7), self.path)
        self.assertEqual(load_profile(self.path), LabProfile(**GOOD, speed=7))
        self.assertEqual(sorted(p.name for p in self.path.parent.iterdir()), ["lab.json"])
        self.assertIsNone(load_profile(self.path.parent / "missing.json"))
        self.path.write_text('{"robot_id": "x"}', encoding="utf-8")
        with self.assertRaises(ProfileError):
            load_profile(self.path)

    def test_first_run_asks_every_field_then_saves(self):
        op = ScriptedOperator(["lab-er4u-1", "A-12", "C-3", "WinUSB", "SR", "", "y"])
        profile = ensure_profile(self.path, op)
        self.assertEqual(profile, LabProfile(**GOOD))          # blank speed keeps 10
        self.assertEqual(load_profile(self.path), profile)

    def test_existing_profile_kept_with_enter(self):
        save_profile(LabProfile(**GOOD), self.path)
        op = ScriptedOperator([""])
        self.assertEqual(ensure_profile(self.path, op), LabProfile(**GOOD))
        self.assertIn("lab-er4u-1", " ".join(op.shown))

    def test_change_keeps_blank_fields_and_rejects_placeholders(self):
        save_profile(LabProfile(**GOOD), self.path)
        op = ScriptedOperator(["c", "", "", "", "", "your initials", "",   # rejected
                               "", "", "", "", "JD", "12", "y"])
        profile = ensure_profile(self.path, op)
        self.assertEqual((profile.operator, profile.speed), ("JD", 12))
        self.assertEqual(load_profile(self.path).operator, "JD")
        self.assertTrue(any("your initials" in m.lower() or "example" in m.lower()
                            for m in op.shown))

    def test_not_saved_when_operator_says_no(self):
        op = ScriptedOperator(["lab-er4u-1", "A-12", "C-3", "WinUSB", "SR", "", "n"])
        self.assertEqual(ensure_profile(self.path, op), LabProfile(**GOOD))
        self.assertFalse(self.path.exists())


class ScriptedOperatorTests(unittest.TestCase):
    def test_answers_and_fallbacks(self):
        op = ScriptedOperator(["home", "x", "x", "x", "", lambda: "q", "note"])
        self.assertTrue(op.confirm("Type HOME: ", "HOME"))
        self.assertEqual(op.choose("LED? ", {"y": "lit"}), "unsure")    # 3 invalid
        self.assertEqual(op.choose("Keep? ", {ENTER: "keep"}), "keep")  # blank = Enter
        self.assertEqual(op.key("key: "), "q")
        self.assertEqual(op.text("note: "), "note")
        self.assertEqual(op.key("key: "), "")           # end of input
        self.assertEqual(op.choose("LED? ", {"y": "lit"}), "unsure")
        self.assertFalse(op.confirm("Type ARM: ", "ARM"))
        self.assertEqual(op.text("x"), "")

    def test_status_line_render(self):
        line = StatusLine("SIMULATED", True, False, "base", 0.5, 10, None).render()
        self.assertEqual(line, "SIMULATED | HOMED | DISARMED | base | step 0.5 deg | "
                               "speed 10 | fault: none")
        self.assertIn("HOME NEEDED", StatusLine("REAL", False, False, "base", 1.0, 10,
                                                "x").render())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m unittest tests.test_lab_profile -v`
Expected: `ModuleNotFoundError: No module named 'scorbot.lab'`.

- [ ] **Step 3: Implement.** `scorbot/lab/__init__.py`:

```python
"""Guided lab session: a UI-free engine plus a standard-library terminal front end.

Nothing here opens USB at import time; motion only happens through Scorbot's gates.
"""
```

`scorbot/lab/operator.py`:

```python
"""How the lab session talks to a person, and a scripted stand-in for tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

ENTER = "enter"
CHOICE_ATTEMPTS = 3


@dataclass(frozen=True)
class StatusLine:
    source: str
    homed: bool
    armed: bool
    joint: str
    step_deg: float
    speed: int
    fault: str | None

    def render(self) -> str:
        return (f"{self.source} | {'HOMED' if self.homed else 'HOME NEEDED'} | "
                f"{'ARMED' if self.armed else 'DISARMED'} | {self.joint} | "
                f"step {self.step_deg:g} deg | speed {self.speed} | "
                f"fault: {self.fault or 'none'}")


class Operator(Protocol):
    def confirm(self, prompt: str, expected: str) -> bool: ...
    def choose(self, prompt: str, options: dict[str, str]) -> str: ...
    def key(self, prompt: str) -> str: ...
    def text(self, prompt: str) -> str: ...
    def show(self, message: str, level: str = "info") -> None: ...
    def status(self, line: StatusLine) -> None: ...


def matches(typed: str, expected: str) -> bool:
    return typed.strip().upper() == expected.strip().upper()


class ScriptedOperator:
    """Answers from a list; an answer may be a callable returning the string."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.shown: list[str] = []
        self.statuses: list[StatusLine] = []

    def _next(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.answers:
            raise EOFError("scripted answers exhausted")
        answer = self.answers.pop(0)
        return answer() if callable(answer) else answer

    def confirm(self, prompt, expected):
        try:
            return matches(self._next(prompt), expected)
        except EOFError:
            return False

    def choose(self, prompt, options):
        for _ in range(CHOICE_ATTEMPTS):
            try:
                key = self._next(prompt).strip().lower() or ENTER
            except EOFError:
                return "unsure"
            if key in options:
                return options[key]
        return "unsure"

    def key(self, prompt):
        try:
            return self._next(prompt).strip().lower()[:1] or ENTER
        except EOFError:
            return ""

    def text(self, prompt):
        try:
            return self._next(prompt).strip()
        except EOFError:
            return ""

    def show(self, message, level="info"):
        self.shown.append(message)

    def status(self, line):
        self.statuses.append(line)
```

Note: `key()` returns `ENTER` for a blank answer, `""` at end of input, and otherwise the first character lowercased.

`scorbot/lab/profile.py`:

```python
"""Saved lab identity for the guided session. A settings file, not evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path

from .operator import ENTER

FIELDS = ("robot_id", "arm_label", "controller_label", "driver", "operator")
LABELS = {"robot_id": "Robot id", "arm_label": "Arm label (nameplate)",
          "controller_label": "Controller label (nameplate)",
          "driver": "USB driver (Device Manager)", "operator": "Operator initials"}
# The placeholder labels examples/bench_joint.py also rejects.
EXAMPLE_VALUES = frozenset({
    "arm nameplate", "controller nameplate", "current windows driver",
    "your initials", "photo/sketch of known start pose",
    "same known pose as idle capture",
})
PROFILE_ATTEMPTS = 3


class ProfileError(ValueError):
    """A lab profile value is missing or invalid."""


@dataclass(frozen=True)
class LabProfile:
    robot_id: str
    arm_label: str
    controller_label: str
    driver: str
    operator: str
    speed: int = 10

    def __post_init__(self):
        for name in FIELDS:
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ProfileError(f"{LABELS[name]} must not be empty")
            if value.strip().lower() in EXAMPLE_VALUES:
                raise ProfileError(f"{LABELS[name]}: replace the example value {value!r}")
        if type(self.speed) is not int or not 1 <= self.speed <= 20:
            raise ProfileError("Speed must be an integer from 1 to 20")

    def summary(self) -> str:
        return ", ".join(f"{LABELS[name].split(' (')[0]}: {getattr(self, name)}"
                         for name in FIELDS) + f", speed: {self.speed}"


def load_profile(path) -> LabProfile | None:
    path = Path(path)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return LabProfile(**{name: data[name] for name in FIELDS},
                          speed=data.get("speed", 10))
    except (ValueError, KeyError, TypeError) as error:
        raise ProfileError(f"{path}: unreadable lab profile ({error})") from None


def save_profile(profile: LabProfile, path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(asdict(profile), indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _ask(operator, current: LabProfile | None) -> LabProfile:
    for _ in range(PROFILE_ATTEMPTS):
        values = {}
        for name in FIELDS:
            old = getattr(current, name) if current else ""
            hint = f" [{old}]" if old else ""
            values[name] = operator.text(f"{LABELS[name]}{hint}: ") or old
        old_speed = current.speed if current else 10
        speed_text = operator.text(f"Speed 1-20 [{old_speed}]: ")
        try:
            speed = int(speed_text) if speed_text else old_speed
            return LabProfile(**values, speed=speed)
        except (ProfileError, ValueError) as error:
            operator.show(f"Not accepted: {error}", "warn")
    raise ProfileError("No valid lab profile after three attempts")


def ensure_profile(path, operator) -> LabProfile:
    """Show the saved profile (Enter keeps it) or ask for one; save on confirmation."""
    current = load_profile(path)
    if current is not None:
        operator.show(f"Lab profile: {current.summary()}")
        if operator.choose("[Enter] keep, [c] change: ", {ENTER: "keep", "c": "change"}) != "change":
            return current
    profile = _ask(operator, current)
    operator.show(f"Lab profile: {profile.summary()}")
    if operator.choose(f"Save to {path}? [y/n] ", {"y": "yes", "n": "no"}) == "yes":
        save_profile(profile, path)
    return profile
```

In `pyproject.toml`, change `packages = ["scorbot", "scorbot.session", "openScorbot"]` to `packages = ["scorbot", "scorbot.session", "scorbot.lab", "openScorbot"]`.

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m unittest tests.test_lab_profile -v`, then `.venv/Scripts/python.exe -m ruff check .`
Expected: 8 OK; `All checks passed!`.

- [ ] **Step 5: Commit**

```bash
git add scorbot/lab/__init__.py scorbot/lab/operator.py scorbot/lab/profile.py pyproject.toml tests/test_lab_profile.py
git commit -m "Add the lab profile and operator protocol for the guided session" -m "Operators retyped five labels on every run. A saved profile keyed to the lab, shown at start with Enter to keep it, removes that; the same placeholder values the bench script rejects are refused. The operator protocol lets the session engine stay free of print and input, so tests, the terminal and later front ends can drive it."
```

---

### Task 2: Shared session review

**Files:**
- Create: `scorbot/lab/review.py`
- Modify: `scripts/review_lab_logs.py` (`main`), `scripts/watch_lab_log.py` (`ALARM_EVENTS`)
- Test: `tests/test_lab_log_review.py` (append a class)

**Interfaces:**
- Consumes: the row shapes defined in the spec (the `type` field plus the fields below).
- Produces: `review_session_rows(rows: list[dict]) -> dict` with keys `data_source`, `robot_id`, `jogs` (a list of dicts with `n`, `move`, `how`, `planned`, `measured`, `direction`, `other_joint_moved`), `problems` (a sorted list of strings) and `declined` (str or None). Also `format_session_review(report) -> str`, which ends with `LOG CHECK: {N} problems (not a safety verdict)`, and `LAB_LED_STEPS = ("after_connect", "after_enable")`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_lab_log_review.py` before `if __name__ == "__main__":`. If the file has no `import json`, `import tempfile` or `from pathlib import Path` at the top, add them there.

```python
class LabSessionReviewTests(unittest.TestCase):
    def rows(self, *, measured_base=-142, shoulder=0, extra=()):
        return [
            {"type": "session", "kind": "lab_session", "schema_version": 2,
             "robot_id": "arm-1", "data_source": "simulated"},
            {"type": "led_observation", "step": "after_connect", "motors_led": "off",
             "power_led": "green"},
            {"type": "led_observation", "step": "after_enable", "motors_led": "lit",
             "power_led": "green"},
            {"type": "jog_preview", "n": 1, "joint": "base",
             "plan": {"motor_count_deltas": {"base": -142}}},
            {"type": "jog_confirmed", "n": 1, "how": "typed", "move": "BASE -1"},
            {"type": "before_jog", "n": 1, "state": {}},
            {"type": "after_jog", "n": 1, "state": {}},
            {"type": "jog_observation", "n": 1, "direction": "toward",
             "other_joint_moved": "no"},
            {"type": "jog_result", "n": 1, "planned": {"base": -142},
             "measured": {"base": measured_base, "shoulder": shoulder, "elbow": 0}},
            *extra,
        ]

    def test_clean_session_has_no_problems_and_a_table(self):
        from scorbot.lab.review import format_session_review, review_session_rows
        report = review_session_rows(self.rows())
        self.assertEqual(report["problems"], [])
        self.assertEqual(report["jogs"][0]["move"], "BASE -1")
        text = format_session_review(report)
        self.assertIn("SIMULATED", text)
        self.assertIn("BASE -1", text)
        self.assertTrue(text.rstrip().endswith("LOG CHECK: 0 problems (not a safety verdict)"))

    def test_problems_are_counted(self):
        from scorbot.lab.review import review_session_rows
        report = review_session_rows(self.rows(measured_base=140, shoulder=35, extra=[
            {"type": "jog_preview", "n": 2, "joint": "base", "plan": {}},
            {"type": "jog_confirmed", "n": 2, "how": "repeat", "move": "BASE -1"},
            {"type": "led_mismatch", "step": "after_enable", "led": "motors",
             "observed": "off", "expected": "lit"},
            {"type": "session_failed", "error": "boom"}]))
        text = " | ".join(report["problems"])
        self.assertIn("jog 1: base moved opposite to the plan", text)
        self.assertIn("jog 1: shoulder moved 35 counts but was not jogged", text)
        self.assertIn("jog 2: started but has no after_jog", text)
        self.assertIn("LED mismatch after_enable", text)
        self.assertIn("session failed: boom", text)

    def test_missing_required_led_and_session_row(self):
        from scorbot.lab.review import review_session_rows
        report = review_session_rows([r for r in self.rows()
                                      if r.get("step") != "after_enable"
                                      and r["type"] != "session"])
        self.assertIn("missing session row", report["problems"])
        self.assertIn("LED observation missing after_enable", report["problems"])

    def test_cli_session_mode_and_json(self):
        import contextlib
        import io
        import sys
        from unittest.mock import patch
        from scripts import review_lab_logs
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "s.jsonl"
            path.write_text("".join(json.dumps(r) + "\n" for r in self.rows()),
                            encoding="utf-8")
            for extra, check in (([], "LOG CHECK: 0 problems"), (["--json"], '"jogs"')):
                out = io.StringIO()
                with patch.object(sys, "argv", ["review", "--session", str(path), *extra]), \
                        contextlib.redirect_stdout(out):
                    self.assertEqual(review_lab_logs.main(), 0)
                self.assertIn(check, out.getvalue())
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m unittest tests.test_lab_log_review -v`
Expected: the new tests ERROR with `No module named 'scorbot.lab.review'`. The CLI test errors because argparse requires `--idle`.

- [ ] **Step 3: Implement** `scorbot/lab/review.py`:

```python
"""Review a guided lab-session JSONL log. Pure: no printing, no hardware.

A clean review means the log is complete and self-consistent. It never says
the motion was safe or accurate; that is the operator's physical review.
"""

from __future__ import annotations

from ..state import JOINTS

LAB_LED_STEPS = ("after_connect", "after_enable")
UNJOGGED_TOLERANCE = 20   # counts a non-jogged motor may drift before it is flagged


def review_session_rows(rows: list[dict]) -> dict:
    session = next((r for r in rows if r.get("type") == "session"), None)
    problems = [] if session else ["missing session row"]
    by_n: dict[int, dict[str, dict]] = {}
    for row in rows:
        if isinstance(row.get("n"), int):
            by_n.setdefault(row["n"], {})[row.get("type")] = row
        if row.get("type") == "session_failed":
            problems.append(f"session failed: {row.get('error')}")
        elif row.get("type") == "led_mismatch":
            problems.append(f"LED mismatch {row.get('step')}: {row.get('led')} reported "
                            f"{row.get('observed')}, software expected {row.get('expected')}")
        elif row.get("type") == "led_gate_failed":
            problems.append(f"LED gate failed: {row.get('reason')}")
    seen_leds = {r.get("step") for r in rows if r.get("type") == "led_observation"}
    problems += [f"LED observation missing {step}" for step in LAB_LED_STEPS
                 if step not in seen_leds]
    jogs = []
    for n in sorted(by_n):
        rows_n = by_n[n]
        confirmed = rows_n.get("jog_confirmed")
        if confirmed is None:
            continue
        if "after_jog" not in rows_n:
            reason = rows_n.get("jog_failed", {}).get("error")
            problems.append(f"jog {n}: failed: {reason}" if reason
                            else f"jog {n}: started but has no after_jog")
        result = rows_n.get("jog_result", {})
        planned = result.get("planned") or {}
        measured = result.get("measured") or {}
        for motor in JOINTS:
            value = measured.get(motor)
            if motor not in measured:
                continue
            if value is None:
                problems.append(f"jog {n}: {motor} count change is ambiguous")
            elif motor in planned:
                if planned[motor] * value < 0:
                    problems.append(f"jog {n}: {motor} moved opposite to the plan")
            elif abs(value) > UNJOGGED_TOLERANCE:
                problems.append(f"jog {n}: {motor} moved {value} counts but was not jogged")
        observation = rows_n.get("jog_observation", {})
        jogs.append({"n": n, "move": confirmed.get("move"), "how": confirmed.get("how"),
                     "planned": planned, "measured": measured,
                     "direction": observation.get("direction"),
                     "other_joint_moved": observation.get("other_joint_moved")})
    declined = next((r.get("text") for r in rows if r.get("type") == "operator_declined"), None)
    return {"data_source": (session or {}).get("data_source", "unknown"),
            "robot_id": (session or {}).get("robot_id"),
            "jogs": jogs, "problems": sorted(set(problems)), "declined": declined}


def format_session_review(report: dict) -> str:
    source = str(report["data_source"]).upper()
    lines = [f"{source} lab session, robot {report['robot_id']}"]
    if source != "REAL":
        lines.append(f"{source} DATA: rehearsal logs, not evidence from the physical arm.")
    lines.append(f"{'#':>3}  {'move':<12} {'how':<7} {'planned':>9} {'measured':>9} "
                 f"{'observed':<9} other")
    for jog in report["jogs"]:
        motor = next(iter(jog["planned"]), None)
        planned = jog["planned"].get(motor, "-") if motor else "-"
        measured = jog["measured"].get(motor, "-") if motor else "-"
        lines.append(f"{jog['n']:>3}  {jog['move'] or '-':<12} {jog['how'] or '-':<7} "
                     f"{planned!s:>9} {measured!s:>9} {jog['direction'] or '-':<9} "
                     f"{jog['other_joint_moved'] or '-'}")
    if report["declined"]:
        lines.append(f"Declined: {report['declined']}")
    lines += [f"  - {problem}" for problem in report["problems"]]
    lines.append(f"LOG CHECK: {len(report['problems'])} problems (not a safety verdict)")
    return "\n".join(lines)
```

In `scripts/review_lab_logs.py`, replace the start of `main` up to and including `reports = ...` with:

```python
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--idle", type=Path)
    parser.add_argument("--bench", type=Path, action="append", default=[])
    parser.add_argument("--session", type=Path,
                        help="Guided lab-session log (python -m scorbot.lab)")
    parser.add_argument("--json", action="store_true", help="With --session: print JSON")
    args = parser.parse_args()
    if args.session is not None:
        from scorbot.lab.review import format_session_review, review_session_rows
        report = review_session_rows(read_rows(args.session))
        print(json.dumps(report, indent=2) if args.json else format_session_review(report))
        return 1 if report["problems"] else 0
    if args.idle is None:
        parser.error("--idle is required unless --session is given")
    reports = [review_idle(args.idle)] + [review_bench(path) for path in args.bench]
```

In `scripts/watch_lab_log.py`, add `"led_gate_failed", "jog_refused", "jog_failed", "disable_failed"` to `ALARM_EVENTS`.

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m unittest tests.test_lab_log_review tests.test_watch_lab_log -v`, then `.venv/Scripts/python.exe -m ruff check .`
Expected: all OK.

- [ ] **Step 5: Commit**

```bash
git add scorbot/lab/review.py scripts/review_lab_logs.py scripts/watch_lab_log.py tests/test_lab_log_review.py
git commit -m "Review guided lab sessions as a readable per-jog table" -m "The bench review printed a JSON dump that assumed one jog per run. Session logs now get a table of every jog (planned vs measured counts, the operator's observation) and one LOG CHECK line, with --json for machines. The logic lives in scorbot.lab.review so the session's finish step and the script report the same thing."
```

---

### Task 3: Session engine

**Files:**
- Create: `scorbot/lab/session.py`
- Test: `tests/test_lab_session.py`

**Interfaces:**
- Consumes (Task 1): `LabProfile`, `ScriptedOperator`, `StatusLine`, `ENTER`, `matches`. Consumes (Task 2): `review_session_rows`, `format_session_review`.
- Consumes (SDK): `SimulatedScorbot(controller=..., log_path=..., robot_id=...)`, `SimulatedController().inject(kind)` and `.commands`. Robot methods `get_state()`, `enable()`, `home(start_position_confirmed=True)`, `preview_jog(joint, delta, speed=, starting_signed_counts=)` (returns a dict with `motor_count_deltas` and `increments`), `jog_joint(joint, delta, speed=)` (returns `RobotState`), `disable()`. Also `SessionWriter.create(...)`, `BestEffortRecorder(writer, warn=)` and `signed_count_delta`.
- Produces: `LabSession(*, profile, operator, robot_factory, data_source, log_path, session_root, preflight=None, clock=time.monotonic, sleep=time.sleep, software_commit="unknown")` with `.run() -> int`. Constants `EXIT_OK=0`, `EXIT_FAILED=1`, `EXIT_DECLINED=3`, `STEPS=(1.0, 0.5)`, `TRAVEL_CAP_DEG=10.0`, `IDLE_DISARM_S=60.0` and `IDLE_SAMPLES=5`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_lab_session.py`:

```python
"""Guided session engine with the simulated controller and scripted answers."""

import json
from pathlib import Path
import tempfile
import unittest

from scorbot import SimulatedScorbot
from scorbot.lab.operator import ScriptedOperator
from scorbot.lab.profile import LabProfile
from scorbot.lab.session import EXIT_DECLINED, EXIT_FAILED, EXIT_OK, LabSession
from scorbot.simulated import SimulatedController

PROFILE = LabProfile("lab-er4u-1", "A-12", "C-3", "WinUSB", "SR")
CHECKLIST = ["y", "y", "y", "y"]
TO_LOOP = CHECKLIST + ["n", "g", "pose matches photo", "HOME", "y", "g", "all axes homed", "y"]
OBS = ["t", "n", ""]                    # toward, no other joint, no note
ARM = ["a", "door", "ARM"]            # first arming also names the landmark
REARM = ["a", "ARM"]
FINISH = ["x", "n", "g"]
JOG_ORDERS = set(range(4, 14))


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class LabSessionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.ctrl = SimulatedController()
        self.clock = Clock()

    def tearDown(self):
        self._tmp.cleanup()

    def run_session(self, answers):
        self.op = ScriptedOperator(answers)
        session = LabSession(
            profile=PROFILE, operator=self.op,
            robot_factory=lambda **kw: SimulatedScorbot(controller=self.ctrl, **kw),
            data_source="simulated", log_path=self.root / "s.jsonl",
            session_root=self.root / "sessions", clock=self.clock, sleep=lambda s: None)
        code = session.run()
        self.rows = [json.loads(line) for line in
                     (self.root / "s.jsonl").read_text(encoding="utf-8").splitlines()]
        return code

    def types(self):
        return [row["type"] for row in self.rows]

    def of(self, kind):
        return [row for row in self.rows if row["type"] == kind]

    def jog_commands(self):
        return [c for c in self.ctrl.commands if c and c[0] in JOG_ORDERS]

    def test_full_session_typed_repeat_and_new_move(self):
        code = self.run_session(TO_LOOP + ARM + ["q", "BASE -1"] + OBS + ["q"] + OBS
                                + ["e", "ELBOW -1"] + OBS + FINISH)
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(self.types()[:4], ["session", "profile", "recorder", "checklist"])
        self.assertEqual(len(self.of("idle_sample")), 5)
        self.assertEqual([r["how"] for r in self.of("jog_confirmed")], ["typed", "repeat", "typed"])
        self.assertEqual([r["move"] for r in self.of("jog_confirmed")],
                         ["BASE -1", "BASE -1", "ELBOW -1"])
        self.assertEqual(len(self.jog_commands()), 3)
        self.assertEqual(self.of("armed")[0]["landmark"], "door")
        self.assertEqual(self.of("jog_observation")[0]["direction"], "toward")
        self.assertEqual([r["step"] for r in self.of("led_observation")],
                         ["after_connect", "after_enable", "after_disable"])
        self.assertIn("disabled", self.types())
        summary = self.of("summary")[0]
        self.assertEqual((summary["jogs"], summary["problems"]), (3, 0))
        self.assertTrue(all(row["data_source"] == "simulated" for row in self.of("session")))
        self.assertTrue(any("LOG CHECK: 0 problems" in m for m in self.op.shown))

    def test_wrong_typed_move_declines_and_queues_nothing(self):
        code = self.run_session(TO_LOOP + ARM + ["q", "BASE +1"] + FINISH)
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(self.jog_commands(), [])
        self.assertEqual(len(self.of("jog_declined")), 1)
        self.assertEqual(self.of("disarmed")[0]["reason"], "confirmation declined")

    def test_jog_keys_do_nothing_while_disarmed(self):
        self.run_session(TO_LOOP + ["q", "1"] + FINISH)
        self.assertEqual(self.jog_commands(), [])
        self.assertEqual(sum("DISARMED" in m for m in self.op.shown), 2)

    def test_unknown_key_and_wrist_keys_disarm(self):
        self.run_session(TO_LOOP + ARM + ["4"] + REARM + ["z", "q"] + FINISH)
        reasons = [r["reason"] for r in self.of("disarmed")]
        self.assertEqual(reasons, ["unknown key '4'", "unknown key 'z'"])
        self.assertEqual(self.jog_commands(), [])

    def test_idle_timeout_disarms_and_ignores_that_key(self):
        def late_q():
            self.clock.t += 61
            return "q"
        self.run_session(TO_LOOP + ARM + [late_q] + FINISH)
        self.assertEqual(self.of("disarmed")[0]["reason"], "idle for more than 60 s")
        self.assertEqual(self.jog_commands(), [])

    def test_rearming_requires_typed_move_again(self):
        self.run_session(TO_LOOP + ARM + ["q", "BASE -1"] + OBS + ["d"] + REARM
                         + ["q", "BASE -1"] + OBS + FINISH)
        self.assertEqual([r["how"] for r in self.of("jog_confirmed")], ["typed", "typed"])

    def test_step_change_needs_new_typed_move(self):
        self.run_session(TO_LOOP + ARM + ["q", "BASE -1"] + OBS + ["s", "q", "BASE -0.5"]
                         + OBS + FINISH)
        self.assertEqual([r["move"] for r in self.of("jog_confirmed")], ["BASE -1", "BASE -0.5"])
        self.assertEqual([r["how"] for r in self.of("jog_confirmed")], ["typed", "typed"])

    def test_travel_cap_refuses_the_eleventh_degree(self):
        answers = TO_LOOP + ARM + ["q", "BASE -1"] + OBS
        for _ in range(9):
            answers += ["q"] + OBS
        answers += ["q"] + FINISH
        self.run_session(answers)
        self.assertEqual(len(self.jog_commands()), 10)
        refused = self.of("jog_refused")
        self.assertEqual(len(refused), 1)
        self.assertIn("10", refused[0]["reason"])
        self.assertEqual(self.of("disarmed")[-1]["reason"], "travel cap")

    def test_unsure_led_after_connect_fails_before_enable(self):
        code = self.run_session(CHECKLIST + ["u", "g"])
        self.assertEqual(code, EXIT_FAILED)
        self.assertIn("led_gate_failed", self.types())
        self.assertFalse([c for c in self.ctrl.commands if c and c[0] == 17])

    def test_fault_during_jog_latches_and_still_finishes(self):
        def inject_then_type():
            self.ctrl.inject("controller_error")
            return "BASE -1"
        code = self.run_session(TO_LOOP + ARM + ["q", inject_then_type, "n", "g"])
        self.assertEqual(code, EXIT_FAILED)
        self.assertEqual(len(self.of("jog_failed")), 1)
        self.assertTrue(self.of("disable_failed") or self.of("disabled"))
        self.assertEqual(len(self.of("summary")), 1)
        self.assertTrue(any("physical stop" in m for m in self.op.shown))

    def test_checklist_no_and_home_declined_exit_3(self):
        self.assertEqual(self.run_session(["y", "n"]), EXIT_DECLINED)
        self.assertEqual(self.ctrl.commands, [])
        self.setUp()
        code = self.run_session(CHECKLIST + ["n", "g", "pose", "STOP"])
        self.assertEqual(code, EXIT_DECLINED)
        self.assertFalse([c for c in self.ctrl.commands if c and c[0] == 18])

    def test_end_of_input_in_loop_finishes_safely(self):
        code = self.run_session(TO_LOOP + ARM + ["q", "BASE -1"] + OBS)
        self.assertEqual(code, EXIT_OK)
        self.assertIn("disabled", self.types())
        self.assertEqual(self.of("led_observation")[-1]["motors_led"], "unsure")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m unittest tests.test_lab_session -v`
Expected: `ModuleNotFoundError: No module named 'scorbot.lab.session'`.

- [ ] **Step 3: Implement** `scorbot/lab/session.py`:

```python
"""Guided lab session engine: steps, arming, gates and logging. No print or input.

Every motion goes through Scorbot's own gates (enable, home, jog_joint) and its
fault latch. The session only adds stricter limits: 1 degree steps, base,
shoulder and elbow only, a 10 degree net travel cap per joint from home, and an
armed state that anything unexpected clears. The physical stop is the stop.
"""

from __future__ import annotations

from dataclasses import asdict
import json
import time

from ..calibration import signed_count_delta
from ..provenance import motion_source_sha256
from ..session import BestEffortRecorder, SessionWriter
from ..state import JOINTS
from .operator import ENTER, StatusLine
from .review import format_session_review, review_session_rows

EXIT_OK, EXIT_FAILED, EXIT_DECLINED = 0, 1, 3
STEPS = (1.0, 0.5)
TRAVEL_CAP_DEG = 10.0
IDLE_DISARM_S = 60.0
IDLE_SAMPLES = 5
STABLE_COUNTS = 2
JOG_KEYS = {"1": ("base", 1), "q": ("base", -1), "2": ("shoulder", 1),
            "w": ("shoulder", -1), "3": ("elbow", 1), "e": ("elbow", -1)}
CHECKLIST = (
    "Base clamped, path clear, 70 cm around the arm",
    "Physical stop located and tested",
    "Teach pendant on Auto or unplugged; Intelitek software closed",
    "Arm in the known start pose (matches the photo)",
)
MOTORS_KEYS = {"y": "lit", "n": "off", "u": "unsure"}
POWER_KEYS = {"g": "green", "o": "orange", "f": "flashing", "u": "unsure"}
DIRECTION_KEYS = {"t": "toward", "a": "away", "n": "none", "u": "unsure"}
YES_NO_UNSURE = {"y": "yes", "n": "no", "u": "unsure"}
HELP = ("Keys: 1/q base +/-   2/w shoulder +/-   3/e elbow +/-   s step size   "
        "a arm   d disarm   ? help   x finish.  One press = one step. "
        "The physical stop is the stop.")
_MISMATCH_TEXT = {
    ("motors", "off", "lit"): "Software says motors are DISABLED but the MOTORS LED is LIT.",
    ("motors", "lit", "off"): "Software says motors are ENABLED but the MOTORS LED is OFF; "
                              "the controller may have cut motor power.",
    ("power", "green", "orange"): "POWER LED ORANGE: the controller is not communicating.",
    ("power", "green", "flashing"): "POWER LED FLASHING: USB timeout.",
}


class Declined(Exception):
    """The operator chose not to continue; nothing further moves."""


class SessionFailed(RuntimeError):
    """A required check failed; the session ends."""


class LabSession:
    def __init__(self, *, profile, operator, robot_factory, data_source, log_path,
                 session_root, preflight=None, clock=time.monotonic, sleep=time.sleep,
                 software_commit="unknown"):
        self.profile, self.op = profile, operator
        self.robot_factory, self.data_source = robot_factory, data_source
        self.log_path, self.session_root = log_path, session_root
        self.events_path = log_path.with_name(log_path.stem + ".controller.jsonl")
        self.preflight, self.clock, self.sleep = preflight, clock, sleep
        self.software_commit = software_commit
        self.rows: list[dict] = []
        self.robot = self.rec = self.fault = self.landmark = None
        self.homed = self.armed = False
        self.joint, self.step = "base", STEPS[0]
        self.confirmed: set = set()
        self.travel = {"base": 0.0, "shoulder": 0.0, "elbow": 0.0}
        self.jogs = 0

    # -- plumbing -------------------------------------------------------------

    def _write(self, kind, **fields):
        row = {"type": kind, "host_monotonic_ns": time.monotonic_ns(), **fields}
        self._stream.write(json.dumps(row, allow_nan=False) + "\n")
        self._stream.flush()
        self.rows.append(row)

    def _status(self):
        self.op.status(StatusLine(self.data_source.upper(), self.homed, self.armed,
                                  self.joint, self.step, self.profile.speed, self.fault))

    def _state(self, kind, **fields):
        state = self.robot.get_state()
        self._write(kind, state=asdict(state), **fields)
        self.rec.log_state(state)
        return state

    # -- run ------------------------------------------------------------------

    def run(self) -> int:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("x", encoding="utf-8") as self._stream:
            self._write("session", schema_version=2, kind="lab_session",
                        robot_id=self.profile.robot_id, data_source=self.data_source,
                        profile=asdict(self.profile), software_commit=self.software_commit,
                        motion_source_sha256=motion_source_sha256(),
                        controller_event_log=self.events_path.name, led_prompts=True)
            self._write("profile", **asdict(self.profile))
            writer = SessionWriter.create(
                self.session_root, data_source=self.data_source,
                robot_id=self.profile.robot_id, controller_id=self.profile.controller_label,
                operator=self.profile.operator, task=f"guided lab session ({self.log_path.name})",
                usb_driver=self.profile.driver)
            with writer:
                self.rec = BestEffortRecorder(writer, warn=lambda m: self.op.show(m, "warn"))
                self._write("recorder", mcap_session=self.rec.path.name)
                try:
                    return self._steps()
                except Declined as declined:
                    self._write("operator_declined", text=str(declined))
                    self.rec.log_decision("declined", reason=str(declined))
                    self.op.show(f"Stopped: {declined}. Nothing further will move.")
                    return EXIT_DECLINED
                except (Exception, KeyboardInterrupt) as error:
                    self._write("session_failed", error_type=type(error).__name__,
                                error=str(error))
                    self.op.show(f"Session failed: {error}. If motor state is uncertain, "
                                 "use the physical stop.", "alarm")
                    if isinstance(error, KeyboardInterrupt):
                        raise
                    return EXIT_FAILED

    def _steps(self) -> int:
        self._checklist()
        if self.preflight is not None:
            checks = self.preflight()
            for check in checks:
                self.op.show(f"{'PASS' if check.passed else 'FAIL'} {check.name}: {check.detail}")
            self._write("preflight", checks=[asdict(c) for c in checks])
            if not all(check.passed for check in checks):
                raise SessionFailed("preflight failed; no controller connection attempted")
        with self.robot_factory(log_path=self.events_path, robot_id=self.profile.robot_id) as robot:
            self.robot = robot
            self._state("connected")
            self._led("after_connect", motors="off", power="green", required=True)
            self._idle()
            self._home()
            self._jog_loop()
            self._finish()
        return EXIT_FAILED if self.fault else EXIT_OK

    # -- steps ----------------------------------------------------------------

    def _checklist(self):
        answers = {}
        for item in CHECKLIST:
            answers[item] = self.op.choose(f"{item}  [Enter/y done, n not done] ",
                                           {ENTER: "done", "y": "done", "n": "not done"})
            if answers[item] != "done":
                self._write("checklist", items=answers)
                raise Declined(f"checklist not done: {item}")
        self._write("checklist", items=answers)

    def _led(self, step, *, motors, power=None, required=False):
        self.op.show(f"LED check {step.replace('_', ' ')}: look at the controller front panel.")
        seen_motors = self.op.choose("  MOTORS LED lit? [y/n/u=unsure] ", MOTORS_KEYS)
        seen_power = self.op.choose("  POWER LED colour? [g/o/f/u=unsure] ", POWER_KEYS)
        self._write("led_observation", step=step, motors_led=seen_motors, power_led=seen_power,
                    expected_motors_led=motors, expected_power_led=power)
        self.rec.log_decision(f"motors_led={seen_motors} power_led={seen_power}",
                              reason=f"LED observation {step}")
        for led, expected, seen in (("motors", motors, seen_motors), ("power", power, seen_power)):
            if expected is not None and seen not in ("unsure", expected):
                message = _MISMATCH_TEXT.get((led, expected, seen),
                                             f"{led.upper()} LED {seen}, software expects {expected}.")
                self._write("led_mismatch", step=step, led=led, observed=seen,
                            expected=expected, message=message)
                self.op.show(f"{message} This does not cut motor power; use the physical "
                             "stop if in doubt.", "alarm")
        if required:
            failed = [f"{led}={seen} (expected {expected})" for led, expected, seen in
                      (("motors", motors, seen_motors), ("power", power, seen_power))
                      if expected is not None and seen != expected]
            if failed:
                reason = f"LED check {step} not confirmed: " + ", ".join(failed)
                self._write("led_gate_failed", step=step, reason=reason)
                raise SessionFailed(reason)

    def _idle(self):
        self.op.show(f"Idle check: {IDLE_SAMPLES} readings, 1 s apart. Do not touch the arm.")
        first = last = None
        for index in range(IDLE_SAMPLES):
            if index:
                self.sleep(1.0)
            last = self._state("idle_sample", index=index)
            first = first or last
        changed = {j: signed_count_delta(last.encoder_counts[j], first.encoder_counts[j])
                   for j in JOINTS}
        moving = {j: d for j, d in changed.items() if abs(d) > STABLE_COUNTS}
        if moving:
            self.op.show(f"Counts changed while idle: {moving}. Check before homing.", "warn")
        else:
            self.op.show("Counts stable while idle.")

    def _home(self):
        self.op.show("HOME NEEDED. Homing searches every axis switch from the known start pose.",
                     "warn")
        self._write("start_pose", text=self.op.text("Start pose: does it match the photo? "
                                                    "Describe: ") or "not recorded")
        if not self.op.confirm("Type HOME to enable motors and search home: ", "HOME"):
            raise Declined("declined before homing")
        self.robot.enable()
        self._write("enabled")
        self._led("after_enable", motors="lit", power="green", required=True)
        command = self.rec.log_command("home", {"start_position_confirmed": True})
        self.robot.home(start_position_confirmed=True)
        self.rec.log_command_result(command, "completed", completion_source="home() returned")
        self._state("home_complete")
        self.homed = True
        self._write("home_observation", text=self.op.text(
            "Describe what moved during homing and the final pose: ") or "not recorded")
        answer = self.op.choose("Did home look right, and is the travel path clear? [y/n] ",
                                {"y": "yes", "n": "no"})
        self._write("home_ok", answer=answer)
        if answer != "yes":
            raise Declined("stopped after homing; no jog requested")

    # -- jog loop -------------------------------------------------------------

    def _disarm(self, reason):
        if self.armed:
            self.armed = False
            self._write("disarmed", reason=reason)
            self.op.show(f"DISARMED: {reason}.", "warn")

    def _arm(self):
        if self.landmark is None:
            self.landmark = self.op.text("Name a fixed landmark for directions "
                                         "(e.g. the door): ") or "not recorded"
        self.op.show("Path clear, hand on the physical stop?")
        if self.op.confirm("Type ARM to arm jogging: ", "ARM"):
            self.armed = True
            self.confirmed.clear()
            self._write("armed", landmark=self.landmark)
        else:
            self.op.show("Not armed.")

    def _jog_loop(self):
        self.op.show(HELP)
        last = self.clock()
        while self.fault is None:
            self._status()
            key = self.op.key("key: ")
            now = self.clock()
            if key in ("", "x"):
                break
            if self.armed and now - last > IDLE_DISARM_S:
                self._disarm(f"idle for more than {IDLE_DISARM_S:g} s")
            elif key == "?":
                self.op.show(HELP)
            elif key == "s":
                self.step = STEPS[(STEPS.index(self.step) + 1) % len(STEPS)]
                self.op.show(f"Step size {self.step:g} degree.")
            elif key == "a":
                self._arm()
            elif key == "d":
                self._disarm("operator")
            elif key in JOG_KEYS:
                if self.armed:
                    self._jog(*JOG_KEYS[key])
                else:
                    self.op.show("DISARMED: press a to arm.")
            elif self.armed:
                self._disarm(f"unknown key {key!r}")
            else:
                self.op.show(f"Unknown key {key!r}; press ? for help.")
            last = self.clock()

    def _jog(self, joint, sign):
        self.joint = joint
        delta = sign * self.step
        if abs(self.travel[joint] + delta) > TRAVEL_CAP_DEG + 1e-9:
            reason = (f"{joint} would be {self.travel[joint] + delta:+g} degrees from home; "
                      f"the session cap is {TRAVEL_CAP_DEG:g}")
            self._write("jog_refused", joint=joint, delta_deg=delta, reason=reason)
            self.op.show(f"Refused: {reason}.", "alarm")
            self._disarm("travel cap")
            return
        n = self.jogs + 1
        before = self.robot.get_state()
        plan = self.robot.preview_jog(joint, delta, speed=self.profile.speed,
                                      starting_signed_counts=before.signed_encoder_counts)
        self._write("jog_preview", n=n, joint=joint, delta_deg=delta, plan=plan)
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
        self._write("jog_confirmed", n=n, how=how, move=move)
        self._write("before_jog", n=n, state=asdict(before))
        self.rec.log_state(before)
        command = self.rec.log_command("jog_joint", {"joint": joint, "delta_degrees": delta,
                                                     "speed": self.profile.speed})
        try:
            after = self.robot.jog_joint(joint, delta, speed=self.profile.speed)
        except Exception as error:
            self.fault = str(error)
            self._write("jog_failed", n=n, error=self.fault)
            self.rec.log_command_result(command, "faulted", detail=self.fault)
            self.rec.log_fault(self.fault)
            self.op.show(f"Jog failed: {error}. The session is latched; use the physical "
                         "stop if anything is still moving.", "alarm")
            self._disarm("jog failed")
            return
        self.jogs = n
        self.travel[joint] += delta
        self._write("after_jog", n=n, state=asdict(after))
        self.rec.log_command_result(command, "completed", completion_source="jog_joint() returned")
        self.rec.log_state(after)
        direction = self.op.choose(f"Which way did {joint} move relative to {self.landmark}? "
                                   "[t toward / a away / n none / u unsure] ", DIRECTION_KEYS)
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

    def _finish(self):
        self.armed = False
        try:
            self.robot.disable()
            self._state("disabled")
        except Exception as error:
            self._write("disable_failed", error=str(error))
            self.op.show(f"Software disable failed ({error}). The SDK queues a best-effort "
                         "disable after a fault, but only the physical stop is certain.", "alarm")
        self._led("after_disable", motors="off")
        report = review_session_rows(self.rows)
        self._write("summary", jogs=self.jogs, fault=self.fault, problems=len(report["problems"]))
        self.op.show(format_session_review(report))
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m unittest tests.test_lab_session -v`
Expected: 12 OK. If `test_fault_during_jog_latches_and_still_finishes` shows the simulated `controller_error` does not raise from `jog_joint`, check `scorbot/simulated.py:worker` for how that fault kind answers the next command, and pick the kind that makes `jog_joint` raise. Record the choice as a ruling.

Then run `.venv/Scripts/python.exe -m unittest tests.test_simulated -v` and `.venv/Scripts/python.exe -m ruff check .`, and expect OK. The import test still passes because `scorbot/lab` is not imported by `scorbot/__init__.py`.

- [ ] **Step 5: Commit**

```bash
git add scorbot/lab/session.py tests/test_lab_session.py
git commit -m "Add the guided lab session engine" -m "One session now connects, checks the LEDs, samples idle, homes and runs several bounded jogs, instead of one jog per run. Jogging needs an armed state; the first move of each kind is typed (for example BASE -1) and repeats take one key; unknown keys, 60 s idle, a declined confirmation or a failure disarm. A 10 degree net travel cap per joint bounds repeated presses. Every motion still goes through Scorbot's gates and fault latch, and the engine never prints or reads input."
```

---

### Task 4: Terminal front end and command

**Files:**
- Create: `scorbot/lab/terminal.py`, `scorbot/lab/__main__.py`
- Test: `tests/test_lab_terminal.py`

**Interfaces:**
- Consumes: `ensure_profile`, `LabSession`, `ENTER`, `matches`, `StatusLine`, `SimulatedScorbot`, `Scorbot`, `run_checks`.
- Produces: `TerminalOperator(getwch=None, isatty=None)`, `next_log_path(folder, robot_id, today=None) -> Path`, `termination_as_interrupt()` and `main(argv=None) -> int`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_lab_terminal.py`:

```python
"""Terminal front end and the lab command; no hardware, no real console."""

from datetime import date
import contextlib
import io
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scorbot.lab.__main__ import next_log_path
from scorbot.lab.operator import ENTER
from scorbot.lab.terminal import TerminalOperator, termination_as_interrupt


class TerminalOperatorTests(unittest.TestCase):
    def keys(self, *sequence):
        chars = iter(sequence)
        return TerminalOperator(getwch=lambda: next(chars), isatty=lambda: True)

    def test_single_keys_enter_and_special_keys(self):
        op = self.keys("Q", "\r", "\xe0", "H", "x")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(op.key("k: "), "q")
            self.assertEqual(op.key("k: "), ENTER)
            self.assertEqual(op.key("k: "), "<special>")   # arrow key: unknown, never a jog
            self.assertEqual(op.choose("? ", {"x": "done"}), "done")

    def test_ctrl_c_key_raises(self):
        op = self.keys("\x03")
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(KeyboardInterrupt):
            op.key("k: ")

    def test_non_tty_falls_back_to_input(self):
        op = TerminalOperator(getwch=None, isatty=lambda: False)
        with patch("builtins.input", side_effect=["y", "", "BASE -1", EOFError()]), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(op.key("k: "), "y")
            self.assertEqual(op.key("k: "), ENTER)
            self.assertTrue(op.confirm("Type: ", "base -1"))
            self.assertEqual(op.key("k: "), "")

    def test_alarm_lines_are_marked(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            TerminalOperator(getwch=None, isatty=lambda: False).show("stop", "alarm")
        self.assertIn("!!! stop", out.getvalue())

    def test_termination_signals_become_keyboard_interrupt(self):
        before = signal.getsignal(signal.SIGTERM)
        with termination_as_interrupt():
            with self.assertRaises(KeyboardInterrupt):
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        self.assertIs(signal.getsignal(signal.SIGTERM), before)


class LogPathTests(unittest.TestCase):
    def test_next_free_number_and_safe_name(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            day = date(2026, 9, 30)
            first = next_log_path(folder, "lab er4u/1", day)
            self.assertEqual(first.name, "20260930-lab-er4u-1-session-01.jsonl")
            first.write_text("", encoding="utf-8")
            (folder / "20260930-lab-er4u-1-session-02.controller.jsonl").write_text(
                "", encoding="utf-8")
            self.assertEqual(next_log_path(folder, "lab er4u/1", day).name,
                             "20260930-lab-er4u-1-session-03.jsonl")


class CommandSmokeTests(unittest.TestCase):
    def test_simulated_session_end_to_end_then_review(self):
        answers = ["", "y", "y", "y", "y", "n", "g", "pose ok", "HOME", "y", "g",
                   "homed", "y", "a", "door", "ARM", "q", "BASE -1", "t", "n", "",
                   "q", "t", "n", "", "x", "n", "g"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / "lab.json"
            profile.write_text(json.dumps({"robot_id": "lab-er4u-1", "arm_label": "A-12",
                                           "controller_label": "C-3", "driver": "none",
                                           "operator": "SR", "speed": 10}), encoding="utf-8")
            run = subprocess.run(
                [sys.executable, "-m", "scorbot.lab", "--simulate", "--profile", str(profile),
                 "--logs", str(root / "rehearsal")],
                input="\n".join(answers) + "\n", capture_output=True, text=True,
                encoding="utf-8", timeout=120)
            self.assertEqual(run.returncode, 0, run.stdout[-2000:] + run.stderr[-2000:])
            self.assertIn("SIMULATED", run.stdout)
            self.assertIn("LOG CHECK: 0 problems", run.stdout)
            logs = sorted((root / "rehearsal").glob("*-session-*.jsonl"))
            session_log = [p for p in logs if not p.name.endswith(".controller.jsonl")][0]
            review = subprocess.run(
                [sys.executable, "scripts/review_lab_logs.py", "--session", str(session_log)],
                capture_output=True, text=True, encoding="utf-8", timeout=60)
            self.assertEqual(review.returncode, 0, review.stdout + review.stderr)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m unittest tests.test_lab_terminal -v`
Expected: `ModuleNotFoundError` for `scorbot.lab.__main__`.

- [ ] **Step 3: Implement** `scorbot/lab/terminal.py`:

```python
"""Standard-library terminal front end for the guided lab session.

Single keys come from msvcrt.getwch() on a Windows console; anywhere else, or
when input is piped, each answer is one line from input(). There are no global
keyboard hooks: keys only count while this window has focus.
"""

from __future__ import annotations

import contextlib
import signal
import sys

from .operator import CHOICE_ATTEMPTS, ENTER, matches

SPECIAL = "<special>"


def _default_getwch():
    try:
        import msvcrt
    except ImportError:
        return None
    return msvcrt.getwch


class TerminalOperator:
    def __init__(self, getwch=None, isatty=None):
        self._getwch = getwch if getwch is not None else _default_getwch()
        self._isatty = isatty or (lambda: sys.stdin.isatty())

    def _single_keys(self):
        return self._getwch is not None and self._isatty()

    def key(self, prompt):
        if not self._single_keys():
            try:
                line = input(prompt).strip().lower()
            except EOFError:
                return ""
            return line[:1] or ENTER
        print(prompt, end="", flush=True)
        char = self._getwch()
        if char == "\x03":
            raise KeyboardInterrupt
        if char in ("\x00", "\xe0"):
            self._getwch()               # arrow and function keys send two characters
            print()
            return SPECIAL
        if char in ("\r", "\n"):
            print()
            return ENTER
        print(char)
        return char.lower()

    def choose(self, prompt, options):
        for _ in range(CHOICE_ATTEMPTS):
            answer = self.key(prompt)
            if answer == "":
                return "unsure"
            if answer in options:
                return options[answer]
            print(f"  Press one of: {', '.join(k for k in options if k != ENTER)}"
                  + (" or Enter" if ENTER in options else ""))
        return "unsure"

    def confirm(self, prompt, expected):
        try:
            return matches(input(prompt), expected)
        except EOFError:
            return False

    def text(self, prompt):
        try:
            return input(prompt).strip()
        except EOFError:
            return ""

    def show(self, message, level="info"):
        prefix = {"alarm": "!!! ", "warn": "! "}.get(level, "")
        for line in str(message).splitlines() or [""]:
            print(f"{prefix}{line}")

    def status(self, line):
        print(f"[ {line.render()} ]")


@contextlib.contextmanager
def termination_as_interrupt():
    """Treat SIGTERM and Windows SIGBREAK (console close, unverified) like Ctrl-C."""
    def interrupt(signum, _frame):
        raise KeyboardInterrupt(f"stopped by signal {signum}")

    previous = {}
    for name in ("SIGTERM", "SIGBREAK"):
        number = getattr(signal, name, None)
        if number is not None:
            try:
                previous[number] = signal.signal(number, interrupt)
            except ValueError:
                break
    try:
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
```

`scorbot/lab/__main__.py`:

```python
"""Guided lab session: python -m scorbot.lab [--simulate] [--profile lab.json] [--logs DIR]

Without --simulate this connects to the real controller, and connecting
energises the motors: an operator must be at the physical stop.
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
import re
import subprocess
import sys

from .profile import ProfileError, ensure_profile
from .session import EXIT_FAILED, LabSession
from .terminal import TerminalOperator, termination_as_interrupt

_REPO_ROOT = Path(__file__).resolve().parents[2]


def next_log_path(folder, robot_id, today=None) -> Path:
    """First unused <date>-<robot>-session-NN.jsonl (never overwrites)."""
    folder = Path(folder)
    stem = f"{(today or date.today()):%Y%m%d}-{re.sub(r'[^A-Za-z0-9_-]+', '-', robot_id)}"
    for number in range(1, 100):
        path = folder / f"{stem}-session-{number:02d}.jsonl"
        if not path.exists() and not path.with_name(path.stem + ".controller.jsonl").exists():
            return path
    raise FileExistsError(f"No free session number left in {folder} for {stem}")


def _software_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=_REPO_ROOT,
                                       text=True, timeout=3).strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scorbot.lab", description=__doc__)
    parser.add_argument("--simulate", action="store_true",
                        help="Rehearse with the simulated controller: no USB, no robot")
    parser.add_argument("--profile", type=Path, default=Path("lab.json"))
    parser.add_argument("--logs", type=Path, default=None,
                        help="Log folder (default: logs, or rehearsal with --simulate)")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    operator = TerminalOperator()
    with termination_as_interrupt():
        if args.simulate:
            from ..simulated import SimulatedScorbot as robot_class
            source, preflight = "simulated", None
            operator.show("SIMULATED rehearsal: no USB and no robot are used.")
        else:
            from ..preflight import run_checks as preflight
            from ..robot import Scorbot as robot_class
            source = "real"
            operator.show("REAL session: connecting energises the motors. Stand at the "
                          "physical stop.", "warn")
        try:
            profile = ensure_profile(args.profile, operator)
        except ProfileError as error:
            operator.show(str(error), "alarm")
            return EXIT_FAILED
        folder = args.logs or Path("rehearsal" if args.simulate else "logs")
        log_path = next_log_path(folder, profile.robot_id)
        operator.show(f"Logging to {log_path}")
        return LabSession(profile=profile, operator=operator, robot_factory=robot_class,
                          data_source=source, log_path=log_path,
                          session_root=folder / "sessions", preflight=preflight,
                          software_commit=_software_commit()).run()


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m unittest tests.test_lab_terminal -v`
Expected: 7 OK. The smoke test takes about 5 s because of the idle samples.

- [ ] **Step 5: Try it once by hand in simulation**

Run: `.venv/Scripts/python.exe -m scorbot.lab --simulate --profile rehearsal/lab.json --logs rehearsal`, in a real console if possible, and answer the prompts.
Expected: every screen reads sensibly, and it ends with `LOG CHECK: 0 problems`. Note anything confusing in the final report. The agent cannot use a real console; the smoke test covers piped input.

- [ ] **Step 6: Commit**

```bash
git add scorbot/lab/terminal.py scorbot/lab/__main__.py tests/test_lab_terminal.py
git commit -m "Add the python -m scorbot.lab command" -m "The guided session gets its terminal front end: single keys through msvcrt on a Windows console, one line per answer when input is piped, no global keyboard hooks, alarm lines marked with !!!. Logs get a dated, numbered name that never overwrites, rehearsals go to rehearsal/ by default, and SIGTERM or a closed console behaves like Ctrl-C."
```

---

### Task 5: Docs

**Files:**
- Create: `docs/LAB_SESSION.md`
- Modify: `docs/ARM_CONTROL_BENCH.md` (top), `START_HERE_WINDOWS.md` (where the bench commands are introduced), `docs/OPERATOR_UX.md` (backlog), `CLAUDE.md` (Commands), `scorbot/CLAUDE.md` (module table)

**Interfaces:** none (documentation only).

- [ ] **Step 1: Write `docs/LAB_SESSION.md`**

```markdown
# Guided lab session

One command runs a whole bench session: profile, checklist, connect, LED
checks, idle, home, and several small jogs. Rehearse first; the rehearsal
uses no USB and no robot.

    .\.venv\Scripts\python.exe -m scorbot.lab --simulate     # rehearsal, logs in rehearsal\
    .\.venv\Scripts\python.exe -m scorbot.lab                # real arm, logs in logs\

**Connecting energises the motors.** Someone stands at the physical stop the
whole time. Software disarm and disable are not emergency stops.

## Steps

| Step | What you do |
|---|---|
| Profile | First time: type the robot id, labels, driver, initials. Later: Enter keeps them |
| Checklist | Enter or `y` for each item; `n` ends the session, nothing moves |
| LED checks | Look at the controller: MOTORS `y` lit / `n` off / `u` unsure; POWER `g` / `o` / `f` / `u`. Answer what you see; the expected state is not shown first |
| Idle | Hands off for 5 s |
| Home | Describe the start pose, type `HOME`, answer the LEDs, watch the search, describe it, `y` if it looked right |
| Jog | Press `a`, name a landmark once, type `ARM`. Then keys below |
| Finish | `x`: motors off, LED check, summary and `LOG CHECK` line |

## Jog keys

| Key | Action |
|---|---|
| `1` / `q` | base + / - |
| `2` / `w` | shoulder + / - |
| `3` / `e` | elbow + / - |
| `s` | step size 1 or 0.5 degree |
| `a` / `d` | arm / disarm |
| `?` | help |
| `x` | finish |

One press is one step; nothing moves while a key is held. The first move of a
kind asks you to type it (for example `BASE -1`); pressing the same key again
repeats it. Unknown keys, 60 s without a key, a declined confirmation or any
error disarm. Each joint can move at most 10 degrees from home per session
(legacy scale, not measured). After each move you say which way it went and
whether anything else moved, before the numbers are shown.

## Logs and review

Logs go to `logs\<date>-<robot>-session-NN.jsonl` (never overwritten) with a
`.controller.jsonl` and an MCAP session in `logs\sessions\`. Review:

    .\.venv\Scripts\python.exe scripts\review_lab_logs.py --session logs\<file>.jsonl

`LOG CHECK: 0 problems` means the log is complete and consistent, not that
the motion was safe or accurate. The older `examples\bench_joint.py` and
`examples\record_raw_state.py` still work as the fallback procedure.
```

- [ ] **Step 2: Point the existing docs to it**

- At the top of `docs/ARM_CONTROL_BENCH.md`, after the title, add: `> The normal way to run a bench session is now the guided session: [LAB_SESSION.md](LAB_SESSION.md). This page documents the older per-script procedure, which still works as a fallback.`
- In `START_HERE_WINDOWS.md`, where it first introduces running the bench scripts, add one line: `For a guided session (recommended), see [docs/LAB_SESSION.md](docs/LAB_SESSION.md).`
- In `docs/OPERATOR_UX.md`, add this line under the Backlog heading: `Items 1-4 and 6 are addressed for the guided session (docs/LAB_SESSION.md): typed move confirmations, observation before numbers, structured observations, a readable review, and a saved profile instead of flags. The per-script procedure keeps the old prompts.`
- In `CLAUDE.md` Commands, after the rehearsal block's first command, add `python -m scorbot.lab --simulate --profile rehearsal\lab.json --logs rehearsal   # guided session rehearsal`.
- In `scorbot/CLAUDE.md`, in the Modules table, add the row: `| lab/ | Guided session. session.py LabSession engine (no print/input; Operator protocol), terminal.py front end, profile.py lab.json, review.py shared with scripts/review_lab_logs.py --session. Adds no motion capability: 1 degree steps, base/shoulder/elbow, 10 degree net travel cap |`.

- [ ] **Step 3: Full check**

Run: `.venv/Scripts/python.exe -m compileall -q scorbot openScorbot scripts examples tests`, then `.venv/Scripts/python.exe -m ruff check .`, then `.venv/Scripts/python.exe -m unittest discover -s tests`
Expected: no compile output, `All checks passed!`, and `OK (skipped=..., expected failures=2)`.

- [ ] **Step 4: Commit**

```bash
git add docs/LAB_SESSION.md docs/ARM_CONTROL_BENCH.md START_HERE_WINDOWS.md docs/OPERATOR_UX.md CLAUDE.md scorbot/CLAUDE.md
git commit -m "Document the guided lab session as the normal bench procedure" -m "Operators need one page that says what each step asks and what each key does. The older per-script pages stay, marked as the fallback, and the UX backlog records which items the guided session addresses."
```
