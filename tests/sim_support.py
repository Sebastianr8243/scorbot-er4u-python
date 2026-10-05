"""A simulated robot with its event log, for tests that drive the real facade.

Three test files each built the same thing: a ``SimulatedScorbot`` writing to
a temporary controller log, connected, enabled and usually homed, with short
settle times, disconnected at the end even when the session has faulted.
"""

import json
from pathlib import Path
import tempfile
import unittest

from scorbot import ScorbotError


class SimulatedRobotCase(unittest.TestCase):
    """``self.robot()`` gives a ready simulated robot; ``rows`` and ``events`` read its log."""

    SETTLE_TIMEOUT_S = 0.5
    HOMED = True
    CONTROLLER_DEFAULTS: dict = {}

    def robot(self, **controller_kwargs):
        from scorbot.simulated import SimulatedController, SimulatedScorbot
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.log = Path(folder.name) / "run.controller.jsonl"
        for name, value in self.CONTROLLER_DEFAULTS.items():
            controller_kwargs.setdefault(name, value)
        robot = SimulatedScorbot(controller=SimulatedController(**controller_kwargs),
                                 log_path=self.log)
        robot.STOP_SETTLE_INTERVAL_S = 0.01
        robot.STOP_SETTLE_TIMEOUT_S = self.SETTLE_TIMEOUT_S
        robot.connect()
        self.addCleanup(self.disconnect, robot)
        robot.enable()
        if self.HOMED:
            robot.home(start_position_confirmed=True)
        return robot

    @staticmethod
    def disconnect(robot):
        try:
            robot.disconnect()
        except ScorbotError:
            pass  # a latched session may refuse a clean exit; the worker is a daemon

    def rows(self):
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def events(self):
        return [row["event"] for row in self.rows()]
