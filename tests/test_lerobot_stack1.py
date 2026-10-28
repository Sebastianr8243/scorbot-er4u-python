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
    ActionRefused, AdapterStopped, DatasetWriter, MixedDataError, Stack1RobotConfig, Stack1Robot, read_episode)
from lerobot_robot_scorbot_stack1 import robot as robot_module  # noqa: E402
from scorbot.bridge import ArmError, FakeArm  # noqa: E402

BOX = {"x": [250, 350], "y": [-50, 50], "z": [150, 250]}
NOOP = {"dx.mm": 0.0, "dy.mm": 0.0, "dz.mm": 0.0, "gripper.pos": 0.0}


def act(**fields):
    return {**NOOP, **fields}


def make(arm=None, homed=True, **config):
    """A connected robot on a FakeArm (start 300, 0, 200 inside BOX), homed unless homed=False."""
    robot = Stack1Robot(Stack1RobotConfig(box_mm=config.pop("box_mm", BOX), **config))
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
            robot = Stack1Robot(Stack1RobotConfig(calibration_dir=folder))
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
                Stack1RobotConfig(max_step_mm=value)
        Stack1RobotConfig(max_step_mm=10)
        robot = make(max_step_mm=10)
        with self.assertRaises(ActionRefused):
            robot.send_action(act(**{"dx.mm": 11.0}))

    def test_per_arm_fields_and_box_from_the_calibration_dir(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "arm.json").write_text(json.dumps({"box_mm": BOX}), encoding="utf-8")
            config = Stack1RobotConfig(arm_id="arm7", port=8771, host="127.0.0.2", calibration_dir=folder)
            self.assertEqual((config.arm_id, config.port, config.host), ("arm7", 8771, "127.0.0.2"))
            robot = Stack1Robot(config)
            robot.connect(arm=FakeArm(homed=False))
            robot.home()
            robot.send_action(act(**{"dx.mm": 10.0}))
            with self.assertRaises(ActionRefused):
                robot.send_action(act(**{"dx.mm": 45.0}))       # 310 + 45 is past 350
        defaults = Stack1RobotConfig()
        self.assertEqual((defaults.port, str(defaults.calibration_dir), defaults.allow_real), (8765, "calibration", False))


    def test_lerobot_can_find_the_robot_from_the_config_class_name(self):
        """LeRobot's make_device_from_device_class cuts 'Config' off the config class name and looks that name up in the
        plugin package (read from huggingface/lerobot import_utils.py, not run here: lerobot is not installed)."""
        import lerobot_robot_scorbot_stack1 as package
        name = Stack1Robot.config_class.__name__
        self.assertTrue(name.endswith("Config"))
        self.assertIs(getattr(package, name[:-6], None), Stack1Robot)

    def test_calibration_dir_is_always_a_path(self):
        """LeRobot's Robot.__init__ calls config.calibration_dir.mkdir(), so a plain string would crash it."""
        self.assertIsInstance(Stack1RobotConfig().calibration_dir, Path)
        self.assertIsInstance(Stack1RobotConfig(calibration_dir="calibration/arm3").calibration_dir, Path)


class ConnectTests(unittest.TestCase):
    def test_a_real_server_is_refused_without_the_flag(self):
        with mock.patch.object(robot_module, "Arm", side_effect=AssertionError("must not connect")):
            robot = Stack1Robot(Stack1RobotConfig(box_mm=BOX))
            with self.assertRaises(ValueError) as raised:
                robot.connect()
            self.assertIn("allow_real", str(raised.exception))
            self.assertFalse(robot.is_connected)

    def test_the_flag_connects_to_the_configured_port(self):
        stub = mock.MagicMock()
        stub.ping.return_value = {"ok": True, "simulated": False}
        with mock.patch.object(robot_module, "Arm", return_value=stub) as arm_class:
            robot = Stack1Robot(Stack1RobotConfig(box_mm=BOX, allow_real=True, port=8771, host="127.0.0.3"))
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


