# Code health implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the test gaps around the safety core, split `scorbot/robot.py` into focused modules with no behaviour change, and fix the one backlog bug that needs no hardware evidence.

**Architecture:** Characterisation tests first (they run against the unchanged code and must pass at once, then each is mutation-checked). Then `Scorbot`'s methods move into three mixin modules by a script that copies each method's source text unchanged, and an AST comparison proves every moved body is identical. A public-surface snapshot test proves nothing visible changed.

**Tech Stack:** Python 3.10+, `unittest`/pytest, ruff, `ast`, git. No new dependency.

**Spec:** `docs/archive/specs/2026-10-12-code-health-design.md` (owner decisions at its end: item 20 is out of scope; no split beyond the three extractions).

## Global Constraints

- Never change what is sent to the arm: no edit to packet construction, sequence bytes or `WRITE`/`READ` sleeps in `openScorbot/`, and no edit to the logic of `_latch_fault`, `request_stop`, `disable` or the stop events (root `CLAUDE.md`).
- No test opens USB or moves the arm (`tests/CLAUDE.md`): use `SimulatedScorbot`, fakes or patched modules; temp directories for every file.
- The full suite (`python -m pytest -q tests tests_extra`) and `ruff check .` pass before and after every commit. Baseline on 2026-10-12: 1160 passed, 1 skipped, ruff clean.
- Branch `code-health`, small commits, no push, no AI attribution lines in commits (owner's rule). Commit style: imperative sentence-case subject, no prefix, body says why.
- A refactor commit contains no edit to fault, latch or stop logic and no test change except the surface snapshot.
- Do not touch `openScorbot/libdef.py` (`from numpy import *` stays).
- Files use CRLF in some places: edit with tools that preserve line endings (the helper scripts below do).
- Reviewers after each phase: `bash ~/.agents/scripts/review-both.sh standard --strict ...` for tests, `deep` for each extraction. Triage every finding and log it with `node ~/.agents/scripts/lib/review-log.mjs mark <run id> <accepted> <rejected>`.

## Review Focus

Inputs and conditions the spec implies that no task exercises directly; each line is pinned by a test in the task named in brackets.

1. A method moved to a mixin that still reads a module-level name left behind in `robot.py` (fails only when that method runs). [Task 6, the names report]
2. `mock.patch.object(robot, "_command")` style patches in existing tests must keep working after the split. [Task 6, full suite]
3. `scorbot.provenance` hashes the motion sources: a new module outside `_SOURCE_FILES` would let motion code change without the fingerprint moving. [Task 6]
4. `connect` after a fault, twice, or with no controller attached must leave a latched session and no half-open device. [Task 3]
5. A corrupt calibration file (wrong types, out-of-range counts) must raise `ValueError`, never load partially. [Task 5]

## File Structure

| File | Responsibility |
|---|---|
| `tests/test_robot_surface.py` + `tests/fixtures/scorbot_surface.json` | Pins the public names and signatures of `scorbot.robot` |
| `tests/test_robot_guards.py` | Refusals and argument checks in `Scorbot` that run before anything is sent |
| `tests/test_robot_connect.py` | The real `connect`/`disconnect` against a fake `usb` |
| `tests/test_preflight_more.py`, `tests/test_calibration_load.py` | Remaining branches of `preflight.py` and `calibration.py` |
| `scorbot/errors.py` (new) | `ScorbotError`, `MotionStopped`, `_WorkerCrashed`, no imports |
| `scorbot/homing.py` (new) | `HomingMixin`: `home`, `home_inch`, `vendor_home` and their helpers |
| `scorbot/gripper.py` (new) | `GripperMove`, `GripperMixin` |
| `scorbot/stream_control.py` (new) | `StreamMixin`: `start_stream`, `_stream_faulted`, `_end_stream`, `_abandon_stream` |
| `scorbot/robot.py` | `Scorbot(HomingMixin, GripperMixin, StreamMixin)` keeps the worker threads, `_command`, the fault latch, jogs and state |
| `scorbot/provenance.py` | the new modules join `_SOURCE_FILES` |

Deferred (UI and hardware-adjacent, outside the 95% target): `scorbot/lab/terminal.py`, `scorbot/lab/__main__.py`, `scorbot/camera/source.py`, `scorbot/transport/device_file.py`.

---

### Task 0: Branch and baseline

**Files:** none changed except git state.

- [ ] **Step 1:** Make sure the working tree holds only this plan and the spec. If `docs/archive/lab/VISIT_A_AGENT_CHECKLIST.md` or `CLAUDE.md` still show as modified, commit them first on `home-diagnostics` (separate commit, not part of this work).

Run: `git status --short`
Expected: only `docs/archive/specs/2026-10-12-code-health-design.md`, `docs/archive/specs/2026-10-12-code-health-plan.md` and the two older untracked specs.

- [ ] **Step 2:** Record the baseline.

Run: `.venv/Scripts/python.exe -m pytest -q tests tests_extra 2>&1 | tail -1` and `.venv/Scripts/python.exe -m ruff check . | tail -1`
Expected: `1160 passed, 1 skipped` and `All checks passed!`

- [ ] **Step 3:** Branch and commit the spec and plan.

```bash
git switch -c code-health
git add docs/archive/specs/2026-10-12-code-health-design.md docs/archive/specs/2026-10-12-code-health-plan.md
git commit -m "Add the code-health spec and plan"
```

---

### Task 1: Pin the public surface of `scorbot.robot`

**Files:**
- Create: `tests/test_robot_surface.py`, `tests/fixtures/scorbot_surface.json`

**Interfaces:**
- Produces: `surface() -> dict[str, str]` in the test module; every later task must keep `tests/test_robot_surface.py` green.

- [ ] **Step 1: Write the test**

```python
"""The public surface of scorbot.robot, pinned so a refactor cannot drop or rename anything."""

import inspect
import json
import os
import unittest
from pathlib import Path

import scorbot
from scorbot.robot import Scorbot

SNAPSHOT = Path(__file__).resolve().parent / "fixtures" / "scorbot_surface.json"
SIMPLE = (int, float, str, bool, tuple, type(None))


def surface():
    """Every attribute of Scorbot (inherited ones included) with its signature or simple value."""
    out = {}
    for name, member in inspect.getmembers(Scorbot):
        if name.startswith("__") and name != "__init__":
            continue
        if inspect.isfunction(member):
            out[name] = "def" + str(inspect.signature(member))
        elif isinstance(member, SIMPLE):
            out[name] = "value " + repr(member)
        elif isinstance(member, (dict, frozenset, set, list)):
            out[name] = "value " + repr(sorted(member, key=repr) if not isinstance(member, dict) else sorted(member.items(), key=repr))
    out["__all__"] = repr(sorted(scorbot.__all__))
    return out


class SurfaceTests(unittest.TestCase):
    def test_the_public_surface_is_unchanged(self):
        current = surface()
        if os.environ.get("UPDATE_SURFACE") == "1":
            SNAPSHOT.write_text(json.dumps(current, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        self.assertEqual(sorted(set(expected) - set(current)), [], "names removed from Scorbot")
        self.assertEqual(sorted(set(current) - set(expected)), [], "names added to Scorbot")
        changed = {name for name in expected if name in current and expected[name] != current[name]}
        self.assertEqual(sorted(changed), [], "a signature or constant changed")
```

- [ ] **Step 2: Run it to see it fail (no snapshot yet)**

Run: `.venv/Scripts/python.exe -m pytest tests/test_robot_surface.py -q`
Expected: FAIL with `FileNotFoundError` for `scorbot_surface.json`.

- [ ] **Step 3: Generate the snapshot from the unchanged code**

Run: `UPDATE_SURFACE=1 .venv/Scripts/python.exe -m pytest tests/test_robot_surface.py -q` (PowerShell: `$env:UPDATE_SURFACE=1; ...; Remove-Item Env:UPDATE_SURFACE`)
Expected: PASS and the JSON file exists. Open it: it must list `connect`, `home`, `jog_joint`, `request_stop`, `start_stream`, `vendor_home`, `move_gripper` with their signatures.

- [ ] **Step 4: Run it without the variable, then the full suite**

Run: `.venv/Scripts/python.exe -m pytest -q tests tests_extra 2>&1 | tail -1`
Expected: `1161 passed`.

- [ ] **Step 5: Mutation check.** Temporarily rename `def request_stop(self)` to `def request_stop2(self)` in `scorbot/robot.py`; the test must fail naming `request_stop`. Restore with `git checkout scorbot/robot.py`.

- [ ] **Step 6: Commit**

```bash
git add tests/test_robot_surface.py tests/fixtures/scorbot_surface.json
git commit -m "Pin the public surface of Scorbot before splitting robot.py"
```

---

### Task 2: Guard branches of `Scorbot` that fire before anything is sent

**Files:**
- Create: `tests/test_robot_guards.py`

**Interfaces:**
- Consumes: `Scorbot(...)` unconnected (`_device is None`), `SimulatedRobotCase` from `tests/sim_support.py`.

- [ ] **Step 1: Write the tests** (each asserts the right exception and that nothing was queued)

```python
"""Refusals in Scorbot that must happen before anything is queued or sent."""

import unittest

from scorbot import Scorbot, ScorbotError

try:
    from tests.sim_support import SimulatedRobotCase
except ImportError:
    from sim_support import SimulatedRobotCase


class UnconnectedTests(unittest.TestCase):
    def setUp(self):
        self.robot = Scorbot(command_timeout=0.2)

    def assertNothingQueued(self):
        self.assertTrue(self.robot._commands.empty())

    def test_a_command_before_connect_is_refused(self):
        with self.assertRaisesRegex(ScorbotError, "Not connected"):
            self.robot._command([16, 1, 1])
        self.assertNothingQueued()

    def test_a_stream_before_connect_is_refused(self):
        with self.assertRaisesRegex(ScorbotError, "Not connected"):
            self.robot.start_stream()
        self.assertNothingQueued()

    def test_calibration_needs_a_robot_id(self):
        with self.assertRaisesRegex(ValueError, "robot_id is required"):
            Scorbot(calibration_path="calibration.json")

    def test_preview_jog_rejects_bad_input_without_opening_anything(self):
        for args, message in (
                (("nowhere", 1), "Unknown joint"),
                (("base", 0), "nonzero"),
                (("base", float("nan")), "finite"),
                (("base", 99), "jog ceiling"),
                (("base", True), "finite")):
            with self.subTest(args=args):
                with self.assertRaisesRegex(ValueError, message):
                    self.robot.preview_jog(*args)
        with self.assertRaisesRegex(ValueError, "mapping"):
            self.robot.preview_jog("base", 1, starting_signed_counts=[1, 2])
        with self.assertRaisesRegex(ValueError, "Missing or invalid signed count"):
            self.robot.preview_jog("base", 1, starting_signed_counts={})
        self.assertNothingQueued()

    def test_calibrated_angles_need_a_loaded_calibration(self):
        with self.assertRaisesRegex(ScorbotError, "No validated physical calibration"):
            self.robot.get_joint_angles()
        with self.assertRaisesRegex(ScorbotError, "No validated physical calibration"):
            self.robot.move_joint("base", 1)
        self.assertNothingQueued()


class ConnectedArgumentTests(SimulatedRobotCase):
    def assertQuiet(self, robot, before):
        self.assertEqual(len(robot.sim.commands), before, "a refused call must send nothing")

    def test_park_at_home_checks_its_arguments_before_sending(self):
        robot = self.robot()
        before = len(robot.sim.commands)
        with self.assertRaisesRegex(ValueError, "operator is at the physical stop"):
            robot.park_at_home()
        with self.assertRaisesRegex(ValueError, "speed must be an integer"):
            robot.park_at_home(operator_at_stop=True, speed=0)
        with self.assertRaisesRegex(ValueError, "speed must be an integer"):
            robot.park_at_home(operator_at_stop=True, speed=True)
        self.assertQuiet(robot, before)

    def test_park_at_home_needs_a_home(self):
        self.HOMED = False
        robot = self.robot()
        with self.assertRaisesRegex(ScorbotError, "Home first"):
            robot.park_at_home(operator_at_stop=True)

    def test_a_jog_with_a_bad_speed_is_refused(self):
        robot = self.robot()
        before = len(robot.sim.commands)
        for speed in (0, 21, True, 1.5):
            with self.subTest(speed=speed):
                with self.assertRaisesRegex(ValueError, "speed must be an integer"):
                    robot.jog_joint("base", 1, speed=speed)
        self.assertQuiet(robot, before)

    def test_a_jog_before_home_is_refused(self):
        self.HOMED = False
        robot = self.robot()
        before = len(robot.sim.commands)
        with self.assertRaisesRegex(ScorbotError, "Enable and home"):
            robot.jog_joint("base", 1)
        self.assertQuiet(robot, before)

    def test_a_second_connect_is_refused(self):
        robot = self.robot()
        with self.assertRaisesRegex(ScorbotError, "Already connected"):
            robot.connect()


if __name__ == "__main__":
    unittest.main()
```

Note for the implementer: `robot.sim.commands` is the list the simulator records (see `tests/test_simulated.py`); if a name differs, read how `test_simulated.py` asserts "nothing queued" and use the same attribute. `SimulatedRobotCase.HOMED` is a class attribute: set it with `self.HOMED = False` before calling `self.robot()` (as written) or in a subclass.

- [ ] **Step 2: Run**

Run: `.venv/Scripts/python.exe -m pytest tests/test_robot_guards.py -q`
Expected: PASS (these characterise existing behaviour). A failure means the test misreads the code: fix the test, never the code.

- [ ] **Step 3: Mutation checks.** For each of: the `"Not connected"` raise in `_command` (line ~344), the speed check in `park_at_home` (~1013), the `"Home first"` raise (~1021), remove it, run the file, see the matching test fail, then `git checkout scorbot/robot.py`.

- [ ] **Step 4: Coverage and suite**

Run: `.venv/Scripts/python.exe -m coverage run -m pytest -q tests tests_extra >/dev/null 2>&1; .venv/Scripts/python.exe -m coverage report --include=scorbot/robot.py`
Expected: `robot.py` coverage above 86% (was 84%).

- [ ] **Step 5: Commit**

```bash
git add tests/test_robot_guards.py
git commit -m "Test the refusals Scorbot makes before it sends anything"
```

---

### Task 3: The real `connect` and `disconnect` against a fake `usb`

**Files:**
- Create: `tests/test_robot_connect.py`

**Interfaces:**
- Consumes: `Scorbot.connect`, `Scorbot.disconnect`, `scorbot.state.decode_state`, `Scorbot._legacy`, `_run_sync_worker`, `_run_command_worker`, `_command`, `get_state`.

`SimulatedScorbot` overrides `connect`, so the real one (lines 193 to 280) is untested. The test replaces `usb`, the legacy modules and the worker threads with fakes; nothing opens a device.

- [ ] **Step 1: Write the tests**

```python
"""Scorbot.connect and disconnect with a fake usb package, fake legacy modules and no worker threads."""

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from scorbot import Scorbot, ScorbotError
from scorbot.state import decode_state


class FakeEndpoint:
    def __init__(self, address):
        self.bEndpointAddress = address
        self.wMaxPacketSize = 64


def fake_usb(device, in_endpoint=True, out_endpoint=True):
    class USBError(Exception):
        pass

    core = types.SimpleNamespace(find=lambda **kw: device, USBError=USBError)
    endpoints = {0x83: FakeEndpoint(0x83) if in_endpoint else None,
                 0x02: FakeEndpoint(0x02) if out_endpoint else None}

    def find_descriptor(interface, custom_match):
        for endpoint in (e for e in endpoints.values() if e is not None):
            if custom_match(endpoint):
                return endpoint
        return None

    util = types.SimpleNamespace(
        ENDPOINT_IN=0x80, ENDPOINT_OUT=0x00,
        endpoint_direction=lambda address: address & 0x80,
        find_descriptor=find_descriptor,
        create_buffer=lambda size: bytearray(size))
    package = types.SimpleNamespace(core=core, util=util)
    return {"usb": package, "usb.core": core, "usb.util": util}


class FakeDevice:
    def __init__(self):
        self.resets = 0
        self.closed = False

    def reset(self):
        self.resets += 1

    def __getitem__(self, index):
        return types.SimpleNamespace(interfaces=lambda: [types.SimpleNamespace(bInterfaceNumber=0)])

    def is_kernel_driver_active(self, number):
        return False

    def get_active_configuration(self):
        return {(0, 0): object()}


def fake_legacy(name):
    if name == "conf":
        return types.SimpleNamespace(setup=lambda: None)
    if name == "libsync":
        return types.SimpleNamespace(msg_start=lambda out, inp, buf: (1, [0] * 6))
    return types.SimpleNamespace()


class ConnectTests(unittest.TestCase):
    def robot(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        robot = Scorbot(log_path=Path(folder.name) / "run.controller.jsonl", command_timeout=0.2)
        patches = [
            mock.patch.object(robot, "_legacy", side_effect=fake_legacy),
            mock.patch.object(robot, "_run_sync_worker"),
            mock.patch.object(robot, "_run_command_worker"),
            mock.patch.object(robot, "_command"),
            mock.patch("scorbot.robot.sys.platform", "linux"),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        packet = bytearray(64)
        packet[21] = packet[26] = packet[31] = 128
        state = decode_state(bytes(packet), connected=True, enabled=False, homed=False, fault=None)
        patch = mock.patch.object(robot, "get_state", return_value=state)
        patch.start()
        self.addCleanup(patch.stop)
        return robot

    def test_a_good_connect_leaves_the_motors_disabled_and_logs_it(self):
        robot, device = self.robot(), FakeDevice()
        with mock.patch.dict(sys.modules, fake_usb(device)):
            robot.connect()
        self.assertIs(robot._device, device)
        self.assertEqual(device.resets, 1)
        self.assertFalse(robot._enabled)
        robot._command.assert_called_with([16, 1, 1])     # the handshake energises, so it disables at once
        events = [line for line in Path(robot._log_path).read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any('"connect"' in line for line in events))

    def test_no_controller_latches_a_fault_and_says_motors_are_unverified(self):
        robot = self.robot()
        with mock.patch.dict(sys.modules, fake_usb(None)):
            with self.assertRaisesRegex(ScorbotError, "09F1:0007 was not found.*unverified"):
                robot.connect()
        self.assertIsNotNone(robot._fault)
        with self.assertRaisesRegex(ScorbotError, "new Scorbot instance"):
            robot.connect()

    def test_missing_endpoints_latch_a_fault(self):
        for kwargs in ({"in_endpoint": False}, {"out_endpoint": False}):
            with self.subTest(kwargs=kwargs):
                robot = self.robot()
                with mock.patch.dict(sys.modules, fake_usb(FakeDevice(), **kwargs)):
                    with self.assertRaisesRegex(ScorbotError, "endpoints were not found"):
                        robot.connect()
                self.assertIsNotNone(robot._fault)

    def test_a_second_connect_is_refused_without_touching_the_device(self):
        robot, device = self.robot(), FakeDevice()
        with mock.patch.dict(sys.modules, fake_usb(device)):
            robot.connect()
            with self.assertRaisesRegex(ScorbotError, "Already connected"):
                robot.connect()
        self.assertEqual(device.resets, 1)


if __name__ == "__main__":
    unittest.main()
```

Note for the implementer: `robot._log_path` and `robot._legacy` are the real attribute names to check against `Scorbot.__init__` (line 126) before running; if the log path attribute differs, read the log file path you passed instead. `test_a_good_connect...` ends without calling `disconnect()`: the worker threads are patched out, so nothing is left running.

- [ ] **Step 2: Run**

Run: `.venv/Scripts/python.exe -m pytest tests/test_robot_connect.py -q`
Expected: PASS. If `connect` calls an attribute the fakes lack (for example `disconnect` during cleanup imports `usb`), extend the fake minimally; do not change `robot.py`.

- [ ] **Step 3: Mutation checks.** Remove `self._enabled = False` (line ~277): the first test must fail on `assertFalse(robot._enabled)`. Remove the `raise ScorbotError("...not found")` and replace with `pass`: the second must fail. Restore with `git checkout`.

- [ ] **Step 4: Full suite, coverage of `robot.py`, commit**

Run: full suite and the coverage command from Task 2.
Expected: `robot.py` above 88%.

```bash
git add tests/test_robot_connect.py
git commit -m "Test the real connect path against a fake usb package"
```

---

### Task 4: Stream, command and disconnect error branches

**Files:**
- Create: `tests/test_robot_error_paths.py`

**Interfaces:**
- Consumes: `SimulatedRobotCase`, `Scorbot._command`, `_end_stream`, `_abandon_stream`, `disconnect`, `_settled_after_stop`.

- [ ] **Step 1: Write the tests.** Each reaches one missed branch listed in the spec. Use these exact cases:

```python
"""Error branches of command handling, streams and disconnect."""

import queue
import unittest
from unittest import mock

from scorbot import Scorbot, ScorbotError

try:
    from tests.sim_support import SimulatedRobotCase
except ImportError:
    from sim_support import SimulatedRobotCase


class CommandErrorPaths(unittest.TestCase):
    def setUp(self):
        self.robot = Scorbot(command_timeout=0.05)
        self.robot._device = object()
        self.robot._buffer = bytearray(64)

    def test_a_command_during_a_stream_is_refused_before_it_is_queued(self):
        self.robot._stream = object()
        with self.assertRaisesRegex(ScorbotError, "A stream is active"):
            self.robot._command([17, 1, 1])
        self.assertTrue(self.robot._commands.empty())

    def test_a_timeout_latches_a_fault_and_queues_a_disable(self):
        with self.assertRaises(ScorbotError):
            self.robot._command([17, 1, 1])
        self.assertIsNotNone(self.robot._fault)

    def test_keyboard_interrupt_latches_cancels_and_reraises(self):
        with mock.patch.object(self.robot, "_next_result", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.robot._command([17, 1, 1])
        self.assertIsNotNone(self.robot._fault)
        self.assertTrue(self.robot._cancel_event.is_set())


class StreamErrorPaths(SimulatedRobotCase):
    def test_ending_a_stream_twice_is_refused(self):
        robot = self.robot()
        stream = robot.start_stream()
        stream.stop()
        with self.assertRaisesRegex(ScorbotError, "already ended"):
            robot._end_stream(stream)

    def test_a_stream_before_home_is_refused(self):
        self.HOMED = False
        robot = self.robot()
        with self.assertRaises(ScorbotError):
            robot.start_stream()


if __name__ == "__main__":
    unittest.main()
```

Note for the implementer: read `_command` (lines 342 to 395) and `_end_stream` (592 to 660) first and adjust each test to the exact attribute names and the state in which the branch fires (for example, which method `stop` is on `Stream`, and what `_end_stream` takes). If a branch cannot be reached without a real thread, drop that single test and say so in the commit message; do not alter `robot.py`.

- [ ] **Step 2: Run, mutation-check each test (remove its guard line, see it fail, `git checkout scorbot/robot.py`), run the full suite and coverage.**
Expected: `robot.py` above 90%.

- [ ] **Step 3: Commit**

```bash
git add tests/test_robot_error_paths.py
git commit -m "Test command, stream and disconnect error paths"
```

---

### Task 5: `preflight.py` and `calibration.py`

**Files:**
- Create: `tests/test_preflight_more.py`, `tests/test_calibration_load.py`

- [ ] **Step 1: Preflight tests**

```python
"""The preflight branches test_preflight.py does not reach. Never touches USB."""

import io
import types
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from scorbot import preflight


def importer(find=None, backend=object(), raise_for=()):
    def fake_import(name):
        if name in raise_for:
            raise ImportError(f"no {name}")
        if name == "libusb_package":
            return types.SimpleNamespace(get_libusb1_backend=lambda: backend)
        if name == "usb.backend.libusb1":
            return types.SimpleNamespace(get_backend=lambda: backend)
        if name == "usb.core":
            return types.SimpleNamespace(find=find or (lambda **kw: object()))
        return types.SimpleNamespace()
    return fake_import


class PreflightMore(unittest.TestCase):
    def run_with(self, platform, **kwargs):
        with patch.object(preflight, "import_module", side_effect=importer(**kwargs)), \
                patch.object(preflight.metadata, "version", return_value="t"), \
                patch.object(preflight.sys, "platform", platform):
            return preflight.run_checks()

    def test_a_missing_dependency_stops_the_checks_early(self):
        checks = self.run_with("win32", raise_for=("usb.core",))
        self.assertEqual([c.name for c in checks][-1], "pyusb")
        self.assertFalse(checks[-1].passed)

    def test_the_posix_path_uses_the_system_libusb(self):
        checks = self.run_with("linux")
        backend = [c for c in checks if c.name == "libusb backend"][0]
        self.assertTrue(backend.passed)
        self.assertIn("libusb-1.0", backend.detail)

    def test_main_prints_pass_and_returns_zero(self):
        out = io.StringIO()
        with patch.object(preflight, "run_checks", return_value=[preflight.Check("x", True, "ok")]), \
                redirect_stdout(out):
            self.assertEqual(preflight.main(), 0)
        self.assertIn("PASS x: ok", out.getvalue())
        self.assertIn("Preflight passed", out.getvalue())

    def test_main_prints_fail_and_returns_one(self):
        out = io.StringIO()
        with patch.object(preflight, "run_checks", return_value=[preflight.Check("x", False, "bad")]), \
                redirect_stdout(out):
            self.assertEqual(preflight.main(), 1)
        self.assertIn("FAIL x: bad", out.getvalue())
        self.assertIn("Preflight failed", out.getvalue())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Calibration tests**

```python
"""load_calibration and JointCalibration: every rejection, and one good file."""

import json
import tempfile
import unittest
from pathlib import Path

from scorbot.calibration import JointCalibration, load_calibration

GOOD = {
    "schema_version": 1, "status": "validated", "robot_id": "arm-1", "source_sha256": "a" * 64,
    "joints": {"base": {
        "encoder": "base", "home_count": 1000, "home_tolerance_counts": 5, "home_angle_deg": 0,
        "counts_per_degree": 10, "soft_min_deg": -10, "soft_max_deg": 10,
        "validation_max_error_deg": 1, "motion_max_error_deg": 1,
        "fit_points": 4, "validation_points": 3, "motion_validation_points": 3, "home_points": 3}},
}


def copy(**joint_changes):
    data = json.loads(json.dumps(GOOD))
    data["joints"]["base"].update(joint_changes)
    return data


class LoadTests(unittest.TestCase):
    def load(self, data, robot_id="arm-1"):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "calibration.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return load_calibration(path, robot_id=robot_id)

    def test_a_good_file_loads(self):
        calibration = self.load(GOOD)
        self.assertEqual(calibration.joints["base"].counts_per_degree, 10.0)

    def test_every_bad_field_is_refused(self):
        bad = {
            "home count": copy(home_count=70000), "home count type": copy(home_count="1000"),
            "tolerance": copy(home_tolerance_counts=40000), "mapping": copy(encoder="shoulder"),
            "scale": copy(counts_per_degree=0.5), "nan": copy(home_angle_deg=float("nan")),
            "bool": copy(soft_min_deg=True), "range": copy(soft_min_deg=5, soft_max_deg=-5),
            "error too big": copy(validation_max_error_deg=3), "few points": copy(fit_points=3),
            "few home points": copy(home_points=2),
        }
        for label, data in bad.items():
            with self.subTest(label):
                with self.assertRaises(ValueError):
                    self.load(data)

    def test_the_header_is_checked(self):
        for label, change in (("version", {"schema_version": 2}), ("status", {"status": "draft"}),
                              ("robot", {"robot_id": "other"}), ("hash", {"source_sha256": "xyz"}),
                              ("joints", {"joints": {}}), ("wrist", {"joints": {"wrist_pitch": GOOD["joints"]["base"]}})):
            with self.subTest(label):
                data = json.loads(json.dumps(GOOD))
                data.update(change)
                with self.assertRaises(ValueError):
                    self.load(data)
        with self.assertRaises(ValueError):
            self.load(GOOD, robot_id="")

    def test_validate_home_uses_the_tolerance_and_the_wrap(self):
        joint = JointCalibration("base", 1000, 0.0, 10.0, -10.0, 10.0, 5, 1.0, 1.0)
        joint.validate_home(1004)
        with self.assertRaisesRegex(ValueError, "home count"):
            joint.validate_home(1006)
        near_seam = JointCalibration("base", 65534, 0.0, 10.0, -10.0, 10.0, 5, 1.0, 1.0)
        near_seam.validate_home(2)             # 65534 to 2 is 3 counts across the 0/65535 seam


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run, fix the tests (never the code) until they pass.** If `check_soft_limit_span` rejects the fixture's ±10 degree span, use the span in `tests/test_arm_control.py`'s `CalibrationTests`.

- [ ] **Step 4: Mutation checks.** In `calibration.py` change `abs(scale) < 1` to `abs(scale) < 0` and `> 2` (error limit) to `> 20`; the "every bad field" test must fail each time. Restore with `git checkout`.

- [ ] **Step 5: Suite, coverage (`preflight.py` and `calibration.py` above 95%), commit**

```bash
git add tests/test_preflight_more.py tests/test_calibration_load.py
git commit -m "Test the remaining preflight and calibration branches"
```

- [ ] **Step 6: Review gate for Phase 1.** `bash ~/.agents/scripts/review-both.sh standard --strict --tests <suite tail file> --intent "Characterisation tests only; no production code changed" "Do any tests assert implementation details a refactor would break, or pass vacuously?"`. Triage, fix, log.

---

### Task 6: Extraction tooling and the exceptions module

**Files:**
- Create: `scorbot/errors.py`, scratch scripts `move_methods.py` and `compare_bodies.py` (kept outside the repo, e.g. in the session scratchpad)
- Modify: `scorbot/robot.py`, `scorbot/provenance.py`

**Interfaces:**
- Produces: `scorbot.errors.ScorbotError`, `MotionStopped`, `_WorkerCrashed`; `move_methods.py SRC DST CLASS MIXIN names...`; `compare_bodies.py OLD_REF NEW_FILES...`.

- [ ] **Step 1: Write `compare_bodies.py`** (proves every moved method body is identical)

```python
"""python compare_bodies.py GIT_REF scorbot/robot.py scorbot/homing.py ...
Every function defined in GIT_REF's scorbot/robot.py must exist in exactly one of the new files with an identical AST."""
import ast
import subprocess
import sys

ref, files = sys.argv[1], sys.argv[2:]
old_src = subprocess.run(["git", "show", f"{ref}:scorbot/robot.py"], capture_output=True, text=True, check=True).stdout


def functions(source):
    out = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.setdefault(node.name, []).append(ast.dump(node))
    return out


old = functions(old_src)
new = {}
for path in files:
    for name, dumps in functions(open(path, encoding="utf-8").read()).items():
        new.setdefault(name, []).extend(dumps)
problems = [f"missing: {n}" for n in old if n not in new]
problems += [f"changed: {n}" for n, d in old.items() if n in new and sorted(d) != sorted(new[n])]
problems += [f"added: {n}" for n in new if n not in old]
print("\n".join(problems) or f"all {len(old)} functions identical")
sys.exit(1 if problems else 0)
```

- [ ] **Step 2: Write `move_methods.py`** (copies the source text of named methods unchanged)

```python
"""python move_methods.py scorbot/robot.py scorbot/NEW.py ClassName MixinName method1 method2 ...
Cuts the named methods out of ClassName in the first file and writes them, byte for byte, into class MixinName in NEW.py.
Prints module-level names the moved code uses, so the import block can be written by hand."""
import ast
import os
import sys

src_path, dst_path, cls, mixin, *names = sys.argv[1:]
raw = open(src_path, encoding="utf-8", newline="").read()
crlf = "\r\n" in raw
text = raw.replace("\r\n", "\n")
tree = ast.parse(text)
lines = text.split("\n")
klass = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls)
spans, moved = [], []
for node in klass.body:
    if isinstance(node, ast.FunctionDef) and node.name in names:
        first = min([node.lineno] + [d.lineno for d in node.decorator_list])
        while first > 1 and lines[first - 2].lstrip().startswith("#"):
            first -= 1                                   # keep the comments right above the method
        spans.append((first, node.end_lineno))
        moved.append(node)
missing = set(names) - {n.name for n in moved}
if missing:
    sys.exit(f"not found in {cls}: {sorted(missing)}")
body = "\n\n".join("\n".join(lines[a - 1:b]) for a, b in spans)
for a, b in sorted(spans, reverse=True):
    del lines[a - 1:b]
    while a - 1 < len(lines) and a > 1 and lines[a - 2] == "" and lines[a - 1] == "":
        del lines[a - 1]                                 # collapse the blank gap left behind
top = set()
for node in tree.body:
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        top |= {(a.asname or a.name).split(".")[0] for a in node.names}
    elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
        top.add(node.name)
used = {n.id for m in moved for n in ast.walk(m) if isinstance(n, ast.Name)} & top
print("names used from robot.py's module scope:", sorted(used))
nl = "\r\n" if crlf else "\n"
open(src_path, "w", encoding="utf-8", newline="").write(nl.join(lines))
header = open(dst_path, encoding="utf-8").read() if os.path.exists(dst_path) else ""
open(dst_path, "w", encoding="utf-8", newline="").write(
    (header or f'"""Moved out of robot.py unchanged; see docs/archive/specs/2026-10-12-code-health-design.md."""\n\n# IMPORTS GO HERE\n\n\nclass {mixin}:\n') + "\n" + body.replace("\n", nl) + nl)
```

Run it on a copy first (`cp scorbot/robot.py /tmp/robot_try.py`) to see the "names used" report before the real run.

- [ ] **Step 3: Move the three exception classes into `scorbot/errors.py`** by hand: cut `ScorbotError`, `_WorkerCrashed` and `MotionStopped` (lines 53 to 74 of `robot.py`) into the new file unchanged; in `robot.py` replace them with `from .errors import MotionStopped, ScorbotError, _WorkerCrashed  # noqa: F401` (re-export, so `from scorbot.robot import ScorbotError` keeps working). `errors.py` imports nothing.

- [ ] **Step 4: Add `scorbot/errors.py` to `provenance._SOURCE_FILES`** (the motion fingerprint must cover every file that can change what is sent; exceptions decide fault behaviour).

- [ ] **Step 5: Run everything**

Run: full suite, `ruff check .`, then `python compare_bodies.py HEAD scorbot/robot.py scorbot/errors.py` (here it only checks function bodies: `MotionStopped.__init__` must be identical).
Expected: all green, `all N functions identical`.

- [ ] **Step 6: Commit**

```bash
git add scorbot/errors.py scorbot/robot.py scorbot/provenance.py
git commit -m "Move the exception classes out of robot.py into errors.py

robot.py re-exports them, so every import path still works. The new file
joins the motion fingerprint."
```

---

### Task 7: Extract the homing family into `scorbot/homing.py`

**Files:**
- Create: `scorbot/homing.py`
- Modify: `scorbot/robot.py`, `scorbot/provenance.py`, `scorbot/CLAUDE.md`, root `CLAUDE.md`, `tests/fixtures/scorbot_surface.json` (only if names legitimately move; they must not)

**Interfaces:**
- Produces: `scorbot.homing.HomingMixin` with `home`, `home_inch`, `vendor_home`, `pre_home_jog`, `park_at_home`, `_coupled_run`, `_vendor_home_failure`, `_record_home_trace`, `_fail_coupled`, `_moved_since`, `_disable_best_effort` and any other method used only by those (find them with the names report).
- `class Scorbot(HomingMixin)` keeps its MRO-visible API: `tests/test_robot_surface.py` must pass unchanged.

- [ ] **Step 1: Find the group.** For each candidate method, list who calls it: `grep -n "self\.<name>(" scorbot/robot.py`. A helper moves only if every caller is already in the group; otherwise it stays.

- [ ] **Step 2: Dry run** `python move_methods.py scorbot/robot.py /tmp/homing_try.py Scorbot HomingMixin <names...>` on a copy; read the "names used" report; every name must be importable from the new module (`inch_home`, `joint_move`, `source_model`, `signed_count_delta`, `vendor_home_port`, `VendorHomeSource`, `asdict`, `ScorbotError`, ...).

- [ ] **Step 3: Real run, then write the import block** at `# IMPORTS GO HERE` using the same import lines `robot.py` uses (relative imports: `from . import inch_home, joint_move, source_model` etc.), and in `robot.py` change `class Scorbot:` to `class Scorbot(HomingMixin):` with `from .homing import HomingMixin`. Delete imports from `robot.py` that ruff now reports unused.

- [ ] **Step 4: Prove nothing changed**

Run: `python compare_bodies.py <commit before this task> scorbot/robot.py scorbot/homing.py scorbot/errors.py` then full suite, ruff, `tests/test_robot_surface.py`.
Expected: `all N functions identical`; 1175-ish tests pass (baseline plus the new ones); surface test passes with the snapshot unchanged.

- [ ] **Step 5: Add `scorbot/homing.py` to `_SOURCE_FILES`; run `tests/test_provenance.py`.** Update `scorbot/CLAUDE.md` (the `Scorbot.vendor_home` and `robot.py` rows) and the root module index.

- [ ] **Step 6: Mutation check on the move.** In `homing.py` delete one `self._latch_fault(...)` line inside `vendor_home`'s failure path; the existing `tests/test_vendor_home_robot.py` must fail. Restore.

- [ ] **Step 7: Review.** `review-both.sh deep --strict` with focus "Is every moved method identical, and does any caller outside the moved group reach a name that no longer resolves?" Triage and log.

- [ ] **Step 8: Commit**

```bash
git add scorbot/homing.py scorbot/robot.py scorbot/provenance.py scorbot/CLAUDE.md CLAUDE.md
git commit -m "Move the homing methods out of robot.py into homing.py

The method bodies are unchanged (checked by comparing their syntax trees);
Scorbot now takes them from HomingMixin. Public names and signatures are
pinned by tests/test_robot_surface.py."
```

---

### Task 8: Extract the gripper into `scorbot/gripper.py`

Same procedure as Task 7 with these specifics.

**Files:** Create `scorbot/gripper.py`; modify `scorbot/robot.py`, `scorbot/provenance.py`, `scorbot/__init__.py` (import `GripperMove` from `.gripper`; `scorbot.robot` re-exports it).

- Moves: `GripperMove` (the dataclass, lines 78 to 90) and `move_gripper` with its helpers (`open_gripper`, `close_gripper` and any helper only they call).
- [ ] **Steps 1 to 8 as Task 7**, with `compare_bodies.py` run against the commit before Task 8, the mutation check in `move_gripper`'s `gripper_fault` branch (remove the wrong-direction fault; `tests_extra/test_gripper.py` must fail), and `review-both.sh deep --strict` focused on "does `GripperMove` still pickle, compare and print the same, and does `move_gripper` still refuse while a stream is active?".
- [ ] **Commit message:** "Move the gripper into gripper.py".

---

### Task 9: Extract stream control into `scorbot/stream_control.py`

Same procedure.

**Files:** Create `scorbot/stream_control.py`; modify `scorbot/robot.py`, `scorbot/provenance.py`.

- Moves: `start_stream`, `_stream_faulted`, `_end_stream`, `_abandon_stream` (and `_refuse_if_streaming` only if no caller remains in `robot.py`; it is used by every command path, so it most likely stays).
- Constraint: `_end_stream` latches before it writes the large `stream_trace` row and treats Ctrl-C like `_command` (scorbot/CLAUDE.md, Streaming). The move is text-identical, so this holds; the mutation check removes the `_latch_fault` call in `_end_stream` and `tests_extra/test_streaming.py` must fail.
- [ ] **Steps 1 to 8 as Task 7**; review focus: "does a stream that faults on the worker thread still latch the session and queue the disable?".
- [ ] **Step 9: Re-measure.** `wc -l scorbot/robot.py` and `coverage report`. If `robot.py` is under about 1100 lines and reads well, stop here (spec: no deeper split). Write the size in the commit body.
- [ ] **Commit message:** "Move stream start and shutdown into stream_control.py".

---

### Task 10: Backlog 15, the hard-coded pause marker

**Files:**
- Modify: `openScorbot/libsync.py:syncro`, `docs/archive/project/BACKLOG.md`
- Test: `tests/test_pause_marker.py`

- [ ] **Step 1: Read the code.** `grep -n "256" openScorbot/libsync.py` and `grep -n "MAX_COUNT" openScorbot/*.py`. Confirm the `256` in `syncro` is the pause marker and that `libcomm` compares against `MAX_COUNT` read from `data.json`. **If changing it could alter a byte sent or a sleep, stop and report: it stays out of scope.**

- [ ] **Step 2: Write a failing test** that sets `MAX_COUNT` to a value other than 256 through the legacy `conf` data (patch `conf.readData`) and runs `syncro` against fake queues, asserting the pause marker equals `MAX_COUNT`. Use the neighbouring legacy-loop tests (`tests/test_software_stop.py`, `tests/test_python_api.py` `_legacy("libsync")` usage) as the pattern: patch `time.sleep`, fake endpoints, no USB.

- [ ] **Step 3: Run it; expect FAIL** (the marker is 256).

- [ ] **Step 4: Replace the literal with the configured value** (the same name `libcomm` uses), no other change.

- [ ] **Step 5: Run the new test, the full suite and `tests/test_legacy_properties.py`; expect PASS** and an unchanged default (`MAX_COUNT` default is 256, so nothing differs on the wire).

- [ ] **Step 6: Mark BACKLOG item 15 fixed (date, test name, "never run on the arm").**

- [ ] **Step 7: Review** with `review-both.sh deep --strict`, focus "can this change any packet byte or message timing?". Commit:

```bash
git add openScorbot/libsync.py tests/test_pause_marker.py docs/archive/project/BACKLOG.md
git commit -m "Read the pause marker from the configured MAX_COUNT in syncro"
```

---

### Task 11: Close out

- [ ] **Step 1:** Final numbers: suite count, `ruff check .`, coverage table for the safety modules, `wc -l` of `robot.py` and the three new modules. Put them in the spec's status line.
- [ ] **Step 2:** Update `docs/archive/project/BACKLOG.md` (a "code health, 2026-10-12" note listing what was done and what was left on purpose: items 19 to 23), `scorbot/CLAUDE.md` module table, `tests/CLAUDE.md` index, and the memory note.
- [ ] **Step 3: Whole-branch review.** `review-both.sh max --strict --base home-diagnostics` with focus "behaviour changes anywhere in the diff other than the pause marker; anything that could change what is sent to the arm". Triage and log.
- [ ] **Step 4:** Report to the owner. Do not push or merge.

---

## Self-review

- **Spec coverage:** Phase 1 tests are Tasks 1 to 5 (deferred UI modules named in File Structure); Phase 2 is Tasks 6 to 9 (exceptions first, then the three extractions, with the stop-and-re-read check in Task 9); Phase 3 is Task 10; Phase 4 (reviews, records) is in every task and Task 11.
- **Placeholders:** the two "Note for the implementer" paragraphs in Tasks 2 to 4 name attributes to confirm against the code before running; they are verification instructions, not missing content. Task 7 to 9 lists method names to move; the `names` and the final group are fixed by the callers check in Step 1.
- **Type and name consistency:** `HomingMixin`, `GripperMixin`/`GripperMove`, `StreamMixin` are used consistently; `scorbot.errors` exports the same three names re-exported by `robot.py`; the surface snapshot is the check that nothing public moved.
