# Experiment record and replay: design

**Status:** Approved direction; storage format revised to MCAP (see §2)
**Date:** 2026-09-25
**Implements:** System design §5.4, requirements R-06, R-07 (schema part), R-09; GitHub work item 2
**Branch:** `feat/experiment-record` from `main`. No USB code, no dependency on PR #1.

## 1. Purpose

Give every experiment one local, append-only record of what was commanded,
what the controller and camera reported, and what the operator decided, so a
run can be replayed and inspected with no robot attached. This is the
foundation the simulated backend and camera adapter will write into.

**Success**
- A synthetic session written by the recorder is read back by the replay
  tool, reproduces the same ordered event timeline, and reports integrity
  problems explicitly.
- The replay shows at a glance whether the data is real, simulated, or
  synthetic.
- The same file opens in an off-the-shelf robotics viewer: camera frames on
  a timeline and encoder counts as plots, with no custom GUI.
- All tests run offline under `unittest`.

## 2. Format decision: MCAP instead of JSONL

The system design (§5.4) proposed `events.jsonl` plus separate image files.
This design deliberately switches to **[MCAP](https://mcap.dev)**, written
with the pure-Python `mcap` package. Reasons:

- **An existing standard.** MCAP is the log format used by ROS 2 (rosbag2)
  and by Foxglove and Lichtblick. Students can open a session in a viewer
  and see video, plots, and messages on one timeline. We don't have to build
  a replay GUI.
- **One file per run.** Frames, states, commands, and notes are all in it.
  JSON messages with JSON Schema stay inspectable by hand through the
  reader.
- **The time model is built in.** Every message has a `log_time` and a
  `publish_time`, which map directly to our "logged" and "observed"
  timestamps.
- **Crash-safe when written unchunked.** Measured on this machine: a writer
  flushed after each message and killed with `os._exit(1)` partway through
  lost nothing. All 137 of 137 messages were recovered by streaming the
  file.
- **A path to later export.** ROS 2 and LeRobot tooling can consume MCAP, so
  the later LeRobot export (work item 6) starts from a standard file.

**Rejected alternatives**
- **`foxglove-sdk` as the writer.** It encodes protobuf and manages sequence
  numbers and timing internally. We need full control of both for
  provenance. It is a candidate for a later, optional live-view feature.
- **Rerun.** Its `.rrd` format is not a stable archival format.
- **rosbag2.** It requires ROS.

**Cost:** one new runtime dependency, `mcap>=1.5,<2`. It pulls in the
`lz4` and `zstandard` wheels, which are available for Windows. The robot PC
already installs from PyPI (`scripts/setup_windows.ps1`).

## 3. Non-goals

- No change to `openScorbot/`, `scorbot/robot.py`, or `scorbot/state.py`.
- No simulated backend, camera capture, OpenCV, or VLM code (later slices).
- No LeRobot export, plots, CSV export, cross-run comparison, or live
  streaming yet.
- `examples/record_raw_state.py` is left as is; porting it is a follow-up.

## 4. Session layout

```
<root>/<session_id>/
  session.mcap    # all events and frames; unchunked, append-only
  metadata.json   # human-readable copy; written on open, rewritten atomically on close
  notes.md        # blank operator observation form, filled by hand
```

- `session_id` is `YYYYMMDDTHHMMSSZ-<6 hex>`.
- The directory is created exclusively; an existing session is never
  overwritten.
- `session.mcap` also carries the opening metadata as an MCAP `Metadata`
  record named `scorbot.session`, so a lone `.mcap` file still describes
  itself.
- `*.mcap` is added to `.gitignore`.

## 5. Metadata

| Field | Meaning |
|---|---|
| `schema_version` | `1` |
| `session_id` | as above |
| `data_source` | **required**: `"real"`, `"simulated"`, or `"synthetic"` |
| `robot_id`, `controller_id` | operator labels, e.g. `lab-er4u-1` |
| `operator`, `task`, `start_pose_note` | free text; `start_pose_note` defaults to `"unmeasured"` |
| `camera_ids` | list, may be empty |
| `calibration` | `{"path", "sha256"}` or `null` |
| `code` | `{"git_commit", "git_dirty", "source_fingerprint"}`; the fingerprint is a SHA-256 over `scorbot/` and `openScorbot/` `.py` files so it works outside git |
| `environment` | Python version, platform, `mcap`/`pyusb`/`numpy` versions if installed, `usb_driver` operator label |
| `clock` | `{"started_monotonic_ns", "started_epoch_ns"}`: the anchor mapping monotonic time to MCAP epoch time |
| `started_utc` | set on open |
| `ended_utc`, `event_count`, `closed_cleanly` | set on close in `metadata.json`; absent after a crash |

MCAP `Metadata` records hold only string values, so the MCAP copy stores
the whole dict as one JSON string under the key `json`.

## 6. Events

**Time.** The recorder reads `time.monotonic_ns()` inside its write lock and
converts it to epoch nanoseconds:
`epoch = started_epoch_ns + (mono - started_monotonic_ns)`.

- MCAP `log_time` is the converted logging time. It never decreases, so
  viewers show real wall time without clock jumps.
- MCAP `publish_time` is the converted observed time when the source
  supplies one (camera capture time, or `RobotState.host_monotonic_ns` from
  PR #1). Otherwise it equals `log_time`. MCAP requires the field, so the
  fallback is never treated as a measurement.
- Every JSON payload also carries a `_rec` block with the raw values:
  ```json
  "_rec": {"seq": 12, "logged_monotonic_ns": 130, "observed_monotonic_ns": 123}
  ```
  `observed_monotonic_ns` is `null` when the source gave no observed time.
  Analysis must use `_rec`, not `publish_time`.
- `seq` is global across all topics: it starts at 0 and increases by 1 with
  no gaps. It is also written to the MCAP `sequence` field.
- Payloads are strict JSON; NaN and Infinity are rejected.

| Topic | Schema name | Required payload keys | Notes |
|---|---|---|---|
| `/robot/state` | `scorbot.RobotState` | `encoder_counts`, `home_switch_bits`, `connected` | Accepts a `RobotState` or a dict and stores `asdict()` unchanged, so PR #1's fields pass through. Optional `raw_packet_hex` keeps the undecoded controller bytes (§5.4 "preserve raw observations"). |
| `/robot/command` | `scorbot.Command` | `command_id`, `kind`, `params` | `command_id` is generated by the recorder |
| `/robot/command_result` | `scorbot.CommandResult` | `command_id`, `status` | `status` ∈ `completed`, `faulted`, `timeout`, `rejected`; optional `completion_source`, `detail` |
| `/camera/<id>/image` | `foxglove.CompressedImage` (official JSON schema, vendored) | `timestamp`, `frame_id`, `data` (base64), `format` | Plus `frame_number`, `width`, `height`, and optional `camera_config_id`, `mount_id` inside `_rec`, which keeps the Foxglove-shaped fields clean |
| `/camera/<id>/detections` | `scorbot.Detection` | `frame_number`, `label`, `bbox_xyxy`, `confidence`, `model_id` | optional `operator_correction` |
| `/operator/decision` | `scorbot.Decision` | `choice` | optional `refers_to_seq`, `reason` |
| `/session/fault` | `scorbot.Fault` | `message` | optional `command_id` |
| `/session/note` | `scorbot.Note` | `text` | |

- Each topic has its own JSON Schema (`message_encoding="json"`,
  `schema encoding="jsonschema"`).
- The Foxglove `CompressedImage` schema is vendored from
  `foxglove/foxglove-sdk` `schemas/jsonschema/CompressedImage.json` at
  commit `dcbc667`. The source URL is kept in the file header.
- The writer rejects missing required keys. The reader reports unknown
  topics but keeps them.

## 7. Components

**`scorbot/session/record.py`: `SessionWriter`**

```python
with SessionWriter.create(root, data_source="synthetic", robot_id="lab-er4u-1", ...) as rec:
    rec.log_state(state)                       # observed time from state.host_monotonic_ns if present
    cid = rec.log_command("jog_joint", {"joint": "base", "delta_counts": 50})
    rec.log_command_result(cid, "completed", completion_source="encoder_delta")
    rec.log_frame("cam0", 0, png_bytes, format="png", width=64, height=48,
                  observed_monotonic_ns=t_capture)
```

- It uses `mcap.writer.Writer(use_chunking=False)` and flushes the file
  after every message.
- A lock covers the clock read, `seq`, and the write, so camera and robot
  threads can share one writer.
- `close()` calls `writer.finish()`, then fsync, then rewrites
  `metadata.json` through a temp file plus `os.replace`.
- Writing after `close()` raises an error.

**`scorbot/session/replay.py`: `load_session(path) -> Session`**

`Session` holds `metadata`, `events` (one decoded dict per message, with
topic, `log_time`, `publish_time`, and payload), and `report`. The reader
streams the MCAP file, so it works on both finished and crashed files.

It tells a crash apart from corruption like this: it records the byte
offset after the last complete record and compares it with the file size.
- A clean end, or at most one partial trailing record, counts as a crash
  tail. That is a **warning**.
- A parse failure with more data after it is an **error**.

Findings:
- **Errors:** a gap or duplicate in `seq`; `logged_monotonic_ns` going
  backwards; corruption before the tail; `data_source` missing;
  `schema_version` newer than supported.
- **Warnings:** a truncated tail; the session was never closed (no MCAP
  footer, or `closed_cleanly` absent); a command has no result; an unknown
  topic.

Helper: `nearest(events, t_ns, topic="/robot/state")` returns the closest
event, compared on `_rec.observed_monotonic_ns` (falling back to
`logged_monotonic_ns`), and its signed time difference.

**`python -m scorbot.session <dir>`: CLI replay**

- Prints a banner with `data_source` in capitals.
- Then the metadata summary, one line per event
  (`+t_ms  seq  topic  summary`), counts per topic, and the integrity report.
- Exits 1 if there are any errors, otherwise 0.
- `--limit N` caps how many timeline rows are printed.
- It ends with the hint: "Open session.mcap in Foxglove or Lichtblick for
  video and plots."

**`examples/make_synthetic_session.py`**

Writes a session marked `data_source="synthetic"`. It contains:
- idle states;
- one command, its states, and its result;
- two generated PNG frames (stdlib `zlib` + `struct`);
- a detection, an operator decision, and one fault;
- a note.

It prints the path and then replays it.

**`docs/EXPERIMENT_RECORDING.md`** is a short user guide. It covers how to
record, replay from the CLI, and open the file in a viewer. Foxglove opens
local files by drag and drop. Lichtblick is the open-source, no-account
alternative.

## 8. Testing (`tests/test_session.py`, unittest, temp dirs only)

1. **Round trip:** write the synthetic session, then load it. Events equal
   what was written, `seq` is 0..N-1, and there are no errors (R-06).
2. **`data_source`:** required and must be one of the three values. The
   replay banner shows it (R-09).
3. **Writer validation:** rejects NaN, missing keys, and writes after close.
4. **Hard crash:** a subprocess logs N events and exits with `os._exit(1)`.
   All N load back, with warnings and no errors.
5. **Truncated tail vs. corruption:** truncating the last record gives a
   warning. Overwriting bytes mid-file gives an error, and the CLI exits 1.
6. **Concurrency:** two threads write 500 events each. The result has 1000
   contiguous `seq` values and no errors.
7. **`nearest()`:** returns the correct event and signed time difference.
   It prefers the observed time, and observed times out of order produce no
   error. When there is no observed time, the payload shows `null`.
8. **`RobotState` round trip:** a state from `decode_state()` round-trips
   through `log_state`, and `raw_packet_hex` is stored when supplied.
9. **No hardware imports:** a fresh subprocess that runs
   `import scorbot.session` leaves `usb` and `openScorbot` absent from
   `sys.modules`.
10. **Image message shape:** its payload validates against the vendored
    Foxglove schema's required keys, and its `data` decodes from base64 to
    the original bytes.

Run the tests with `python -m unittest discover -s tests`. Code must stay
compatible with Python 3.10: no `datetime.UTC`, `typing.Self`, or `tomllib`.

**Acceptance step for the user, not verified here:** open the example
session in Foxglove or Lichtblick. Check that the images display and that
`/robot/state.encoder_counts.base` plots.

## 9. Files

- **New:**
  - `scorbot/session/__init__.py`, `record.py`, `replay.py`, `schemas.py`,
    `__main__.py`
  - `scorbot/session/foxglove_CompressedImage.json`
  - `examples/make_synthetic_session.py`
  - `tests/test_session.py`
  - `docs/EXPERIMENT_RECORDING.md`
- **Changed:**
  - `pyproject.toml`: add the `mcap` dependency; add `scorbot.session` to
    `packages`; add package data for the JSON schema.
  - `.gitignore`: add `*.mcap`.
  - `README.md`: add a short "Recording an experiment" link.

## 10. Amendments after the whole-branch review (2026-09-25)

- **Writer consumes `seq` before writing.** A Ctrl-C that arrives after a
  record reaches disk no longer causes the next event to reuse the number.
  After any write failure (for example, disk full), the writer stops:
  - every later call raises `SessionError`;
  - `close()` skips the summary;
  - `metadata.json` records `closed_cleanly: false` and `write_error`.
- **Metadata cross-check.** When both copies exist, `metadata.json` and the
  embedded `scorbot.session` record must agree on `session_id`,
  `data_source`, `schema_version`, `robot_id`, and `clock`. A mismatch is an
  error, and the embedded values, which are CRC-protected, are shown.
- **A closed file has no crash tail.** If the file ends with the MCAP magic,
  or `metadata.json` says `closed_cleanly: true`, any unreadable record is an
  error. It is also an error when `event_count` in `metadata.json` differs
  from the number of events read.
- **A lone finished `.mcap` counts as closed.** Its Footer plus a passing
  CRC is enough when there is no `metadata.json`.
- **Clock information.** `metadata.clock` records the monotonic clock's
  implementation and resolution. Windows on Python < 3.13 uses about 15.6 ms
  steps.
- **Streaming reader (deferred).** `load_session` parses record by record
  but reads the file into memory and keeps frame payloads. True streaming,
  and lazy loading of frame `data`, are deferred to the camera-adapter
  slice, where hour-long video sessions first occur.
