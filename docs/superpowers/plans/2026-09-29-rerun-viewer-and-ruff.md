# Rerun Viewer and Ruff Pre-commit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Open a recorded ScorBot session in Rerun (`python -m scorbot.session view`), and run the CI lint rules, plus bugbear, as a pre-commit hook.

**Architecture:** A pure function turns a loaded `Session` into `Item`s: a path, a kind, a value and a time. A single `send()` is the only code that touches the Rerun SDK. The CLI wires them together behind an optional `viz` extra. Ruff reads its rules from `pyproject.toml` in both pre-commit and CI.

**Tech Stack:** Python >= 3.10, `unittest`, `rerun-sdk` 0.38.x (optional), `ruff` 0.16.9, `pre-commit`.

**Spec:** [docs/superpowers/specs/2026-09-29-rerun-viewer-and-ruff-design.md](../specs/2026-09-29-rerun-viewer-and-ruff-design.md)

## Global Constraints

- Nothing here opens USB, moves the arm or changes a lab script (`examples/bench_joint.py`, `examples/record_raw_state.py`).
- `rerun` is imported only inside functions in `scorbot/session/rerun_view.py`. `import scorbot` and `import scorbot.session` must still work without it.
- Extra: `viz = ["rerun-sdk>=0.38,<0.39"]`. Do not add it to `dev` or to the base dependencies.
- One session per `view` call; sessions are never merged (real and simulated never share a view).
- `--save` never overwrites: it refuses an existing path with exit 2.
- Exit codes: 0 when there are no integrity errors; 1 when there are integrity errors; 2 when Rerun is missing, the session cannot be opened, or `--save` exists.
- Tests use `unittest`; a test needing Rerun skips when `rerun` is not importable.
- Commit messages: imperative, sentence-case subject, no prefix, a body that explains why, and **no AI attribution lines**.
- Run commands with `.venv\Scripts\python.exe` (PowerShell) or `.venv/Scripts/python.exe` (bash).

## Review Focus

1. A session with no events apart from the metadata still gives a `session/info` document and no crash. The test is in Task 2.
2. A damaged session (integrity errors) is still viewable: the errors appear in `session/info`, and `view` exits 1. The tests are in Tasks 2 and 3.
3. A state whose counts are `None`, strings or booleans (for example after a fault) skips those values and never raises. The test is in Task 2.
4. An image whose `data` is not valid base64 is skipped, not fatal. The test is in Task 2.
5. A command payload missing `kind` or `params` shows as `(unreadable payload)` in the log instead of raising `KeyError` from `event_summary`. The test is in Task 2.

---

### Task 1: Ruff pre-commit hook and bugbear rules

**Files:**
- Create: `.pre-commit-config.yaml`
- Modify: `pyproject.toml` (`[tool.ruff.lint]` block and the `dev` extra)
- Modify: `tests/test_calibration_capture.py` (around lines 212-231, the `FakeRobot` inside the loop)
- Modify: `docs/BACKLOG.md` (remove row 42)
- Modify: `CLAUDE.md` (Commands block)

**Interfaces:**
- Consumes: nothing.
- Produces: the lint rules `["E4", "E7", "E9", "F", "B"]` with `ignore = ["B905"]`, which every later task must pass.

- [ ] **Step 1: See the failure the new rules will find**

Run: `.venv/Scripts/python.exe -m ruff check --select E4,E7,E9,F,B --ignore B905 .`
Expected: 3 × `B023 Function definition does not bind loop variable 'calls'` in `tests/test_calibration_capture.py`.

- [ ] **Step 2: Widen the rules in `pyproject.toml`**

Replace the current `[tool.ruff.lint]` comment and `select` line with:

```toml
[tool.ruff.lint]
# Correctness rules (ruff defaults) plus flake8-bugbear, pinned so a personal
# ruff config cannot change them. B905 (zip strict=) is left out: it is style here.
select = ["E4", "E7", "E9", "F", "B"]
ignore = ["B905"]
```

In the `dev` extra, add `"pre-commit>=4,<5",` after the `ruff` line.

- [ ] **Step 3: Fix the B023 findings by binding the list at class creation**

In `tests/test_calibration_capture.py`, inside `test_unsafe_after_connect_led_stops_before_sampling`, change the `FakeRobot` class so its methods use a class attribute that is bound when the class is created in this loop iteration:

