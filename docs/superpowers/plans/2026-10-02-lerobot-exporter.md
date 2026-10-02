# LeRobot Exporter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `python -m scorbot.lerobot_export` turns completed lab episodes into a LeRobot dataset (state, action, video, task) at a fixed rate, refusing damaged or inconsistent evidence, with atomic publication and provenance.

**Architecture:** `load.py` reads one lab session (lab JSONL, SDK controller events, MCAP, camera index) into plain dataclasses. `checks.py` applies the refusal rules. `frames.py` resamples to the grid. `write.py` (the only module importing `lerobot`) builds the dataset in a temporary folder, verifies it, and renames it into place. All but `write.py` run in the main test suite.

**Tech Stack:** Python 3.10+ core (`mcap`, `numpy`); `lerobot==0.6.1[dataset]` only in `.venv-lerobot` (Python 3.13, CPU torch); `unittest`.

**Spec:** `docs/superpowers/specs/2026-10-02-lerobot-exporter-design.md`

## Global Constraints

- Motors in this order: base, shoulder, elbow, wrist_motor_1, wrist_motor_2. Units: encoder counts from the session home via `signed_count_delta` ("uncalibrated encoder counts from session home"). Never degrees.
- Defaults: `--fps 10`, `--max-frame-gap 0.2` s, target tolerance 20 counts (legacy settle band, `DRIFT_COUNTS`), minimum 2 frames per episode, clock resolution at most 1 ms.
- Real and simulated never share a dataset. Video and no-video episodes never share a dataset. No camera requires `--no-video`.
- Only `write.py` and the round-trip test import `lerobot`; importing `scorbot.lerobot_export` must not.
- No `lerobot` extra in `pyproject.toml` (Ruling: its OpenCV pin conflicts with the `camera` extra in one universal lock). Install command lives in the docs.
- Output folder must not exist; build in `<out>.partial-<hex>`, verify, rename; delete the partial folder on any failure.
- No upload to the Hugging Face Hub.
- Tests that export recorded fixture sessions skip when `time.get_clock_info("monotonic").resolution > 1e-3` (Windows CI on Python 3.10): the clock rule would rightly refuse them.
- Run with `.venv/Scripts/python.exe`; lint `.venv/Scripts/ruff.exe check .`; round trip with `.venv-lerobot/Scripts/python.exe`.
- Commits: imperative sentence-case subject, body explains why, no AI attribution.

## Review Focus

- A lab JSONL whose MCAP session folder was moved or deleted: a clear refusal naming the missing path, not a traceback (Task 1 test `test_missing_session_folder_is_reported`).
- A session where the operator never homed (no `home_complete` row): refused, since counts cannot be made relative to home (Task 2 test `test_session_without_home_is_refused`).
- An episode whose end row is missing (crash mid-episode): skipped as not completed (Task 2 test `test_episode_without_end_is_skipped`).
- A trace packet that fails to decode: skipped, and coverage is judged on the decodable ones (Task 1 test `test_undecodable_trace_packet_is_skipped`).
- `--out` pointing at an existing folder: refused before anything is built (Task 4 test `test_existing_output_is_refused`).

---

### Task 1: Load a lab session

**Files:**
- Create: `scorbot/lerobot_export/__init__.py`, `scorbot/lerobot_export/load.py`
- Create: `tests/lerobot_fixtures.py` (shared simulated-session recorder)
- Modify: `pyproject.toml` (packages list adds `"scorbot.lerobot_export"`)
- Test: `tests/test_lerobot_export.py`

**Interfaces:**
- Produces: `MOTORS` tuple; dataclasses `Jog(index, joint, command_ns, result_ns, sdk_start_raw, deltas, sdk_target_signed, lab_target_signed, trace, trace_dropped)`, `Episode(number, task, status, reason, start_ns, end_ns, camera)`, `LabSessionData(jsonl_path, name, rows, data_source, mcap_folder, mcap, home_raw, states, jogs, lab_command_count, episodes, mcap_episodes, camera, clock_resolution_s, load_errors)`; `load_lab_session(jsonl_path) -> LabSessionData`; `rel(raw, home) -> list[int]`.
- `states` and `Jog.trace` are lists of `(t_ns, raw_counts_dict)`.
- Produces (tests): `tests.lerobot_fixtures.record(root, answers, *, camera=False) -> Path` (lab JSONL path); `paced(key, seconds=0.15)`; constants `TO_LOOP`, `ARM`, `FINISH`.

- [ ] **Step 1: Failing tests.** `tests/lerobot_fixtures.py`:

```python
"""Real simulated lab sessions for exporter tests (LabSession + ScriptedOperator)."""

import time
from pathlib import Path

from scorbot import SimulatedScorbot
from scorbot.lab.operator import ScriptedOperator
from scorbot.lab.profile import LabProfile
from scorbot.lab.session import LabSession
from scorbot.simulated import SimulatedController

PROFILE = LabProfile("lab-er4u-1", "A-12", "C-3", "WinUSB", "SR")
TO_LOOP = ["y"] * 4 + ["n", "g", "pose matches photo", "HOME", "y", "g", "all axes homed", "y"]
ARM = ["a", "door", "y", "g", "ARM"]
FINISH = ["x", "n", "g"]


def paced(key, seconds=0.15):
    """A scripted key pressed after a pause, so jogs land in different grid ticks."""
    def answer():
        time.sleep(seconds)
        return key
    return answer


def record(root, answers, *, camera=False, controller=None, name="s"):
    root = Path(root)
    holder = {}

    def wait_live(then):
        def answer():
            for _ in range(300):
                cam = holder["session"].camera
                if cam is not None and cam.live():
                    break
                time.sleep(0.01)
            return then
        return answer
    answers = [wait_live(a.removeprefix("LIVE:")) if isinstance(a, str) and a.startswith("LIVE:")
               else a for a in answers]
    camera_factory = None
    if camera:
        from scorbot.camera.source import FakeSource
        camera_factory = lambda: FakeSource(pace=True)  # noqa: E731
    ctrl = controller or SimulatedController()
    session = LabSession(
        profile=PROFILE, operator=ScriptedOperator(answers),
        robot_factory=lambda **kw: SimulatedScorbot(controller=ctrl, **kw),
        data_source="simulated", log_path=root / f"{name}.jsonl",
        session_root=root / "sessions", sleep=lambda s: None,
        camera_factory=camera_factory)
    holder["session"] = session
    session.run()
    return root / f"{name}.jsonl"
```

