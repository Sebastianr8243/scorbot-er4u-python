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
    parser.add_argument("--rehearse-motors-dropped", action="store_true",
                        help="With --simulate: the simulated controller cuts motor power "
                             "right after homing, without telling the software")
    parser.add_argument("--profile", type=Path, default=Path("lab.json"))
    parser.add_argument("--camera", default=None,
                        help="Record a webcam during the session: an index (0, 1, ...), "
                             "or 'fake' with --simulate")
    parser.add_argument("--inch-home", action="store_true",
                        help="Home by inching (shoulder, elbow, base) instead of the legacy "
                             "search; start within a few tens of degrees of the home pose")
    parser.add_argument("--coupled-pre-home", action="store_true",
                        help="With --inch-home, make the pre-home moves drive the elbow and "
                             "wrist motors too (our own option, never run on the arm); the "
                             "default drives only the motor asked for, like the vendor's jog")
    parser.add_argument("--logs", type=Path, default=None,
                        help="Log folder (default: logs, or rehearsal with --simulate)")
    args = parser.parse_args(argv)
    if args.rehearse_motors_dropped and not args.simulate:
        parser.error("--rehearse-motors-dropped needs --simulate")
    camera_factory = None
    if args.camera is not None:
        if args.camera == "fake":
            if not args.simulate:
                parser.error("--camera fake needs --simulate")
            from ..camera.source import FakeSource
            camera_factory = lambda: FakeSource(width=640, height=480, pace=True)  # noqa: E731
        else:
            try:
                index = int(args.camera)
            except ValueError:
                parser.error("--camera must be a number or 'fake'")
            from ..camera.source import OpenCVSource
            camera_factory = lambda: OpenCVSource(index)  # noqa: E731
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    operator = TerminalOperator()
    with termination_as_interrupt():
        if args.simulate:
            from functools import partial

            from ..simulated import REHEARSAL_PROFILE, SimulatedController, SimulatedScorbot
            source, preflight = "simulated", None
            operator.show("SIMULATED rehearsal: no USB and no robot are used. Homing, "
                          "switches, rest noise and the LED panel are modeled, not measured.")
            # Start away from home so the modeled homing has somewhere to travel.
            controller = SimulatedController(
                drop_motors_after_home=args.rehearse_motors_dropped, profile=REHEARSAL_PROFILE,
                start_counts={"base": 2000, "shoulder": -1500, "elbow": 1200})
            robot_class = partial(SimulatedScorbot, controller=controller)
            if args.rehearse_motors_dropped:
                operator.show("SIMULATED FAULT: the controller will cut motor power right "
                              "after homing. Watch the SIMULATED panel: its MOTORS LED goes "
                              "off, as the real one would.", "warn")
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
                          software_commit=_software_commit(), inch_home=args.inch_home,
                          coupled_pre_home=args.coupled_pre_home,
                          camera_factory=camera_factory).run()


if __name__ == "__main__":
    raise SystemExit(main())