```python
                class FakeRobot:
                    log = calls          # bound per iteration (B023)

                    def __init__(self, *, log_path, robot_id):
                        Path(log_path).write_text("", encoding="utf-8")

                    def __enter__(self):
                        self.log.append("connect")
                        return self

                    def __exit__(self, *_args):
                        self.log.append("disconnect")

                    def get_state(self):
                        self.log.append("get_state")
                        raise AssertionError("unsafe LED check must stop before sampling")
```

- [ ] **Step 4: Lint and run the changed test**

Run: `.venv/Scripts/python.exe -m ruff check .`
Expected: `All checks passed!`
Run: `.venv/Scripts/python.exe -m unittest tests.test_calibration_capture -v`
Expected: all OK.

- [ ] **Step 5: Add the pre-commit config**

Create `.pre-commit-config.yaml`:

```yaml
# Runs the same Ruff rules as CI (read from pyproject.toml) before each commit.
# Install once per clone: python -m pip install -e ".[dev]"; pre-commit install
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.16.9
    hooks:
      - id: ruff-check
```

- [ ] **Step 6: Try the hook once**

Run: `.venv/Scripts/python.exe -m pip install "pre-commit>=4,<5"`, then `.venv/Scripts/python.exe -m pre_commit run --all-files`
Expected: `ruff check....Passed`. The first run downloads the hook, which needs network access. If it cannot download, note that and continue; CI still enforces the rules.

- [ ] **Step 7: Docs**

In `docs/BACKLOG.md`, delete the row that starts with `| 42 |`.
In `CLAUDE.md`, under Commands, replace the ruff line with:

```powershell
ruff check .                                   # CI rules incl. bugbear; `pre-commit install` runs it on every commit
```

- [ ] **Step 8: Commit**

```bash
git add .pre-commit-config.yaml pyproject.toml tests/test_calibration_capture.py docs/BACKLOG.md CLAUDE.md
git commit -m "Run Ruff before each commit and add the bugbear rules" -m "CI already ran ruff; a pre-commit hook reading the same pyproject rules catches problems before they are pushed. Bugbear finds real bug patterns (late-bound loop variables, mutable defaults); its three hits were test closures, now bound per iteration."
```

---

### Task 2: Pure session-to-Rerun mapping

**Files:**
- Create: `scorbot/session/rerun_view.py`
- Test: `tests/test_rerun_view.py`

**Interfaces:**
- Consumes: `scorbot.session.replay.Session` (fields `path`, `metadata`, `events`, `findings`; `.errors`), `Finding(level, message)`; event dicts `{"seq", "topic", "schema", "log_time", "publish_time", "payload"}`; `scorbot.session.analysis.event_summary(event) -> str`; `scorbot.state.JOINTS`; `examples.make_synthetic_session.write_synthetic_session(root) -> Path`.
- Produces:
  - `Item(path: str, kind: str, value: object, time_s: float | None = None, seq: int | None = None, level: str | None = None)`, a frozen dataclass. `kind` is one of `"scalar"`, `"text_log"`, `"image"`, `"boxes"`, `"document"`. For an image the `value` is `{"contents": bytes, "media_type": str}`; for boxes it is `{"xyxy": [x0, y0, x1, y1], "label": str}`.
  - `session_items(session) -> list[Item]`
  - `info_markdown(session) -> str`
  - `RerunUnavailable(RuntimeError)`
  - `require_rerun()` returns the `rerun` module or raises `RerunUnavailable('Viewing needs Rerun: pip install -e ".[viz]"')`.
  - `APPLICATION_ID = "scorbot_session"`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_rerun_view.py`:

```python
"""Session -> Rerun mapping; runs without rerun installed."""

from pathlib import Path
import tempfile
import unittest

from scorbot.session.replay import Finding, Session, load_session
from scorbot.session.rerun_view import Item, info_markdown, session_items

T0 = 1_700_000_000_000_000_000


def event(seq, topic, ms, **payload):
    return {"seq": seq, "topic": topic, "schema": None, "log_time": T0 + ms * 1_000_000,
            "publish_time": T0 + ms * 1_000_000, "payload": payload}


def session(events=(), data_source="real", findings=()):
    meta = {"session_id": "s1", "data_source": data_source, "robot_id": "arm-1",
            "operator": "XX", "task": "bench", "code": {"git_commit": "abc", "git_dirty": False}}
    return Session(Path("s1"), meta, list(events), list(findings))


