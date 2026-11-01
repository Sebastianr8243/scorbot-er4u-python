# Stack 2: our own USB protocol (`scorbot.Scorbot`)

> **Experimental.** This is the long-term no-DLL path. It has never moved the arm; its one real home attempt (2026-10-06) failed. To move the arm today use the vendor-DLL path: [lab/RUN_THE_ARM.md](lab/RUN_THE_ARM.md) and [API.md](API.md). Moved here from the old README unchanged apart from this banner and link paths.

This repository contains the original OpenScorbot USB controller code and a small Python adapter for the **Intelitek ScorBot ER-4U**. The current milestone is supervised Python control through the original controller. The adapter exposes fresh raw USB responses, legacy homing, and small **relative** joint jogs. Calibrated absolute base/shoulder/elbow moves are gated behind per-arm physical measurements and a validated calibration file. It does not provide Cartesian motion, a verified software stop, or autonomous control.

## Windows installation

For a robot PC with only VS Code installed, use [START_HERE_WINDOWS.md](../START_HERE_WINDOWS.md). It starts with a GitHub ZIP download, installs the Python environment, and checks USB without commanding the arm.

From PowerShell in the repository root:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[windows]"
.\.venv\Scripts\python.exe -m scorbot.preflight
```

Going to the lab? Start with lab/LAB_VISIT_HANDOFF.md (in git history). To move this working version to the robot PC, clone or download the branch you intend to test, or build a ZIP with `python scripts/build_bench_kit.py`. For the first lab visit, keep the short G1 lab card open or printed. The kit includes the verified Zadig 2.9 USB driver tool when it is present under `dist/usb-tools/`.

The preflight only enumerates USB; it does not reset or command the robot. The original controller is identified in the legacy code as USB vendor/product `09F1:0007`. PyUSB also needs a libusb backend and a compatible Windows device driver. The `windows` extra supplies a libusb library, but it does not change the device driver. See the [PyUSB Windows FAQ](https://github.com/pyusb/pyusb/blob/master/docs/faq.rst) if discovery fails. Confirm the driver choice for your lab setup before changing it, since it may affect the Intelitek software. Follow the [Windows bench runbook](lab/WINDOWS_BENCH_RUN.md) on the robot PC.

## First Python session

Use the robot's known legacy homing start pose. Keep the physical emergency stop accessible. The legacy USB handshake sends motor-on packets; `connect()` queues a motor-disable command immediately afterward. This transition has not been measured on this lab's hardware.

```python
from dataclasses import asdict
from scorbot import Scorbot

with Scorbot(log_path="session.jsonl") as robot:
    print(asdict(robot.get_state()))  # Raw controller counts, not joint angles.
    robot.enable()
    robot.home(start_position_confirmed=True)
    robot.jog_joint("base", 1, speed=10)  # Relative one-degree jog.
    robot.disable()
```

This snippet illustrates the API. For the first hardware session, follow the first lab visit procedure; it captures idle responses and requires a physical home check before offering a jog. `jog_joint` currently accepts `base`, `shoulder`, or `elbow`; live wrist jogs remain gated pending two-motor measurements. Each call is limited to 5 degrees and legacy speed values 1–20. `speed` is the most encoder counts added per host packet (about 13 ms), not the controller's ten SCORBASE speed levels. The positive direction mapping is inherited from the old code and needs physical verification. Use the offline jog preview (`python examples\preview_jog.py --joint base --delta 1 --speed 10`) to inspect planned motor counts before a supervised trial; it shows requested setpoints, not observed motion, and opens no USB.

For one-joint supervised trials, follow the arm-control bench procedure. Start with [raw-state recording](../examples/record_raw_state.py), then use [bench_joint.py](../examples/bench_joint.py) and [review_lab_logs.py](../scripts/review_lab_logs.py). Use the [measurement and calibration guide](lab/PHYSICAL_CALIBRATION.md) when an independent angle reference is available. The fitter in `scripts/fit_calibration.py` refuses vendor-only data and requires holdout and movement verification before emitting a calibrated file.

To record an experiment (commands, controller state, camera frames, operator decisions) and replay it without hardware, see Recording and replaying experiments. Try it first with `.\.venv\Scripts\python.exe examples\make_synthetic_session.py`. To develop or rehearse without the arm, use `SimulatedScorbot`, or add `--simulate` to the lab scripts. It runs the same safety code against a fake controller, and everything it produces is labelled simulated. To list, export to CSV, or compare recorded runs, use `python -m scorbot.session list|export|compare`.

`disable()` is a queued controller command. It cannot interrupt a stalled command and is **not** an emergency stop. `request_stop()` asks a jog in progress to end early (`jog_joint` then raises `MotionStopped`); it needs a working USB link, the arm coasts, and it has not been tried on the arm, so it is **not** an emergency stop either. The physical emergency stop remains authoritative. If a command times out, the SDK faults and rejects more motion; it cannot guarantee motor shutdown after USB loss or a Python crash.

Legacy homing switch searches now have a provisional 30-second deadline per axis and check a cancellation signal. That deadline has not been tuned on the physical arm. An error or `homed: true` from the SDK is not independent proof of motor state or home calibration.

## Current design

```text
Python script
   -> scorbot.Scorbot (validation, state, JSONL event log)
   -> legacy command worker / sync worker
   -> OpenScorbot USB packet code
   -> original ER-4U controller
