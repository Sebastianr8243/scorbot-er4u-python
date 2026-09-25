# Simulated Robot and Recorded Bench Runs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A fake ER-4U controller behind the real `Scorbot` facade, and lab scripts that record MCAP sessions and can be rehearsed with `--simulate`.

**Architecture:** `SimulatedScorbot(Scorbot)` overrides only `connect()` and `disconnect()`. It installs two fakes:
- a command worker on the legacy result protocol, run as `self._command_thread`;
- an input endpoint whose `snapshot()` returns real 64-byte packets.

The real facade's validation, gates, and fault latch run unchanged. The lab scripts wrap their existing flow in a `SessionWriter`, which starts first.

**Tech Stack:** Python ≥3.10 stdlib, the existing `scorbot`, `scorbot.session`, and `openScorbot/motion_profile.py`, and `unittest`.

**Spec:** `docs/superpowers/specs/2026-09-25-simulated-robot-design.md`

## Global Constraints

- Do not modify `openScorbot/`, `Scorbot.connect`, or `Scorbot.disconnect`.
- `import scorbot` and `import scorbot.simulated` must not import `usb` or change `sys.path`.
- `RobotState.simulated` defaults to `False`, and existing positional construction must keep working.
- Keep the lab-script JSONL `type` sequence and its fields unchanged. Only add `data_source` to the session row.
- Real and `--simulate` runs differ in exactly one conditional.
- `--session-root` defaults to `<output folder>/sessions`.
- Tests: `.venv/Scripts/python.exe -m unittest discover -s tests`.

## Review Focus

1. **The G1 run is interrupted with Ctrl-C at a prompt**, whether simulated or real. Expected: a JSONL `session_failed` row; the MCAP holds a fault, with any open command marked `faulted`; the session still loads. (Task 3)
2. **`--session-root` points at a folder that doesn't exist yet.** Expected: it is created. (Task 3)
3. **`SimulatedScorbot` is used after `disconnect()`, or connected twice.** Expected: the same `ScorbotError` the real facade raises. (Task 1)
4. **A jog would take a simulated count beyond ±65535.** Expected: the controller answers an error code, and the facade faults. It must not wrap around silently. (Task 1)
5. **A `record_raw_state --simulate` run uses a `--hz` or `--seconds` at the edge of its range.** Expected: the sample count matches between the JSONL and the MCAP. (Task 3)

---

### Task 1: SimulatedController and SimulatedScorbot

**Files:**
- Create: `scorbot/simulated.py`, `tests/test_simulated.py`
- Modify: `scorbot/state.py` (add the `simulated` field), `scorbot/__init__.py` (export)

**Interfaces (produced):**
- `encode_packet(signed_counts: dict[str, int], switch_bits: int = 0, sign_override: dict | None = None) -> bytes` returns 64 bytes.
- `SimulatedController(home_counts=None, start_counts=None, step_delay_s=0.0)` provides:
  - `.counts` (dict), `.commands` (list), `.inject(kind)`;
  - `.worker(commands_q, results_q)`, the thread target;
  - `.snapshot(after_index=None, timeout=2.0, max_age=2.0) -> PacketSnapshot`.
- `SimulatedScorbot(*, controller=None, **Scorbot_kwargs)` has `.sim` (the controller), and `get_state()` returns `simulated=True`.
- `FAULT_KINDS = ("timeout", "controller_error", "worker_crash", "stale_feedback", "corrupt_packet")`

- [ ] **Step 1: Write the failing tests.**
  - The encoder round-trips at 0, ±1, ±142, −65535, and 65535, and out-of-range values are rejected.
  - Full cycle: base +1 moves the count by exactly `preview_jog`'s delta, the state is `simulated`, and indices increase.
  - A jog before home, a wrist jog, and a jog of 6° are each rejected. A wrist jog queues no order.
  - For each fault kind:
    - the triggering call raises `ScorbotError`;
    - `enable()` then raises with the fault text;
    - `jog_joint` and `home` fail;
    - with stale feedback, no order from 4–15 or 18 is queued.
  - `disconnect()` joins the worker, and a second `connect()` on the same instance raises. **(RF3)**
  - A count overflow gives an error and a fault. **(RF4)**
  - Log rows carry `simulated: true`.
  - A subprocess shows that `import scorbot.simulated` loads no `usb` and leaves `sys.path` unchanged.
  - `RobotState` still constructs positionally without `simulated`.
