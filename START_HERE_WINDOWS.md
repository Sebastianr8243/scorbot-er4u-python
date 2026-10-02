# Start here on the robot PC

This is the shortest path for a lab PC that may have only VS Code. Git, the old OpenScorbot GUI, a camera, and an AI model are not needed for the first USB check. VS Code is an editor; Python is installed separately.

If this checkout already has a working `.venv` and the setup script passed earlier, skip to step 3. You do not need to reinstall packages or rerun the full test suite on every lab visit. If you downloaded a new ZIP or changed Python, run step 2 once for that checkout.

## 1. Get the project and Python

1. On the robot PC, open the [repository](https://github.com/Sebastianr8243/scorbot-er4u-python). Select the branch you intend to test, then choose **Code → Download ZIP**. Extract it to a writable folder and open that folder in VS Code. If Git already works, pulling the same branch is fine. A GitHub ZIP does not contain the optional Zadig executable from a locally built bench kit.
2. Open **Terminal → New Terminal** in VS Code and select PowerShell. The terminal should be in the folder containing `pyproject.toml` and `scripts/`.
3. Run `py -3 --version`. If Python is missing or older than 3.10, install [Python 3.13 from Python.org](https://www.python.org/downloads/release/python-31315/) using the Windows installer for that PC's architecture. Reopen the VS Code terminal after installing and check again. Python 3.13.5 passed this project's software setup on the development PC.

## 2. Install the Python dependencies

### Option A (recommended): `uv`, exact locked versions

`uv` installs Python 3.12 and the exact package versions in `uv.lock`, without
admin rights, so the lab PC matches the development PC and the GPU server.
Install `uv` once (per user, no admin):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Reopen the terminal, then in the project folder:

```powershell
uv sync --locked --extra windows --extra test
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

`uv sync` creates or updates `.venv` with Python 3.13 (`.python-version`; 3.13 is the first Windows release with a fine-grained monotonic clock) and
the locked packages; `--locked` refuses to run if `uv.lock` and
`pyproject.toml` disagree, instead of silently picking new versions. It does
not access USB, change drivers or move the arm. All later commands in the docs
(`.\.venv\Scripts\python.exe ...`) work unchanged. The WinUSB driver step
still needs admin once ([docs/WINDOWS_BENCH_RUN.md](docs/WINDOWS_BENCH_RUN.md)).

### Option B: the setup script (pip)

In the VS Code PowerShell terminal, run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1
```

This creates or reuses `.venv`, installs the Python packages, and runs the offline tests. Run it once for a new checkout; it does not access USB, change drivers, or move the arm. The execution-policy option applies only to this one PowerShell process. If Python is missing, the script prints the official download page and stops. Internet access is needed for the package install. If installation or tests fail, save the exact output; you can still diagnose Windows USB visibility in Device Manager, but do not start a live Python controller session.

## 3. Check that Windows and Python see the controller

Plug the **original ER-4U controller** into the PC with a USB data cable and power it according to your lab procedure. Close Intelitek software and the old GUI. Keep the physical emergency stop accessible. Then run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows_usb_check.ps1
```

The script lists any present Windows device with hardware ID `VID_09F1&PID_0007`, then runs the read-only Python preflight. The desired result is `PASS USB 09F1:0007`. This shows that the PC and Python can enumerate the controller; it does not test robot commands. Neither check sends motion commands. If the Python part cannot run because setup failed, the Windows device listing is still useful evidence.

If Windows does not list the ID, check power, cable, USB port, and the controller's **Hardware Ids** in Device Manager. If Windows lists the correct ID but Python preflight fails, read [the USB driver section of the bench guide](docs/WINDOWS_BENCH_RUN.md#2-usb-driver-and-read-only-preflight). Only consider [official Zadig](https://zadig.akeo.ie/) for that exact device after recording the current driver. Changing the driver can prevent the original Intelitek software from using the controller.

After the preflight passes, run the [guided lab session](docs/LAB_SESSION.md) (recommended), or follow the [per-script lab runbook](docs/ARM_CONTROL_BENCH.md). Connecting starts the legacy USB handshake, so the idle capture also requires an operator at the arm. Do not run a home or jog until the start pose from a prior successful ScorBot-software home has been reproduced and the idle capture succeeds.

Keep the visit's raw logs for review. [Physical calibration](docs/PHYSICAL_CALIBRATION.md) is a later step that requires independent angle measurements.