class SimulateTests(NoSocketCase):
    """`simulate=True` is for lerobot-record on a laptop: connect() builds its own FakeArm, already homed, because
    lerobot-record never calls home(). It must never build an Arm."""

    def robot(self, **config):
        with mock.patch.object(robot_module, "Arm", side_effect=AssertionError("simulate must never build an Arm")):
            robot = Stack1Robot(Stack1RobotConfig(simulate=True, **config))
            robot.connect()
        return robot

    def test_connects_without_an_arm_or_allow_real_and_starts_homed(self):
        robot = self.robot()
        self.assertEqual(robot.data_source, "simulated")
        self.assertTrue(robot.is_connected and robot.is_calibrated)
        self.assertEqual(robot.get_observation(), {"x.mm": 300.0, "y.mm": 0.0, "z.mm": 200.0, "gripper.pos": 0.0})
        robot.send_action(act(**{"dx.mm": 20.0}))       # no home() call
        self.assertEqual(robot.get_observation()["x.mm"], 320.0)
        self.assertFalse(robot._arm.open)               # the FakeArm agrees with the adapter's idea of a closed gripper

    def test_simulate_wins_over_allow_real(self):
        robot = self.robot(allow_real=True)
        self.assertEqual(robot.data_source, "simulated")

    def test_the_safety_layer_still_applies(self):
        robot = self.robot()
        with self.assertRaises(ActionRefused):
            robot.send_action(act(**{"dx.mm": 60.0}))
        self.assertEqual(robot._box, robot_module.SIM_BOX)
        with self.assertRaises(ActionRefused):          # 300 -> 350 -> 400 is the edge, the third step is past it
            for _ in range(3):
                robot.send_action(act(**{"dx.mm": 50.0}))
        self.assertEqual(robot.get_observation()["x.mm"], 400.0)

    def test_an_explicit_box_replaces_the_default_one(self):
        robot = self.robot(box_mm=BOX)
        robot.send_action(act(**{"dx.mm": 45.0}))
        with self.assertRaises(ActionRefused):
            robot.send_action(act(**{"dx.mm": 10.0}))

    def test_arm_json_in_the_calibration_dir_replaces_the_default_box(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "arm.json").write_text(json.dumps({"box_mm": BOX}), encoding="utf-8")
            robot = self.robot(calibration_dir=folder)
        self.assertEqual(robot._box, {axis: [float(lo), float(hi)] for axis, (lo, hi) in BOX.items()})

    def test_the_dataset_type_says_simulated(self):
        """lerobot-record writes robot.name into the dataset's robot_type, and LeRobot v3 has no data_source field."""
        self.assertEqual(Stack1Robot(Stack1RobotConfig(simulate=True)).name, "scorbot_stack1_sim")
        self.assertEqual(Stack1Robot(Stack1RobotConfig()).name, "scorbot_stack1")
        self.assertEqual(Stack1Robot.name, "scorbot_stack1")

    def test_disconnect_then_connect_starts_a_fresh_arm(self):
        robot = self.robot()
        robot.send_action(act(**{"dx.mm": 30.0}))
        robot.disconnect()
        robot.connect()
        self.assertEqual(robot.get_observation()["x.mm"], 300.0)
        with self.assertRaises(RuntimeError):
            robot.connect()

    def test_reset_needs_a_new_home_like_the_real_path(self):
        robot = self.robot()
        robot.reset()
        with self.assertRaises(ActionRefused):
            robot.send_action(act(**{"dx.mm": 10.0}))
        robot.home()
        robot.send_action(act(**{"dx.mm": 10.0}))

    def test_an_arm_passed_in_is_used_as_before(self):
        arm = FakeArm(homed=False)
        robot = Stack1Robot(Stack1RobotConfig(simulate=True, box_mm=BOX))
        robot.connect(arm=arm)
        self.assertIs(robot._arm, arm)
        with self.assertRaises(ActionRefused):          # not homed: only a simulate-built arm starts homed
            robot.send_action(act(**{"dx.mm": 10.0}))


class StandInCamera:
    """What the robot needs from a lerobot Camera: height, width, is_connected, connect, read_latest, disconnect."""

    def __init__(self, height=4, width=6, simulated=False, fail_connect=False):
        self.height, self.width, self.simulated, self.fail_connect = height, width, simulated, fail_connect
        self.is_connected, self.reads, self.fail_read = False, 0, False

    def connect(self):
        if self.fail_connect:
            raise OSError("no such camera")
        self.is_connected = True

    def read_latest(self):
        if self.fail_read:
            raise TimeoutError("camera stopped sending frames")
        self.reads += 1
        return f"frame-{self.reads}"

    def disconnect(self):
        self.is_connected = False


class ClosingArm(FakeArm):
    closed = False

    def close(self):
        self.closed = True


