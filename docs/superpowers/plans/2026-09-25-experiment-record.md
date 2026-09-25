# Experiment Record and Replay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record every experiment as one crash-safe MCAP session and replay it without USB hardware.

**Architecture:** A new `scorbot.session` package, independent of USB, with four parts:
- `schemas.py`: the topic contracts.
- `record.py`: the thread-safe `SessionWriter`, which writes unchunked MCAP with JSON messages and flushes after each one.
- `replay.py`: a streaming `load_session` with an integrity report, plus `nearest()`.
- `__main__.py`: the CLI timeline.

Viewers (Foxglove/Lichtblick) read the same file.

**Tech Stack:** Python ≥3.10 stdlib, `mcap>=1.5,<2`, `unittest`.

**Spec:** `docs/superpowers/specs/2026-09-25-experiment-record-design.md`

## Global Constraints

- Python 3.10 compatible: no `datetime.UTC`, `typing.Self`, `tomllib`.
- The only new runtime dependency is `mcap>=1.5,<2`.
- Do not modify `openScorbot/`, `scorbot/robot.py`, or `scorbot/state.py`.
- `import scorbot.session` must not import `usb` or `openScorbot`.
- The writer uses `Writer(f, use_chunking=False)` and flushes after every record.
- `data_source` ∈ `("real", "simulated", "synthetic")`, and it is required.
- `seq` is global, starts at 0, and has no gaps. The raw monotonic values live in `payload["_rec"]`.
- Tests run with `.venv/Scripts/python.exe -m unittest discover -s tests`.

## Review Focus

1. **Unserializable payload.** For example, `log_command(params={"x": object()})` or a NaN. Expected: `SessionError`, no bytes written, and `seq` not consumed, so the next event's `seq` is still contiguous. (Task 2)
2. **An exception raised inside a `with SessionWriter...` block.** Expected: a `/session/fault` event records the exception, the file is closed, and the exception still propagates. (Task 2)
3. **A session root that doesn't exist yet**, nested or with spaces. Expected: parents are created. (Task 2)
4. **`load_session` or the CLI given the `.mcap` file instead of the folder.** Expected: both work. (Task 3)
5. **CLI given a path that doesn't exist.** Expected: a one-line message and exit code 2, no traceback. (Task 4)

---

### Task 1: Packaging and topic schemas

**Files:**
- Modify: `pyproject.toml`
- Modify: `.gitignore` (add `*.mcap`, `sessions/`)
- Create: `scorbot/session/__init__.py`, `scorbot/session/schemas.py`
- Create: `scorbot/session/foxglove_CompressedImage.json`, vendored from foxglove-sdk at commit `dcbc667`
- Test: `tests/test_session.py`

**Interfaces (produced):**
- `SCHEMA_VERSION = 1`, `DATA_SOURCES`, `COMMAND_STATUSES`
- `TOPICS: dict[str, str]` maps each fixed topic to its schema name
- `REQUIRED: dict[str, tuple[str, ...]]` maps each schema name to its required payload keys
- `schema_name_for_topic(topic) -> str | None` also handles `/camera/<id>/image|detections`
- `schema_json(name) -> bytes` returns the JSON Schema bytes to register

- [ ] **Step 1: Write the failing test.** `SchemaTests` covers three things:
  - every schema in `REQUIRED` produces valid JSON whose `required` contains the keys;
  - the Foxglove image schema's required keys are `timestamp`, `frame_id`, `data`, `format`;
  - `schema_name_for_topic("/camera/cam0/image") == "foxglove.CompressedImage"`, and an unknown topic gives `None`.
- [ ] **Step 2:** Run it. It should fail with an `ImportError`.
- [ ] **Step 3:** Implement `schemas.py`. Load the vendored JSON with `importlib.resources.files(__package__)`. The `scorbot.*` schemas are generated as `{"title", "type": "object", "required", "properties": {}}`. Changes to `pyproject.toml`:
  - dependency `"mcap>=1.5,<2"`;
  - add `scorbot.session` to `packages`;
  - `[tool.setuptools.package-data] scorbot = ["session/*.json"]`.
- [ ] **Step 4:** Run the tests. They should pass, and the whole existing suite should still pass.
- [ ] **Step 5:** Commit.

### Task 2: SessionWriter

**Files:**
- Create: `scorbot/session/record.py`
- Test: `tests/test_session.py`

**Interfaces:**
- Consumes: `schemas.*`
- Produces:
  - `SessionError(RuntimeError)`
  - `SessionWriter.create(root, *, data_source, robot_id, controller_id="unknown", operator="", task="", start_pose_note="unmeasured", camera_ids=(), calibration_path=None, usb_driver="unknown") -> SessionWriter`
  - attributes `.path`, `.mcap_path`, `.metadata`
  - `log_state(state, *, raw_packet=None, observed_monotonic_ns=None) -> int`
  - `log_command(kind, params) -> str` (the command_id `cmd-0001`, …)
  - `log_command_result(command_id, status, *, completion_source=None, detail=None) -> int`
  - `log_frame(camera_id, frame_number, data, *, format, width, height, observed_monotonic_ns=None, camera_config_id=None, mount_id=None) -> int`
  - `log_detection(camera_id, frame_number, label, bbox_xyxy, confidence, model_id, *, operator_correction=None, observed_monotonic_ns=None) -> int`
  - `log_decision(choice, *, refers_to_seq=None, reason=None) -> int`
  - `log_fault(message, *, command_id=None) -> int`
  - `log_note(text) -> int`
  - `close()`, plus the context-manager methods

