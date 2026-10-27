"""plugins/lerobot_robot_scorbot_stack1 on FakeArm: no arm, no server, no USB, no DLL, no lerobot package.

Requirement ids (L01 ...) are in docs/requirements/stack1-lerobot-adapter.md.
"""

import json
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "plugins" / "lerobot_robot_scorbot_stack1"))
sys.path.insert(0, str(ROOT))

from examples import sim_demo  # noqa: E402
from lerobot_robot_scorbot_stack1 import (  # noqa: E402
    ActionRefused, AdapterStopped, DatasetWriter, MixedDataError, Stack1Config, Stack1Robot, read_episode)
from lerobot_robot_scorbot_stack1 import robot as robot_module  # noqa: E402
from scorbot.bridge import ArmError, FakeArm  # noqa: E402

BOX = {"x": [250, 350], "y": [-50, 50], "z": [150, 250]}
NOOP = {"dx.mm": 0.0, "dy.mm": 0.0, "dz.mm": 0.0, "gripper.pos": 0.0}


def act(**fields):
    return {**NOOP, **fields}


def make(arm=None, homed=True, **config):
    """A connected robot on a FakeArm (start 300, 0, 200 inside BOX), homed unless homed=False."""
    robot = Stack1Robot(Stack1Config(box_mm=config.pop("box_mm", BOX), **config))
    robot.connect(arm=arm or FakeArm(homed=False))
    if homed:
        robot.home()
    return robot


class NoSocketCase(unittest.TestCase):
    """L11: any test that tries to reach an arm server fails loudly."""

    def setUp(self):
        guard = mock.patch.object(socket, "create_connection", side_effect=AssertionError("a test opened a socket"))
        guard.start()
        self.addCleanup(guard.stop)


class BrokenArm(FakeArm):
    def __init__(self, fail="move_by", **kw):
        super().__init__(**kw)
        self.fail = fail

    def move_by(self, *a, **kw):
        if self.fail == "move_by":
            raise ArmError("the DLL refused to set the move up")
        return super().move_by(*a, **kw)

    def where(self):
        if self.fail == "where":
            raise ArmError("where failed")
        return super().where()