- [ ] **Step 2:** Run them. They should fail with `ImportError`.
- [ ] **Step 3:** Implement. Worker loop:
  - `get` a command and append it to `.commands`;
  - consume any armed one-shot fault;
  - apply the command: 16 or 17 toggles motors; 18 sets counts to home; 4–13 use `plan_jog` deltas; 528 answers 0 and exits; anything else answers code 1, then 0;
  - `put(0)`.

  `worker_crash` puts the exception and returns. `timeout` skips putting anything. `SimulatedScorbot.connect()`:
  - sets `_device` to a sentinel and `_input` to the controller;
  - starts `self._command_thread`;
  - runs `self._command([16, 1, 1])`;
  - records `connect`.

  `disconnect()` mirrors the base class without `usb.util`.
- [ ] **Step 4:** Run them. They should pass, along with the full suite.
- [ ] **Step 5:** Commit.

### Task 2: SessionWriter refuses mixed sources

**Files:**
- Modify: `scorbot/session/record.py`
- Test: `tests/test_session.py`

- [ ] **Step 1: Write the failing tests.**
  - A simulated state in a `real` session raises `SessionError`.
  - A real `RobotState` (`simulated=False`) in a `simulated` session raises `SessionError`.
  - A dict without the key is accepted.
  - The `synthetic` data source accepts only states whose `simulated` key is missing or `False`. Ruling: synthetic data is invented and never comes from the simulator.
- [ ] **Step 2:** Run them. They should fail.
- [ ] **Step 3:** Implement the check in `log_state` before `_emit`.
- [ ] **Step 4:** Run the suite. It should pass.
- [ ] **Step 5:** Commit.

### Task 3: Lab scripts record MCAP and support --simulate

**Files:**
- Modify: `examples/bench_joint.py`, `examples/record_raw_state.py`, `scripts/review_lab_logs.py`
- Test: `tests/test_simulated.py` (the G1 subprocess sequence), `tests/test_calibration_capture.py` (still passes and writes nothing to the repo)

- [ ] **Step 1: Write the failing tests.**
  - The G1 sequence, in subprocesses:
    1. `record_raw_state --simulate --seconds 1 --hz 2`, which should give 2 samples in both the JSONL and the MCAP. **(RF5)**
    2. `bench_joint --simulate` with stdin `HOME`, a description, `HOME_OK`, `MOVE`, and 4 answers.
    3. `review_lab_logs --idle --bench`.
  - Every step exits 0. The bench JSONL types equal `[session, connected, home_complete, home_observation, motion_preview, before_jog, after_jog, operator_observation, disabled]`.
  - The review output contains `SIMULATED`.
  - Both MCAP sessions load with no errors and are `simulated`. The bench session has `home` and `jog_joint` commands with `completed` results.
  - `--session-root` pointing at a missing nested folder is created. **(RF2)**
  - A bench run where stdin ends at the `HOME_OK` prompt (EOF) exits non-zero. It has a `session_failed` row, and its MCAP has a fault and no errors. **(RF1: EOF stands in for Ctrl-C, since both raise out of `input()`.)**
- [ ] **Step 2:** Run them. They should fail.
- [ ] **Step 3:** Implement:
  - a shared helper per script;
  - one conditional selecting (`SimulatedScorbot`, `"simulated"`, skip preflight) or (`Scorbot`, `"real"`, preflight);
  - `SessionWriter` as the outermost `with`;
  - the mapping from spec §5;
  - `review_lab_logs` prints `SIMULATED` from the session row.
- [ ] **Step 4:** Run the suite. It should pass.
- [ ] **Step 5:** Commit.

### Task 4: Docs

- [ ] G1 checklist: a rehearsal item using `--simulate` and a rehearsal folder; §G copies `logs\sessions\` and `notes.md`.
- [ ] `EXPERIMENT_RECORDING.md`: a "Simulated robot" section with the API, the fault kinds, and what it does *not* model.
- [ ] README: a one-line pointer.
- [ ] Run the rehearsal commands from the docs for real, then commit.