Key algorithm for `_emit(topic, payload, observed_ns)`:
1. Validate the required keys.
2. Copy the payload and serialize it *once, outside the lock*, using a placeholder `_rec`. Better: build `_rec` inside the lock, then call `json.dumps(allow_nan=False)`. A failure raises `SessionError` *before* `seq` is incremented.
3. Under the lock: read `mono = time.monotonic_ns()`, `seq = self._seq`, build `_rec`, `dumps`, register the channel lazily, `add_message(log_time=epoch(mono), publish_time=epoch(observed or mono), sequence=seq)`, `flush()`, then `self._seq += 1`.

`__exit__` logs a fault with the exception text, then closes; the exception is not suppressed.

- [ ] **Step 1: Write the failing tests.**
  - The `data_source` value is validated.
  - The session directory, `metadata.json` (with `closed_cleanly` absent before close and `True` after), and `notes.md` are created.
  - Writes with NaN or missing keys are rejected, and so is writing after close.
  - **Review Focus 1:** an `object()` param raises `SessionError`, and the next log still gets `seq == 1`.
  - **Review Focus 2:** an exception inside the `with` block produces a fault event and `closed_cleanly`.
  - **Review Focus 3:** a nested root that doesn't exist is created.
  - Concurrency: two threads each log 500 events. `seq` values are 0..999 when read with the raw `mcap` StreamReader.
  - `raw_packet` is stored as hex. `observed_monotonic_ns` is taken from `RobotState.host_monotonic_ns` when that attribute exists, and is `None` otherwise.
  - The image message's `data` base64-decodes back to the input bytes.
- [ ] **Step 2:** Run them. They should fail.
- [ ] **Step 3:** Implement `record.py`, including the metadata helpers: `git rev-parse HEAD` / `git status --porcelain` with a timeout and `None` on failure, the SHA-256 source fingerprint, environment versions via `importlib.metadata`, and the atomic `os.replace` write.
- [ ] **Step 4:** Run them. They should pass.
- [ ] **Step 5:** Commit.

### Task 3: load_session and nearest

**Files:**
- Create: `scorbot/session/replay.py`
- Test: `tests/test_session.py`

**Interfaces:**
- Consumes: the writer output
- Produces:
  - `Finding(level: str, message: str)`
  - `Session(path, metadata, events, findings)` with `.errors` and `.warnings`; events are dicts with `seq`, `topic`, `schema`, `log_time`, `publish_time`, `payload`
  - `load_session(path)`, which accepts a directory or a `.mcap` file
  - `nearest(events, t_ns, topic="/robot/state") -> tuple[dict, int] | None`

Tail classification: iterate `StreamReader(BytesIO(data)).records` and record `bio.tell()` after each one. On an exception, read the 9-byte header at `last_good`:
- known opcode and `9 + length > remaining` → truncated tail, a **warning**;
- `remaining == 0` → a clean crash boundary, no truncation finding;
- anything else → corruption, an **error**.

The session counts as never closed if there is no `Footer` record or `closed_cleanly` is not `True`; that is a warning.

- [ ] **Step 1: Write the failing tests.**
  - Round trip: events match what was written, and there are no errors.
  - Hard crash: a subprocess writes 25 events and then calls `os._exit(1)`. All 25 load, with an "not closed" warning and no errors.
  - Truncating the last 5 bytes gives a warning and 24 events.
  - Overwriting 32 bytes mid-file with `0xFF` gives an error.
  - A hand-edited `seq` gap gives an error. To produce it, write a session with the raw `mcap` writer that skips `seq` 3.
  - **Review Focus 4:** passing the `.mcap` path works.
  - `nearest` returns the correct event and signed difference, and prefers the observed time.
  - Out-of-order observed times give no error.
  - A missing command result gives a warning.
- [ ] **Step 2:** Run them. They should fail.
- [ ] **Step 3:** Implement `replay.py`.
- [ ] **Step 4:** Run them. They should pass.
- [ ] **Step 5:** Commit.

### Task 4: CLI, synthetic example, isolation

**Files:**
- Create: `scorbot/session/__main__.py`, `examples/make_synthetic_session.py`
- Test: `tests/test_session.py`

**Interfaces:**
- `main(argv: list[str] | None = None) -> int` in `__main__.py`
- `write_synthetic_session(root) -> Path` in the example. The tests import it through `importlib.util` from the file path.

- [ ] **Step 1: Write the failing tests.**
  - The CLI on a synthetic session exits 0, and its output contains `SYNTHETIC` and `Foxglove`.
  - The CLI on a corrupted session exits 1.
  - **Review Focus 5:** a path that doesn't exist exits 2 with no `Traceback`.
  - A subprocess runs `import scorbot.session, sys; print('usb' in sys.modules or 'openScorbot' in sys.modules)` and prints `False`.
  - The synthetic session contains at least one event of every topic kind and has no errors.
- [ ] **Step 2:** Run them. They should fail.
- [ ] **Step 3:** Implement the CLI (banner, metadata, timeline with `--limit`, counts, findings, viewer hint) and the example, which generates PNGs with `zlib` + `struct`.
- [ ] **Step 4:** Run them. They should pass, and so should the full suite.
- [ ] **Step 5:** Commit.

### Task 5: User docs

**Files:**
- Create: `docs/EXPERIMENT_RECORDING.md`
- Modify: `README.md`

- [ ] **Step 1:** Write the guide: install, record from a script, replay from the CLI, open in Foxglove (drag and drop) or Lichtblick (open source, no account), the integrity messages explained, and the field meanings (logged vs observed time).
- [ ] **Step 2:** Add a link from the README.
- [ ] **Step 3:** Run the example end to end and paste the real output into the guide.
- [ ] **Step 4:** Commit.
