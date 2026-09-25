# ER-4U Windows bench run

Use this on the Windows PC connected to the **original ScorBot ER-4U USB controller**. The Python adapter and homing sequence have not yet been verified on this robot. Keep a trained operator at the arm, the work area clear, and the physical emergency stop within reach throughout the session. Do not run the old GUI or Intelitek application at the same time.

## 1. Copy and install

If this PC only has VS Code, follow [START_HERE_WINDOWS.md](../START_HERE_WINDOWS.md) first. It does not require Git.

Copy the **current working tree** to the robot PC, including `scorbot/`, `openScorbot/`, `examples/`, `pyproject.toml`, and `tests/`. The easiest transfer is the ZIP created by `python scripts/build_bench_kit.py`; extract it to a writable directory on the robot PC. This ZIP also includes `usb-tools/zadig-2.9.exe` when the verified download is present in `dist/usb-tools/`. Alternatively, clone the standalone [scorbot-er4u-python repository](https://github.com/Sebastianr8243/scorbot-er4u-python):

```powershell
git clone https://github.com/Sebastianr8243/scorbot-er4u-python.git
cd scorbot-er4u-python
git switch feat/arm-control-bench-calibration
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

## 3. Idle capture, home, and small jogs

Follow the [first lab visit runbook](ARM_CONTROL_BENCH.md) for the current scripts and stop points. It uses `record_raw_state.py` for the idle capture and `bench_joint.py` for one supervised home and jog at a time. The older `python_control.py` example is not the first-visit procedure because it does not prompt for a physical home observation before offering motion.

The [offline log reviewer](../scripts/review_lab_logs.py) reports packet freshness, source fingerprint consistency, and planned versus observed motor-count changes. It does not establish physical angle accuracy or safety. Keep the controller event JSONL files and all operator observations.
