# ScorBot ER-4U Python control

This repository contains the original OpenScorbot USB controller code and a small Python adapter for the **Intelitek ScorBot ER-4U**. The current milestone is supervised Python control through the original controller. The adapter exposes fresh raw USB responses, legacy homing, and small **relative** joint jogs. Calibrated absolute base/shoulder/elbow moves are gated behind per-arm physical measurements and a validated calibration file. It does not provide Cartesian motion, a verified software stop, or autonomous control.

## Windows installation

For a robot PC with only VS Code installed, use [START_HERE_WINDOWS.md](START_HERE_WINDOWS.md). It starts with a GitHub ZIP download, installs the Python environment, and checks USB without commanding the arm.

From PowerShell in the repository root:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[windows]"
.\.venv\Scripts\python.exe -m scorbot.preflight
```

To move this working version to the robot PC, clone or download the `main` branch, or build a ZIP with `python scripts/build_bench_kit.py`. For the first lab visit, print the [G1 lab checklist](docs/G1_LAB_CHECKLIST.md). The kit includes the verified Zadig 2.9 USB driver tool when it is present under `dist/usb-tools/`.

The preflight only enumerates USB; it does not reset or command the robot. The original controller is identified in the legacy code as USB vendor/product `09F1:0007`. PyUSB also needs a libusb backend and a compatible Windows device driver. The `windows` extra supplies a libusb library, but it does not change the device driver. See the [PyUSB Windows FAQ](https://github.com/pyusb/pyusb/blob/master/docs/faq.rst) if discovery fails. Confirm the driver choice for your lab setup before changing it, since it may affect the Intelitek software. Follow the [Windows bench runbook](docs/WINDOWS_BENCH_RUN.md) on the robot PC.

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

This snippet illustrates the API. For the first hardware session, follow the [first lab visit procedure](docs/ARM_CONTROL_BENCH.md); it captures idle responses and requires a physical home check before offering a jog. `jog_joint` currently accepts `base`, `shoulder`, or `elbow`; live wrist jogs remain gated pending two-motor measurements. Each call is limited to 5 degrees and legacy speed values 1–20. The positive direction mapping is inherited from the old code and needs physical verification. Use the [offline jog preview](docs/OFFLINE_SAFETY_FIXES.md) to inspect planned motor counts before a supervised trial.

For one-joint supervised trials, follow the [arm-control bench procedure](docs/ARM_CONTROL_BENCH.md). Start with [raw-state recording](examples/record_raw_state.py), then use [bench_joint.py](examples/bench_joint.py) and [review_lab_logs.py](scripts/review_lab_logs.py). Use the [measurement and calibration guide](docs/PHYSICAL_CALIBRATION.md) when an independent angle reference is available. The fitter in `scripts/fit_calibration.py` refuses vendor-only data and requires holdout and movement verification before emitting a calibrated file.

To record an experiment (commands, controller state, camera frames, operator decisions) and replay it without hardware, see [Recording and replaying experiments](docs/EXPERIMENT_RECORDING.md). Try it first with `.\.venv\Scripts\python.exe examples\make_synthetic_session.py`. To develop or rehearse without the arm, use `SimulatedScorbot`, or add `--simulate` to the lab scripts. It runs the same safety code against a fake controller, and everything it produces is labelled simulated. To list, export to CSV, or compare recorded runs, use `python -m scorbot.session list|export|compare`.

`disable()` is a queued controller command. It cannot interrupt a stalled command and is **not** an emergency stop. The physical emergency stop remains authoritative. If a command times out, the SDK faults and rejects more motion; it cannot guarantee motor shutdown after USB loss or a Python crash.

Legacy homing switch searches now have a provisional 30-second deadline per axis and check a cancellation signal. That deadline has not been tuned on the physical arm. An error or `homed: true` from the SDK is not independent proof of motor state or home calibration.

## Current design

```text
Python script
   -> scorbot.Scorbot (validation, state, JSONL event log)
   -> legacy command worker / sync worker
   -> OpenScorbot USB packet code
   -> original ER-4U controller
```

The original code remains under `openScorbot/`. The new adapter is under `scorbot/`. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the system review, known gaps, and the path toward a research SDK. The old PyQt GUI is retained as reference code; it is not the SDK interface.

## Development checks

The tests use synthetic USB responses and measurements; they do not open USB or move the robot:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The current adapter is based on static code inspection. Hardware communication, homing completion, joint directions, and stop behavior still require supervised bench validation on your ER-4U. The SDK timestamps successful USB reads, but a new packet does not by itself prove movement or a switch state. Motor behavior after communication failure has not been verified with this Python path.

## Original project

OpenScorbot was developed by José Luis Pérez Pérez and Yolanda M. Gimeno Rodríguez at the University of La Laguna. The [original project](https://github.com/tidus747/openScorbot) includes the USB protocol implementation, a Qt GUI, mechanical models, and a Spanish-language manual in `references/`. This repository retains the GPL-3.0 license.
