# CLAUDE.md

Python SDK, legacy USB protocol code, lab bench tools and offline research tools for the Intelitek ScorBot ER-4U arm (USB `09F1:0007`, original Controller-USB). Nothing here is hardware-validated. Milestone: supervised connect, raw state, legacy homing, small relative joint jogs. Deeper docs: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/PROTOCOL.md](docs/PROTOCOL.md) and [docs/SAFETY_CASE.md](docs/SAFETY_CASE.md), [docs/HARDWARE_REFERENCE.md](docs/HARDWARE_REFERENCE.md).

Each subdirectory has its own CLAUDE.md with module-level rules. Read it before editing there.

## Safety invariants (never break)

| Rule | Why | Enforced by |
|---|---|---|
| Tests, simulators, scripts you run and CI never open USB or move the arm. Never call `Scorbot.connect()` (real class), `examples/python_control.py`, or a lab script without `--simulate`. | The legacy handshake energises the motors. | `SimulatedScorbot` replaces only `connect`/`disconnect`; `tests/test_simulated.py:test_import_loads_no_usb_and_leaves_sys_path_alone`; CI comment in `.github/workflows/tests.yml`. No test-time guard blocks USB access; review does. |
| Do not change `openScorbot/` packet construction, sequence bytes, or the `WRITE`/`READ` sleeps without captured traces. | Byte layout and timing are unverified against the controller; the sleeps are load-bearing. | `docs/USB_CAPTURE.md`, `scripts/usb_trace.py`; `scorbot/provenance.py:motion_source_sha256` fingerprints the motion path into every lab log. |
| `disable()` is not an emergency stop. Never document or name it as one. | It is queued behind the running command and cannot interrupt it. | `scorbot/robot.py:Scorbot.disable` docstring; `README.md`. The physical stop is authoritative. |
| Any fault latches the session and rejects further motion. New failure paths must call `Scorbot._latch_fault`. | Motor and home state are unverified after a fault. | `scorbot/robot.py:_command`, `_motion_state`; `tests/test_simulated.py:test_every_fault_kind_latches_the_session`. Known exceptions that set `_fault` directly: `jog_joint`/`get_joint_angles` on invalid calibrated state. |
| Wrist jogs stay disabled. | Pitch and roll drive two coupled motors; unmeasured. | `Scorbot.jog_joint`; `tests/test_arm_control.py:test_wrist_jog_rejected_without_queuing_motion`. |
| Jog ceiling is 5 degrees in the SDK (`max_jog_degrees`, constructor rejects more) and 1 degree in `examples/bench_joint.py --delta`. Speed is an int 1-20. | Directions and scales are inherited, not measured. | `Scorbot.__init__`, `jog_joint`; `test_jog_ceiling_cannot_be_raised_past_five_degrees`. |
| No Cartesian motion, no coordinated motion, no gripper. `move_joint` is one bounded jog step and needs a validated calibration. | Kinematics, frames and signs are unverified. Legacy order 19 (`moveXYZ`) and 14/15 (clamp) exist but `Scorbot` never sends them. | `scorbot/kinematics.py` is offline and wired into nothing. |
| `home()` needs `start_position_confirmed=True`, enabled motors, and refuses `home_switch_bits` with unknown bits (>= 32). | `libdef.get_switch` misreads byte 5 >= 32; homing assumes a fixed start pose. | `Scorbot.home`; `test_home_refuses_switch_byte_the_legacy_decoder_misreads`. |
| Real and simulated data never share a session, review or comparison. | A simulated run could pass as evidence. | `SessionWriter.log_state` raises `SessionError`; `analysis.compare` gives no pooled row for mixed sources; `scripts/review_lab_logs.py` flags mixed logs. |
| Calibration refuses vendor-display (SCORBASE) data and soft-limit spans beyond the manual's travel. | Only independent physical angle measurements validate a scale. | `scripts/fit_calibration.py:fit` (needs `reference_source == "physical"`), `scorbot/calibration.py:load_calibration`, `scorbot/nominal.py:check_soft_limit_span`. Only spans are compared; manual zero/sign are unknown. |
| Never commit Intelitek manuals (`references/er4u_manual_100343-b.pdf`, `references/controller_usb_manual_100341-g.pdf`). | Copyrighted; the repo is public. | `.gitignore`. Note `scripts/build_bench_kit.py` zips all of `references/` (no PDF exclusion); check before building a kit. |

Nominal manual values are priors, never calibration: `scorbot/nominal.py` values carry `status = "nominal, from manual, not measured"`. Do not present a legacy scale (`openScorbot/motion_profile.py:COUNTS_PER_DEGREE`) as measured.

## Layers

```mermaid
flowchart TD
  A["examples/, scripts/ (lab and offline tools)"] --> B["scorbot.Scorbot / SimulatedScorbot (gates, fault latch, JSONL log)"]
  A --> S["scorbot.session (MCAP record, replay, analysis)"]
  B --> L["openScorbot (legacy workers via Scorbot._legacy)"]
  B -. "SimulatedScorbot swaps this out" .-> M["SimulatedController (no USB)"]
  L --> U["pyusb -> ER-4U controller"]
```

## Repo map

| Path | Responsibility |
|---|---|
| `scorbot/` | Public SDK: facade, state decode, calibration, nominal manual values, kinematics (offline), simulator, preflight |
| `scorbot/session/` | MCAP session recorder, replay, analysis, CLI (`python -m scorbot.session`). Never imports USB |
| `openScorbot/` | Original GPL OpenScorbot protocol code plus `motion_profile.py`; frozen legacy backend, PyQt GUI kept as reference |
| `examples/` | Supervised bench procedures (`--simulate` capable), synthetic session, offline preview and kinematics check |
| `scripts/` | Offline analysis (fit, review, live view, USB trace), kit and Windows setup |
| `tests/` | `unittest` suite, no hardware |
| `docs/` | Design, hardware reference, bench and lab checklists, capture and recording guides. `docs/superpowers/` holds old plans and specs |
| `tools/foxglove/` | Foxglove layout for recorded sessions |
| `src/`, `models/`, `references/` | GPL license text only (no control code) / OpenSCAD, STL, DXF parts (encoder, home jig) / Spanish 4pc manual |

