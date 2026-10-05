# Camera capture into sessions (M1 step 3, S3)

> **Built.** Kept as the record of a decision; the code may have moved on since. The status line below is as written at the time.

Date: 2026-10-01. Status: draft, revised after a Codex review (blocking:
shutdown could delay the robot procedure; a shared manifest had race and
crash windows. Both removed by making each camera file self-describing and
sharing no state with the robot recording). Parent:
[M1 roadmap](2026-10-01-m1-roadmap-design.md), section 3 and contracts C1
(clock), C4 (evidence order), C5 (optional deps), C6 (no hardware in tests),
C7 (packages).

## Goal

Record webcam frames during a session, on the session clock, in a file a
camera failure cannot damage, with enough health data to judge the video
before it is used for training. Lab integration (record an episode from
`python -m scorbot.lab`) is step 4; this step delivers the library, the file
contract, the reader and a camera check command.

## Non-goals

No lab-session wiring, no exporter, no MP4, no live preview window, no
multi-camera sync beyond "each camera has its own file on the same clock",
no detection.

## 1. Isolation rule

The camera never shares a lock, a file, a thread or mutable state with the
robot recording. Concretely:
- its own MCAP file and its own small JSON sidecar; it never writes the
  parent's `metadata.json` or `session.mcap`;
- its own threads; nothing it does runs on the caller's thread except
  `start()` and `stop()`;
- `stop()` returns within a bounded time (default 3 s) whatever the camera
  or disk does (section 4);
- the parent `SessionWriter.close()` does not know about cameras and is
  unchanged.

The only shared resources are the disk and the CPU. That is a stated limit,
not solved here.

## 2. File contract

```
<session_id>/
  metadata.json                parent, unchanged (camera_ids declared at create)
  notes.md
  session.mcap                 robot, commands, decisions, faults (unchanged)
  camera-<camera_id>.mcap      one per camera: /camera/<camera_id>/image only
  camera-<camera_id>.json      written once, atomically, when the stream closes
```

**Writer.** `CameraStream.create(session_writer, camera_id, settings) ->
CameraStream`:
- `camera_id` must be in the parent's `camera_ids` (existing validation).
- Reads the parent's identity once (`session_id`, `data_source`, `clock`)
  from `session_writer.metadata`; after that it holds no reference to the
  parent.
- Creates `camera-<camera_id>.mcap` with `open(..., "xb")` (never
  overwrites), unchunked, data CRCs on, like `session.mcap`. First record:
  embedded MCAP metadata `scorbot.stream` = `{schema_version, stream_version:
  1, session_id, camera_id, data_source, clock, camera: settings}`.
- Only method that writes: `log_frame(frame_number, jpeg_bytes, width,
  height, observed_monotonic_ns)` (required, section 3). Own lock, own
  sequence numbers, own fsync timer, own `_broken` flag.
- `close()`: finish the MCAP footer (if not broken), then write
  `camera-<camera_id>.json` = `{closed_cleanly, event_count, ended_utc,
  write_error?, health_summary}` atomically. If finishing fails, the sidecar
  is still written with `closed_cleanly: false`.

Implementation reuses `SessionWriter._emit` logic by factoring the
MCAP-writing core (`_emit`, `_channel`, fsync, broken handling) into a shared
base class; `SessionWriter` keeps its public API.

**Crash and recovery rules (no recovery step needed, only classification):**

| Files present | Meaning | Reader result |
|---|---|---|
| `.mcap` with footer, sidecar `closed_cleanly: true`, counts match | normal | no finding |
| `.mcap`, no sidecar | crash or stuck stop | warning "camera stream not closed cleanly"; readable prefix used, tail classified like `session.mcap` |
| sidecar `closed_cleanly: false` | write error | error with `write_error` |
| sidecar count differs from events read | damage | error |
| declared camera id with no `.mcap` | camera never started | warning |
| `.mcap` whose embedded `session_id`, `data_source` or `clock` differs from the folder's session | copied or orphaned file | error, frames not used |

