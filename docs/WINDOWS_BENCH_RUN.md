# ER-4U Windows bench run

Use this on the Windows PC connected to the **original ScorBot ER-4U USB controller**. The Python adapter and homing sequence have not yet been verified on this robot. Keep a trained operator at the arm, the work area clear, and the physical emergency stop within reach throughout the session. Do not run the old GUI or Intelitek application at the same time.

## 1. Copy and install

If this PC only has VS Code, follow [START_HERE_WINDOWS.md](../START_HERE_WINDOWS.md) first. It does not require Git.

Copy the **current working tree** to the robot PC, including `scorbot/`, `openScorbot/`, `examples/`, `pyproject.toml`, and `tests/`. The easiest transfer is the ZIP created by `python scripts/build_bench_kit.py`; extract it to a writable directory on the robot PC. This ZIP also includes `usb-tools/zadig-2.9.exe` when the verified download is present in `dist/usb-tools/`. Alternatively, clone the standalone [scorbot-er4u-python repository](https://github.com/Sebastianr8243/scorbot-er4u-python):

```powershell
git clone https://github.com/Sebastianr8243/scorbot-er4u-python.git
cd scorbot-er4u-python
```

A fresh clone of the original OpenScorbot repository does not include the Python adapter. In PowerShell, change to the extracted or cloned repository directory. Python 3.10 or newer and internet access are required for this install:

```powershell
py -3 --version
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[windows]"
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The tests do not access USB. If `py -3` is unavailable, install a current Python 3 release from [Python.org](https://www.python.org/downloads/windows/), then repeat this section. The Python package installs the `libusb-package` library, **not** a Windows USB device driver.

## 2. USB driver and read-only preflight

In Windows Device Manager, inspect the controller's **Hardware Ids**. Confirm vendor/product `VID_09F1&PID_0007`, and record its current driver before changing anything. The [libusb Windows guide](https://github.com/libusb/libusb/wiki/Windows#driver-installation) recommends WinUSB for libusb access. PyUSB's [project README](https://github.com/pyusb/pyusb/blob/master/README.rst) explains the separate Windows libusb library requirement.

With the arm clear and the physical stop accessible, run:

```powershell
.\.venv\Scripts\python.exe -m scorbot.preflight
```

Expected: PASS for Python, required packages, the Windows libusb backend, and USB controller `09F1:0007`; exit code 0. This preflight only enumerates USB. It does **not** reset the controller, open the robot session, enable motors, or command motion. Check the exit code with `$LASTEXITCODE`. If any check fails, stop here, keep the full error output, and fix that check before connecting.

If the controller appears in Device Manager with the correct ID but preflight cannot enumerate it, the current device driver may be incompatible. The transfer ZIP can include [official Zadig 2.9](https://zadig.akeo.ie/), a driver installation tool; it is **not** installed by extracting the ZIP. Before running it, verify the executable's Windows signature says **Akeo Consulting** and its SHA-256 is `4ECAA95DF3DA3621486A043AEF8B3050B8BAFE7C901402871E816229EF82039B`:

```powershell
Get-AuthenticodeSignature .\usb-tools\zadig-2.9.exe
Get-FileHash .\usb-tools\zadig-2.9.exe -Algorithm SHA256
```

If the ZIP did not include Zadig, download it from the official site above on the robot PC; check its signature, but expect its hash to differ if a newer release is available. In Zadig, use **Options → List All Devices** if needed, select only the controller with USB ID `09F1:0007`, choose **WinUSB**, and install or replace the driver for that exact device. Confirm the ID again before clicking the install button. Administrative rights are required. Rebinding the driver may prevent the Intelitek software from using the controller; keep the original driver details so it can be restored. Rerun preflight afterward. Do not change drivers if preflight already passes. [Zadig's official guide](https://github.com/pbatard/libwdi/wiki/Zadig) shows the device selection and driver installation screens.

## 3. State-only Python session

**Connecting is not electrically passive:** the legacy handshake sends motor-on packets, and the adapter queues motor-disable immediately afterward. That behavior and its timing are not yet measured on this controller. The following session asks for state only; it does not intentionally command arm motion. Have the operator watch the arm and use the physical stop if anything moves unexpectedly.

Save the following as `bench_state.py` in the repository root:

```python
from dataclasses import asdict
import json
import time

from scorbot import Scorbot

with Scorbot(log_path="logs/state_only.jsonl") as robot:
    for _ in range(3):
        print(json.dumps(asdict(robot.get_state()), sort_keys=True))
        time.sleep(0.5)
```

Run it with:

```powershell
.\.venv\Scripts\python.exe .\bench_state.py
Get-Content .\logs\state_only.jsonl
```

Expected: three JSON lines with `connected: true`, `enabled: false`, `homed: false`, a UTC timestamp, six raw `encoder_counts`, six `controller_error_counts`, and `home_switch_bits`. Values are controller counts, **not joint angles**; do not infer safe travel from them. The JSONL log should include `connect` and controller command events. If the arm moves, output is missing or implausible, USB fails, or an exception appears, use the physical stop as needed and end the session. Record the exact error and observations. A software `disable()` or process exit is not a verified emergency stop.

For a timestamped set of raw samples to start calibration, use [record_raw_state.py](../examples/record_raw_state.py) and [the calibration guide](CALIBRATION_START.md) after this first state-only check succeeds.

## 4. Homing and one-degree jog: supervised only

Proceed only after the state-only session succeeds **and** the operator can independently identify and place the arm in the required legacy homing start pose. The code does not document exact joint angles for this pose. Its homing order is shoulder, elbow, wrist pitch, wrist roll, then base; it is not safe to assume it can start anywhere. If the start pose is unknown, stop after section 3 and record that gap. Clear the entire possible travel path, select a base direction with known clearance, and have the operator ready at the physical stop.

The existing [example](../examples/python_control.py) executes `enable()`, `home(start_position_confirmed=True)`, and a relative `jog_joint("base", 1, speed=10)`. Review it on the robot PC, then run **once** under direct supervision:

```powershell
.\.venv\Scripts\python.exe .\examples\python_control.py
Get-Content .\session.jsonl
```

Expected: one raw-state print before homing, one after homing, then a single one-degree **requested** base jog and a motor-disable command. Confirm actual direction and displacement visually; the positive direction and degree conversion are inherited from the legacy code and remain unverified. `session.jsonl` should contain connect, command, and state snapshots. A returned command or `homed: true` only reflects software history; neither proves the switches, position, or motor state. If any motion is unexpected, press the physical emergency stop. Do not issue a reverse jog, repeat homing, or retry a failed command until the cause is understood.

## If something fails

| Observation | Action |
| --- | --- |
| `ER-4U USB controller 09F1:0007 was not found` | Check power, cable, Device Manager hardware ID and driver, then rerun preflight. |
| Backend/USB permission error | Capture the full traceback and driver name. Recheck the exact-device WinUSB binding; do not change unrelated drivers. |
| Homing or jog raises `ScorbotError`, times out, or the robot stalls | Use the physical stop if motion or motor state is uncertain. Do not retry or rely on queued `disable()`. Keep the log and traceback. |
| Python exits during a command or USB disconnects | Treat motor state as unknown. Use the physical stop and restore a safe state according to the lab's controller procedure before another session. |

After a normal, completed session, `with Scorbot(...)` calls `disconnect()`; it sends a legacy shutdown command and releases USB resources. This path and its effect on hardware still need bench verification. Preserve the original driver information so it can be restored if necessary.

## Record the first bench result

Save the PC's Windows and Python versions, repository commit or copy date, `pip check` and test results, preflight output, Device Manager hardware ID and bound driver, USB controller/firmware label if visible, operator and start-pose photo or sketch, time of session, printed states, `session.jsonl` or `logs/state_only.jsonl`, any traceback, actual direction and approximate motion of the one-degree jog, and what happened on disconnect. Note any physical stop activation. These observations are the evidence needed before calling this driver hardware verified or adding autonomous control.