def by_path(items, path):
    return [item for item in items if item.path == path]


class SessionItemsTests(unittest.TestCase):
    def test_state_counts_prefer_signed_and_skip_bad_values(self):
        items = session_items(session([
            event(0, "/robot/state", 0, encoder_counts={"base": 65530, "shoulder": 7},
                  signed_encoder_counts={"base": -5, "shoulder": 7},
                  controller_error_counts={"base": 2}, home_switch_bits=4),
            event(1, "/robot/state", 250, encoder_counts={"base": 12, "shoulder": None,
                                                           "elbow": "x", "gripper": True}),
        ]))
        base = by_path(items, "state/counts/base")
        self.assertEqual([(i.value, i.time_s, i.seq) for i in base],
                         [(-5.0, 0.0, 0), (12.0, 0.25, 1)])
        self.assertEqual(by_path(items, "state/counts/shoulder")[0].value, 7.0)
        self.assertEqual(len(by_path(items, "state/counts/shoulder")), 1)   # None skipped
        self.assertEqual(by_path(items, "state/counts/elbow"), [])            # string skipped
        self.assertEqual(by_path(items, "state/counts/gripper"), [])          # bool skipped
        self.assertEqual(by_path(items, "state/controller_error/base")[0].value, 2.0)
        self.assertEqual(by_path(items, "state/home_switch_bits")[0].value, 4.0)
        self.assertTrue(all(i.kind == "scalar" for i in items if i.path.startswith("state/")))

    def test_event_log_levels_and_text(self):
        items = session_items(session([
            event(0, "/robot/command", 0, command_id="c1", kind="jog_joint", params={"joint": "base"}),
            event(1, "/robot/command_result", 10, command_id="c1", status="timeout"),
            event(2, "/robot/command_result", 20, command_id="c2", status="completed"),
            event(3, "/operator/decision", 30, choice="MOVE"),
            event(4, "/session/note", 40, text="n" * 80),
            event(5, "/session/fault", 50, message="stale feedback"),
        ]))
        log = by_path(items, "events")
        self.assertEqual([i.level for i in log], ["INFO", "WARN", "INFO", "INFO", "INFO", "ERROR"])
        self.assertTrue(log[0].value.startswith("/robot/command: c1 jog_joint"))
        self.assertEqual(log[4].value, "/session/note: " + "n" * 80)   # notes not truncated
        self.assertIn("FAULT stale feedback", log[5].value)
        self.assertTrue(all(i.kind == "text_log" for i in log))

    def test_malformed_payload_is_logged_not_raised(self):
        items = session_items(session([event(0, "/robot/command", 0, command_id="c1")]))
        self.assertEqual(by_path(items, "events")[0].value, "/robot/command: (unreadable payload)")

    def test_bad_image_data_is_skipped(self):
        items = session_items(session([
            event(0, "/camera/cam0/image", 0, data="!!not base64!!", format="png"),
        ]))
        self.assertEqual(by_path(items, "camera/cam0/image"), [])

    def test_info_document_labels_source_and_findings(self):
        empty = session_items(session(data_source="simulated",
                                      findings=[Finding("error", "CRC mismatch")]))
        self.assertEqual(len(empty), 1)
        info = empty[0]
        self.assertEqual((info.path, info.kind, info.time_s), ("session/info", "document", None))
        self.assertIn("# SIMULATED session", info.value)
        self.assertIn("not evidence from the physical arm", info.value)
        self.assertIn("ERROR: CRC mismatch", info.value)
        real = info_markdown(session())
        self.assertIn("# REAL session", real)
        self.assertNotIn("not evidence", real)
        self.assertIn("No findings.", real)

    def test_synthetic_session_images_and_detections(self):
        from examples.make_synthetic_session import write_synthetic_session
        with tempfile.TemporaryDirectory() as directory:
            loaded = load_session(write_synthetic_session(directory))
        items = session_items(loaded)
        images = by_path(items, "camera/cam0/image")
        self.assertTrue(images)
        self.assertEqual(images[0].value["media_type"], "image/png")
        self.assertTrue(images[0].value["contents"].startswith(b"\x89PNG"))
        boxes = by_path(items, "camera/cam0/image/detections")
        self.assertTrue(boxes)
        self.assertEqual(len(boxes[0].value["xyxy"]), 4)
        self.assertEqual(boxes[0].value["label"], "red_block 0.90")
        self.assertIn("# SYNTHETIC session", items[0].value)
        times = [i.time_s for i in items if i.time_s is not None]
        self.assertEqual(min(times), 0.0)
        self.assertIsInstance(items[0], Item)