**Reader.**
- New `scan_stream(session_dir, camera_id) -> StreamIndex`: reads the stream
  embedded metadata (`scorbot.stream`, its own decoder, not
  `_read_mcap`'s `scorbot.session` path), validates stream version, topic
  (`/camera/<camera_id>/image` only), seq order, logged time order, footer
  and sidecar, cross-checks identity against the parent (sidecar
  `metadata.json`, falling back to `session.mcap`'s embedded metadata when the
  sidecar is missing), and returns per frame only `seq`, `frame_number`,
  `observed_monotonic_ns`, `logged_monotonic_ns`, `width`, `height` and the
  findings. It does not keep image bytes, so a long stream fits in memory.
- New `iter_frames(session_dir, camera_id)`: a generator over decoded JPEG
  bytes in order, for the exporter (step 5) and viewers.
- `load_session` is unchanged.
- `find_sessions` (`analysis.py:363`) learns the stream file name pattern:
  `camera-*.mcap` next to a `session.mcap` is listed as a stream of that
  session, not "NOT A SESSION". `python -m scorbot.session list` shows, per
  session, each camera's frame count and worst finding.

**Schema version.** `session.mcap` and `metadata.json` are unchanged, so
`SCHEMA_VERSION` stays 1 for them. The stream file carries its own
`stream_version: 1`. An older checkout's `list` command reports a camera file
as "NOT A SESSION"; that is acceptable (it never loads it as a session) and
noted in the docs.

## 3. Timestamps (contract C1)

- The capture thread stamps `time.monotonic_ns()` immediately after `read()`
  returns. This is the **read-return time**, not exposure time: the camera
  and driver may buffer frames before `read()` returns. It is the image
  `timestamp`, the MCAP `publish_time` and `_rec.observed_monotonic_ns`;
  `logged_monotonic_ns` is the write time, and their difference is the
  pipeline latency, reported in health.
- `CameraStream.log_frame` requires `observed_monotonic_ns`. The existing
  `SessionWriter.log_frame` also requires it from now on; callers updated in
  the same commit (`examples/make_synthetic_session.py`,
  `tests/test_session.py`).
- `python -m scorbot.camera check` warns when
  `time.get_clock_info("monotonic").resolution` is above 1 ms.

## 4. Camera package `scorbot/camera/`

| Module | Responsibility | Depends on |
|---|---|---|
| `stream.py` | `CameraStream` writer, `scan_stream`, `iter_frames` | `scorbot.session` internals |
| `source.py` | `FrameSource` protocol (`open`, `read -> (ok, image)`, `settings`, `close`); `OpenCVSource`, `FakeSource` | `cv2` only inside `OpenCVSource` |
| `recorder.py` | `CameraRecorder`: reader thread, bounded queue, writer thread, health | `FrameSource`, `CameraStream`, JPEG encoder |
| `__main__.py` | `python -m scorbot.camera check` | the above |

**Design reference:** LeRobot `OpenCVCamera` (Apache-2.0): config names
(`index_or_path`, `fps`, `width`, `height`, `color_mode`, `warmup_s`) and the
background-reader pattern, copied as design, not code, so the later
`lerobot_camera_*` plugin maps one-to-one.

**`OpenCVSource`:**
- Windows: `cv2.VideoCapture(index, cv2.CAP_DSHOW)`; elsewhere the default
  backend.
- Requests width, height, fps, autofocus off with a fixed focus, manual
  exposure with a fixed value. For each property, `settings()` records three
  separate things: the value requested, whether `set()` returned success, and
  the raw `get()` value. It does **not** interpret the raw value as
  "accepted": OpenCV's DirectShow getter returns backend flags (for example
  autofocus read-back is a DirectShow flag, and auto-exposure has no getter
  case and reads -1), so equality checks would be wrong.
- What is verified instead: the actual frame width and height from the first
  frames, and the measured fps over the warm-up. A mismatch with the request
  is recorded and shown, never fatal.
- Discards frames for `warmup_s` (default 1 s).
- Defaults: 640x480 at 30 fps, JPEG quality 90. Exposure and focus values
  are configuration, never claimed to be right.

**`FakeSource`:** deterministic images with the frame number encoded in pixel
blocks; `pace=False` returns immediately (tests), `pace=True` sleeps to its
fps (CLI `--fake`); injectable `fail_at` (raise on frame N) and `block_at`
(block forever on frame N, until `close()`).

**`CameraRecorder(source, stream, *, queue_bytes=64 MiB, max_latency_s=1.0,
clock=time.monotonic_ns)`:**
- Reader thread: `read()`, stamp with `clock()`, put
  `(frame_number, t_ns, image)` on the queue.
- Queue bounded by bytes (`queue_bytes`, sized from the first frame's
  `nbytes`), not by frame count, so memory does not scale with resolution.
  Full: drop the oldest, count `dropped_queue`.
- Writer thread: takes the oldest frame; if older than `max_latency_s`, drop
  it and count `dropped_late` (so frames are never written seconds late);
  else JPEG-encode (`cv2.imencode`) and `stream.log_frame`.