`tests/test_lerobot_export.py`:

```python
"""Session-to-LeRobot exporter: load, checks, frames, dry run. No lerobot needed."""

import json
from pathlib import Path
import tempfile
import unittest

from tests.lerobot_fixtures import ARM, FINISH, TO_LOOP, paced, record

EPISODE = ARM + ["t", paced("r"), "reach left", paced("q"), paced("q"), paced("r"), "t"]


class LoadTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_loads_episodes_jogs_states_and_home(self):
        from scorbot.lerobot_export.load import MOTORS, load_lab_session
        path = record(self.root, TO_LOOP + EPISODE + FINISH)
        data = load_lab_session(path)
        self.assertEqual(data.load_errors, [])
        self.assertEqual(data.data_source, "simulated")
        self.assertEqual(set(data.home_raw), set(MOTORS) | {"gripper"})
        [episode] = data.episodes
        self.assertEqual((episode.number, episode.task, episode.status, episode.camera),
                         (1, "reach left", "completed", False))
        self.assertEqual(len(data.jogs), 2)
        self.assertEqual(data.lab_command_count, 2)
        jog = data.jogs[0]
        self.assertEqual(jog.joint, "base")
        self.assertLess(jog.command_ns, jog.result_ns)
        self.assertEqual(jog.sdk_target_signed["base"], jog.lab_target_signed["base"])
        self.assertTrue(data.states)
        self.assertEqual([e["event"] for e in data.mcap_episodes], ["start", "end"])
        self.assertIsNone(data.camera)

    def test_missing_session_folder_is_reported(self):
        import shutil
        from scorbot.lerobot_export.load import load_lab_session
        path = record(self.root, TO_LOOP + FINISH)
        shutil.rmtree(self.root / "sessions")
        data = load_lab_session(path)
        self.assertTrue(any("MCAP session folder" in e for e in data.load_errors))

    def test_undecodable_trace_packet_is_skipped(self):
        from scorbot.lerobot_export.load import _decode_trace
        good = "00" * 64
        samples = _decode_trace([{"direction": "in", "host_monotonic_ns": 5, "hex": "zz"},
                                 {"direction": "out", "host_monotonic_ns": 6, "hex": good},
                                 {"direction": "in", "host_monotonic_ns": 7, "hex": good}])
        self.assertEqual([t for t, _ in samples], [7])

    def test_rel_is_wrap_aware(self):
        from scorbot.lerobot_export.load import MOTORS, rel
        home = {m: 1 for m in MOTORS}
        raw = dict(home, base=65533)
        self.assertEqual(rel(raw, home)[0], -3)


if __name__ == "__main__":
    unittest.main()
```

Note `decode_state` needs `PACKET_MIN_LENGTH` bytes with valid sign bytes; if 64 zero bytes are rejected (sign byte must be 127/128), build the good packet with `scorbot.simulated.encode_packet({})` instead and adjust the test.

- [ ] **Step 2: Run** `.venv/Scripts/python.exe -m unittest tests.test_lerobot_export` — Expected: ImportError.

- [ ] **Step 3: Implement** `scorbot/lerobot_export/__init__.py`:

```python
"""Lab sessions to LeRobot datasets. Importing this package never imports lerobot."""
```

`scorbot/lerobot_export/load.py`:

