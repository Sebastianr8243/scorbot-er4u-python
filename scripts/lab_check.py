"""Before a Stack 1 lab run: read-only checks of everything the arm path needs. Never opens the DLL or the arm.

    .venv/Scripts/python.exe scripts/lab_check.py [--bin C:/scratch/bin] [--port 8765] [--box calibration/arm.json]

Prints PASS / WARN / FAIL per line; exit 1 if anything FAILs. WARN means the run can start but something is limited.
"""
import argparse
import json
import re
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
VENDOR_FILES = ("USBC.dll", "USBC.INI", "ER4CONF.INI", "ROB_4u.ini")
TRACKED = re.compile(r"(?i)(usbc[.](dll|ini)|er4.*[.]ini|rob_4u[.]ini|[.]pcap(ng)?)$")      # RUN_THE_ARM.md: never commit these


def check_bin(bin_dir):
    bin_dir = Path(bin_dir)
    missing = [name for name in VENDOR_FILES if not (bin_dir / name).is_file()]
    if not (bin_dir / "Par").is_dir():
        missing.append("Par/")
    if not list(bin_dir.glob("ER4Ax*.ini")):
        missing.append("ER4Ax*.ini (the DLL refuses to initialise without them)")
    return ("FAIL", f"missing in {bin_dir}: {', '.join(missing)}") if missing else ("PASS", f"{bin_dir} has the DLL, ini files and Par/")


def check_python32(bin_dir):
    exe = Path(bin_dir) / "python.exe"
    if not exe.is_file():
        return "FAIL", f"{exe} missing: unpack the 32-bit embeddable Python there (RUN_THE_ARM.md, section 1, step 3)"
    bits = subprocess.run([str(exe), "-c", "import struct; print(struct.calcsize('P') * 8)"],
                          capture_output=True, text=True, timeout=30).stdout.strip()
    return ("PASS", "bin python is 32-bit") if bits == "32" else ("FAIL", f"bin python reports {bits or 'no output'} bits; the DLL needs 32")


def check_port(port):
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
            return "PASS", f"port {port} is free: start the server"
        except OSError:
            pass
    try:
        from scorbot.bridge import Arm
        with Arm(port=port, timeout=5) as arm:
            reply = arm.ping()
        return "WARN", f"an arm server is already on {port} (homed={reply.get('homed')}, simulated={reply.get('simulated')})"
    except Exception as error:
        return "FAIL", f"port {port} is taken by something that is not the arm server ({error})"


def check_box(box):
    box = Path(box)
    if not box.is_file():
        return "WARN", f"{box} missing: move_to is refused until arm_characterise.py writes it (move_by still works)"
    measured = json.loads(box.read_text(encoding="utf-8")).get("measured") is True
    return ("PASS", f"{box} is measured") if measured else ("WARN", f"{box} exists but is not measured by arm_characterise.py")


def powershell(command):
    return subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True, timeout=60).stdout


def check_usb():
    service = powershell("(Get-CimInstance Win32_PnPEntity | Where-Object DeviceID -like '*VID_09F1&PID_0007*').Service").strip()
    if not service:
        return "FAIL", "controller 09F1:0007 not listed: check power, cable, port"
    if "usb" in service.lower() and service.lower() != "erusb":
        return "FAIL", f"driver is {service}, not Intelitek ERUSB (Zadig swapped it): see RUN_THE_ARM.md, section 6, If it goes wrong"
    return "PASS", f"controller listed, driver {service}"


def check_scorbase():
    running = "scorbase" in subprocess.run(["tasklist"], capture_output=True, text=True, timeout=30).stdout.lower()
    return ("FAIL", "SCORBASE is running: close it, only one program can talk to the controller") if running else ("PASS", "SCORBASE is not running")


def check_tracked():
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split("\n")
    bad = [name for name in files if TRACKED.search(name)]
    return ("FAIL", f"vendor or capture files are tracked by git: {bad[:5]}") if bad else ("PASS", "no vendor files tracked by git")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only checks before a Stack 1 lab run")
    parser.add_argument("--bin", default="C:/scratch/bin")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--box", default=str(ROOT / "calibration" / "arm.json"))
    args = parser.parse_args(argv)
    results = [(name, *check()) for name, check in (
        ("vendor files", lambda: check_bin(args.bin)), ("32-bit python", lambda: check_python32(args.bin)),
        ("controller USB", check_usb), ("SCORBASE", check_scorbase), ("server port", lambda: check_port(args.port)),
        ("working box", lambda: check_box(args.box)), ("git hygiene", check_tracked))]
    for name, status, detail in results:
        print(f"{status:4} {name}: {detail}")
    failed = any(status == "FAIL" for _, status, _ in results)
    print("Fix the FAIL lines before the lab run." if failed else "Ready to start the server. The red physical stop is the only emergency stop.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