- Health: once per second the writer thread appends a row to an internal
  thread-safe deque (bounded, 600 rows). The owner reads them with
  `drain_health()` on its own thread; no callback ever runs on a camera
  thread, so the lab's JSONL writer is only touched by its owner. Row:
  `frames`, `dropped_queue`, `dropped_late`, `max_gap_ms`,
  `max_latency_ms`, `measured_fps`, `queue_bytes`, `status` (`ok`,
  `degraded`, `failed`). `degraded` when any frame was dropped in that second
  or the gap exceeded 2.5 frame periods.
- Failure: an exception in either thread sets `status = "failed"` with the
  reason, stops both threads and closes the stream. Nothing is raised into
  the owner's thread; `status` and `drain_health()` report it.
- `stop(timeout_s=3.0)`, bounded:
  1. set the stop flag; call `source.close()` (unblocks most blocked
     `read()` calls);
  2. join the reader for up to half the timeout, then the writer for the
     rest;
  3. if the writer finished, `stream.close()` (writes the sidecar);
  4. if either thread is still alive, leave it (daemon threads), do not
     close the stream from this thread (the writer may hold its lock), mark
     `status = "failed: stuck <thread>"`, and return. The stream has no
     sidecar and the reader reports it as not closed cleanly.
  `stop()` never raises.

**`python -m scorbot.camera check [--index 0 | --fake] [--seconds 10]
[--record DIR]`:** opens the camera, prints the settings table (requested,
`set()` result, raw read-back), actual size, measured fps, max gap, max
latency and the clock resolution. With `--record`, writes a camera-only
session (`data_source="real"`, `robot_id="camera-check"`; `--fake` uses
`data_source="simulated"`) so the file contract is exercised on the lab PC.
Opens no USB to the arm.

## 5. Dependencies (contract C5)

- New extra `camera = ["opencv-python-headless>=4.9,<5"]`, also in `dev`
  so CI runs the encoder and OpenCV-adapter tests.
- `uv lock` in the same commit (CI checks it).
- `scorbot.camera` added to the setuptools package list (C7).
- Importing `scorbot.camera` does not import `cv2`; only `OpenCVSource` and
  the encoder do, lazily.

## 6. Tests (no camera hardware, C6; no wall-clock timing assertions)

All recorder tests use `FakeSource(pace=False)` and an injected clock;
ordering is checked with events and barriers, not sleeps.

| Area | Tests |
|---|---|
| stream contract | creates file, refuses overwrite and undeclared camera ids; embedded metadata copies session id, data source, clock; only `log_frame`; sidecar on close with count |
| isolation | stream write error leaves `session.mcap` writable and `load_session` clean; parent close while a stream is open is unaffected |
| crash classes | each row of the crash table produces its finding (no sidecar, `closed_cleanly: false`, count mismatch, missing stream, orphaned file with a foreign session id or clock) |
| reader | `scan_stream` keeps no image bytes; `iter_frames` yields bytes in order; identity fallback to `session.mcap` when `metadata.json` is missing; `find_sessions` lists streams under their session |
| timestamps | `log_frame` without a stamp raises (both writers); stored timestamp equals the injected stamp |
| recorder | frames written in order with the injected stamps; health rows on the owner's `drain_health()` |
| backpressure | blocked writer: queue drops oldest by bytes, counted, status degraded; stale frames dropped by `max_latency_s` |
| failure | `fail_at` in the reader, an exception in `log_frame`: status failed, stream closed with sidecar, a parallel `SessionWriter` keeps recording |
| stuck | `block_at` in `read()` that `close()` does not release, and a writer blocked inside `log_frame`: `stop()` returns within its timeout, never raises, reports `stuck`, no sidecar |
| races | `stop()` twice; `stop()` concurrent with a failure; `stream.close()` twice |
| OpenCV adapter | settings records requested, set result and raw get with a fake `cv2.VideoCapture`; actual size and fps verification; skipped without `cv2` |
| CLI | `check --fake --seconds 1 --record DIR` writes a stream that `scan_stream` reads clean |

## 7. Documentation

`docs/design/EXPERIMENT_RECORDING.md`: stream layout, crash table, `scan_stream` /
`iter_frames`, read-return timestamps. `docs/project/PROJECT_LOG.md` entry. Roadmap
step 3 marked done.

## 8. Left to later steps

- Step 4: whether recording requires `status == "ok"` to start, what the
  operator sees when a camera degrades mid-episode, and where health rows go
  in the lab JSONL (drained on the lab's own thread).
- Step 5: the frame-gap and latency tolerances that refuse an episode.
