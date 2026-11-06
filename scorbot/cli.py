"""The Stack 2 launcher and read-only setup doctor."""

from __future__ import annotations

import argparse
from dataclasses import replace
import difflib
import importlib
from importlib import metadata, util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from . import calibration, preflight

try:
    import tomllib
except ImportError:
    tomllib = None

try:
    import winreg
except ImportError:
    winreg = None


REPO_ROOT = Path(__file__).resolve().parent.parent
TARGETS = {
    ("sim", "check"): ("script", "examples/lerobot_sim_check.py"),
    ("sim", "view"): ("script", "examples/mujoco_twin_view.py"),
    ("jog", "preview"): ("script", "examples/preview_jog.py"),
    ("lab", "day"): ("script", "scripts/lab_day.py"),
    ("lab", "session"): ("module", "scorbot.lab"),
    ("lab", "bring-up"): ("script", "scripts/bring_up.py"),
    ("lab", "watch"): ("script", "scripts/watch_lab_log.py"),
    ("lab", "summary"): ("script", "scripts/lab_summary.py"),
    ("analyze", "review-logs"): ("script", "scripts/review_lab_logs.py"),
    ("analyze", "follow"): ("script", "scripts/follow_report.py"),
    ("analyze", "usb-trace"): ("script", "scripts/usb_trace.py"),
    ("analyze", "vendor-check"): ("script", "scripts/vendor_check.py"),
    ("analyze", "fit-calibration"): ("script", "scripts/fit_calibration.py"),
    ("analyze", "build-calibration-csv"): ("script", "scripts/build_calibration_csv.py"),
    ("analyze", "export-dataset"): ("script", "scripts/export_policy_dataset.py"),
}
DESCRIPTIONS = {
    "sim check": "Check the LeRobot simulation pipeline",
    "sim view": "View the simulated arm",
    "jog preview": "Preview a jog without moving the arm",
    "lab day": "Run the supervised lab checklist",
    "lab session": "Start a guided lab session",
    "lab bring-up": "Run the supervised bring-up steps",
    "lab watch": "Watch a lab log",
    "lab summary": "Summarize lab measurements",
    "analyze review-logs": "Review lab logs",
    "analyze follow": "Measure how far a stream lagged its target",
    "analyze usb-trace": "Read a USB capture",
    "analyze vendor-check": "Compare a trace with the vendor protocol",
    "analyze fit-calibration": "Fit calibration from physical measurements",
    "analyze build-calibration-csv": "Build a calibration measurement table",
    "analyze export-dataset": "Export policy logs as a dataset",
}
SYNC_FIX = "uv sync --locked --extra windows --extra test --extra planning"
CALIBRATION_FIX = (
    "Measure D1-D7 in docs/lab/PHYSICAL_CALIBRATION.md, then: "
    "scorbot analyze fit-calibration --help"
)
DRIVER_FIX = (
    "Use --transport auto with Intelitek's driver. For WinUSB see "
    "docs/lab/WINDOWS_BENCH_RUN.md section 2."
)


def run_child(argv):
    """Let the child's own Ctrl+C cleanup finish: abort, then motors off."""
    child = subprocess.Popen(argv)
    while True:
        try:
            return child.wait()
        except KeyboardInterrupt:
            continue


def _check(name, level, detail, fix=""):
    return preflight.Check(name, level == "ok", detail, level, fix)


def check_python():
    supported = sys.version_info >= (3, 10)
    return _check("Python", "ok" if supported else "fail", sys.version.split()[0],
                  "Install Python 3.13; see START_HERE_WINDOWS.md section 1." if not supported else "")


def check_venv():
    if sys.prefix != sys.base_prefix:
        return _check("Venv", "ok", sys.prefix)
    return _check("Venv", "warn", "not running in a virtual environment",
                  f"{SYNC_FIX}, then run .\\.venv\\Scripts\\scorbot.exe doctor")


def check_packages():
    return preflight.run_checks()


def check_lock():
    path = REPO_ROOT / "uv.lock"
    if tomllib is None:
        return _check("Locked deps", "skip", "requires Python 3.11 or newer to read uv.lock")
    if not path.is_file():
        return _check("Locked deps", "skip", "uv.lock is absent from this install")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return _check("Locked deps", "warn", f"Cannot read uv.lock: {exc}", SYNC_FIX)
    versions = {}
    required = set()
    for package in data.get("package", []):
        if package.get("name") == "scorbot-er4u":
            required = {re.sub(r"[-_.]+", "-", dep["name"]).lower() for dep in package.get("dependencies", [])}
        if "version" in package:
            name = re.sub(r"[-_.]+", "-", package["name"]).lower()
            versions.setdefault(name, set()).add(package["version"])
    mismatches, missing = [], []
    for name, locked in versions.items():
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            if name in required:
                missing.append(name)
            continue  # Optional extras and other platforms need not be installed.
        if installed not in locked:
            mismatches.append(f"{name}: installed {installed}, locked {' or '.join(sorted(locked))}")
    if missing:
        return _check("Locked deps", "fail", f"required package not installed: {', '.join(sorted(missing))}",
                      SYNC_FIX)
    if mismatches:
        return _check("Locked deps", "warn", "; ".join(mismatches), SYNC_FIX)
    return _check("Locked deps", "ok", "installed versions match uv.lock")