if __name__ == "__main__":
    unittest.main()
```


- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m unittest tests.test_rerun_view -v`
Expected: ERROR, `ModuleNotFoundError: No module named 'scorbot.session.rerun_view'`.

- [ ] **Step 3: Implement `scorbot/session/rerun_view.py` (mapping part)**

```python
"""Show a recorded session in Rerun (optional: pip install -e ".[viz]").

session_items() is pure and needs no Rerun, so CI tests it everywhere and a
later live source can produce the same Items. send() and view() are the only
code that touches the Rerun SDK. Nothing here opens USB or writes a session.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import math
import numbers

from ..state import JOINTS
from .analysis import event_summary

APPLICATION_ID = "scorbot_session"
_LOG_TOPICS = ("/robot/command", "/robot/command_result", "/operator/decision",
               "/session/note", "/session/fault")


class RerunUnavailable(RuntimeError):
    """rerun-sdk is not installed."""


@dataclass(frozen=True)
class Item:
    """One thing to log: where, what kind, the value, and when (None = static)."""

    path: str
    kind: str   # scalar | text_log | image | boxes | document
    value: object
    time_s: float | None = None
    seq: int | None = None
    level: str | None = None


def require_rerun():
    try:
        import rerun
    except ImportError as error:
        raise RerunUnavailable('Viewing needs Rerun: pip install -e ".[viz]"') from error
    return rerun


def _number(value) -> bool:
    return (isinstance(value, numbers.Real) and not isinstance(value, bool)
            and math.isfinite(value))


def info_markdown(session) -> str:
    meta = session.metadata
    source = str(meta.get("data_source") or "unknown").upper()
    code = meta.get("code") or {}
    lines = [f"# {source} session", ""]
    if source != "REAL":
        lines += [f"**{source} data: not evidence from the physical arm.**", ""]
    for label, key in (("Session", "session_id"), ("Robot", "robot_id"),
                       ("Operator", "operator"), ("Task", "task"),
                       ("Start pose", "start_pose_note"), ("Started", "started_utc")):
        lines.append(f"- {label}: {meta.get(key) or '-'}")
    dirty = " (uncommitted changes)" if code.get("git_dirty") else ""
    lines.append(f"- Code commit: {code.get('git_commit') or '-'}{dirty}")
    lines += ["", "## Integrity", ""]
    lines += ([f"- {f.level.upper()}: {f.message}" for f in session.findings]
              or ["- No findings."])
    return "\n".join(lines)


def _state_items(payload, t, seq):
    counts = payload.get("signed_encoder_counts")
    if not isinstance(counts, dict):
        counts = payload.get("encoder_counts")
    for group, values in (("counts", counts),
                          ("controller_error", payload.get("controller_error_counts"))):
        if isinstance(values, dict):
            for joint in JOINTS:
                if _number(values.get(joint)):
                    yield Item(f"state/{group}/{joint}", "scalar", float(values[joint]), t, seq)
    if _number(payload.get("home_switch_bits")):
        yield Item("state/home_switch_bits", "scalar", float(payload["home_switch_bits"]), t, seq)


def _log_item(event, t, seq):
    topic, payload = event["topic"], event["payload"]
    try:
        text = payload["text"] if topic == "/session/note" else event_summary(event)
    except (KeyError, TypeError, ValueError):
        text = "(unreadable payload)"
    if topic == "/session/fault":
        level = "ERROR"
    elif topic == "/robot/command_result" and payload.get("status") != "completed":
        level = "WARN"
    else:
        level = "INFO"
    return Item("events", "text_log", f"{topic}: {text}", t, seq, level)


def _camera_item(topic, payload, t, seq):
    _, _, camera_id, kind = topic.split("/", 3)
    if kind == "image":
        try:
            contents = base64.b64decode(payload["data"], validate=True)
        except (KeyError, TypeError, binascii.Error):
            return None
        return Item(f"camera/{camera_id}/image", "image",
                    {"contents": contents, "media_type": f"image/{payload.get('format')}"},
                    t, seq)
    box = payload.get("bbox_xyxy")
    if kind == "detections" and isinstance(box, list) and len(box) == 4 \
            and all(_number(v) for v in box):
        confidence = payload.get("confidence")
        label = str(payload.get("label", ""))
        if _number(confidence):
            label = f"{label} {confidence:.2f}"
        return Item(f"camera/{camera_id}/image/detections", "boxes",
                    {"xyxy": [float(v) for v in box], "label": label}, t, seq)
    return None


def session_items(session) -> list[Item]:
    """Everything to show for one session, in recorded order."""
    items = [Item("session/info", "document", info_markdown(session))]
    start = min((e["publish_time"] for e in session.events), default=0)
    for event in session.events:
        topic, payload = event["topic"], event["payload"]
        t, seq = (event["publish_time"] - start) / 1e9, event["seq"]
        if topic == "/robot/state":
            items.extend(_state_items(payload, t, seq))
        elif topic in _LOG_TOPICS:
            items.append(_log_item(event, t, seq))
        elif topic.startswith("/camera/") and topic.count("/") == 3:
            item = _camera_item(topic, payload, t, seq)
            if item is not None:
                items.append(item)
    return items
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m unittest tests.test_rerun_view -v`
Expected: 6 tests OK. Then run `.venv/Scripts/python.exe -m ruff check scorbot tests` and expect `All checks passed!`.

