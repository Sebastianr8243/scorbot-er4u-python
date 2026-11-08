"""Build the separate LeRobot environment; run from any working directory."""

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent


def venv_python(venv: Path, platform: str | None = None) -> Path:
    return venv / ("Scripts/python.exe" if (platform or sys.platform) == "win32" else "bin/python")


def check_symlinks() -> bool:
    try:
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "target"
            target.mkdir()
            os.symlink(target, Path(folder) / "link", target_is_directory=True)
    except OSError:
        print("WARNING: lerobot-train will crash on checkpoints/last (huggingface/lerobot#4059); "
              "turn on Developer Mode (Settings > System > For developers) or run elevated.")
        return False
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--venv", type=Path, default=Path("~/envs/lerobot-stack2"))
    parser.add_argument("--dry-run", action="store_true", help="print commands, run nothing")
    parser.add_argument("--skip-check", action="store_true")
    args = parser.parse_args(argv)
    if sys.version_info[:2] != (3, 12):
        print("WARNING: this setup expects Python 3.12; run it with a Python 3.12 interpreter.")
    python = str(venv_python(args.venv.expanduser().resolve()))
    steps = [
        ("venv", [sys.executable, "-m", "venv", str(args.venv.expanduser().resolve())],
         Path(python).is_file()),
        ("lerobot", [python, "-m", "pip", "install", "lerobot[dataset,training]==0.6.1"], False),
        ("repo", [python, "-m", "pip", "install", "-e", "."], False),
        ("plugin", [python, "-m", "pip", "install", "-e", "plugins/lerobot_robot_scorbot"], False),
        ("check", [python, "examples/lerobot_sim_check.py"], args.skip_check),
    ]
    results = {}
    if sys.platform == "win32":
        results["symlinks"] = "skipped" if args.dry_run else ("ok" if check_symlinks() else "failed")
    failed = False
    for name, command, skip in steps:
        print(f"{name} (cwd: {ROOT}): {subprocess.list2cmdline(command)}")
        results[name] = "skipped"
        if skip or args.dry_run or failed:
            continue
        try:
            subprocess.run(command, cwd=ROOT, check=True)
        except (OSError, subprocess.CalledProcessError) as error:
            print(f"{name}: {error}")
            results[name], failed = "failed", True
        else:
            results[name] = "ok"
    for name, result in results.items():
        print(f"{name}: {result}")
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