```python
"""Read one lab session into plain data: the lab JSONL (primary), the SDK's
controller events (what each jog actually sent, and its USB packets), the
MCAP session and the camera index. Never imports lerobot or USB code."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path

from ..calibration import signed_count_delta
from ..camera.stream import StreamIndex, scan_stream
from ..session.replay import Session, load_session
from ..state import decode_state

MOTORS = ("base", "shoulder", "elbow", "wrist_motor_1", "wrist_motor_2")
CAMERA_ID = "main"


@dataclass
class Jog:
    index: int
    joint: str
    command_ns: int | None = None
    result_ns: int | None = None
    sdk_start_raw: dict = field(default_factory=dict)
    deltas: dict = field(default_factory=dict)
    sdk_target_signed: dict = field(default_factory=dict)
    lab_target_signed: dict | None = None
    trace: list = field(default_factory=list)
    trace_dropped: int | None = None


@dataclass
class Episode:
    number: int
    task: str
    status: str
    reason: str | None
    start_ns: int
    end_ns: int | None
    camera: bool


@dataclass
class LabSessionData:
    jsonl_path: Path
    name: str
    rows: list
    data_source: str | None = None
    mcap_folder: Path | None = None
    mcap: Session | None = None
    home_raw: dict | None = None
    states: list = field(default_factory=list)
    jogs: list = field(default_factory=list)
    lab_command_count: int = 0
    episodes: list = field(default_factory=list)
    mcap_episodes: list = field(default_factory=list)
    camera: StreamIndex | None = None
    clock_resolution_s: float | None = None
    load_errors: list = field(default_factory=list)


def rel(raw: dict, home: dict) -> list[int]:
    """Counts from the session home for each motor, across the 0/65535 wrap."""
    return [signed_count_delta(raw[m], home[m]) for m in MOTORS]


def _read_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def _decode_trace(packets) -> list:
    samples = []
    for packet in packets:
        if packet.get("direction") != "in":
            continue
        try:
            state = decode_state(bytes.fromhex(packet["hex"]), connected=True, enabled=True,
                                 homed=True, fault=None)
        except (ValueError, KeyError, TypeError):
            continue
        samples.append((int(packet["host_monotonic_ns"]), dict(state.encoder_counts)))
    return samples


def _jogs_from_controller(rows) -> list[Jog]:
    jogs: list[Jog] = []
    for row in rows:
        event = row.get("event")
        if event == "motion_preview":
            plan = row.get("plan", {})
            jogs.append(Jog(index=len(jogs), joint=plan.get("joint"),
                            sdk_start_raw=dict(row["state"]["encoder_counts"]),
                            deltas=dict(plan.get("motor_count_deltas", {})),
                            sdk_target_signed=dict(plan.get("target_signed_counts", {}))))
        elif not jogs:
            continue
        elif event == "motion_start":
            jogs[-1].command_ns = row["host_monotonic_ns"]
        elif event == "motion_trace":
            jogs[-1].trace = _decode_trace(row.get("packets", []))
            jogs[-1].trace_dropped = row.get("dropped_packets", 0)
        elif event == "motion_complete":
            jogs[-1].result_ns = row["host_monotonic_ns"]
    return jogs


def _episodes(rows) -> list[Episode]:
    starts, ends = {}, {}
    for row in rows:
        if row["type"] == "episode_start":
            starts[row["episode"]] = row
        elif row["type"] == "episode_end":
            ends[row["episode"]] = row
    episodes = []
    for number, start in sorted(starts.items()):
        end = ends.get(number)
        episodes.append(Episode(number, start["task"],
                                end["status"] if end else "missing end",
                                end.get("reason") if end else "no episode_end row",
                                start["host_monotonic_ns"],
                                end["host_monotonic_ns"] if end else None,
                                bool(start.get("camera"))))
    return episodes


def load_lab_session(jsonl_path) -> LabSessionData:
    path = Path(jsonl_path)
    rows = _read_jsonl(path)
    data = LabSessionData(path, path.stem, rows)
    session_row = next((r for r in rows if r["type"] == "session"), None)
    recorder = next((r for r in rows if r["type"] == "recorder"), None)
    homes = [r for r in rows if r["type"] == "home_complete"]
    data.data_source = session_row.get("data_source") if session_row else None
    data.home_raw = dict(homes[-1]["state"]["encoder_counts"]) if homes else None
    data.episodes = _episodes(rows)
    if session_row and session_row.get("controller_event_log"):
        events_path = path.parent / session_row["controller_event_log"]
        if events_path.is_file():
            data.jogs = _jogs_from_controller(_read_jsonl(events_path))
        else:
            data.load_errors.append(f"SDK controller event log not found: {events_path}")
    if recorder is None:
        data.load_errors.append("no recorder row: the MCAP session is unknown")
        return data
    folder = path.parent / "sessions" / recorder["mcap_session"]
    if not (folder / "session.mcap").is_file():
        data.load_errors.append(f"MCAP session folder not found: {folder}")
        return data
    data.mcap_folder = folder
    data.mcap = load_session(folder)
    data.clock_resolution_s = (data.mcap.metadata.get("clock") or {}).get(
        "monotonic_resolution_s")
    lab_targets = []
    for event in data.mcap.events:
        payload = event["payload"]
        if event["topic"] == "/robot/state":
            t = payload["_rec"].get("observed_monotonic_ns") or payload.get("host_monotonic_ns")
            if t is not None:
                data.states.append((int(t), dict(payload["encoder_counts"])))
        elif event["topic"] == "/robot/command" and payload.get("kind") == "jog_joint":
            lab_targets.append(payload["params"].get("target_signed_counts"))
        elif event["topic"] == "/session/episode":
            data.mcap_episodes.append(payload)
    data.states.sort(key=lambda sample: sample[0])
    data.lab_command_count = len(lab_targets)
    for jog, target in zip(data.jogs, lab_targets):
        jog.lab_target_signed = target
    if (folder / f"camera-{CAMERA_ID}.mcap").is_file():
        data.camera = scan_stream(folder, CAMERA_ID)
    return data
```

Add `"scorbot.lerobot_export"` to the setuptools packages list in `pyproject.toml`.

- [ ] **Step 4: Run** `.venv/Scripts/python.exe -m unittest tests.test_lerobot_export` — Expected: PASS.
- [ ] **Step 5: Commit** — "Read a lab session for dataset export".

---

### Task 2: Refusal checks

**Files:** Create `scorbot/lerobot_export/checks.py`; Test: `tests/test_lerobot_export.py` (class `CheckTests`).

**Interfaces:**
- Consumes: Task 1 dataclasses.
- Produces: `Refusal(scope, session, episode, reason)` with scope in `("export", "session", "episode")`; `TARGET_TOLERANCE_COUNTS = 20`; `MAX_CLOCK_RESOLUTION_S = 1e-3`; `check_export(sessions, *, video: bool) -> list[Refusal]`; `check_session(data, *, video: bool) -> list[Refusal]`; `check_episode(data, episode, *, fps, max_frame_gap_s, video) -> list[Refusal]`; `exportable(data, *, fps, max_frame_gap_s, video) -> (list[Episode], list[Refusal])` (session + episode checks combined).

- [ ] **Step 1: Failing tests.** Hand-built data, so each rule is tested alone. Add to `tests/test_lerobot_export.py`:

