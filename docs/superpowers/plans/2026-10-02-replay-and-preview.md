# Supervised Replay, Simulator Plugin and Episode Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish M1: exported episodes replay on the arm through the lab tool's gates (key `p`), a simulator-only LeRobot robot plugin works with `lerobot-replay`, and `--preview` writes an HTML report of episodes before export.

**Architecture:** The exporter adds `scorbot_episodes.jsonl` (stdlib-readable actions) verified against LeRobot's own copy. `scorbot/lab/replay.py` (pure) loads and preflights an episode and turns its actions into lab `Move`s on the 0.5 degree grid; `LabSession._replay` runs them with the existing `_run_plan`. `scorbot/follow.py` steps toward targets for the simulator plugin in `plugins/lerobot_robot_scorbot`. `scorbot/lerobot_export/preview.py` renders a self-contained HTML report.

**Tech Stack:** Python 3.10+ stdlib, existing SDK; `lerobot==0.6.1` only in `.venv-lerobot`.

**Spec:** `docs/superpowers/specs/2026-10-02-replay-and-preview-design.md`

## Global Constraints

- No new motion capability: every step is `LabSession._prepare` + `_execute` (lab) or `Scorbot.jog_joint` within the same cap (plugin). 10 degree cap, base/shoulder/elbow only, wrist within half a wrist step of home, drift check, fault latch.
- Real-arm replay only through the lab tool; the plugin refuses `simulate=False`.
- Replay preflight refuses: missing/unparsable files, data source different from the session's, any source `robot_id` different from the profile's, units or motor order different, `step_counts` different from this SDK's `preview_jog`, any action beyond the cap or moving a wrist motor, consecutive grid actions differing by more than one step on one joint.
- Preview: self-contained HTML, escaped text, at most 8 thumbnails per episode at most 160 px wide, at most 200 episodes, at most 8 MB of images.
- Run with `.venv/Scripts/python.exe`; LeRobot parts with `.venv-lerobot/Scripts/python.exe`.
- Commits: imperative sentence-case subject, body explains why, no AI attribution.

## Review Focus

- A dataset whose `scorbot_episodes.jsonl` was hand-edited after export: replay of a real dataset still checks the sidecar's sha256 against the provenance (Task 1 records it; Task 3 test `test_edited_sidecar_is_refused`).
- An episode number that does not exist in the dataset: refused with a message, no motion (Task 3 test `test_unknown_episode_is_refused`).
- Operator types a dataset path that does not exist: refused, session continues armed? No: disarmed with the reason (Task 3 test `test_missing_dataset_is_refused`).
- Replay when the arm is already at the episode start: no start plan, straight to play (Task 3 test `test_start_plan_skipped_when_already_there`).
- Preview with a task text containing HTML: escaped (Task 6 test `test_task_text_is_escaped`).

---

### Task 1: Exporter writes and verifies the replay sidecar

**Files:** Modify `scorbot/lerobot_export/write.py`; Create `scorbot/lerobot_export/sidecar.py`; Test `tests/test_lerobot_export.py` (class `SidecarTests`), `tests/test_lerobot_export_write.py`.

**Interfaces:**
- Produces: `sidecar.STEP_JOINTS = ("base", "shoulder", "elbow")`; `sidecar.step_counts() -> dict[str, int]` (abs `preview_jog(joint, 1.0)` count for each arm joint, offline); `sidecar.episode_record(dataset_episode: int, data, frames) -> dict` with keys `dataset_episode, session, source_episode, task, fps, motors, units, step_counts, first_state, final_state, actions`; `sidecar.SIDECAR = "scorbot_episodes.jsonl"`.
- Provenance gains `robot_id` per session and `episodes_sidecar_sha256`.
- `write_dataset` verification compares LeRobot `action` per frame with the sidecar (`np.allclose`, exact for float32 counts).

