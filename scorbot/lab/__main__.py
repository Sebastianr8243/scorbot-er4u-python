"""Guided lab session: python -m scorbot.lab [--simulate] [--profile lab.json] [--logs DIR]

Without --simulate this connects to the real controller, and connecting
energises the motors: an operator must be at the physical stop.
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
import re
import subprocess
import sys

from .profile import ProfileError, ensure_profile
from .session import EXIT_FAILED, LabSession
from .terminal import TerminalOperator, termination_as_interrupt

_REPO_ROOT = Path(__file__).resolve().parents[2]


def next_log_path(folder, robot_id, today=None) -> Path:
    """First unused <date>-<robot>-session-NN.jsonl (never overwrites)."""
    folder = Path(folder)
    stem = f"{(today or date.today()):%Y%m%d}-{re.sub(r'[^A-Za-z0-9_-]+', '-', robot_id)}"
    for number in range(1, 100):
        path = folder / f"{stem}-session-{number:02d}.jsonl"
        if not path.exists() and not path.with_name(path.stem + ".controller.jsonl").exists():
            return path
    raise FileExistsError(f"No free session number left in {folder} for {stem}")


def _software_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=_REPO_ROOT,
                                       text=True, timeout=3).strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scorbot.lab", description=__doc__)
    parser.add_argument("--simulate", action="store_true",
                        help="Rehearse with the simulated controller: no USB, no robot")
    parser.add_argument("--profile", type=Path, default=Path("lab.json"))
    parser.add_argument("--logs", type=Path, default=None,
                        help="Log folder (default: logs, or rehearsal with --simulate)")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    operator = TerminalOperator()
    with termination_as_interrupt():
        if args.simulate:
            from ..simulated import SimulatedScorbot as robot_class
            source, preflight = "simulated", None
            operator.show("SIMULATED rehearsal: no USB and no robot are used.")
        else:
            from ..preflight import run_checks as preflight
            from ..robot import Scorbot as robot_class
            source = "real"
            operator.show("REAL session: connecting energises the motors. Stand at the "
                          "physical stop.", "warn")
        try:
            profile = ensure_profile(args.profile, operator)
        except ProfileError as error:
            operator.show(str(error), "alarm")
            return EXIT_FAILED
        folder = args.logs or Path("rehearsal" if args.simulate else "logs")
        log_path = next_log_path(folder, profile.robot_id)
        operator.show(f"Logging to {log_path}")
        return LabSession(profile=profile, operator=operator, robot_factory=robot_class,
                          data_source=source, log_path=log_path,
                          session_root=folder / "sessions", preflight=preflight,
                          software_commit=_software_commit()).run()


if __name__ == "__main__":
    raise SystemExit(main())