```python
def make_data(**overrides):
    """A minimal valid session: home 0, one completed episode 0..1 s, no jogs, no camera."""
    from scorbot.lerobot_export.load import MOTORS, Episode, LabSessionData
    home = {m: 0 for m in MOTORS}
    data = LabSessionData(Path("lab.jsonl"), "lab", [], data_source="simulated",
                          home_raw=home, states=[(0, dict(home))], clock_resolution_s=1e-7,
                          episodes=[Episode(1, "task", "completed", None, 0, 1_000_000_000,
                                            False)],
                          mcap_episodes=[{"episode": 1, "event": "start", "status": None},
                                         {"episode": 1, "event": "end",
                                          "status": "completed"}])
    for key, value in overrides.items():
        setattr(data, key, value)
    return data


def make_jog(command_ns, result_ns, *, index=0, delta=142, trace=(), lab_offset=0):
    from scorbot.lerobot_export.load import MOTORS, Jog
    start = {m: 0 for m in MOTORS}
    return Jog(index, "base", command_ns, result_ns, start, {"base": delta},
               {"base": delta}, {"base": delta + lab_offset, "shoulder": 0},
               list(trace), 0)


class CheckTests(unittest.TestCase):
    def reasons(self, data, *, video=False, fps=10):
        from scorbot.lerobot_export.checks import exportable
        episodes, refusals = exportable(data, fps=fps, max_frame_gap_s=0.2, video=video)
        return episodes, [r.reason for r in refusals]

    def test_clean_session_exports_its_episode(self):
        episodes, reasons = self.reasons(make_data())
        self.assertEqual(len(episodes), 1)
        self.assertEqual(reasons, [])

    def test_session_without_home_is_refused(self):
        _, reasons = self.reasons(make_data(home_raw=None))
        self.assertTrue(any("home" in r for r in reasons))

    def test_coarse_clock_is_refused(self):
        _, reasons = self.reasons(make_data(clock_resolution_s=0.0156))
        self.assertTrue(any("clock" in r for r in reasons))

    def test_failed_session_is_refused(self):
        _, reasons = self.reasons(make_data(rows=[{"type": "session_failed",
                                                   "host_monotonic_ns": 5}]))
        self.assertTrue(any("session_failed" in r for r in reasons))

    def test_episode_without_end_is_skipped(self):
        from scorbot.lerobot_export.load import Episode
        data = make_data(episodes=[Episode(1, "t", "missing end", "no episode_end row", 0,
                                           None, False)])
        episodes, reasons = self.reasons(data)
        self.assertEqual(episodes, [])
        self.assertTrue(any("not completed" in r for r in reasons))

    def test_mcap_episode_marks_must_match(self):
        _, reasons = self.reasons(make_data(mcap_episodes=[]))
        self.assertTrue(any("MCAP" in r for r in reasons))

    def test_fault_inside_window_is_refused(self):
        _, reasons = self.reasons(make_data(rows=[{"type": "counts_drift",
                                                   "host_monotonic_ns": 500_000_000}]))
        self.assertTrue(any("counts_drift" in r for r in reasons))

    def test_lab_and_sdk_targets_must_agree(self):
        far = make_data(jogs=[make_jog(200_000_000, 250_000_000, lab_offset=25)])
        near = make_data(jogs=[make_jog(200_000_000, 250_000_000, lab_offset=3)])
        self.assertTrue(any("target" in r for r in self.reasons(far)[1]))
        self.assertEqual(self.reasons(near)[1], [])

    def test_unmatched_jog_is_refused(self):
        jog = make_jog(200_000_000, 250_000_000)
        jog.lab_target_signed = None
        self.assertTrue(any("matched" in r for r in self.reasons(make_data(jogs=[jog]))[1]))

    def test_two_jogs_in_one_interval_are_refused(self):
        data = make_data(jogs=[make_jog(210_000_000, 220_000_000),
                               make_jog(250_000_000, 260_000_000, index=1)])
        self.assertTrue(any("one grid interval" in r for r in self.reasons(data)[1]))

    def test_video_jog_without_trace_coverage_is_refused(self):
        from scorbot.lerobot_export.load import MOTORS
        slow = make_jog(150_000_000, 450_000_000)
        covered = make_jog(150_000_000, 450_000_000,
                           trace=[(t, {m: 0 for m in MOTORS})
                                  for t in range(160_000_000, 450_000_000, 13_000_000)])
        frames = [{"seq": i, "frame_number": i, "observed_monotonic_ns": i * 33_000_000,
                   "width": 64, "height": 48} for i in range(40)]
        from scorbot.camera.stream import StreamIndex
        camera = StreamIndex("main", {}, frames, [])
        from scorbot.lerobot_export.load import Episode
        episode = [Episode(1, "t", "completed", None, 0, 1_000_000_000, True)]
        bad = make_data(jogs=[slow], camera=camera, episodes=episode)
        good = make_data(jogs=[covered], camera=camera, episodes=episode)
        self.assertTrue(any("packet" in r for r in self.reasons(bad, video=True)[1]))
        self.assertEqual(self.reasons(good, video=True)[1], [])

    def test_frame_gap_is_refused(self):
        from scorbot.camera.stream import StreamIndex
        from scorbot.lerobot_export.load import Episode
        frames = [{"seq": 0, "frame_number": 0, "observed_monotonic_ns": 0, "width": 64,
                   "height": 48},
                  {"seq": 1, "frame_number": 1, "observed_monotonic_ns": 900_000_000,
                   "width": 64, "height": 48}]
        data = make_data(camera=StreamIndex("main", {}, frames, []),
                         episodes=[Episode(1, "t", "completed", None, 0, 1_000_000_000, True)])
        self.assertTrue(any("frame gap" in r for r in self.reasons(data, video=True)[1]))

    def test_too_short_episode_is_refused(self):
        from scorbot.lerobot_export.load import Episode
        data = make_data(episodes=[Episode(1, "t", "completed", None, 0, 50_000_000, False)])
        self.assertTrue(any("too short" in r for r in self.reasons(data)[1]))

    def test_export_level_rules(self):
        from scorbot.lerobot_export.checks import check_export
        real, sim = make_data(data_source="real"), make_data()
        self.assertTrue(any("real and simulated" in r.reason
                            for r in check_export([real, sim], video=False)))
        self.assertTrue(any("--no-video" in r.reason
                            for r in check_export([sim], video=True)))
```

- [ ] **Step 2: Run** `.venv/Scripts/python.exe -m unittest tests.test_lerobot_export.CheckTests` — Expected: ImportError.

- [ ] **Step 3: Implement** `scorbot/lerobot_export/checks.py`:

