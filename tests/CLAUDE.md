# tests/

`unittest` suite. It must never open USB, enumerate a real device, or move the arm. Root rules in [../CLAUDE.md](../CLAUDE.md) apply.

```powershell
python -m pytest -n 2 tests                                  # the lab-visit gate: about 645 tests, under 2 min
python -m pytest -n 2 tests tests_extra                      # everything: about 930 tests, 2 to 3 min
python -m unittest discover -s tests -v                      # the gate with plain unittest
python -m unittest tests.test_simulated -v                   # one module
python -m unittest tests.test_python_api.CommandTests -v     # one class
```

`tests/` is the gate: connect, state, home, jog, the safety invariants, the packet pins, the lab session and the evidence path. `tests_extra/` (see [../tests_extra/CLAUDE.md](../tests_extra/CLAUDE.md)) holds tests for code that is off the first-trial path; CI runs both, the lab-visit check is `tests/` only.

Run from the repo root. Tests import `scripts.<name>` and `examples.<name>` as namespace packages, and `scorbot` from the editable install or the current directory. CI runs the same command on Windows and Linux, Python 3.10 and 3.13, with `.[dev]` (Hypothesis) and, on Linux only, `.[kinematics]`. Optional dependencies skip cleanly (`hypothesis`, `roboticstoolbox`, `usb`); a skipped test is not a pass, so install them locally before trusting a green run.

## How tests avoid hardware

| Technique | Where |
|---|---|
| `SimulatedScorbot` through the real facade (gates, fault latch, event log) | `test_simulated.py`, `ready_robot()` helper |
| `Scorbot()` with `_device = object()`, fake `_commands`/`_results` queues and a worker thread, no `connect()` | `test_python_api.py`, `test_arm_control.py` |
| Fake input endpoint with `snapshot()` returning built packets | `test_arm_control.py` (`class Input`) |
| Patch `run_checks` and replace the `Scorbot` class in the script module, patch `builtins.input` with scripted answers | `test_bench_joint.py`, `test_calibration_capture.py`. A new script test that skips these patches would reach real USB. Use `--simulate` or the patches |
| `preflight.import_module` and `platform` patched; `find` is a fake | `test_preflight.py` |
| Synthetic packets and pcap bytes built in the test | `test_usb_trace.py`, `test_legacy_properties.py`, `test_lab_log_review.py`, `test_watch_lab_log.py` |
| Temp directories for every file written | all; never write into the repo or `logs/` |

Legacy modules are loaded through `Scorbot()._legacy("libdef")` for pure functions only; `libdef` needs PyUSB installed, which is why those classes are `skipUnless(find_spec("usb"))`.

## Module index

| File | Covers |
|---|---|
| `test_python_api.py` | State decode, command/result queues, timeouts, worker crashes, home refusal, disconnect edge cases, legacy arithmetic integers |
| `test_arm_control.py` | Fresh-packet tracking, fit/load calibration, vendor-display refusal, wrap, calibrated move, soft limits, wrist gate, jog ceiling, stale feedback |
| `test_simulated.py` | Simulator fidelity to the facade, every fault kind latches, full G1 rehearsal, LED mismatch, recorder failure, interrupts |
| `test_session.py`, `test_schemas.py`, `test_analysis.py` | Recorder, replay integrity and crash tails, schemas, source mixing, list/export/compare |
| `test_bench_joint.py`, `test_calibration_capture.py` | Prompt gates and exit codes of the lab scripts |
| `test_lab_log_review.py`, `test_watch_lab_log.py`, `test_usb_trace.py` | Offline analysis scripts |
| `test_nominal.py`, `test_motion_profile.py` | Manual values and span bound, jog planning |
| `test_legacy_properties.py` | Hypothesis properties of encoder and packet arithmetic; known bugs are pinned as `test_known_bug_*` with `expectedFailure`; none is open since 2026-10-06 (the two for `suma`/`resta` were fixed and flipped), and an equivalence test pins that real steps are unchanged |
| `test_source_model.py` | The source-based joint model: it reproduces the USNA toolbox's home position, uses the vendor's scales, round-trips, couples shoulder and elbow as the vendor does, and stays inside the travel cap |
| `test_limits.py` | Every module's travel cap, motor list and count band is the one in `scorbot/limits.py` |
| `test_software_stop.py` | The software stop: legacy jog loops against fake endpoints (exact command sequence), `request_stop` through the simulator, the bench stop trial |
| `test_vendor_check.py` | The vendor-layout checker |
| `test_lab_session.py`, `test_lab_terminal.py`, `test_lab_profile.py`, `test_lab_operator.py`, `test_lab_moves.py`, `test_lab_faults.py`, `test_lab_teleop.py` | The guided session (`scorbot/lab`): engine through the simulator, terminal front end, profile file, moves, fault guidance, teleop mode (key `t`) |
| `test_motion_trace.py`, `test_provenance.py` | Packets copied during jogs and streams and their export; the motion fingerprint |
| `test_transport_codec.py` | The pure packet codec against the legacy code's bytes (golden tests) |
| `test_vendor_model.py`, `test_vendor_profile.py` | The vendor's count/angle formula and motion profile, read from the DLL |

Shared helpers: `cli_support.py` (run `python -m scorbot.session` in-process or as a child). Import it as `from tests.<name> import ...` with a bare-name fallback, so both `unittest discover -s tests` and `python -m unittest tests.test_x` work.

## Rules

- Every new gate or rejection test asserts that nothing was queued (`robot._commands.empty()`, `robot.sim.commands` unchanged), not only that an exception was raised.
- A new fault path gets a kind in `simulated.FAULT_KINDS` or a queue-level test, and must show the session latched (`assert_latched` in `test_simulated.py`).
- Do not weaken or delete an `expectedFailure` to get green. When product code fixes the bug the test reports an unexpected success; then remove the decorator and keep the assertion.
- Do not start a child Python process when calling the entry point works. `cli_support.session_cli` and `test_simulated.run_script` call a script's `main` in-process; a child costs a second or more for the imports alone. Keep a child only where the process itself is the subject (exit code, output encoding, what gets imported), and one end-to-end rehearsal.
- Tests of the legacy message loops check which bytes are sent, not when: patch `time.sleep` as `LegacyStopTests.setUp` does rather than wait out the 20 ms per message.
- Do not use real sleeps longer than needed; command timeouts in tests are 0.01-0.5 s. Late-answer and worker-crash tests depend on short real timing (`late_answer_s = 0.5`, crash sleep 0.3 s); do not tighten them without checking for flakiness on Windows, where `time.monotonic` steps at about 15.6 ms on Python < 3.13.
- Tests must not depend on `openScorbot/data.json` contents or on its absence. Importing the legacy modules creates it (git-ignored).
- Keep test data synthetic. Example CSVs and calibration files in tests are format examples, never real measurements; do not paste lab data or `robot_id`s from real runs.
- Do not read `references/` PDFs in tests.

## Adding tests

Match the neighbors: `unittest.TestCase`, temp dirs via `tempfile`, imports at module top except where a test asserts on import behavior (`test_simulated.py` imports inside tests so that a subprocess check of `sys.modules` stays meaningful). Put pure-arithmetic checks in `test_legacy_properties.py` only if they need `hypothesis`; otherwise in the module of the code under test.

Ruff: `ruff check --select F scorbot scripts examples tests` is clean; keep it clean. Default rules flag `l` as a variable name in `test_simulated.py`; that is accepted noise.
