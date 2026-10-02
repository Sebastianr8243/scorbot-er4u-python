"""Real simulated lab sessions for exporter tests (LabSession + ScriptedOperator)."""

import time
from pathlib import Path

from scorbot import SimulatedScorbot
from scorbot.lab.operator import ScriptedOperator
from scorbot.lab.profile import LabProfile
from scorbot.lab.session import LabSession
from scorbot.simulated import SimulatedController

PROFILE = LabProfile("lab-er4u-1", "A-12", "C-3", "WinUSB", "SR")
TO_LOOP = ["y"] * 4 + ["n", "g", "pose matches photo", "HOME", "y", "g", "all axes homed", "y"]
ARM = ["a", "door", "y", "g", "ARM"]
FINISH = ["x", "n", "g"]
# The exporter refuses sessions timed by a coarse clock (Windows on Python < 3.13).
COARSE_CLOCK = time.get_clock_info("monotonic").resolution > 1e-3


def paced(key, seconds=0.15):
    """A scripted key pressed after a pause, so jogs land in different grid ticks."""
    def answer():
        time.sleep(seconds)
        return key
    return answer


def record(root, answers, *, camera=False, controller=None, name="s"):
    """Run a simulated guided session; "LIVE:<key>" waits for live camera frames first."""
    root = Path(root)
    holder = {}

    def wait_live(then):
        def answer():
            for _ in range(300):
                cam = holder["session"].camera
                if cam is not None and cam.live():
                    break
                time.sleep(0.01)
            return then
        return answer
    answers = [wait_live(a.removeprefix("LIVE:"))
               if isinstance(a, str) and a.startswith("LIVE:") else a for a in answers]
    camera_factory = None
    if camera:
        from scorbot.camera.source import FakeSource
        camera_factory = lambda: FakeSource(pace=True)  # noqa: E731
    ctrl = controller or SimulatedController()
    session = LabSession(
        profile=PROFILE, operator=ScriptedOperator(answers),
        robot_factory=lambda **kw: SimulatedScorbot(controller=ctrl, **kw),
        data_source="simulated", log_path=root / f"{name}.jsonl",
        session_root=root / "sessions", sleep=lambda s: None,
        camera_factory=camera_factory)
    holder["session"] = session
    session.run()
    return root / f"{name}.jsonl"
