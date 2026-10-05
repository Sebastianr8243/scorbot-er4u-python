# Rerun session viewer and Ruff pre-commit: design

> **Built.** Kept as the record of a decision; the code may have moved on since. The status line below is as written at the time.

Date: 2026-09-29. Status: approved in conversation, awaiting written-spec review.

## Goal

1. Let the team open a recorded session in [Rerun](https://rerun.io) and see
   joint counts, commands, decisions, faults and camera frames on one
   timeline. This is for debugging bench runs and for teaching.
2. Catch lint problems before they reach CI.

Both must stay optional and outside the safety path. Neither may open USB,
change a lab script, or add a dependency to the base install.

## Decisions

| Decision | Choice | Reason |
|---|---|---|
| Scope | Recorded sessions now; live feed later, separate design | No change to the lab run path; the conversion is shaped so a live source can reuse the sender |
| Approach | Our converter over the Rerun Python SDK | Rerun's own MCAP loader (experimental since 0.25) targets ROS 2 and protobuf; our JSON payloads would show as raw blobs |
| Version | `rerun-sdk>=0.38,<0.39` in a new `viz` extra | Rerun is pre-1.0 and renames APIs between minor releases (0.23 changed `set_time*`, `Scalar`). 0.38.1 is current (2026-09-16) |
| Ruff | Keep the CI step; add a pre-commit hook and the `B` (flake8-bugbear) rules except `B905` | CI already runs `ruff check .` with `E4, E7, E9, F`. `B` finds 3 issues today. `I` (61 files) and `B905` (16 places) would be churn |

## Part 1: Rerun viewer

### Units

`scorbot/session/rerun_view.py`, new:

| Function | Does | Depends on |
|---|---|---|
| `session_items(session) -> list[Item]` | Pure mapping from a loaded `Session` to items. No Rerun import | `replay.Session`, `analysis.event_summary` |
| `send(items, recording) -> None` | Logs items to a Rerun `RecordingStream` | `rerun` (imported inside) |
| `require_rerun()` | Imports `rerun` or raises `RerunUnavailable` with the install hint | nothing |

`Item` is a small frozen dataclass: `path: str`, `kind: str` (one of `scalar`,
`text_log`, `image`, `boxes`, `document`), `time_s: float | None` (`None` means
static), `seq: int | None`, `value` (kind-specific, plain Python), `level: str | None`.

Keeping the mapping pure means CI tests it without Rerun, and a later live
source only has to produce `Item`s.

### Mapping

Times: `time_s` is the event's `publish_time` minus the session's earliest
`publish_time`, in seconds. `publish_time` is the observed time when the
recorder had one, else the logged time. Two Rerun timelines are set per item:
`session_time` (duration) and `seq` (sequence).

| Topic | Path | Kind | Value |
|---|---|---|---|
| (metadata, static) | `session/info` | `document` | Markdown: `REAL` or `SIMULATED` or `SYNTHETIC` heading from `data_source`, robot id, operator, task, start pose note, code commit, integrity findings |
| `/robot/state` | `state/counts/<joint>` | `scalar` | `signed_encoder_counts[joint]` if present, else `encoder_counts[joint]` |
| `/robot/state` | `state/controller_error/<joint>` | `scalar` | `controller_error_counts[joint]` when present |
| `/robot/state` | `state/home_switch_bits` | `scalar` | `home_switch_bits` |
| `/robot/command`, `/robot/command_result`, `/operator/decision`, `/session/note` | `events` | `text_log` | `"<topic>: " + event_summary(event)`; level `WARN` for a result whose status is not `completed`, else `INFO` |
| `/session/fault` | `events` | `text_log` | same format, level `ERROR` |
| `/camera/<id>/image` | `camera/<id>/image` | `image` | decoded bytes and media type `image/<format>` |
| `/camera/<id>/detections` | `camera/<id>/image/detections` | `boxes` | `bbox_xyxy`, label with confidence |

Topics not listed are skipped. Values that are missing or not numbers are
skipped for that path only.

`send` logs `scalar` as `rr.Scalars`, `text_log` as `rr.TextLog` with the
level, `image` as `rr.EncodedImage(contents=..., media_type=...)`, `boxes` as
`rr.Boxes2D(array=..., array_format=XYXY, labels=...)` and `document` as
static `rr.TextDocument(..., media_type=markdown)`.

### Command

`python -m scorbot.session view SESSION [--save OUT.rrd]`

- Loads one session with `load_session`, like `replay`.
- The recording id is the session folder name, and the application id is
  `scorbot_session`, so two sessions opened one after the other stay separate
  recordings in the viewer. One session per call: sessions are never merged,
  so real and simulated data never share a view.
- Without `--save`: spawns the viewer.
- With `--save`: writes the file; refuses if it exists (exit 2, nothing written).
- Exit codes follow the other subcommands (`replay`, `plot`): 0 when the
  session has no integrity errors; 1 when it has errors (they are still shown
  in `session/info`); 2 when Rerun is missing, the session cannot be opened,
  or `--save` exists.
- Rerun missing: prints `Viewing needs Rerun: pip install -e ".[viz]"`, no traceback.

### Packaging and docs

- `pyproject.toml`: `viz = ["rerun-sdk>=0.38,<0.39"]`. Not added to `dev`, so
  CI does not download the viewer.
- `docs/design/EXPERIMENT_RECORDING.md`: a "View in Rerun" subsection under
  "Viewing recordings": install, the two commands, what each panel shows,
  and that dragging `session.mcap` into Rerun is an experimental alternative
  that shows raw JSON.
- `scorbot/CLAUDE.md` table under `scorbot/session/`: one line for `rerun_view.py`.

### Tests (`tests/test_rerun_view.py`)

Without Rerun (always run):
- A synthetic session with states, a command, a failed result, a decision, a
  note and a fault maps to the expected paths, kinds, levels and relative times.
- Signed counts are preferred; raw counts are used when signed are absent;
  non-numeric values are skipped.
- A camera frame and a detection map to `image` and `boxes` with the right
  media type and label.
- `session/info` says `SIMULATED` for a simulated session and lists integrity errors.
- `view --save` on an existing path exits 2 and leaves the file unchanged.
- `view` with `require_rerun` patched to raise prints the install hint and exits 2.

With Rerun (skipped when `rerun` is not importable):
- `view --save` on a session from `examples/make_synthetic_session.py` writes a
  non-empty `.rrd`.

## Part 2: Ruff pre-commit and bugbear

- `.pre-commit-config.yaml` with the `ruff-pre-commit` hook (`ruff check`),
  pinned to a release in the `ruff>=0.6,<1` range already in `dev`. It reads
  the rules from `pyproject.toml`, so local and CI rules cannot drift.
- `pyproject.toml`: `select = ["E4", "E7", "E9", "F", "B"]`, `ignore = ["B905"]`;
  add `pre-commit` to `dev`. Update the comment that says "correctness rules only".
- Fix the three `B023` findings (loop variable captured in a test closure): bind
  the variable as a default argument, or add a justified `noqa` if the closure
  runs inside the loop iteration.
- `docs/project/BACKLOG.md` item 42: remove (done).
- `CLAUDE.md` Commands section: mention `pre-commit install` once.

## Out of scope

Live streaming from a running `Scorbot`, 3D arm models or URDF in Rerun,
converting several sessions into one recording, a Rerun blueprint file,
and import sorting or other Ruff rule families.

## Risks

- The Rerun API changes between minor releases. The pin and the single `send`
  function keep a future upgrade to one place.
- The `rerun-sdk` wheel is large (includes the viewer). It stays an optional
  extra and is never needed on the lab PC.