```python
"""Refusal rules for dataset export (spec section 2). Pure functions over loaded data."""

from __future__ import annotations

from dataclasses import dataclass

TARGET_TOLERANCE_COUNTS = 20   # the legacy settle band (lab DRIFT_COUNTS)
MAX_CLOCK_RESOLUTION_S = 1e-3
MIN_FRAMES = 2
FAULT_ROWS = ("jog_failed", "counts_drift")


@dataclass(frozen=True)
class Refusal:
    scope: str
    session: str
    episode: int | None
    reason: str


def grid(start_ns: int, end_ns: int, fps: int) -> list[int]:
    step = 1e9 / fps
    count = int((end_ns - start_ns) // step) + 1
    return [start_ns + round(k * step) for k in range(count)]


def check_export(sessions, *, video: bool) -> list[Refusal]:
    refusals = []
    sources = {s.data_source for s in sessions}
    if len(sources) > 1:
        refusals.append(Refusal("export", "-", None, f"inputs mix real and simulated "
                                                     f"sources {sorted(map(str, sources))}"))
    cameras = {e.camera for s in sessions for e in s.episodes if e.status == "completed"}
    if video and False in cameras:
        refusals.append(Refusal("export", "-", None, "some completed episodes have no "
                                                     "camera; export them with --no-video"))
    if video:
        sizes = {(f["width"], f["height"]) for s in sessions if s.camera
                 for f in s.camera.frames[:1]}
        if len(sizes) > 1:
            refusals.append(Refusal("export", "-", None, f"camera sizes differ: {sizes}"))
    return refusals


def check_session(data, *, video: bool) -> list[Refusal]:
    def refuse(reason):
        return Refusal("session", data.name, None, reason)
    out = [refuse(error) for error in data.load_errors]
    if data.home_raw is None:
        out.append(refuse("no home_complete row: counts cannot be made relative to home"))
    if data.mcap is not None and data.mcap.errors:
        out.append(refuse("MCAP session has integrity errors: "
                          + "; ".join(f.message for f in data.mcap.errors)))
    if any(row.get("type") == "session_failed" for row in data.rows):
        out.append(refuse("lab session_failed row"))
    if data.clock_resolution_s is None or data.clock_resolution_s > MAX_CLOCK_RESOLUTION_S:
        out.append(refuse(f"clock resolution {data.clock_resolution_s} s is coarser than "
                          f"{MAX_CLOCK_RESOLUTION_S} s"))
    if video and data.camera is not None and data.camera.errors:
        out.append(refuse("camera stream has errors: "
                          + "; ".join(f.message for f in data.camera.errors)))
    return out


def _in_window(t, episode):
    return t is not None and episode.start_ns <= t <= episode.end_ns


def check_episode(data, episode, *, fps, max_frame_gap_s, video) -> list[Refusal]:
    def refuse(reason):
        return Refusal("episode", data.name, episode.number, reason)
    if episode.status != "completed":
        return [refuse(f"not completed ({episode.status}: {episode.reason})")]
    out = []
    marks = {(m.get("episode"), m.get("event"), m.get("status")) for m in data.mcap_episodes}
    if ((episode.number, "start", None) not in marks
            or (episode.number, "end", "completed") not in marks):
        out.append(refuse("MCAP lacks matching /session/episode start and completed end"))
    for row in data.rows:
        if row.get("type") in FAULT_ROWS and _in_window(row.get("host_monotonic_ns"), episode):
            out.append(refuse(f"{row['type']} inside the episode"))
    ticks = grid(episode.start_ns, episode.end_ns, fps)
    if len(ticks) < MIN_FRAMES:
        out.append(refuse(f"too short: {len(ticks)} frame(s) at {fps} fps"))
    step = 1e9 / fps
    jogs = [j for j in data.jogs if _in_window(j.command_ns, episode)]
    for jog in jogs:
        if jog.lab_target_signed is None or jog.result_ns is None:
            out.append(refuse(f"jog {jog.index} could not be matched to its SDK record"))
            continue
        moving = jog.deltas.keys()
        if any(abs(jog.lab_target_signed.get(m, 10**9) - jog.sdk_target_signed.get(m, 0))
               > TARGET_TOLERANCE_COUNTS for m in moving):
            out.append(refuse(f"jog {jog.index}: lab and SDK target differ by more than "
                              f"{TARGET_TOLERANCE_COUNTS} counts"))
    commands = sorted(j.command_ns for j in jogs)
    for first, second in zip(commands, commands[1:]):
        if second - first < step:
            out.append(refuse("two jogs commanded within one grid interval"))
            break
    if video:
        out.extend(refuse(r) for r in _video_problems(data, episode, ticks, step, jogs,
                                                      max_frame_gap_s))
    return out


def _video_problems(data, episode, ticks, step, jogs, max_frame_gap_s):
    problems = []
    if data.camera is None or not data.camera.frames:
        return ["no camera frames for a video episode"]
    for jog in jogs:
        inside = [t for t in ticks if jog.command_ns < t < (jog.result_ns or t)]
        if not inside:
            continue
        if jog.trace_dropped:
            problems.append(f"jog {jog.index}: trace dropped packets during motion")
            continue
        times = [t for t, _ in jog.trace]
        for t in inside:
            if not any(t - step < s <= t for s in times):
                problems.append(f"jog {jog.index}: no state packet within one grid interval "
                                "of a tick during motion")
                break
    stamps = [f["observed_monotonic_ns"] for f in data.camera.frames]
    limit = max_frame_gap_s * 1e9
    for t in ticks:
        before = [s for s in stamps if s <= t]
        if not before or t - before[-1] > limit:
            problems.append(f"frame gap: no frame within {max_frame_gap_s} s before a tick")
            break
    return problems


def exportable(data, *, fps, max_frame_gap_s, video):
    refusals = check_session(data, video=video)
    if refusals:
        return [], refusals
    kept = []
    for episode in data.episodes:
        problems = check_episode(data, episode, fps=fps, max_frame_gap_s=max_frame_gap_s,
                                 video=video)
        if problems:
            refusals.extend(problems)
        else:
            kept.append(episode)
    return kept, refusals
```

- [ ] **Step 4: Run** the CheckTests — Expected: PASS. Also run LoadTests.
- [ ] **Step 5: Commit** — "Refuse episodes whose evidence cannot make a trustworthy dataset".

---

### Task 3: Resampling

**Files:** Create `scorbot/lerobot_export/frames.py`; Test: `tests/test_lerobot_export.py` (class `FrameTests`).

**Interfaces:**
- Consumes: Task 1 data, Task 2 `grid`.
- Produces: `EpisodeFrames(session, episode, task, times_ns, state, action, frame_seq)`; `resample(data, episode, *, fps, video) -> EpisodeFrames`. `state`, `action`: lists of 5-float lists; `frame_seq`: list of camera `seq` or None.

- [ ] **Step 1: Failing tests:**

