# Start here on the robot PC

This path needs only Windows, VS Code, an internet connection, and the original ScorBot ER-4U USB controller. You do **not** need Git, the old OpenScorbot GUI, a camera, or an AI model for the first connection check. VS Code is an editor; install Python separately.

## 1. Get the project and Python

1. On the robot PC, open the [standalone GitHub repository](https://github.com/Sebastianr8243/scorbot-er4u-python). Select **Code → Download ZIP**, then extract it to a writable folder. Open that extracted folder in VS Code. A ZIP downloaded from GitHub contains source code but not the optional Zadig executable from the locally built bench kit.
2. Open **Terminal → New Terminal** in VS Code and select PowerShell. The terminal should be in the folder containing `pyproject.toml` and `scripts/`.
3. Run `py -3 --version`. If Python is missing or older than 3.10, install [Python 3.13 from Python.org](https://www.python.org/downloads/release/python-31315/) using the Windows installer for that PC's architecture. Reopen the VS Code terminal after installing and check again. Python 3.13.5 passed this project's software setup on the development PC.

## 2. Install the Python dependencies

In the VS Code PowerShell terminal, run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1
```

This creates `.venv`, installs the package, PyUSB, NumPy, and the Windows libusb library, then runs the software tests. It does not access USB, change drivers, or move the arm. The execution-policy option applies only to this one PowerShell process. If Python is missing, the script prints the official download page and stops. Internet access is needed for the package install.

## 3. Check that Windows and Python see the controller

Plug the **original ER-4U controller** into the PC with a USB data cable and power it according to your lab procedure. Close Intelitek software and the old GUI. Keep the physical emergency stop accessible. Then run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows_usb_check.ps1
```

The script lists any present Windows device with hardware ID `VID_09F1&PID_0007`, then runs the read-only Python preflight. The desired result is `PASS USB 09F1:0007`. This proves the PC and Python can enumerate the controller; it does **not** prove that robot commands work. Neither the Windows listing nor the preflight sends motion commands.

If Windows does not list the ID, check power, cable, USB port, and the controller's **Hardware Ids** in Device Manager. If Windows lists the correct ID but Python preflight fails, read [the USB driver section of the bench guide](docs/WINDOWS_BENCH_RUN.md#2-usb-driver-and-read-only-preflight). Only consider [official Zadig](https://zadig.akeo.ie/) for that exact device after recording the current driver. Changing the driver can prevent the original Intelitek software from using the controller.

After the preflight passes, follow the [state-only session](docs/WINDOWS_BENCH_RUN.md#3-state-only-python-session). Connecting starts the legacy USB handshake, so do that step with an operator at the arm. Do not run the homing and jog example until the required physical homing start pose is known and the state-only session succeeds.

After that session, use [the calibration guide](docs/CALIBRATION_START.md) to record raw encoder data and plan measured joint calibration.
