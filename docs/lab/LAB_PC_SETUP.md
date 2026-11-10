# Lab PC setup (before lab day)

Gets the lab PC ready in about 15 minutes. Nothing here moves the arm or opens the controller; do not add `--online` to anything. Run commands in PowerShell, one line at a time.

Two things to check first, because nobody has confirmed them yet:

- **Internet.** `uv` needs it once to install. Open any web page on the lab PC. No internet: do steps 1 and 2 on a PC that has it, or bring the packages another way.
- **Where SCORBASE keeps its `BIN` folder.** Step 3 finds it; do not assume a path.

## 1. Get the code

On the lab PC, open the [repository](https://github.com/Sebastianr8243/scorbot-er4u-python), pick branch `stack2`, then **Code → Download ZIP**. Extract it to a writable folder, open that folder in VS Code, and open a PowerShell terminal (**Terminal → New Terminal**).

```powershell
py -3 --version
```

**Pass:** the terminal is in the folder with `pyproject.toml` and `scripts\`. **Stop if:** you cannot find that folder. A missing or old Python is fine: `uv` brings Python 3.13 in step 2.
Details: [START_HERE_WINDOWS.md step 1](../../START_HERE_WINDOWS.md#1-get-the-project-and-python).

## 2. Install

Install `uv` once (per user, no admin), reopen the terminal, then install the locked packages:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
uv sync --locked --extra windows --extra test --extra planning
```

**Pass:** `uv sync` ends without an error and `.venv` exists. **Stop if:** it fails (often no internet, or `--locked` refusing a changed lock); save the exact output.
Details: [START_HERE_WINDOWS.md step 2](../../START_HERE_WINDOWS.md#2-install-the-python-dependencies).

## 3. Vendor folder and 32-bit Python

Find the folder with `USBC.dll`, `USBC.INI`, `ER4CONF.INI` and `Par\`:

```powershell
Get-ChildItem "C:\Program Files (x86)", "C:\Program Files" -Filter USBC.dll -Recurse -ErrorAction SilentlyContinue
```

If it is only in the SCORBASE install, copy that whole `BIN` folder to `C:\scratch\bin`, never into the repo. If `C:\scratch\bin\python.exe` is missing, add the 32-bit Python next to the DLL:

```powershell
Invoke-WebRequest https://www.python.org/ftp/python/3.13.5/python-3.13.5-embed-win32.zip -OutFile $env:TEMP\py32.zip
Expand-Archive $env:TEMP\py32.zip -DestinationPath C:\scratch\bin -Force
```

Then check it:

```powershell
$bin = "C:\scratch\bin"
$py = "$bin\python.exe"
& $py -c "import struct; print(struct.calcsize('P') * 8)"
```

**Pass:** the last line prints `32`. **Stop if:** no `USBC.dll` is found anywhere, or it prints `64`.
Details: [RUN_THE_ARM.md setup steps 2 and 3](RUN_THE_ARM.md#setup-once-per-pc).

## 4. Plug in and run doctor

Plug the ER-4U controller in with a USB data cable and power it. Close SCORBASE, Intelitek tools and the old GUI. Keep the red emergency stop in reach.

```powershell
.\.venv\Scripts\scorbot.exe doctor
```

Doctor only reads setup and lists USB; it never opens the controller. **Pass:** Python and package lines are green `[ok]`. On Intelitek's driver (ERUSB, the lab default) a red USB/libusb line is expected: that is not a reason to stop, and do not run Zadig. **Stop if:** Python or package lines are red, or Windows does not show the controller in Device Manager (`VID_09F1&PID_0007`); check power, cable and port.
Details: [START_HERE_WINDOWS.md step 3](../../START_HERE_WINDOWS.md#3-check-that-windows-and-python-see-the-controller) and [WINDOWS_BENCH_RUN.md section 2](WINDOWS_BENCH_RUN.md#2-usb-driver-and-read-only-preflight).

## Done

The PC is ready. On lab day, follow [MONDAY.md](MONDAY.md) from "Before you start".