class SafetyTests(NoSocketCase):
    def moves(self, robot):
        return [c for c in robot._arm.commands if c[0] in ("move_by", "move_to", "gripper")]

    def test_a_step_over_50_mm_on_one_axis_is_refused(self):
        robot = make()
        for axis in ("dx.mm", "dy.mm", "dz.mm"):
            with self.assertRaises(ActionRefused):
                robot.send_action(act(**{axis: 50.1}))
            with self.assertRaises(ActionRefused):
                robot.send_action(act(**{axis: -50.1}))
        self.assertEqual(self.moves(robot), [])
        robot.send_action(act(**{"dx.mm": 50.0}))      # exactly 50 is allowed
        self.assertEqual(robot.get_observation()["x.mm"], 350.0)

    def test_a_target_outside_the_box_is_refused(self):
        robot = make()
        with self.assertRaises(ActionRefused) as raised:
            robot.send_action(act(**{"dz.mm": 50.0}))       # z 200 -> 250 is the edge: allowed below, so go past it
            robot.send_action(act(**{"dz.mm": 1.0}))
        self.assertIn("box", str(raised.exception))
        self.assertEqual(robot.get_observation()["z.mm"], 250.0)        # the first step ran, the second did not
        self.assertEqual(len(self.moves(robot)), 1)
        robot.send_action(act(**{"dz.mm": -10.0}))                  # not latched by a refusal

    def test_no_box_means_no_move(self):
        with tempfile.TemporaryDirectory() as folder:
            robot = Stack1Robot(Stack1Config(calibration_dir=folder))
            robot.connect(arm=FakeArm(homed=False))
            robot.home()
            with self.assertRaises(ActionRefused) as raised:
                robot.send_action(act(**{"dx.mm": 1.0}))
            self.assertIn("box", str(raised.exception))
            self.assertEqual(self.moves(robot), [])

    def test_nothing_moves_until_homed(self):
        robot = make(homed=False)
        with self.assertRaises(ActionRefused) as raised:
            robot.send_action(act(**{"dx.mm": 1.0}))
        self.assertIn("Home", str(raised.exception))
        with self.assertRaises(ActionRefused):
            robot.send_action(act(**{"gripper.pos": 1.0}))
        self.assertEqual(self.moves(robot), [])
        self.assertEqual(robot.get_observation()["x.mm"], 300.0)        # looking is allowed
        robot.home()
        robot.send_action(act(**{"dx.mm": 1.0}))
        self.assertEqual(robot.get_observation()["x.mm"], 301.0)

    def test_malformed_or_non_finite_actions_are_refused(self):
        robot = make()
        bad = [{"dx.mm": 1.0}, {**NOOP, "extra": 1.0}, {**NOOP, "dx.mm": "5"}, {**NOOP, "dx.mm": float("nan")},
               {**NOOP, "dy.mm": float("inf")}, {**NOOP, "gripper.pos": float("nan")}, {**NOOP, "dz.mm": True}]
        for action in bad:
            with self.assertRaises(ActionRefused, msg=str(action)):
                robot.send_action(action)
        self.assertEqual(self.moves(robot), [])

    def test_an_arm_error_stops_and_latches(self):
        robot = make(arm=BrokenArm(homed=False))
        with self.assertRaises(AdapterStopped):
            robot.send_action(act(**{"dx.mm": 5.0}))
        self.assertEqual(robot._arm.commands.count(("stop",)), 1)
        for _ in range(2):      # latched: refused with no arm command, and no second stop
            with self.assertRaises(AdapterStopped):
                robot.send_action(act())
        self.assertEqual(robot._arm.commands.count(("stop",)), 1)
        self.assertEqual([c for c in robot._arm.commands if c[0] == "move_by"], [])     # the broken arm never recorded a move

    def test_an_error_while_observing_also_latches(self):
        robot = make(arm=BrokenArm(fail="where", homed=False), homed=False)
        with self.assertRaises(AdapterStopped):
            robot.get_observation()
        self.assertEqual(robot._arm.commands.count(("stop",)), 1)
        with self.assertRaises(AdapterStopped):
            robot.send_action(act())

    def test_reset_unlatches_but_needs_a_new_home(self):
        arm = BrokenArm(homed=False)
        robot = make(arm=arm)
        with self.assertRaises(AdapterStopped):
            robot.send_action(act(**{"dx.mm": 5.0}))
        arm.fail = None
        robot.reset()
        with self.assertRaises(ActionRefused) as raised:
            robot.send_action(act(**{"dx.mm": 5.0}))
        self.assertIn("Home", str(raised.exception))
        robot.home()
        robot.send_action(act(**{"dx.mm": 5.0}))
        self.assertEqual(robot.get_observation()["x.mm"], 305.0)

    def test_gripper_is_only_commanded_when_it_changes(self):
        robot = make()
        robot.send_action(act(**{"gripper.pos": 1.0}))
        robot.send_action(act(**{"gripper.pos": 1.0}))
        robot.send_action(act(**{"gripper.pos": 0.0}))
        self.assertEqual([c for c in robot._arm.commands if c[0] == "gripper"], [("gripper", True), ("gripper", False)])
        self.assertEqual(robot.get_observation()["gripper.pos"], 0.0)


class ConfigTests(NoSocketCase):
    def test_max_step_cannot_exceed_50(self):
        for value in (50.1, 0, -1, float("nan")):
            with self.assertRaises(ValueError):
                Stack1Config(max_step_mm=value)
        Stack1Config(max_step_mm=10)
        robot = make(max_step_mm=10)
        with self.assertRaises(ActionRefused):
            robot.send_action(act(**{"dx.mm": 11.0}))

    def test_per_arm_fields_and_box_from_the_calibration_dir(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "arm.json").write_text(json.dumps({"box_mm": BOX}), encoding="utf-8")
            config = Stack1Config(arm_id="arm7", port=8771, host="127.0.0.2", calibration_dir=folder)
            self.assertEqual((config.arm_id, config.port, config.host), ("arm7", 8771, "127.0.0.2"))
            robot = Stack1Robot(config)
            robot.connect(arm=FakeArm(homed=False))
            robot.home()
            robot.send_action(act(**{"dx.mm": 10.0}))
            with self.assertRaises(ActionRefused):
                robot.send_action(act(**{"dx.mm": 45.0}))       # 310 + 45 is past 350
        defaults = Stack1Config()
        self.assertEqual((defaults.port, str(defaults.calibration_dir), defaults.allow_real), (8765, "calibration", False))