```python
class FrameTests(unittest.TestCase):
    def frames(self, data, video=False, fps=10):
        from scorbot.lerobot_export.frames import resample
        return resample(data, data.episodes[0], fps=fps, video=video)

    def test_grid_and_state_hold_at_rest(self):
        out = self.frames(make_data())
        self.assertEqual(len(out.times_ns), 11)
        self.assertTrue(all(row == [0.0] * 5 for row in out.state))
        self.assertEqual(out.action, out.state)
        self.assertEqual(out.task, "task")

    def test_short_jog_labels_exactly_one_tick(self):
        from scorbot.lerobot_export.load import MOTORS
        after = dict({m: 0 for m in MOTORS}, base=142)
        jog = make_jog(205_000_000, 215_000_000)
        # The post-jog reading arrives late (350 ms) so a target label is visible.
        data = make_data(jogs=[jog], states=[(0, {m: 0 for m in MOTORS}), (350_000_000, after)])
        out = self.frames(data)
        labelled = [k for k, row in enumerate(out.action) if row != out.state[k]]
        self.assertEqual(labelled, [3])              # 300 ms: first tick after the command
        self.assertEqual(out.action[3][0], 142.0)

    def test_action_is_target_while_in_flight_and_state_follows_trace(self):
        from scorbot.lerobot_export.load import MOTORS
        rest = {m: 0 for m in MOTORS}
        trace = [(t, dict(rest, base=(t - 150_000_000) // 3_000_000))
                 for t in range(160_000_000, 450_000_000, 13_000_000)]
        jog = make_jog(150_000_000, 450_000_000, trace=trace)
        data = make_data(jogs=[jog], states=[(0, rest), (451_000_000, dict(rest, base=142))])
        out = self.frames(data)
        self.assertEqual([row[0] for row in out.action[2:5]], [142.0] * 3)
        self.assertTrue(0 < out.state[3][0] < 142)
        self.assertEqual(out.action[6][0], out.state[6][0])

    def test_state_across_the_wrap_is_continuous(self):
        from scorbot.lerobot_export.load import MOTORS
        home = dict({m: 0 for m in MOTORS}, base=1)
        data = make_data(home_raw=home, states=[(0, dict(home, base=65533))])
        self.assertEqual(self.frames(data).state[0][0], -3.0)

    def test_frame_index_is_latest_at_or_before_tick(self):
        from scorbot.camera.stream import StreamIndex
        from scorbot.lerobot_export.load import Episode
        frames = [{"seq": i, "frame_number": i, "observed_monotonic_ns": i * 40_000_000,
                   "width": 64, "height": 48} for i in range(30)]
        data = make_data(camera=StreamIndex("main", {}, frames, []),
                         episodes=[Episode(1, "t", "completed", None, 0, 1_000_000_000, True)])
        out = self.frames(data, video=True)
        self.assertEqual(out.frame_seq[:4], [0, 2, 5, 7])   # 0, 100, 200, 300 ms
```

- [ ] **Step 2: Run** FrameTests — Expected: ImportError.

- [ ] **Step 3: Implement** `scorbot/lerobot_export/frames.py`:

```python
"""Resample one checked episode onto a fixed-rate grid (spec section 3).

state: latest robot reading at or before each tick (MCAP states plus the
decoded USB packets recorded during each jog). action: the target the SDK
sent while a jog is in flight or was commanded since the previous tick, else
the current state. image: the latest camera frame at or before the tick.
All counts are from the session home, uncalibrated.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

from .checks import grid
from .load import MOTORS, rel


@dataclass
class EpisodeFrames:
    session: str
    episode: int
    task: str
    times_ns: list
    state: list
    action: list
    frame_seq: list | None


def _latest(times, values, t):
    index = bisect_right(times, t) - 1
    return values[index] if index >= 0 else None


def resample(data, episode, *, fps: int, video: bool) -> EpisodeFrames:
    home = data.home_raw
    samples = sorted([*data.states, *(s for jog in data.jogs for s in jog.trace)],
                     key=lambda sample: sample[0])
    times = [t for t, _ in samples]
    values = [[float(v) for v in rel(raw, home)] for _, raw in samples]
    step = 1e9 / fps
    jogs = [j for j in data.jogs if j.command_ns is not None]
    targets = {j.index: [float(v + j.deltas.get(m, 0)) for m, v in
                         zip(MOTORS, rel(j.sdk_start_raw, home))] for j in jogs}
    ticks = grid(episode.start_ns, episode.end_ns, fps)
    state, action = [], []
    for t in ticks:
        current = _latest(times, values, t)
        state.append(current)
        label = current
        for jog in jogs:
            in_flight = jog.command_ns <= t < (jog.result_ns or t)
            just_commanded = t - step < jog.command_ns <= t
            if in_flight or just_commanded:
                label = targets[jog.index]
        action.append(label)
    frame_seq = None
    if video and data.camera is not None:
        stamps = [f["observed_monotonic_ns"] for f in data.camera.frames]
        seqs = [f["seq"] for f in data.camera.frames]
        frame_seq = [_latest(stamps, seqs, t) for t in ticks]
    return EpisodeFrames(data.name, episode.number, episode.task, ticks, state, action,
                         frame_seq)
```

- [ ] **Step 4: Run** FrameTests and the whole file — Expected: PASS.
- [ ] **Step 5: Commit** — "Resample checked episodes onto a fixed-rate grid".

---

### Task 4: Writer, CLI and dry run

**Files:** Create `scorbot/lerobot_export/write.py`, `scorbot/lerobot_export/__main__.py`; Test: `tests/test_lerobot_export.py` (class `CliTests`), `tests/test_lerobot_export_write.py` (round trip, skipped without lerobot).

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces: `plan_export(paths, *, fps, max_frame_gap_s, video) -> ExportPlan(sessions, episodes: list[(LabSessionData, EpisodeFrames)], refusals)`; `write_dataset(plan, out: Path, repo_id: str, fps: int, video: bool) -> Path`; `main(argv=None) -> int`.

- [ ] **Step 1: Failing tests.** Append to `tests/test_lerobot_export.py`:

```python
class CliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, *args):
        import contextlib
        import io
        from scorbot.lerobot_export.__main__ import main
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main([str(a) for a in args])
        return code, out.getvalue()

    def test_dry_run_lists_kept_and_refused_and_writes_nothing(self):
        path = record(self.root, TO_LOOP + ARM + ["t", paced("r"), "reach", paced("q"),
                                                  paced("r"), paced("r"), paced("z")]
                      + FINISH)
        code, text = self.run_cli(path, "--out", self.root / "ds", "--repo-id", "local/test",
                                  "--no-video", "--dry-run")
        self.assertEqual(code, 0, text)
        self.assertIn("KEEP", text)
        self.assertIn("SKIP", text)
        self.assertIn("SIMULATED", text)
        self.assertFalse((self.root / "ds").exists())

    def test_existing_output_is_refused(self):
        path = record(self.root, TO_LOOP + EPISODE + FINISH)
        (self.root / "ds").mkdir()
        code, text = self.run_cli(path, "--out", self.root / "ds", "--repo-id", "local/t",
                                  "--no-video")
        self.assertEqual(code, 1)
        self.assertIn("already exists", text)

    def test_no_camera_needs_no_video_flag(self):
        path = record(self.root, TO_LOOP + EPISODE + FINISH)
        code, text = self.run_cli(path, "--out", self.root / "ds", "--repo-id", "local/t",
                                  "--dry-run")
        self.assertEqual(code, 1)
        self.assertIn("--no-video", text)

    def test_importing_the_package_does_not_import_lerobot(self):
        import subprocess
        import sys
        code = ("import sys, scorbot.lerobot_export.__main__, scorbot.lerobot_export.checks;"
                "assert 'lerobot' not in sys.modules")
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
```