- [ ] **Step 5: Confirm the package still imports without rerun**

Run: `.venv/Scripts/python.exe -m unittest tests.test_simulated.SimulatedRobotTests.test_import_loads_no_usb_and_leaves_sys_path_alone -v`. If that id does not exist, run `grep -n "def test_import_loads_no_usb" tests/test_simulated.py` to find the class.
Expected: OK.

- [ ] **Step 6: Commit**

```bash
git add scorbot/session/rerun_view.py tests/test_rerun_view.py
git commit -m "Map recorded sessions to Rerun items without importing Rerun" -m "The mapping is pure so CI tests it without the 145 MB viewer wheel, and a later live feed can emit the same items. Bad values, bad image data and malformed payloads are skipped or labelled, never fatal, and every view states whether the data is real or simulated."
```

---

### Task 3: Rerun sender, `view` command, extra and docs

**Files:**
- Modify: `scorbot/session/rerun_view.py` (add `send`, `view`)
- Modify: `scorbot/session/__main__.py` (docstring, `SUBCOMMANDS`, parser, `_view`, dispatch)
- Modify: `pyproject.toml` (add the `viz` extra)
- Modify: `docs/EXPERIMENT_RECORDING.md` (the Rerun bullet, around line 73)
- Modify: `scorbot/CLAUDE.md` (the `scorbot/session/` section)
- Test: `tests/test_rerun_view.py`

**Interfaces:**
- Consumes: `Item`, `session_items`, `require_rerun`, `RerunUnavailable` and `APPLICATION_ID` from Task 2; `_open(path)` and `_findings(session)` in `scorbot/session/__main__.py`.
- Produces: `send(items, recording) -> None`; `view(session, save_path: Path | None = None) -> None`; CLI `python -m scorbot.session view PATH [--save OUT.rrd]`.

- [ ] **Step 1: Install Rerun locally to check the API**

Run: `.venv/Scripts/python.exe -m pip install "rerun-sdk>=0.38,<0.39"`, then `.venv/Scripts/python.exe -c "import rerun as rr; print(rr.__version__); print(rr.Scalars, rr.TextLog, rr.EncodedImage, rr.Boxes2D, rr.Box2DFormat.XYXY, rr.TextDocument, rr.MediaType.MARKDOWN, rr.RecordingStream)"`
Expected: `0.38.x`, and every name resolves. If a name differs, use the 0.38 name in Step 3 and note the difference in the commit body.

- [ ] **Step 2: Write the failing CLI tests**

Append to `tests/test_rerun_view.py`, before `if __name__ == "__main__":`:

