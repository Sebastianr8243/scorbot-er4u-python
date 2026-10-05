# tests/

`unittest` suite. It must never open USB, enumerate a real device, or move the arm. Root rules in [../CLAUDE.md](../CLAUDE.md) apply.

```powershell
python -m unittest discover -s tests -v                      # full suite, ~2 min, about 700 tests, 2 expected failures
python -m unittest tests.test_simulated -v                   # one module
python -m unittest tests.test_python_api.CommandTests -v     # one class
```

Run from the repo root. Tests import `scripts.<name>` and `examples.<name>` as namespace packages, and `scorbot` from the editable install or the current directory. CI runs the same command on Windows and Linux, Python 3.10 and 3.13, with `.[dev]` (Hypothesis) and, on Linux only, `.[kinematics]`. Optional dependencies skip cleanly (`hypothesis`, `roboticstoolbox`, `usb`); a skipped test is not a pass, so install them locally before trusting a green run.

## How tests avoid hardware

| Technique | Where |
|---|---|
| `SimulatedScorbot` through the real facade (gates, fault latch, event log) | `test_simulated.py`, `ready_robot()` helper |
| `Scorbot()` with `_device = object()`, fake `_commands`/`_results` queues and a worker thread, no `connect()` | `test_python_api.py`, `test_arm_control.py` |
| Fake input endpoint with `snapshot()` returning built packets | `test_arm_control.py` (`class Input`) |
| Patch `run_checks` and replace the `Scorbot` class in the script module, patch `builtins.input` with scripted answers | `test_bench_joint.py`, `test_calibration_capture.py`. A new script test that skips these patches would reach real USB. Use `--simulate` or the patches |
| `preflight.import_module` and `platform` patched; `find` is a fake | `test_preflight.py` |
| Synthetic packets and pcap bytes built in the test | `test_usb_trace.py`, `test_properties.py`, `test_lab_log_review.py`, `test_watch_lab_log.py` |
| Temp directories for every file written | all; never write into the repo or `logs/` |

Legacy modules are loaded through `Scorbot()._legacy("libdef")` for pure functions only; `libdef` needs PyUSB installed, which is why those classes are `skipUnless(find_spec("usb"))`.

## Module index

| File | Covers |
|---|---|
| `test_python_api.py` | State decode, command/result queues, timeouts, worker crashes, home refusal, disconnect edge cases, legacy arithmetic integers |
| `test_arm_control.py` | Fresh-packet tracking, fit/load calibration, vendor-display refusal, wrap, calibrated move, soft limits, wrist gate, jog ceiling, stale feedback |
| `test_simulated.py` | Simulator fidelity to the facade, every fault kind latches, full G1 rehearsal, LED mismatch, recorder failure, interrupts |
| `test_session.py`, `test_schemas.py`, `test_analysis.py` | Recorder, replay integrity and crash tails, schemas, source mixing, list/export/compare |
| `test_bench_joint.py`, `test_calibration_capture.py`, `test_bench_stream.py` | Prompt gates and exit codes of the lab scripts; the stream trial rehearsed end to end through the simulator |
| `test_lab_log_review.py`, `test_watch_lab_log.py`, `test_usb_trace.py` | Offline analysis scripts |
| `test_nominal.py`, `test_kinematics.py`, `test_motion_profile.py` | Manual values and span bound, offline kinematics and legacy `cIn` findings, jog planning |
| `test_arm_view.py` | The 3D view against a fake recording (no Rerun, no meshes): mesh placement, the line fallback, the notice, the simulated demo, and that zero counts reproduce the USNA toolbox's published home position |
| `test_arm_chain.py` | The viewer's link chain: zero pose, sign conventions, agreement with the manual's lengths and reach, and with the DH model in `kinematics.py` |
| `test_properties.py` | Hypothesis properties of encoder and packet arithmetic; `test_known_bug_*` are `expectedFailure` |
| `test_streaming_core.py`, `test_streaming.py` | The streaming driver: the USB-free core against a small arm model (one or more tests per requirement R1-R12), the legacy loop against fake endpoints (exact messages), and `Scorbot.start_stream` through the simulator |
| `test_software_stop.py` | The software stop: legacy jog loops against fake endpoints (exact command sequence), `request_stop` through the simulator, the bench stop trial |
| `test_vendor_check.py`, `test_usbc_query.py`, `test_usbc_bindings.py` | The vendor-layout checker, the decompiled-dump query tool, the parked DLL bindings (fake library) |

## Rules

- Every new gate or rejection test asserts that nothing was queued (`robot._commands.empty()`, `robot.sim.commands` unchanged), not only that an exception was raised.
- A new fault path gets a kind in `simulated.FAULT_KINDS` or a queue-level test, and must show the session latched (`assert_latched` in `test_simulated.py`).
- Do not weaken or delete an `expectedFailure` to get green. When product code fixes the bug the test reports an unexpected success; then remove the decorator and keep the assertion.
- Do not use real sleeps longer than needed; command timeouts in tests are 0.01-0.5 s. Late-answer and worker-crash tests depend on short real timing (`late_answer_s = 0.5`, crash sleep 0.3 s); do not tighten them without checking for flakiness on Windows, where `time.monotonic` steps at about 15.6 ms on Python < 3.13.
- Tests must not depend on `openScorbot/data.json` contents or on its absence. Importing the legacy modules creates it (git-ignored).
- Keep test data synthetic. Example CSVs and calibration files in tests are format examples, never real measurements; do not paste lab data or `robot_id`s from real runs.
- Do not read `references/` PDFs in tests.

## Adding tests

Match the neighbors: `unittest.TestCase`, temp dirs via `tempfile`, imports at module top except where a test asserts on import behavior (`test_simulated.py` imports inside tests so that a subprocess check of `sys.modules` stays meaningful). Put pure-arithmetic checks in `test_properties.py` only if they need `hypothesis`; otherwise in the module of the code under test.

Ruff: `ruff check --select F scorbot scripts examples tests` is clean; keep it clean. Default rules flag `l` as a variable name in `test_simulated.py`; that is accepted noise.
