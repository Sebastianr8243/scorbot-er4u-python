"""The simulator-only LeRobot robot plugin. Runs only where lerobot is installed:
.venv-lerobot/Scripts/python.exe -m unittest discover -s tests -p "test_lerobot_plugin.py"
"""

import importlib.util
from pathlib import Path
import tempfile
import unittest

HAS_PLUGIN = (importlib.util.find_spec("lerobot") is not None
              and importlib.util.find_spec("lerobot_robot_scorbot") is not None)


@unittest.skipUnless(HAS_PLUGIN, "lerobot and the plugin are not installed "
                                 "(see docs/design/LEROBOT_EXPORT.md)")
class PluginTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def config(self, **overrides):
        from lerobot_robot_scorbot import ScorbotRobotConfig
        values = dict(simulate=True, log_dir=self.root / "replay",
                      calibration_dir=self.root / "calibration")
        values.update(overrides)
        return ScorbotRobotConfig(**values)

    def test_registers_as_scorbot_and_builds_through_lerobot(self):
        from lerobot.robots.utils import make_robot_from_config
        from lerobot_robot_scorbot import ScorbotRobot
        config = self.config()
        self.assertEqual(config.type, "scorbot")
        self.assertIsInstance(make_robot_from_config(config), ScorbotRobot)

    def test_real_arm_is_refused(self):
        from lerobot_robot_scorbot import ScorbotRobot
        with self.assertRaisesRegex(ValueError, "scorbot.lab"):
            ScorbotRobot(self.config(simulate=False))

    def test_features_match_the_exporter(self):
        from lerobot_robot_scorbot import ScorbotRobot
        from scorbot.lerobot_export.load import MOTORS
        robot = ScorbotRobot(self.config())
        self.assertEqual(list(robot.action_features), list(MOTORS))
        self.assertEqual(list(robot.observation_features), list(MOTORS))

    def test_connect_step_and_disconnect(self):
        from lerobot_robot_scorbot import ScorbotRobot
        robot = ScorbotRobot(self.config())
        robot.connect()
        try:
            step = robot._follower.step_counts["base"]
            sent = robot.send_action({"base": -3.0 * step, "shoulder": 0.0, "elbow": 0.0,
                                      "wrist_motor_1": 0.0, "wrist_motor_2": 0.0})
            self.assertEqual(robot.get_observation()["base"], sent["base"])
        finally:
            robot.disconnect()
        self.assertFalse(robot.is_connected)
        robot.disconnect()                       # idempotent

    def test_connect_failure_after_connection_cleans_up(self):
        from unittest import mock
        from lerobot_robot_scorbot import ScorbotRobot
        robot = ScorbotRobot(self.config())
        with mock.patch("scorbot.simulated.SimulatedScorbot.home",
                        side_effect=RuntimeError("homing failed")):
            with self.assertRaises(RuntimeError):
                robot.connect()
        self.assertFalse(robot.is_connected)


@unittest.skipUnless(HAS_PLUGIN, "lerobot and the plugin are not installed")
class LeRobotReplayTests(unittest.TestCase):
    def test_lerobot_replay_drives_the_simulator_to_the_recorded_end(self):
        import json
        from unittest import mock
        from lerobot.scripts.lerobot_replay import DatasetReplayConfig, ReplayConfig, replay
        from lerobot_robot_scorbot import ScorbotRobot, ScorbotRobotConfig
        from scorbot.lerobot_export.__main__ import main as export
        try:
            from tests.lerobot_fixtures import (ARM, COARSE_CLOCK, FINISH, TO_LOOP, paced,
                                                record)
        except ImportError:   # .venv-lerobot has a third-party top-level 'tests' package
            from lerobot_fixtures import ARM, COARSE_CLOCK, FINISH, TO_LOOP, paced, record
        if COARSE_CLOCK:
            self.skipTest("coarse monotonic clock")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            root = Path(folder)
            log = record(root, TO_LOOP + ARM + ["t", paced("r"), "reach", paced("q"),
                                                paced("q"), paced("e"), paced("r"), "y", "t"]
                         + FINISH)
            out = root / "dataset"
            self.assertEqual(export([str(log), "--out", str(out), "--repo-id", "local/s",
                                     "--no-video"]), 0)
            [episode] = [json.loads(line) for line in
                         (out / "scorbot_episodes.jsonl").read_text("utf-8").splitlines()]
            seen = {}
            original = ScorbotRobot.disconnect

            def capture(robot):
                if robot.is_connected:
                    seen.update(robot.get_observation())
                original(robot)
            config = ScorbotRobotConfig(simulate=True, log_dir=root / "replay",
                                        calibration_dir=root / "calibration")
            with mock.patch.object(ScorbotRobot, "disconnect", capture):
                replay(ReplayConfig(robot=config, play_sounds=False,
                                    dataset=DatasetReplayConfig(repo_id="local/s", episode=0,
                                                                root=str(out), fps=10)))
        final = dict(zip(episode["motors"], episode["final_state"]))
        for joint in ("base", "shoulder", "elbow"):
            self.assertEqual(seen[joint], final[joint], joint)


if __name__ == "__main__":
    unittest.main()