```python
import contextlib
import importlib.util
import io
from unittest.mock import patch

from scorbot.session.__main__ import main as session_main
from scorbot.session.rerun_view import RerunUnavailable


class ViewCommandTests(unittest.TestCase):
    def setUp(self):
        from examples.make_synthetic_session import write_synthetic_session
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.session_path = write_synthetic_session(self.root / "sessions")

    def tearDown(self):
        self._tmp.cleanup()

    def run_main(self, *args):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = session_main(["view", *map(str, args)])
        return code, err.getvalue()

    def test_existing_save_path_is_refused_and_kept(self):
        target = self.root / "out.rrd"
        target.write_bytes(b"keep")
        code, err = self.run_main(self.session_path, "--save", target)
        self.assertEqual(code, 2)
        self.assertIn("already exists", err)
        self.assertEqual(target.read_bytes(), b"keep")

    def test_missing_rerun_prints_install_hint(self):
        with patch("scorbot.session.rerun_view.require_rerun",
                   side_effect=RerunUnavailable('Viewing needs Rerun: pip install -e ".[viz]"')):
            code, err = self.run_main(self.session_path, "--save", self.root / "new.rrd")
        self.assertEqual(code, 2)
        self.assertIn('pip install -e ".[viz]"', err)
        self.assertNotIn("Traceback", err)
        self.assertFalse((self.root / "new.rrd").exists())

    def test_unopenable_session_exits_2(self):
        code, _ = self.run_main(self.root / "missing", "--save", self.root / "x.rrd")
        self.assertEqual(code, 2)

    def test_integrity_errors_exit_1_after_viewing(self):
        damaged = Session(Path("d"), {"data_source": "real"}, [],
                          [Finding("error", "CRC mismatch")])
        with patch("scorbot.session.__main__._open", return_value=damaged), \
                patch("scorbot.session.rerun_view.require_rerun"), \
                patch("scorbot.session.rerun_view.view") as view:
            code, err = self.run_main("d", "--save", self.root / "d.rrd")
        self.assertEqual(code, 1)
        view.assert_called_once()
        self.assertIs(view.call_args.args[0], damaged)

    @unittest.skipUnless(importlib.util.find_spec("rerun"), "rerun-sdk not installed")
    def test_save_writes_a_recording(self):
        target = self.root / "synthetic.rrd"
        code, err = self.run_main(self.session_path, "--save", target)
        self.assertEqual(code, 0, err)
        self.assertGreater(target.stat().st_size, 0)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m unittest tests.test_rerun_view -v`
Expected: the new tests fail with argparse `invalid choice: 'view'`, which is `SystemExit 2` inside `main`, or they fail on the missing `view` attribute. Either way they are not passing.

- [ ] **Step 4: Add `send` and `view` to `rerun_view.py`**

Append:

```python
def _archetype(rr, item: Item):
    value = item.value
    if item.kind == "scalar":
        return rr.Scalars(value)
    if item.kind == "text_log":
        return rr.TextLog(value, level=item.level)
    if item.kind == "image":
        return rr.EncodedImage(contents=value["contents"], media_type=value["media_type"])
    if item.kind == "boxes":
        return rr.Boxes2D(array=[value["xyxy"]], array_format=rr.Box2DFormat.XYXY,
                          labels=[value["label"]])
    if item.kind == "document":
        return rr.TextDocument(value, media_type=rr.MediaType.MARKDOWN)
    raise ValueError(f"Unknown item kind: {item.kind}")


def send(items, recording) -> None:
    """Log Items to a Rerun RecordingStream (the only Rerun logging call site)."""
    rr = require_rerun()
    for item in items:
        static = item.time_s is None
        if not static:
            recording.set_time("session_time", duration=item.time_s)
            if item.seq is not None:
                recording.set_time("seq", sequence=item.seq)
        recording.log(item.path, _archetype(rr, item), static=static)


def view(session, save_path=None) -> None:
    """Open one session in the Rerun viewer, or write it to save_path (.rrd)."""
    rr = require_rerun()
    recording_id = session.metadata.get("session_id") or str(session.path)
    recording = rr.RecordingStream(APPLICATION_ID, recording_id=recording_id)
    if save_path is not None:
        recording.save(str(save_path))
    else:
        recording.spawn()
    send(session_items(session), recording)
    recording.flush()
```

If `recording.flush()` in 0.38 needs arguments or is named differently, use the 0.38 equivalent that blocks until the data is written, checked in Step 1.

- [ ] **Step 5: Wire the CLI in `scorbot/session/__main__.py`**

Add to the module docstring, after the `plot` line:

```
  python -m scorbot.session view <session> [--save F] open in Rerun (needs the viz extra)
```