Tests: record has the frames' actions and states, motors and units equal the exporter's, step counts positive ints; round trip (lerobot env) finds the sidecar and its sha256 in provenance.

### Task 2: Pure replay planning (`scorbot/lab/replay.py`)

**Interfaces:**
- `ReplayEpisode` dataclass: `record: dict`, `provenance: dict`, `dataset_dir: Path`.
- `load_episode(dataset_dir, number) -> ReplayEpisode` (raises `ReplayRefused` with the reason).
- `preflight(episode, *, data_source, robot_id, step_counts) -> list[str]` (empty = OK).
- `to_travel(rel: list[float], step_counts) -> dict[str, float]` on the 0.5 grid, raising `ReplayRefused` if off-grid by more than a quarter step.
- `play_moves(episode, step_counts) -> list[Move]`; `start_travel(episode, step_counts) -> dict`; `final_counts(episode, home_raw) -> dict` (raw counts for `_run_plan`'s arrival check, wrap-aware: `(home + rel) % 65535`).

Tests: each preflight rule; grid conversion including rounding tolerance and a refused off-grid value; moves from a step sequence with sensor noise between steps; a two-step jump refused; final counts across the wrap.

### Task 3: Lab key `p`

**Files:** Modify `scorbot/lab/session.py` (key `p`, `HELP`, `_replay`); Test `tests/test_lab_replay.py`.

`_replay`: armed only; ask path (`op.text`) and episode (`op.text`, int); `load_episode` + `preflight` (data source from the session, robot id from the profile, step counts from `scorbot.lerobot_export.sidecar.step_counts()`, sha256 of the sidecar equal to provenance); refusals write `replay_refused` and disarm; else `replay_start` row and MCAP note; start plan via `_run_plan("episode <n> start", plan_moves(self.travel, start), "replay", start_counts, f"START {n}")` when not already there; then `_run_plan(f"episode {n}", moves, "replay", final_counts, f"PLAY {n}")`.

Tests (SimulatedScorbot, ScriptedOperator, dataset folders written directly as JSON): full replay ends at the final counts; start plan when the episode starts away from home and skipped when already there; each refusal (missing dataset, unknown episode, wrong data source, wrong robot id, edited sidecar, cap exceeded); a key during play stops it (`plan_stopped`).

### Task 4: `scorbot/follow.py` TargetFollower

`TargetFollower(robot, home_raw, *, step_deg=1.0, speed=10)`: `observe()`, `step_toward(target) -> dict`; `FollowRefused`. Tests on SimulatedScorbot: one step toward the largest error, still within half a step, wrist refused, cap refused before queuing, drift refused, commanded target returned.

### Task 5: Simulator plugin

**Files:** Create `plugins/lerobot_robot_scorbot/pyproject.toml`, `plugins/lerobot_robot_scorbot/lerobot_robot_scorbot/__init__.py`, `.../config_scorbot.py`, `.../scorbot_robot.py`; Test `tests/test_lerobot_plugin.py` (skips without lerobot).

Tests (lerobot env): config registers as `scorbot`; `simulate=False` raises; connect/observe/step/disconnect; `lerobot.scripts.lerobot_replay.replay(cfg)` replays the round-trip dataset episode to its final position; connect failure after connection disconnects.

### Task 6: HTML preview

**Files:** Create `scorbot/lerobot_export/preview.py`; Modify `__main__.py` (`--preview PATH`); Test `tests/test_lerobot_export.py` (class `PreviewTests`).

Tests: kept episode task, one SVG per moving joint, thumbnails when video, refusal reasons listed, task text escaped, no `http` URLs or external `src`, image byte cap respected, SIMULATED banner.

### Task 7: Docs

`docs/LEROBOT_EXPORT.md` (Replay on the arm, Preview), `docs/LAB_SESSION.md` (key `p`), `docs/PROJECT_LOG.md`, roadmap M1 status; full suite, ruff, compileall, lock check, LeRobot tests.