`tests/test_lerobot_export_write.py`:

```python
"""Round trip through the real LeRobot writer. Runs only where lerobot is installed:
.venv-lerobot/Scripts/python.exe -m unittest tests.test_lerobot_export_write"""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from tests.lerobot_fixtures import ARM, FINISH, TO_LOOP, paced, record

HAS_LEROBOT = importlib.util.find_spec("lerobot") is not None


@unittest.skipUnless(HAS_LEROBOT, "lerobot not installed (see docs/LEROBOT_EXPORT.md)")
class RoundTripTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_video_episode_round_trip(self):
        from lerobot.datasets.lerobot_dataset import LeRobotDataset
        from scorbot.lerobot_export.__main__ import main
        path = record(self.root, TO_LOOP + ARM + ["t", "LIVE:r", "reach left", paced("q"),
                                                  paced("q", 0.3), paced("r", 0.3), "t"]
                      + FINISH, camera=True)
        out = self.root / "dataset"
        self.assertEqual(main([str(path), "--out", str(out), "--repo-id", "local/scorbot"]), 0)
        self.assertFalse(any(p.name.startswith("dataset.partial") for p in self.root.iterdir()))
        dataset = LeRobotDataset("local/scorbot", root=out)
        self.assertEqual(dataset.num_episodes, 1)
        self.assertIn("observation.images.main", dataset.features)
        first = dataset[0]
        self.assertEqual(first["task"], "reach left")
        # Independent check: the SDK's own first preview gives the first jog's target.
        events = [json.loads(line) for line in
                  path.with_name(path.stem + ".controller.jsonl").read_text("utf-8").splitlines()]
        preview = next(e for e in events if e["event"] == "motion_preview")
        delta = preview["plan"]["motor_count_deltas"]["base"]
        actions = [float(dataset[i]["action"][0]) for i in range(dataset.num_frames)]
        self.assertIn(float(delta), actions)          # home 0: target = delta from home
        provenance = json.loads((out / "scorbot_provenance.json").read_text("utf-8"))
        self.assertEqual(provenance["data_source"], "simulated")
        self.assertEqual(len(provenance["episodes"]), 1)
        self.assertEqual(provenance["image_latency"], "unmeasured")
```

- [ ] **Step 2: Run** the CliTests — Expected: ImportError.

- [ ] **Step 3: Implement** `scorbot/lerobot_export/write.py`:

```python
"""Build a LeRobot dataset from checked, resampled episodes. Imports lerobot.

Built in ``<out>.partial-<hex>``, verified by reopening, then renamed to
``<out>``; any failure removes the partial folder. Never uploads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil

import numpy as np

from .load import MOTORS

EXPORTER_VERSION = 1
UNITS = "uncalibrated encoder counts from session home"


@dataclass
class ExportPlan:
    sessions: list
    episodes: list = field(default_factory=list)   # (LabSessionData, EpisodeFrames)
    refusals: list = field(default_factory=list)


def _features(video: bool, size):
    features = {"observation.state": {"dtype": "float32", "shape": (5,), "names": list(MOTORS)},
                "action": {"dtype": "float32", "shape": (5,), "names": list(MOTORS)}}
    if video:
        width, height = size
        features["observation.images.main"] = {"dtype": "video", "shape": (height, width, 3),
                                               "names": ["height", "width", "channels"]}
    return features


def _frames_for(data, wanted):
    import cv2
    from ..camera.stream import iter_frames
    images, wanted = {}, set(wanted)
    for seq, frame in enumerate(iter_frames(data.mcap_folder, "main")):
        if seq in wanted:
            bgr = cv2.imdecode(np.frombuffer(frame.data, np.uint8), cv2.IMREAD_COLOR)
            images[seq] = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    return images


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_dataset(plan: ExportPlan, out: Path, repo_id: str, fps: int, video: bool) -> Path:
    from lerobot.datasets.lerobot_dataset import LeRobotDataset
    out = Path(out)
    if out.exists():
        raise FileExistsError(f"{out} already exists; choose a new --out")
    partial = out.with_name(f"{out.name}.partial-{secrets.token_hex(4)}")
    try:
        size = None
        if video:
            data, _ = plan.episodes[0]
            size = (data.camera.frames[0]["width"], data.camera.frames[0]["height"])
        dataset = LeRobotDataset.create(repo_id, fps, _features(video, size), root=partial,
                                        robot_type="scorbot_er4u", use_videos=video)
        exported = []
        for index, (data, frames) in enumerate(plan.episodes):
            images = _frames_for(data, frames.frame_seq) if video else {}
            for k in range(len(frames.times_ns)):
                frame = {"observation.state": np.asarray(frames.state[k], np.float32),
                         "action": np.asarray(frames.action[k], np.float32),
                         "task": frames.task}
                if video:
                    frame["observation.images.main"] = images[frames.frame_seq[k]]
                dataset.add_frame(frame)
            dataset.save_episode()
            exported.append({"dataset_episode": index, "session": data.name,
                             "source_episode": frames.episode, "task": frames.task,
                             "frames": len(frames.times_ns)})
        dataset.finalize()
        _write_provenance(partial, plan, exported, fps, video)
        reopened = LeRobotDataset(repo_id, root=partial)
        expected = sum(len(f.times_ns) for _, f in plan.episodes)
        if reopened.num_episodes != len(plan.episodes) or reopened.num_frames != expected:
            raise RuntimeError(f"verification failed: {reopened.num_episodes} episodes, "
                               f"{reopened.num_frames} frames; expected "
                               f"{len(plan.episodes)}, {expected}")
        os.replace(partial, out)
        return out
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise


def _write_provenance(folder: Path, plan: ExportPlan, exported, fps, video):
    sessions = []
    for data in plan.sessions:
        session_row = next((r for r in data.rows if r.get("type") == "session"), {})
        sessions.append({"lab_jsonl": str(data.jsonl_path), "sha256": _sha256(data.jsonl_path),
                         "mcap_session": data.mcap_folder.name if data.mcap_folder else None,
                         "motion_source_sha256": session_row.get("motion_source_sha256"),
                         "software_commit": session_row.get("software_commit"),
                         "camera_frames": len(data.camera.frames) if data.camera else 0})
    source = plan.sessions[0].data_source if plan.sessions else None
    provenance = {"exporter_version": EXPORTER_VERSION,
                  "created_utc": datetime.now(timezone.utc).isoformat(),
                  "fps": fps, "units": UNITS, "calibration": "uncalibrated",
                  "image_latency": "unmeasured" if video else None,
                  "data_source": source, "simulated": source != "real",
                  "video": video, "sessions": sessions, "episodes": exported,
                  "refused": [vars(r) for r in plan.refusals]}
    (folder / "scorbot_provenance.json").write_text(
        json.dumps(provenance, indent=2, allow_nan=False) + "\n", encoding="utf-8")
```