class ConnectTests(unittest.TestCase):
    def test_a_real_server_is_refused_without_the_flag(self):
        with mock.patch.object(robot_module, "Arm", side_effect=AssertionError("must not connect")):
            robot = Stack1Robot(Stack1Config(box_mm=BOX))
            with self.assertRaises(ValueError) as raised:
                robot.connect()
            self.assertIn("allow_real", str(raised.exception))
            self.assertFalse(robot.is_connected)

    def test_the_flag_connects_to_the_configured_port(self):
        stub = mock.MagicMock()
        stub.ping.return_value = {"ok": True, "simulated": False}
        with mock.patch.object(robot_module, "Arm", return_value=stub) as arm_class:
            robot = Stack1Robot(Stack1Config(box_mm=BOX, allow_real=True, port=8771, host="127.0.0.3"))
            robot.connect()
        arm_class.assert_called_once_with(host="127.0.0.3", port=8771)
        self.assertTrue(robot.is_connected)
        self.assertEqual(robot.data_source, "real")

    def test_disconnect_closes_the_arm_and_is_idempotent(self):
        robot = make()
        arm = robot._arm
        robot.disconnect()
        robot.disconnect()
        self.assertFalse(robot.is_connected)
        self.assertIsNotNone(arm)
        with self.assertRaises(ConnectionError):
            robot.get_observation()


class FeatureTests(NoSocketCase):
    def test_observation_and_action_keys(self):
        robot = make()
        self.assertEqual(set(robot.observation_features), {"x.mm", "y.mm", "z.mm", "gripper.pos"})
        self.assertEqual(set(robot.action_features), {"dx.mm", "dy.mm", "dz.mm", "gripper.pos"})
        self.assertEqual(set(robot.get_observation()), set(robot.observation_features))
        self.assertEqual(robot.get_observation(), {"x.mm": 300.0, "y.mm": 0.0, "z.mm": 200.0, "gripper.pos": 0.0})