class CameraTests(NoSocketCase):
    """Up to two cameras (or none) through config.cameras; the factory is replaced here, the real one is lerobot's."""

    def robot(self, **cameras):
        with mock.patch.object(robot_module, "make_cameras_from_configs", return_value=cameras):
            return Stack1Robot(Stack1RobotConfig(box_mm=BOX))

    def test_no_cameras_changes_nothing(self):
        robot = make()
        self.assertEqual(robot.cameras, {})
        self.assertEqual(set(robot.observation_features), {"x.mm", "y.mm", "z.mm", "gripper.pos"})

    def test_camera_shapes_are_in_observation_features(self):
        robot = self.robot(front=StandInCamera(4, 6), wrist=StandInCamera(8, 10))
        self.assertEqual(robot.observation_features["front"], (4, 6, 3))
        self.assertEqual(robot.observation_features["wrist"], (8, 10, 3))
        self.assertIs(robot.observation_features["x.mm"], float)
        self.assertEqual(set(robot.action_features), {"dx.mm", "dy.mm", "dz.mm", "gripper.pos"})

    def test_connect_connects_the_cameras_and_disconnect_disconnects_them(self):
        front, wrist = StandInCamera(), StandInCamera()
        robot = self.robot(front=front, wrist=wrist)
        robot.connect(arm=FakeArm(homed=False))
        self.assertTrue(front.is_connected and wrist.is_connected and robot.is_connected)
        wrist.disconnect()
        self.assertFalse(robot.is_connected)            # a dropped camera means not connected
        robot.disconnect()
        robot.disconnect()
        self.assertFalse(front.is_connected)

    def test_observations_carry_one_frame_per_camera(self):
        front, wrist = StandInCamera(), StandInCamera()
        robot = self.robot(front=front, wrist=wrist)
        robot.connect(arm=FakeArm(homed=False))
        robot.home()
        obs = robot.get_observation()
        self.assertEqual(set(obs), set(robot.observation_features))
        self.assertEqual((obs["front"], obs["wrist"]), ("frame-1", "frame-1"))
        self.assertEqual(robot.get_observation()["front"], "frame-2")

    def test_a_camera_that_fails_to_connect_leaves_nothing_open(self):
        good, bad, arm = StandInCamera(), StandInCamera(fail_connect=True), ClosingArm(homed=False)
        robot = self.robot(front=good, wrist=bad)
        with self.assertRaises(OSError):
            robot.connect(arm=arm)
        self.assertFalse(robot.is_connected)
        self.assertTrue(arm.closed)
        self.assertFalse(good.is_connected)

    def test_a_camera_error_does_not_stop_or_latch_the_arm(self):
        """The arm did nothing wrong: a dead camera ends the recording, but no stop is sent and no move was made."""
        cam, arm = StandInCamera(), FakeArm(homed=False)
        robot = self.robot(front=cam)
        robot.connect(arm=arm)
        robot.home()
        cam.fail_read = True
        with self.assertRaises(TimeoutError):
            robot.get_observation()
        self.assertFalse([c for c in arm.commands if c[0] in ("stop", "move_by")])
        cam.fail_read = False
        self.assertEqual(robot.get_observation()["front"], "frame-1")

    def test_a_fake_camera_is_refused_with_a_real_arm(self):
        """Real and simulated data never mix: a fake image next to real arm state would pass as a real demonstration."""
        stub, arm_cam = mock.MagicMock(), StandInCamera(simulated=True)
        stub.ping.return_value = {"ok": True, "simulated": False}
        robot = self.robot(front=arm_cam)
        with self.assertRaises(ValueError) as raised:
            robot.connect(arm=stub)
        self.assertIn("simulated", str(raised.exception))
        self.assertFalse(robot.is_connected)
        stub.close.assert_called_once()
        self.assertFalse(arm_cam.is_connected)

    def test_a_fake_camera_with_the_simulated_arm_is_fine(self):
        robot = self.robot(front=StandInCamera(simulated=True))
        robot.connect(arm=FakeArm(homed=False))
        self.assertTrue(robot.is_connected)


class FeatureTests(NoSocketCase):
    def test_observation_and_action_keys(self):
        robot = make()
        self.assertEqual(set(robot.observation_features), {"x.mm", "y.mm", "z.mm", "gripper.pos"})
        self.assertEqual(set(robot.action_features), {"dx.mm", "dy.mm", "dz.mm", "gripper.pos"})
        self.assertEqual(set(robot.get_observation()), set(robot.observation_features))
        self.assertEqual(robot.get_observation(), {"x.mm": 300.0, "y.mm": 0.0, "z.mm": 200.0, "gripper.pos": 0.0})

    def test_lerobot_record_reads_len_of_cameras_even_with_none(self):
        self.assertEqual(len(make().cameras), 0)


class DataSourceTests(unittest.TestCase):
    def source_for(self, ping):
        stub = mock.MagicMock()
        stub.ping.return_value = ping
        robot = Stack1Robot(Stack1RobotConfig(box_mm=BOX, allow_real=True))
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