`scorbot/lerobot_export/__main__.py`:

```python
"""python -m scorbot.lerobot_export: lab sessions to a local LeRobot dataset.

Run in .venv-lerobot to write (see docs/LEROBOT_EXPORT.md); --dry-run works in
any environment and writes nothing.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .checks import check_export, exportable
from .frames import resample
from .load import load_lab_session


def plan_export(paths, *, fps, max_frame_gap_s, video):
    from .write import ExportPlan
    sessions = [load_lab_session(p) for p in paths]
    plan = ExportPlan(sessions, refusals=check_export(sessions, video=video))
    if plan.refusals:
        return plan
    for data in sessions:
        kept, refusals = exportable(data, fps=fps, max_frame_gap_s=max_frame_gap_s,
                                    video=video)
        plan.refusals.extend(refusals)
        plan.episodes.extend((data, resample(data, e, fps=fps, video=video)) for e in kept)
    return plan


def _print_plan(plan, fps):
    simulated = any(s.data_source != "real" for s in plan.sessions)
    prefix = "SIMULATED " if simulated else ""
    for data, frames in plan.episodes:
        print(f"{prefix}KEEP  {data.name} episode {frames.episode}: {len(frames.times_ns)} "
              f"frames at {fps} fps, task {frames.task!r}")
    for refusal in plan.refusals:
        kind = "SKIP" if refusal.scope == "episode" else "REFUSE"
        where = refusal.session + (f" episode {refusal.episode}" if refusal.episode else "")
        print(f"{prefix}{kind:<5} {where}: {refusal.reason}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scorbot.lerobot_export",
                                     description=__doc__)
    parser.add_argument("lab_logs", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repo-id", required=True, help="e.g. local/scorbot-reach")
    parser.add_argument("--fps", type=int, default=10)
    parser.add_argument("--max-frame-gap", type=float, default=0.2)
    parser.add_argument("--no-video", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    video = not args.no_video
    if args.out.exists():
        print(f"REFUSE {args.out} already exists; choose a new --out")
        return 1
    plan = plan_export(args.lab_logs, fps=args.fps, max_frame_gap_s=args.max_frame_gap,
                       video=video)
    if video and any(not e.camera for s in plan.sessions for e in s.episodes
                     if e.status == "completed"):
        pass  # reported by check_export
    _print_plan(plan, args.fps)
    if any(r.scope == "export" for r in plan.refusals) or not plan.episodes:
        print("Nothing exported." if not plan.episodes else "Export refused.")
        return 1
    if args.dry_run:
        print(f"Dry run: {len(plan.episodes)} episode(s) would be exported; nothing written.")
        return 0
    from .write import write_dataset
    try:
        out = write_dataset(plan, args.out, args.repo_id, args.fps, video)
    except Exception as error:
        print(f"Export failed, nothing published: {type(error).__name__}: {error}")
        return 1
    print(f"Wrote {len(plan.episodes)} episode(s) to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Remove the no-op `if video and any(...): pass` block during implementation (left visible here only to show the rule lives in `check_export`). Note `check_export` must also refuse when `video` and no completed episode has a camera ("export them with --no-video"): the `False in cameras` test covers the all-no-camera case too.

- [ ] **Step 4: Run** `.venv/Scripts/python.exe -m unittest tests.test_lerobot_export` (PASS) and, after installing the project into the LeRobot env (`.venv/Scripts/uv.exe pip install --python .venv-lerobot/Scripts/python.exe -e . --no-deps` then `... mcap pyusb`), run `.venv-lerobot/Scripts/python.exe -m unittest tests.test_lerobot_export_write -v` — Expected: PASS. If LeRobot's reopen needs extra arguments (for example a `revision` or download flags for a local root), adapt `write.py` and record a ledger ruling.

- [ ] **Step 5: Commit** — "Write lab episodes to a LeRobot dataset, atomically".

---

### Task 5: Docs

**Files:** Create `docs/LEROBOT_EXPORT.md`; modify `docs/PROJECT_LOG.md`, `CLAUDE.md` (repo map row for `scorbot/lerobot_export/`, commands block), roadmap progress line.

- [ ] **Step 1:** `docs/LEROBOT_EXPORT.md`: install (`uv venv .venv-lerobot --python 3.13`, `uv pip install --python .venv-lerobot/Scripts/python.exe "lerobot[dataset]==0.6.1"`, then the project with `-e . --no-deps` plus `mcap pyusb`); usage with `--dry-run` first; what is refused and why (table from the spec); units; limitations (step-and-stop motion, states during a jog only from packets on the real arm, uncalibrated counts, unmeasured image latency and the stopwatch measurement); no upload.
- [ ] **Step 2:** `PROJECT_LOG.md` entry "LeRobot exporter (M1 step 5)" with the Codex findings and the Gemini credit note.
- [ ] **Step 3:** `CLAUDE.md`: repo map row; one command line for the dry run and one for the round-trip test.
- [ ] **Step 4:** Full suite, `ruff check .`, `compileall`, round trip in `.venv-lerobot`; commit — "Document exporting lab sessions to LeRobot".

## Self-review notes

- Spec coverage: input (Task 1, 4), rules table (Task 2; the camera-error and size rules in `check_session`/`check_export`), signals (Task 3), features and provenance and atomic publication (Task 4), tests (all), docs (Task 5).
- Type consistency: `Jog`, `Episode`, `LabSessionData`, `StreamIndex.frames` dict keys (`seq`, `observed_monotonic_ns`, `width`, `height`), `grid`, `resample`, `ExportPlan`, `write_dataset` used with the same names throughout.