```

The original code remains under `openScorbot/`. The new adapter is under `scorbot/`. See [docs/design/ARCHITECTURE.md](design/ARCHITECTURE.md) for the system review, known gaps, and the path toward a research SDK. The old PyQt GUI is retained as reference code; it is not the SDK interface.

## Scripts and examples

Run any of these with `--simulate` first. Per-script detail lives in [scripts/README.md](../scripts/README.md) and [examples/README.md](../examples/README.md); add a row there and here when you add a script.

| Script | Moves the arm? | What it does |
|---|---|---|
| `python -m scorbot.lab` | Yes, unless `--simulate` | The guided lab session: the main entry point |
| `examples/bench_joint.py` | Yes, unless `--simulate` | One home and one jog of at most 1 degree |
| `examples/bench_stream.py` | Yes, unless `--simulate` | One streaming trial (never run on the arm) |
| `examples/bench_gripper.py` | Yes, unless `--simulate` | One gripper trial (never run on the arm) |
| `examples/policy_loop.py` | Yes, unless `--simulate` | A bounded, clamped multi-joint policy run (never run on the arm); `scripts/export_policy_dataset.py` turns it into a dataset |
| `scripts/lab_day.py` | Yes, unless `--simulate` | Runs lab steps S, T, P (X) and G of `docs/lab/LAB_CHECKLIST.md` in order, each against its pass line |
| `examples/record_raw_state.py` | Connects, no motion, unless `--simulate` | Idle state recording |
| `tools/usbc_home/` | `--online` only | Vendor-DLL home; needs a 32-bit Python and the vendor DLL |
| `tools/usbc_server/` + `python -m scorbot.bridge` | `--online` only | One homed DLL session serving the Python API (`scorbot.bridge.Arm`); see [RUN_THE_ARM](lab/RUN_THE_ARM.md) |
| `examples/arm_characterise.py`, `examples/pick_place.py` | Yes, through the arm server | Measure frame, scale, repeatability and grid error; scripted pick-and-place with a per-run record |
| `examples/preview_jog.py`, `kinematics_check.py`, `make_synthetic_session.py` | No | Offline previews and a synthetic session |
| `scripts/*.py` (calibration, trace and log readers) | No | Offline analysis of logs and USB captures |
| `scripts/setup_windows.ps1`, `windows_usb_check.ps1` | No | Windows setup and a read-only USB check |

## Hardware reference

Specifications, controller safety behaviour and LED meanings from the Intelitek ER-4u and Controller-USB manuals are summarised in [docs/manual/HARDWARE_REFERENCE.md](manual/HARDWARE_REFERENCE.md). The manuals themselves are copyrighted and kept out of this public repository. The [complete visual verification report](manual/SCORBOT_Manual_Verification.md) records exact source pages, transcribed tables, contradictions and unanswered questions; its [numeric YAML appendix](manual/SCORBOT_Numeric_Facts.yaml) preserves units and evidence tags. These are manual transcriptions, not hardware calibration or verified runtime limits.

## Research tools (offline)

None of these open USB or command the arm.

- `scripts/watch_lab_log.py`: read-only live view of a lab run in a second terminal.
- `scripts/usb_trace.py` with [docs/lab/USB_CAPTURE.md](lab/USB_CAPTURE.md): read Wireshark/USBPcap captures and compare the Intelitek software's packets with this code's.
- `scorbot.kinematics` and `examples/kinematics_check.py`: nominal DH model (forward and inverse kinematics, trapezoidal trajectories) for validating the legacy `libdef.cIn`. The geometry is unmeasured, and the model is not wired into any motion command.
- `tools/foxglove/scorbot_lab_layout.json`: Foxglove layout for recorded sessions; see Viewing recordings.
- design/OPERATOR_UX.md (in git history): the evidence behind the prompts and warnings, and the UX backlog.
- `tests/test_legacy_properties.py`: Hypothesis property tests for the encoder and packet arithmetic. Install with `pip install -e ".[dev]"`; add `kinematics` to also run the Robotics Toolbox cross-check.

## Development checks

The tests use synthetic USB responses and measurements; they do not open USB or move the robot:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"   # once: pytest, coverage, hypothesis, ruff
.\.venv\Scripts\python.exe -m pytest -n 2 tests          # fast loop while editing: the gate suite (1011 tests, 183 s on one PC on 2026-10-13)
.\.venv\Scripts\python.exe -m pytest -n 2 tests tests_extra   # everything: run before a push (not timed)
.\.venv\Scripts\python.exe -m coverage run -m pytest; .\.venv\Scripts\python.exe -m coverage combine; .\.venv\Scripts\python.exe -m coverage report
.\.venv\Scripts\ruff.exe check .                          # correctness lint
```

`python -m unittest discover -s tests` works with the small `test` extra; the Windows setup script installs it automatically.
GitHub runs all of this on Windows and Linux, with Python 3.10 and 3.13, on
every push. The coverage table appears on each run's summary page.

The current adapter is based on static code inspection. Hardware communication, homing completion, joint directions, and stop behavior still require supervised bench validation on your ER-4U. The SDK timestamps successful USB reads, but a new packet does not by itself prove movement or a switch state. Motor behavior after communication failure has not been verified with this Python path.

## Original project

OpenScorbot was developed by José Luis Pérez Pérez and Yolanda M. Gimeno Rodríguez at the University of La Laguna. The [original project](https://github.com/tidus747/openScorbot) includes the USB protocol implementation, a Qt GUI, mechanical models, and a Spanish-language manual in `references/`. This repository retains the GPL-3.0 license.