## Commands

```powershell
python -m pip install -e ".[dev]"              # add ,kinematics for the Robotics Toolbox cross-check; windows extra adds libusb-package; gui adds PyQt5
uv sync --locked --extra windows --extra test  # lab PC: Python 3.12 + exact versions from uv.lock; after editing deps run `uv lock` (CI checks it)
python -m compileall -q scorbot openScorbot scripts examples tests
python -m unittest discover -s tests -v        # ~90 s, 309 tests, 2 expected failures (documented legacy bugs); pytest -n auto is faster
python examples/make_synthetic_session.py --root <tmpdir>   # CI smoke test
ruff check .                                   # CI rules incl. bugbear (openScorbot/ excluded); `pre-commit install` runs it on every commit
```

Simulated rehearsal (no USB; every output says SIMULATED). Use a `rehearsal\` folder, never `logs\`:

```powershell
python -m scorbot.lab --simulate --profile rehearsal\lab.json --logs rehearsal   # guided session rehearsal
python examples\record_raw_state.py --output rehearsal\idle-01.jsonl --robot-id lab-er4u-1 --arm-label x --controller-label x --driver none --operator XX --pose-note rehearsal --seconds 2 --simulate --acknowledge-connect-handshake
python examples\bench_joint.py --output rehearsal\base-01.jsonl --robot-id lab-er4u-1 --arm-label x --controller-label x --driver none --operator XX --start-pose-note rehearsal --joint base --delta 1 --simulate --acknowledge-supervised-motion
python scripts\review_lab_logs.py --idle rehearsal\idle-01.jsonl --bench rehearsal\base-01.jsonl
python -m scorbot.session list|export|compare <paths>
```

CI (`.github/workflows/tests.yml`): Windows and Ubuntu, Python 3.10 and 3.13, compileall, unittest, synthetic session.

## Conventions

- Target is a Windows lab PC with only Python and VS Code. Keep lab-PC tools standard-library-first (`scripts/usb_trace.py`, `watch_lab_log.py` and `scorbot/nominal.py` are stdlib only). Runtime deps: `mcap`, `numpy`, `pyusb`. Python >= 3.10.
- Docs show PowerShell with `.\.venv\Scripts\python.exe`. Open files with `encoding="utf-8"`; console output on Windows may be cp1252.
- Primary evidence is append-only JSONL (`open("x")`, never overwrite) plus an MCAP session (`scorbot/session`). MCAP is best-effort secondary; JSONL is written first. Payloads are strict JSON (`allow_nan=False`).
- Encoder counts are unsigned 16-bit with a 65535 (one's complement) wrap. Take differences with `scorbot.calibration.signed_count_delta`, never subtract raw or signed counts.
- Claims about hardware must say "unverified" unless a bench trace exists. Say so in code comments and docs too.
- Tests use `unittest`. Optional deps skip (`hypothesis`, `roboticstoolbox`, `usb`).
- Commits: imperative sentence-case subject, no prefix, body explains why. Trailers on Claude-authored commits:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` then `Claude-Session: <session url>`. Keep the trailers the session prompt gives you.
- Do not push or open PRs unless asked.

## Where to look

| Question | File |
|---|---|
| Why is the design what it is, open risks | `docs/ARCHITECTURE.md` |
| Packet layout, command codes | `docs/PROTOCOL.md`, `openScorbot/libhex.py`, `scorbot/state.py` |
| Safety argument | `docs/SAFETY_CASE.md` |
| Known bugs and next work | `docs/BACKLOG.md` |
| Manual facts, LEDs, controller safety | `docs/HARDWARE_REFERENCE.md` |
| First lab visit | `docs/G1_LAB_CHECKLIST.md`, `docs/ARM_CONTROL_BENCH.md`, `docs/WINDOWS_BENCH_RUN.md` |
| Prompt and warning design | `docs/OPERATOR_UX.md` |
| Recording, session format, viewers | `docs/EXPERIMENT_RECORDING.md` |
| Wireshark/USBPcap comparison | `docs/USB_CAPTURE.md` |
| Measuring joint angles, CSV format | `docs/PHYSICAL_CALIBRATION.md` |

## Before you change X, read Y

| Change | Read first |
|---|---|
| `scorbot/robot.py` gates, faults, threading | `tests/test_python_api.py`, `tests/test_simulated.py`, `docs/ARCHITECTURE.md` sections 8, 11 |
| `openScorbot/*` | `openScorbot/CLAUDE.md`, `docs/USB_CAPTURE.md` |
| Session schema or topics | `scorbot/session/schemas.py`, `docs/EXPERIMENT_RECORDING.md`; bump `SCHEMA_VERSION` only with a reader migration |
| Prompts in `examples/bench_joint.py` | `docs/OPERATOR_UX.md`, `docs/G1_LAB_CHECKLIST.md`; rehearse with `--simulate` |
| Calibration fields | `scorbot/calibration.py`, `scripts/fit_calibration.py`, `docs/PHYSICAL_CALIBRATION.md` (loader and fitter must agree) |
| Log event names | `scripts/watch_lab_log.py:ALARM_EVENTS`, `scripts/review_lab_logs.py` (both parse them) |
