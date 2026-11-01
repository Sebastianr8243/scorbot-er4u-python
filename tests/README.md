# tests/

`unittest` suite. It must never open USB, enumerate a real device, or move the arm. Root rules in [../CONTRIBUTING.md](../CONTRIBUTING.md) apply.

```powershell
python -m pytest -n 2 tests                                  # the lab-visit gate: about 645 tests, under 2 min
python -m pytest -n 2 tests tests_extra                      # everything: about 930 tests, 2 to 3 min
python -m unittest discover -s tests -v                      # the gate with plain unittest
python -m unittest tests.test_simulated -v                   # one module
python -m unittest tests.test_python_api.CommandTests -v     # one class
```

`tests/` is the gate: connect, state, home, jog, the safety invariants, the packet pins, the lab session and the evidence path. `tests_extra/` (see [../tests_extra/README.md](../tests_extra/README.md)) holds tests for code that is off the first-trial path; CI runs both, the lab-visit check is `tests/` only.

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
| `test_dry_link.py` | SIMULATED endpoint contract, Plant status replies, latency timeout, closed links and real legacy workers through the injection seam |
| `test_transport_probe.py` | Complete connection packet pin and exact command set, decoded probe snapshot, SIMULATED CLI, connection warning and real refusals |
| `test_bring_up.py` | Full vendor Home/jog/back/stream rehearsal, legacy selection, typed confirmations, first injected fault abort and exclusive JSONL creation |
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
| `test_motion_trace.py`, `test_provenance.py` | Packets copied during jogs, streams and the legacy home (also when it fails) and their export; the motion fingerprint |
| `test_collision_flag.py`, `test_home_trace_report.py` | The collision flag decode (IN byte 59) and `scripts/home_trace_report.py` on synthetic home traces |
| `test_transport_codec.py` | The pure packet codec against the legacy code's bytes (golden tests) |
| `test_vendor_home_source.py`, `test_vendor_home_libcomm.py` | The worker-side adapter (stop, cancel, deadline, unreadable reply and emergency all still send the abort messages; it never raises) and the legacy `vendor_home_run` with a fake wire (exact packets, the sequence byte across the wrap, the rebuilt `media`, the failure path, `execute` dispatch; one full run of about 5,400 messages through the legacy helpers takes 25 s) |
| `test_vendor_home_robot.py`, `test_lab_vendor_home.py` | `Scorbot.vendor_home` through the simulator (every gate, the failure paths: emergency, impact, wrong drive sign, jammed wrist motor, stop request, never-found switch; the stop messages; the evidence rows; ±1 degree jogs right after the home) and the guided session with `--vendor-home` (the EMPTY, CLEAR and HOME confirmations, declines send nothing, a failed home ends the session) |
| `test_vendor_home.py` | The vendor-style home against its recording: all 327 non-0D commands equal the vendor DLL's, the packet codec, the coupling tables, emergency, impact, timeout and stall paths, single-axis and newer-version variants. Model in `vendor_home_plant.py`, fixture in `fixtures/` (made by `scripts/make_vendor_home_fixture.py`) |
| `test_desk_byte_check.py` | Stack 2's OUT messages against the lab `USBC.dll`'s, byte for byte, no arm and no USB: the real `Scorbot` (legacy handshake and workers) against a fake `usb` package (`desk_wire.py`), compared with `fixtures/dll_lab_open_loop.json` (made by `scripts/make_open_loop_fixture.py`): handshake, control on, `disable()`, the extra stop group in `disconnect()` (pinned as a known difference), every encoder region through the codec, and the sequence wrap (DLL 255 to 0, legacy 255 to 1, pinned). One recording in one test, because pytest-xdist would start one per worker. Result table: `docs/protocol/DESK_BYTE_CHECK.md` |
| `test_thesis_crosscheck.py`, `test_vendor_home_geometry.py` | The port against the ULPGC thesis's SCORBASE capture (the end of a zeroing, mode T before `48`, the setpoint slot offsets), and against switch geometries other than the recording's (narrow bands, distances, the wrist roll needing a switch at least about 800 counts wide or one that stays on, each joint ending at its edge plus the vendor offset) |
| `test_replay_vendor_home.py` | `scripts/replay_vendor_home.py`: a capture made by the port replays with no difference, a changed vendor command is found at its message and field, sequence byte and setpoints are ignored, unpaired rows are refused |
| `test_hook_log_to_rows.py` | `scripts/hook_log_to_rows.py` on synthetic hook lines: writes become OUT and reads IN in order, empty reads and failed writes dropped, a non-64-byte packet refused |
| `test_pacing.py`, `test_robot_transport.py` | `scorbot/pacing.py` lag ring arithmetic and delay rule, its off-by-default hook in the legacy stream loop; `Scorbot(transport=...)`: pyusb default, device_file and auto through the fake kernel32 (found, not found falls back, other errors raised), simulator accepts the argument |
| `test_transport_device_file.py` | `scorbot/transport/device_file.py` against a fake kernel32: exclusive open and its error messages, 64-byte write, in-place read, zero-byte retry limit, partial read, timeout by cancel, an empty read that took the whole timeout raised as a timeout, close |
| `test_read_timeout_check.py` | `scripts/read_timeout_check.py` (lab step T) against a fake kernel32: refuses without `--online`, never writes, always closes, the 4 to 6 s pass band |
| `test_hand_sweep_report.py` | `scripts/hand_sweep_report.py` on synthetic sweeps: range and end per joint, unwrapping across 0/65535, negative walks, counts per degree, switch bands as edge intervals (two passes, on at the start, wrist switches against both motors, unknown bits), refusal of simulated data, warnings for motors on, jumps and coarse sampling |
| `test_fit_calibration.py` | `scripts/fit_calibration.py:fit` pinned exactly (2026-10-12), because it decides the soft limits the arm is jogged inside: every refusal with the input that triggers it and the exact message, the order the checks run in (so two wrong things at once still report the first), the boundaries, and the full output for a clean and a noisy dataset (3 home, 4 fit, 5 verify, 6 move rows, so mixed-up counts or a mean/median swap show). Mutation-checked: 53 changes to `fit` (every guard, limit and output field), each caught |
| `test_review_session_rows.py` | `scorbot.lab.review.review_session_rows` pinned (2026-10-12): every problem message with the log row that triggers it, the tolerance and sign boundaries, report shape and defaults, sorted and deduplicated problems. Mutation-checked: 20 changes, each caught |
| `test_hand_sweep_analyse.py` | `scripts/hand_sweep_report.py:analyse` pinned (2026-10-12): every refusal and the order they run in, each warning with its boundary (jump, coarse, motors on, unknown bits), unwrapped counts, switch bands and their edges, the roll-axis band and the wrist-motors-apart note. Mutation-checked: 31 changes, 30 caught, the last is equivalent (the `, 1` floor only matters when both ends are 0) |
| `test_docs_index.py` | Every module, test file and script has a row in its folder's index (the `README.md` beside it); fails and names the file when one is added without a row. Matches whole file names or a pattern row such as `test_camera_*.py`, not a bare word |
| `test_check_docs.py` | `scripts/check_docs.py` finds each known problem in a small bad tree and passes a clean one: names, tracked local-notes files, control characters, `venv` without the dot, duplicate IDs, missing links, code-fence handling. |
| `test_coupled_moves.py` | Coupled joint moves on the facade (the pre-home move and the park) against the simulator: the plumbing, the gates, and that the motors asked for are the ones the vendor's joint formulas call for. Whether the wrist signs are right on the arm is for the first supervised trial |
| `test_joint_move.py` | `scorbot.joint_move` on its own: the vectors from the vendor's joint formulas, the cumulative-target ledger, roll drift, single-motor targets. Checks the arithmetic, not the arm |
| `test_inch_home.py` | `scorbot.inch_home` against a fake switch (positions along the approach direction), its constants, and `Scorbot.home_inch` through the simulator |
| `test_settle_seam.py` | The legacy settle checks near the 0/65535 count seam (BACKLOG 5): a target at 0 and a reading of 65532 are 3 counts apart, not 65532 |
| `test_robot_surface.py` | The public names and signatures of `Scorbot` pinned against `fixtures/scorbot_surface.json` (private names and module paths are ignored, so a refactor can move code; `UPDATE_SURFACE=1` rewrites the file on purpose), and `GripperMove` and the three exceptions keeping their module name (`scorbot.robot`: tracebacks, pickles) and resolvable annotations after moving to `gripper.py` and `errors.py` |
| `test_robot_guards.py`, `test_robot_connect.py`, `test_robot_error_paths.py` | Characterisation tests of `Scorbot` (2026-10-12, each guarded line mutation-checked): refusals before anything is queued, the real `connect`/`disconnect` against a fake `usb` package (a failed disable after the handshake, no controller, missing endpoints, the Windows `libusb_package` branch), and the command, stream and stop-settle error paths. Patch a name in the module that looks it up: `scorbot.stream_control.StreamCore`, not `scorbot.robot.StreamCore` |
| `test_preflight_more.py`, `test_calibration_load.py` | The remaining `preflight` branches and every rejection in `load_calibration` |
| `test_libdef_config_reads.py` | The per-message `libdef` helpers (`set_msg`, `countByte1`, `get_media`, `get_encoder`) do not re-parse `data.json`; only the `TIME_OUT_R` read inside the write-to-read gap stays |
| `test_pause_marker.py` | `syncro` pauses on the configured `MAX_COUNT` (BACKLOG 15), including a copy of `libsync` loaded while the config says 300 |
| `test_vendor_model.py`, `test_vendor_profile.py` | The vendor's count/angle formula and motion profile, read from the DLL |

Shared helpers: `desk_wire.py` (a fake USB wire that records the real `Scorbot`'s OUT packets, for `test_desk_byte_check.py`), `cli_support.py` (run `python -m scorbot.session` in-process or as a child). Import it as `from tests.<name> import ...` with a bare-name fallback, so both `unittest discover -s tests` and `python -m unittest tests.test_x` work.

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