Change `SUBCOMMANDS = ("replay", "list", "export", "compare", "plot")` to
`SUBCOMMANDS = ("replay", "list", "export", "compare", "plot", "view")`.

After the `plotting` parser arguments and before `args = parser.parse_args(argv)`, add:

```python
    viewing = sub.add_parser("view", help="Open one session in Rerun (pip install -e \".[viz]\")")
    viewing.add_argument("path", help="Session folder or session.mcap")
    viewing.add_argument("--save", type=Path, default=None,
                         help="Write a .rrd file instead of opening the viewer (never overwrites)")
```

Add `"view": _view` to the dispatch dict. Add this function after `_plot`:

```python
def _view(args) -> int:
    from . import rerun_view
    if args.save is not None and args.save.exists():
        print(f"{args.save} already exists; choose a new --save name.", file=sys.stderr)
        return 2
    try:
        rerun_view.require_rerun()
    except rerun_view.RerunUnavailable as error:
        print(error, file=sys.stderr)
        return 2
    session = _open(args.path)
    if session is None:
        return 2
    rerun_view.view(session, args.save)
    if args.save is not None:
        print(f"Wrote {args.save}")
    _findings(session)
    return 1 if session.errors else 0
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m unittest tests.test_rerun_view -v`
Expected: all OK, and `test_save_writes_a_recording` runs (not skipped) because Rerun is installed locally.

- [ ] **Step 7: Check the viewer by hand once**

Run: `.venv/Scripts/python.exe examples/make_synthetic_session.py --root rehearsal/rr` and note the printed session path. Then run `.venv/Scripts/python.exe -m scorbot.session view <that path> --save rehearsal/rr/s.rrd`.
Expected: `Wrote rehearsal\rr\s.rrd` and exit 0. Opening the viewer (`rerun rehearsal/rr/s.rrd`, or `view` without `--save`) is left to the user, because an agent session has no display.

- [ ] **Step 8: Extra, docs, module notes**

`pyproject.toml`, after the `plot` extra:

```toml
# Rerun viewer for recorded sessions (large wheel; never needed on the lab PC).
viz = ["rerun-sdk>=0.38,<0.39"]
```

`docs/EXPERIMENT_RECORDING.md`: replace the line `- **Rerun:** not supported yet; a future exporter could feed it.` with:

```markdown
- **Rerun:** `pip install -e ".[viz]"`, then
  `python -m scorbot.session view <session>` opens the viewer, or
  `... view <session> --save run.rrd` writes a file to share (never
  overwrites). Panels: `session/info` (REAL or SIMULATED, identity, integrity
  findings), `state/counts/<joint>` (signed counts), `state/controller_error/<joint>`,
  `state/home_switch_bits`, `events` (commands, results, decisions, notes;
  faults as errors, failed results as warnings) and `camera/<id>/image` with
  detection boxes. Timelines: `session_time` (seconds from the first event,
  observed time) and `seq`. One session per recording, so real and simulated
  runs never share a view. Dragging `session.mcap` into Rerun also works
  (experimental MCAP support) but shows raw JSON, not these panels.
```

`scorbot/CLAUDE.md`, in the `## scorbot/session/` bullet list, add:

```markdown
- `rerun_view.py`: `session_items` is pure (no Rerun import) and is the unit to test; `send`/`view` are the only Rerun call sites. Keep `rerun` imports inside functions; the `viz` extra is optional and not in `dev`.
```

- [ ] **Step 9: Full check**

Run: `.venv/Scripts/python.exe -m compileall -q scorbot openScorbot scripts examples tests` and `.venv/Scripts/python.exe -m ruff check .` and `.venv/Scripts/python.exe -m unittest discover -s tests`
Expected: no compile output, `All checks passed!`, and `OK (skipped=..., expected failures=2)`.

- [ ] **Step 10: Commit**

```bash
git add scorbot/session/rerun_view.py scorbot/session/__main__.py pyproject.toml docs/EXPERIMENT_RECORDING.md scorbot/CLAUDE.md tests/test_rerun_view.py
git commit -m "Add a Rerun viewer for recorded sessions" -m "python -m scorbot.session view shows joint counts, the command and fault log and camera frames on one timeline, or saves a .rrd to share. Rerun stays an optional viz extra pinned to 0.38.x because its API changes between minor releases; send() is the single call site to update. --save never overwrites, and damaged sessions still open but exit 1, like replay."
```