def check_driver():
    if sys.platform != "win32" or winreg is None:
        return _check("Controller", "skip", "Windows registry driver check unavailable")
    path = r"SYSTEM\CurrentControlSet\Enum\USB\VID_09F1&PID_0007"
    services = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
            index = 0
            while True:
                try:
                    instance = winreg.EnumKey(key, index)
                except OSError:
                    break
                index += 1
                with winreg.OpenKey(key, instance) as device:
                    services.append(str(winreg.QueryValueEx(device, "Service")[0]))
    except FileNotFoundError:
        return _check("Controller", "warn", "09F1:0007 has no registered driver",
                      "plug in the controller, power it, run scorbot doctor again")
    except OSError as exc:
        return _check("Controller", "warn", f"Cannot read driver: {exc}", DRIVER_FIX)
    visible = any(row.name == "USB 09F1:0007" and row.passed for row in preflight.run_checks())
    detail = f"09F1:0007 registered driver: {', '.join(services) or 'unknown'}"
    if not visible and not any(_driver_started(service) for service in services):
        return _check("Controller", "warn", detail + " (not connected now)",
                      "plug in the controller, power it, run scorbot doctor again")
    if visible and any(service.lower() in {"winusb", "libusbk"} for service in services):
        return _check("Controller", "ok", detail)
    return _check("Controller", "warn", detail, DRIVER_FIX)


def _driver_started(service):
    """True if Windows has started at least one device on this driver now (the Enum key above outlives unplugging)."""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rf"SYSTEM\CurrentControlSet\Services\{service}\Enum") as key:
            return int(winreg.QueryValueEx(key, "Count")[0]) > 0
    except (OSError, ValueError):
        return False


def check_calibration(args):
    if not args.calibration:
        return _check("Calibration", "warn", f'no calibration supplied for robot_id "{args.robot_id}"',
                      CALIBRATION_FIX)
    try:
        calibration.load_calibration(args.calibration, robot_id=args.robot_id)
    except Exception as exc:
        return _check("Calibration", "fail", str(exc), CALIBRATION_FIX)
    return _check("Calibration", "ok", f"{args.calibration} validated for {args.robot_id}")


def check_lerobot():
    names = ("lerobot", "lerobot_robot_scorbot")
    available = [util.find_spec(name) is not None for name in names]
    if not all(available):
        return _check("LeRobot", "skip", "not installed in this env (optional; "
                      "plugins/lerobot_robot_scorbot/README.md for the separate env)")
    try:
        for name in names:
            importlib.import_module(name)
    except Exception as exc:
        return _check("LeRobot", "warn", f"Optional import failed: {exc}",
                      "Use the separate venv in plugins/lerobot_robot_scorbot/README.md.")
    return _check("LeRobot", "ok", "LeRobot and the Scorbot plugin import successfully")


def doctor_checks(args):
    checks = [check_python(), check_venv()]
    driver = check_driver()
    # libusb cannot see a controller bound to Intelitek's driver; Stack 2 still reaches it with --transport auto.
    intelitek = "ERUSB" in driver.detail.upper() and "not connected" not in driver.detail
    for row in check_packages():
        if row.name == "Python":
            continue
        fix = row.fix
        if not row.passed and not fix and "run:" not in row.detail.lower():
            fix = DRIVER_FIX if row.name.startswith("USB ") else "python -m pip install -e '.[windows]'"
        name = f"Packages: {row.name}" if row.name in {"numpy", "pyusb", "libusb backend"} else row.name
        if row.name.startswith("USB ") and not row.passed and intelitek:
            row = replace(row, level="warn")
        checks.append(replace(row, name=name, fix=fix))
    checks.extend([check_lock(), driver, check_calibration(args), check_lerobot()])
    return [replace(row, level=row.level or ("ok" if row.passed else "fail")) for row in checks]