class DataSourceTests(unittest.TestCase):
    def source_for(self, ping):
        stub = mock.MagicMock()
        stub.ping.return_value = ping
        robot = Stack1Robot(Stack1Config(box_mm=BOX, allow_real=True))
        robot.connect(arm=stub)
        return robot.data_source

    def test_source_comes_from_the_arm_not_the_caller(self):
        self.assertEqual(make().data_source, "simulated")
        self.assertEqual(self.source_for({"ok": True, "simulated": True}), "simulated")
        self.assertEqual(self.source_for({"ok": True}), "simulated")                # no claim, no "real"
        self.assertEqual(self.source_for({"ok": True, "simulated": False}), "real")


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name) / "ds"

    def test_every_record_carries_data_source(self):
        robot = make()
        writer = DatasetWriter(self.root, robot.data_source, fps=10)
        path = writer.start_episode("reach")
        writer.add_frame(robot.get_observation(), act(**{"dx.mm": 5.0}), robot.data_source)
        writer.end_episode()
        meta = json.loads((self.root / "info.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["data_source"], "simulated")
        for record in read_episode(path):
            self.assertEqual(record["data_source"], "simulated")

    def test_mixed_sources_are_refused(self):
        writer = DatasetWriter(self.root, "simulated", fps=10)
        writer.start_episode("reach")
        with self.assertRaises(MixedDataError):
            writer.add_frame({}, {}, "real")
        with self.assertRaises(MixedDataError):
            DatasetWriter(self.root, "real", fps=10)
        with self.assertRaises(ValueError):
            DatasetWriter(Path(self.dir.name) / "other", "maybe", fps=10)
        DatasetWriter(self.root, "simulated", fps=10)       # same source reopens fine

    def test_frames_have_lerobot_columns_and_episodes_are_not_overwritten(self):
        robot = make()
        writer = DatasetWriter(self.root, robot.data_source, fps=10)
        sent = []
        first = writer.start_episode("reach")
        for dx in (5.0, -2.0, 1.0):
            observation = robot.get_observation()
            action = act(**{"dx.mm": dx})
            robot.send_action(action)
            writer.add_frame(observation, action, robot.data_source)
            sent.append((observation, action))
        writer.end_episode()
        records = read_episode(first)
        self.assertEqual(len(records), 3)
        self.assertEqual([r["frame_index"] for r in records], [0, 1, 2])
        self.assertEqual([r["next.done"] for r in records], [False, False, True])
        self.assertEqual({r["episode_index"] for r in records}, {0})
        self.assertEqual(records[0]["task"], "reach")
        self.assertEqual(records[0]["timestamp"], 0.0)
        self.assertEqual(records[1]["timestamp"], 0.1)
        for record, (observation, action) in zip(records, sent):        # reloads equal to what was sent
            self.assertEqual(record["observation.state"], observation)
            self.assertEqual(record["action"], action)
        second = DatasetWriter(self.root, "simulated", fps=10).start_episode("again")
        self.assertNotEqual(first, second)
        self.assertEqual(read_episode(first), records)
        with self.assertRaises(FileExistsError):
            writer.start_episode("clash", index=0)

    def test_frames_outside_an_episode_are_refused(self):
        writer = DatasetWriter(self.root, "simulated", fps=10)
        with self.assertRaises(RuntimeError):
            writer.add_frame({}, {}, "simulated")

    def test_a_reused_dict_does_not_change_a_frame_already_added(self):
        writer = DatasetWriter(self.root, "simulated", fps=10)
        path = writer.start_episode("reuse")
        observation, action = {"x.mm": 300.0}, {"dx.mm": 5.0}
        writer.add_frame(observation, action, "simulated")
        observation["x.mm"], action["dx.mm"] = 305.0, -5.0      # the caller reuses its dicts
        writer.add_frame(observation, action, "simulated")
        writer.end_episode()
        records = read_episode(path)
        self.assertEqual([r["observation.state"]["x.mm"] for r in records], [300.0, 305.0])
        self.assertEqual([r["action"]["dx.mm"] for r in records], [5.0, -5.0])

    def test_the_next_episode_index_follows_the_highest_one_on_disk(self):
        writer = DatasetWriter(self.root, "simulated", fps=10)
        writer.start_episode("explicit", index=1)
        writer.end_episode()
        path = writer.start_episode("automatic")        # file count is 1, but episode 1 exists
        writer.end_episode()
        self.assertEqual(path.name, "episode_000002.jsonl")

    def test_reopening_with_a_different_fps_is_refused(self):
        DatasetWriter(self.root, "simulated", fps=10)
        with self.assertRaises(ValueError):
            DatasetWriter(self.root, "simulated", fps=20)
        DatasetWriter(self.root, "simulated", fps=10)       # the same rate reopens fine


class DemoTests(NoSocketCase):
    def test_the_demo_runs_and_saves_an_episode(self):
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as folder:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = sim_demo.main(["--out", str(Path(folder) / "ds")])
            text = out.getvalue()
            self.assertEqual(code, 0)
            self.assertIn("refused: ", text)
            for reason in ("Home", "50", "box"):
                self.assertTrue(any(line.startswith("refused: ") and reason in line for line in text.splitlines()), reason)
            episodes = sorted((Path(folder) / "ds").glob("episode_*.jsonl"))
            self.assertEqual(len(episodes), 1)
            records = read_episode(episodes[0])
            self.assertGreater(len(records), 5)
            self.assertEqual({r["data_source"] for r in records}, {"simulated"})
            self.assertEqual(max(r["observation.state"]["x.mm"] for r in records), 340.0)       # it moved out and came back


class NoHardwareTests(NoSocketCase):
    def test_nothing_opens_a_socket(self):
        robot = make()
        robot.send_action(act(**{"dx.mm": 1.0}))
        robot.disconnect()
        with self.assertRaises(AssertionError):       # the guard itself works
            socket.create_connection(("127.0.0.1", 1))

    def test_imports_without_lerobot(self):
        import lerobot_robot_scorbot_stack1 as plugin
        self.assertIsInstance(plugin.HAVE_LEROBOT, bool)
        if not plugin.HAVE_LEROBOT:
            self.assertNotIn("lerobot", sys.modules)


if __name__ == "__main__":
    unittest.main()
