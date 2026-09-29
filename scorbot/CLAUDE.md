# scorbot/ (public SDK)

Facade and pure helpers over the legacy USB code. Read the root [CLAUDE.md](../CLAUDE.md) invariants first. `import scorbot` must never import `usb` or touch `sys.path` (`tests/test_simulated.py:test_import_loads_no_usb_and_leaves_sys_path_alone`); `robot.py` imports `usb` only inside `connect` and `disconnect`.

## Modules

| File | Contract |
|---|---|
| `robot.py:Scorbot` | Only class that sends commands. Two daemon threads (sync, command) run the legacy loops; queues `_commands`, `_results`, `_sync`, `_reads`. Command codes it sends: 16 disable, 17 enable, 18 home, 4-13 jogs, 528 exit. Never 14/15/19 |
| `packet.py:TrackedInputEndpoint` | Wraps the USB IN endpoint; copies each response out of the legacy's reused buffer, numbers it, timestamps it. `snapshot(after_index=, max_age=)` returns only a fresh, full-length packet or raises `TimeoutError` |
| `state.py` | `decode_state` (raises on short packet or sign byte not 127/128), `RobotState`, offsets `ENCODER_OFFSETS`, `HOME_SWITCH_BITS` (polarity unverified) |
| `calibration.py` | `signed_count_delta`, `load_calibration` (base/shoulder/elbow only) |
| `nominal.py` | Manual values, stdlib only. Enforces span, never signed limits |
| `simulated.py` | `SimulatedScorbot` overrides `connect`, `disconnect`, `get_state`, `_record` only. `SimulatedController.inject(kind)` arms one-shot faults from `FAULT_KINDS` |
| `preflight.py` | `python -m scorbot.preflight`: enumerate only, never open, reset or configure |
| `kinematics.py` | Offline, unvalidated DH model. Wired into no motion command |
| `provenance.py` | SHA-256 over `_SOURCE_FILES` (motion path). Add a file there if it can change what is sent |

## scorbot/session/

- `record.py:SessionWriter._emit` is the single write path: validates required keys (`schemas.REQUIRED`), strict JSON, splices a `_rec` block (seq, logged/observed monotonic ns), flushes per message. A write failure sets `_broken`; later calls raise `SessionError` and `close()` does not append a summary.
- `create()` uses `open(..., "xb")`: sessions are never overwritten. `data_source` is `real`, `simulated` or `synthetic`; `log_state` rejects a state whose `simulated` flag disagrees.
- `BestEffortRecorder` swallows recorder errors after the first warning. Lab scripts write JSONL first, then call the recorder.
- `replay.py:load_session` streams records, so crashed sessions load; damaged tail is a warning, earlier corruption an error. `analysis.py` is pure (no printing); its count deltas use `signed_count_delta`, matching `scripts/review_lab_logs.py`.
- Adding a topic or field: edit `schemas.py` (`TOPICS`, `REQUIRED`, `PROPERTIES`), keep properties permissive (`additionalProperties` true) so old sessions load, and cover it in `tests/test_schemas.py` and `tests/test_session.py`.

## Rules

- Gates live only in `Scorbot`. Add a check to the facade, not to a script; the simulator then exercises it for free. If you override a method in `SimulatedScorbot`, mirror the real cleanup and fault behavior (its `connect` sets `_fault` directly instead of calling `_latch_fault`).
- Lock nesting: `_motion_lock` (RLock, motion methods) outside, then `_lock` (in `_command`) or `_state_lock` (in `get_state`). Never take `_motion_lock` while holding the others. `_command` on error queues a best-effort `[16, 1, 1]` and then raises; it skips the disable when the worker crashed (`_WorkerCrashed`) because nobody would answer.
- After any fault `connect()` refuses; callers build a new instance. `disconnect()` keeps the USB handle open if a worker is still running and raises. Do not "fix" that by force-closing.
- `enabled` and `homed` are command history, not controller truth (`enabled=None` after a fault). Do not present them as measured. The MOTORS LED is the only independent evidence.
- `home()` sets `_home_counts`; `jog_joint` and `get_joint_angles` derive angles as `home_angle + signed_count_delta(count, session_home) / counts_per_degree`. A calibration `home_count` is only checked at home (`validate_home`), not used for angles.
- `move_joint` = one jog to a soft-limit-checked target; raises if the step exceeds `max_jog_degrees`; latches a fault if it lands more than 2 degrees off.
- `get_state` errors inside motion paths become a latched fault through `_motion_state`. Use `_motion_state` in new motion code, not `get_state`.
- `signed_encoder_counts` jumps by 65536 at the 0/65535 seam. Use `signed_count_delta` on `encoder_counts`. `signed_count_delta` uses modulus 65535 and raises on 32767/32768 (ambiguous).
- Log rows (`_record`) are consumed by `scripts/watch_lab_log.py` and `scripts/review_lab_logs.py`. Renaming an event name silently breaks alarms; update `ALARM_EVENTS`.
- `decode_state` needs 49 bytes (`PACKET_MIN_LENGTH`); the simulator emits 64. Real packet length beyond 49 is not characterised here.
- Calibration loader requires `status == "validated"`, matching `robot_id`, 64-hex `source_sha256`, minimum point counts, errors <= 2 degrees, span within manual travel. Keep `scripts/fit_calibration.py:fit` producing exactly what the loader accepts.

## Gotchas

- `Scorbot._legacy(name)` inserts `openScorbot/` at the front of `sys.path` and imports by bare name. `Scorbot().preview_jog` and `_legacy("libdef")` therefore import the legacy modules (and `libdef` imports `usb.core`, PyUSB needed) but do not open the device.
- `connect()` calls `conf.setup()`, which creates `openScorbot/data.json` (git-ignored) once. Legacy modules read it at import.
- `SimulatedController.step_delay_s` defaults to 0; a simulated jog is instant and exact. It says nothing about timing, dynamics, backlash or real controller errors. Never use it to claim protocol or physical correctness.
- Faults injected via `stale_feedback` raise `TimeoutError` in `snapshot`; `worker_crash` sleeps 0.3 s to mimic a still-alive thread.

## Testing changes

`python -m unittest tests.test_python_api tests.test_arm_control tests.test_simulated -v`, then the full suite. New gate: add a test that asserts nothing was queued (`robot._commands.empty()` or `robot.sim.commands` unchanged) when the gate rejects. New fault path: add its kind to `FAULT_KINDS` and cover it in `test_every_fault_kind_latches_the_session`.

## Do not

- Do not add absolute or Cartesian motion, wrist jogs, or gripper commands without measured evidence and a matching test flip.
- Do not raise the 5 degree ceiling or widen speed 1-20.
- Do not import `openScorbot` modules at top level of `scorbot/*` (breaks import without PyUSB).
- Do not report facts through `print`; log via `_record`. The stderr banner in `_worker_died` is deliberate.