def _doctor(argv):
    parser = argparse.ArgumentParser(prog="scorbot doctor", description="Read-only setup checks")
    parser.add_argument("--json", action="store_true", help="Print one JSON object")
    parser.add_argument("--strict", action="store_true", help="Return failure for warnings too")
    parser.add_argument("--verbose", action="store_true", help="Add raw preflight details")
    parser.add_argument("--calibration", help="Validated calibration JSON file")
    parser.add_argument("--robot-id", default="lab-er4u-1", help="Arm identity for calibration")
    args = parser.parse_args(argv)
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(errors="replace")  # a cp1252 console must not stop the report on a non-ASCII path
    checks = doctor_checks(args)
    problems = sum(row.level == "fail" for row in checks)
    warnings = sum(row.level == "warn" for row in checks)
    code = int(bool(problems or (args.strict and warnings)))
    if args.json:
        print(json.dumps({"checks": [{"name": row.name, "level": row.level,
                                      "detail": row.detail, "fix": row.fix} for row in checks],
                          "problems": problems, "warnings": warnings, "exit": code}, indent=2))
        return code
    color = sys.stdout.isatty() and "NO_COLOR" not in os.environ
    if color and sys.platform == "win32":
        os.system("")
    marks = {"ok": "ok", "warn": "!!", "fail": "XX", "skip": "--"}
    colors = {"ok": "32", "warn": "33", "fail": "31", "skip": "90"}
    print("Scorbot doctor (Stack 2)  reads only, never opens the controller")
    for row in checks:
        mark = f"[{marks[row.level]}]"
        if color:
            mark = f"\033[{colors[row.level]}m{mark}\033[0m"
        print(f" {mark} {row.name:<24} {row.detail}")
        if row.level in {"warn", "fail"} and row.fix:
            print(f"      {row.fix}")
        if args.verbose and (row.name.startswith("Packages:") or row.name.startswith("USB ")):
            print(f"      Raw preflight: {row.detail}")
    summary = f"{problems} problem(s), {warnings} warning(s)." if problems or warnings else "All checks passed."
    next_step = "fix the red line, then run scorbot doctor again" if problems else "scorbot sim check"
    print(f"{summary} Next: {next_step}")
    return code


def _unknown(name, choices):
    matches = difflib.get_close_matches(name, choices, n=1)
    hint = f"Did you mean: {matches[0]}?" if matches else f"Choose from: {', '.join(choices)}."
    print(f"scorbot: unknown command '{name}'. {hint}", file=sys.stderr)
    return 2


def _help(group=None):
    if group:
        commands = [" ".join(command) for command in TARGETS if command[0] == group]
        for command in commands:
            print(f"  scorbot {command:<30} {DESCRIPTIONS[command]}")
    else:
        print("Scorbot (Stack 2)")
        print("  scorbot doctor                         Check setup without opening the controller")
        for command in ("sim check", "sim view", "jog preview"):
            print(f"  scorbot {command:<30} {DESCRIPTIONS[command]}")
        print("  scorbot record                         Print the LeRobot simulation record command")
        print("More tools: scorbot lab --help, scorbot analyze --help")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"--help", "-h"}:
        _help()
        return 0
    command = argv[0]
    if command == "doctor":
        return _doctor(argv[1:])
    if command == "record":
        flags = ["lerobot-record", "--robot.type=scorbot", "--robot.simulate=true",
                 "--teleop.type=scorbot_joint_keys", "--robot.operator_at_stop=true",
                 "--robot.jaws_empty=true", "--robot.wrist_clear=true",
                 "--dataset.repo_id=local/my_first_demo", "--dataset.root=rehearsal/my_first_demo",
                 '--dataset.single_task="move the arm"', "--dataset.push_to_hub=false",
                 "--dataset.num_episodes=2", "--dataset.episode_time_s=20",
                 "--dataset.reset_time_s=5", "--dataset.fps=10"]
        print(" `\n  ".join(flags))
        print("Run this in the separate LeRobot venv (plugins/lerobot_robot_scorbot/README.md); "
              "scorbot does not run it.")
        return 0
    groups = list(dict.fromkeys(group for group, task in TARGETS))
    if command not in groups:
        return _unknown(command, ["doctor", "sim", "jog", "record", "lab", "analyze"])
    if len(argv) == 1 or argv[1] in {"--help", "-h"}:
        _help(command)
        return 0
    target = TARGETS.get((command, argv[1]))
    if target is None:
        return _unknown(argv[1], [task for group, task in TARGETS if group == command])
    kind, name = target
    if kind == "script":
        path = REPO_ROOT / name
        if not path.is_file():
            print(f"scorbot: missing script {path}; the launcher needs the source checkout "
                  "(an editable install: pip install -e .)", file=sys.stderr)
            return 1
        child_argv = [sys.executable, str(path)]
    else:
        child_argv = [sys.executable, "-m", name]
    # Absolute-path scripts must use this checkout even with a shared editable venv.
    previous_path = os.environ.get("PYTHONPATH")
    os.environ["PYTHONPATH"] = str(REPO_ROOT) + (os.pathsep + previous_path if previous_path else "")
    try:
        return run_child([*child_argv, *argv[2:]])
    finally:
        if previous_path is None:
            del os.environ["PYTHONPATH"]
        else:
            os.environ["PYTHONPATH"] = previous_path
