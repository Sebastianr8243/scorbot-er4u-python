# OpenScorbot: Python control for the ScorBot ER-4U

This repository contains the original OpenScorbot USB controller code and a small Python adapter for the **Intelitek ScorBot ER-4U**. The current milestone is supervised Python control through the original controller. The adapter exposes raw encoder readings, legacy homing, and small **relative** joint jogs. It does not yet provide calibrated absolute joint positions, Cartesian motion, a verified software stop, or autonomous control.

## Windows installation

From PowerShell in the repository root:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[windows]"
.\.venv\Scripts\python.exe -m scorbot.preflight
```

To move this working version to the robot PC, run `python scripts/build_bench_kit.py` here and copy the ZIP from `dist/`. The original upstream GitHub repository does not contain these local SDK changes yet.

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

The complete script is in [examples/python_control.py](examples/python_control.py). After installation, run it from the repository root with `.\.venv\Scripts\python.exe examples\python_control.py`. Start by running just `connect()` and `get_state()`, then verify the homing start pose and each direction before allowing a jog. `jog_joint` accepts `base`, `shoulder`, `elbow`, `wrist_pitch`, or `wrist_roll`; each call is limited to 5 degrees and legacy speed values 1–20. The positive direction mapping is inherited from the old code and needs physical verification.

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

The tests do not open USB or move the robot:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The current adapter is based on static code inspection. Hardware communication, homing completion, joint directions, and stop behavior still require supervised bench validation on your ER-4U.

## Original project

OpenScorbot was developed by José Luis Pérez Pérez and Yolanda M. Gimeno Rodríguez at the University of La Laguna. The [original project](https://github.com/tidus747/openScorbot) includes the USB protocol implementation, a Qt GUI, mechanical models, and a Spanish-language manual in `references/`. This repository retains the GPL-3.0 license.
