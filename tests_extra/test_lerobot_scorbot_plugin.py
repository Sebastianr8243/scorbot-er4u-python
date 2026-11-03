"""plugins/lerobot_robot_scorbot (Stack 2, simulator only) and examples/lerobot_sim_check.py.

Pure parts run everywhere: feature dicts, the real-arm refusal, the teleoperator maps, the record command. A drive
on the simulated controller runs where Ruckig is installed (the stand-in base class without lerobot). The checks
against a real lerobot run only where lerobot is importable; the full record and replay is the example, by hand.
"""

from importlib.util import find_spec
import math
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "plugins" / "lerobot_robot_scorbot"))
sys.path.insert(0, str(ROOT))

from examples import lerobot_sim_check  # noqa: E402
from lerobot_robot_scorbot import REAL_ARM_REFUSED, ScorbotRobot, ScorbotRobotConfig  # noqa: E402
from lerobot_robot_scorbot.teleop_map import (  # noqa: E402
    DEFAULT_SCRIPT, check_limits, home, keyboard_to_target, parse_script, script_walk, step_toward, to_action)

KEYS = ("base.pos", "shoulder.pos", "elbow.pos", "pitch.pos", "roll.pos")
HAVE_LEROBOT = find_spec("lerobot") is not None


CONFIRMED = dict(operator_at_stop=True, jaws_empty=True, wrist_clear=True)


def sim_config(tmp, confirmed=True, **kw):
    extra = {"calibration_dir": Path(tmp) / "cal"} if HAVE_LEROBOT else {}
    return ScorbotRobotConfig(simulate=True, log_dir=Path(tmp) / "logs", **extra, **(CONFIRMED if confirmed else {}), **kw)


class RobotShapeTests(unittest.TestCase):
    def test_the_real_arm_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            ScorbotRobot(ScorbotRobotConfig())
        self.assertEqual(str(caught.exception), REAL_ARM_REFUSED)
        self.assertFalse(ScorbotRobotConfig().simulate)

    def test_features_are_five_joints_in_degrees(self):
        with tempfile.TemporaryDirectory() as tmp:
            robot = ScorbotRobot(sim_config(tmp))
        self.assertEqual(robot.action_features, dict.fromkeys(KEYS, float))
        self.assertEqual(robot.observation_features, dict.fromkeys(KEYS, float))
        self.assertEqual(robot.name, "scorbot_sim")
        self.assertFalse(robot.is_connected)
        self.assertTrue(robot.is_calibrated)

    def test_not_connected_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            robot = ScorbotRobot(sim_config(tmp))
        with self.assertRaises(ConnectionError):
            robot.get_observation()
        with self.assertRaises(ConnectionError):
            robot.send_action(dict.fromkeys(KEYS, 0.0))
        robot.disconnect()      # nothing to close


@unittest.skipUnless(find_spec("ruckig"), "needs the planning extra")
class SimDriveTests(unittest.TestCase):
    def test_connect_observe_act_disconnect(self):
        with tempfile.TemporaryDirectory() as tmp:
            robot = ScorbotRobot(sim_config(tmp))
            robot.connect()
            try:
                self.assertEqual(robot.get_observation(), dict.fromkeys(KEYS, 0.0))
                sent = robot.send_action({"base.pos": 90.0, "shoulder.pos": 0.25, "elbow.pos": float("nan"),
                                          "pitch.pos": -3.0, "roll.pos": 0.1})
                self.assertEqual(sent, {"base.pos": 0.5, "shoulder.pos": 0.25, "elbow.pos": 0.0,
                                        "pitch.pos": -0.5, "roll.pos": 0.1})
                self.assertEqual(robot._arm._stream.core.motors[3:], ("wrist_motor_1", "wrist_motor_2"))
                with self.assertRaises(RuntimeError):
                    robot.connect()
            finally:
                robot.disconnect()
            self.assertFalse(robot.is_connected)
            robot.disconnect()

    def test_the_home_confirmations_default_to_false(self):
        config = ScorbotRobotConfig()
        self.assertEqual((config.operator_at_stop, config.jaws_empty, config.wrist_clear), (False, False, False))

    def test_connect_without_the_confirmations_fails_before_anything_is_sent(self):
        with tempfile.TemporaryDirectory() as tmp:
            robot = ScorbotRobot(sim_config(tmp, confirmed=False))
            with self.assertRaises(ValueError):
                robot.connect()
            self.assertIsNone(robot._arm)
            self.assertFalse(robot.is_connected)

    def test_connect_runs_the_vendor_home(self):
        from scorbot.vendor_home_source import VENDOR_HOME_ORDER
        with tempfile.TemporaryDirectory() as tmp:
            robot = ScorbotRobot(sim_config(tmp))
            robot.connect()
            try:
                orders = [c[0] for c in robot._arm.robot.sim.commands]
                self.assertIn(VENDOR_HOME_ORDER, orders)
                self.assertNotIn([18, 1, 1], robot._arm.robot.sim.commands)     # the legacy Home
            finally:
                robot.disconnect()

    def test_the_config_speed_reaches_the_arm(self):
        with tempfile.TemporaryDirectory() as tmp:
            robot = ScorbotRobot(sim_config(tmp, speed_pct=80))
            robot.connect()
            try:
                self.assertEqual(robot._arm.speed_pct, 80)
                self.assertEqual(robot._arm.step_limit, "fixed")
            finally:
                robot.disconnect()

    def test_the_config_step_limit_reaches_the_arm(self):
        with tempfile.TemporaryDirectory() as tmp:
            robot = ScorbotRobot(sim_config(tmp, step_limit="speed"))
            robot.connect()
            try:
                self.assertEqual(robot._arm.step_limit, "speed")
            finally:
                robot.disconnect()


class TeleopMapTests(unittest.TestCase):
    def test_keys_step_the_target_and_stop_at_the_cap(self):
        target = home()
        target = keyboard_to_target({"delta_x": 1, "delta_y": -1, "delta_z": 0}, target, 0.5, 2.0)
        self.assertEqual(target, {"base": 0.5, "shoulder": -0.5, "elbow": 0.0, "pitch": 0.0, "roll": 0.0})
        for _ in range(10):
            target = keyboard_to_target({"delta_x": 1, "delta_y": 0, "delta_z": -1}, target, 0.5, 2.0)
        self.assertEqual(target, {"base": 2.0, "shoulder": -0.5, "elbow": -2.0, "pitch": 0.0, "roll": 0.0})

    def test_a_bigger_raw_value_is_still_one_step(self):
        self.assertEqual(keyboard_to_target({"delta_x": 5.0}, home(), 0.5, 2.0)["base"], 0.5)

    def test_no_key_bad_values_and_the_gripper_hold(self):
        start = {"base": 1.0, "shoulder": 0.0, "elbow": -1.0, "pitch": 0.5, "roll": -0.5}
        for raw in ({}, {"gripper": 2}, {"delta_x": float("nan")}, {"delta_x": "x"}, {"delta_x": True}):
            with self.subTest(raw=raw):
                self.assertEqual(keyboard_to_target(raw, start, 0.5, 2.0), start)

    def test_action_keys(self):
        self.assertEqual(tuple(to_action(home())), KEYS)

    def test_limits(self):
        for step, cap in ((0, 2), (0.6, 2), (0.5, 0), (0.5, 2.1), (math.nan, 2), (0.5, math.inf), (True, 2)):
            with self.subTest(step=step, cap=cap), self.assertRaises(ValueError):
                check_limits(step, cap)
        check_limits(0.5, 2.0)
        check_limits(0.1, 0.5)

    def test_scripts(self):
        self.assertEqual(parse_script(DEFAULT_SCRIPT)[-1], home())
        self.assertEqual(parse_script(" 1, -0.5 ,2 "),
                         [{"base": 1.0, "shoulder": -0.5, "elbow": 2.0, "pitch": 0.0, "roll": 0.0}])
        self.assertEqual(parse_script("1,-0.5,2,0.5,-1"),
                         [{"base": 1.0, "shoulder": -0.5, "elbow": 2.0, "pitch": 0.5, "roll": -1.0}])
        for text in ("", "1,2", "1,2,3,4", "1,2,3,4,5,6", "a,0,0", "nan,0,0", "2.1,0,0", "0,0,0,0,2.1",
                     "1,0,0;;0,0,0"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_script(text)

    def test_the_walk_moves_one_step_per_call_and_visits_every_waypoint(self):
        waypoints = parse_script(DEFAULT_SCRIPT)
        walk = script_walk(waypoints, 0.5, 2.0)
        last, visited = home(), []
        for _ in range(40):
            target = next(walk)
            self.assertTrue(all(abs(target[m] - last[m]) <= 0.5 + 1e-9 for m in target))
            if target in waypoints and target not in visited:
                visited.append(target)
            last = target
        self.assertEqual(len(visited), len(waypoints))

    def test_step_toward_stops_at_the_goal(self):
        self.assertEqual(step_toward(home(), {"base": 0.2}, 0.5, 2.0)["base"], 0.2)


class CheckCommandTests(unittest.TestCase):
    def test_the_record_command_is_simulated_local_and_unattended(self):
        args = lerobot_sim_check.command("ds", "scratch")
        for flag in ("--robot.type=scorbot", "--robot.simulate=true", "--dataset.push_to_hub=false",
                     "--teleop.type=scorbot_joint_scripted", "--dataset.video=false"):
            self.assertIn(flag, args)
        self.assertFalse([a for a in args if "push_to_hub=true" in a or "cameras" in a or "keys" in a])
        self.assertEqual(lerobot_sim_check.env()["HF_HUB_OFFLINE"], "1")

    def test_the_script_ends_at_home(self):
        self.assertEqual(parse_script(lerobot_sim_check.SCRIPT)[-1], home())


HAVE_TWIN = HAVE_LEROBOT and find_spec("mujoco") is not None and find_spec("ruckig") is not None
TWIN_TOL_DEG = 1e-9         # the twin is posed kinematically from the same counts: no physics lag


def twin_cameras(width=160, height=120):
    from lerobot_robot_scorbot.sim_cameras import ScorbotTwinCameraConfig
    return {name: ScorbotTwinCameraConfig(camera=name, width=width, height=height) for name in ("front", "wrist")}


@unittest.skipUnless(HAVE_TWIN, "needs lerobot, mujoco and ruckig (the LeRobot venv)")
class TwinCameraTests(unittest.TestCase):
    def test_the_twin_follows_the_commanded_pose(self):
        import time
        from scorbot import mujoco_twin, policy_arm
        with tempfile.TemporaryDirectory() as tmp:
            robot = ScorbotRobot(sim_config(tmp, cameras=twin_cameras()))
            robot.connect()
            try:
                goal = {"base.pos": 1.0, "shoulder.pos": -1.0, "elbow.pos": 1.5, "pitch.pos": 1.0, "roll.pos": -1.0}
                for _ in range(8):
                    robot.send_action(goal)
                    time.sleep(0.05)
                time.sleep(0.8)
                obs = robot.get_observation()
                arm = robot._arm
                commanded = {m: round(goal[m + ".pos"] * arm._per_degree[m]) for m in policy_arm.MOTORS}
                commanded.update(policy_arm.wrist_counts(goal["pitch.pos"], goal["roll.pos"]))
                want = mujoco_twin.chain_angles(commanded)
                data = robot._scene.data
                for name, deg in zip(mujoco_twin.JOINTS, want):
                    got = math.degrees(data.joint(name).qpos[0])
                    self.assertAlmostEqual(got, deg, delta=0.05, msg=name)    # the sim settled on the command
                seen = mujoco_twin.chain_angles(arm.counts_from_home())
                for name, deg in zip(mujoco_twin.JOINTS, seen):
                    self.assertAlmostEqual(math.degrees(data.joint(name).qpos[0]), deg, delta=TWIN_TOL_DEG, msg=name)
                self.assertTrue(all(abs(obs[k] - goal[k]) < 0.1 for k in KEYS), obs)
            finally:
                robot.disconnect()
            self.assertIsNone(robot._scene)

    def test_front_and_wrist_images_come_through_get_observation(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as tmp:
            robot = ScorbotRobot(sim_config(tmp, cameras=twin_cameras(160, 120)))
            self.assertEqual(robot.observation_features["front"], (120, 160, 3))
            self.assertEqual(robot.observation_features["wrist"], (120, 160, 3))
            robot.connect()
            try:
                obs = robot.get_observation()
                for name in ("front", "wrist"):
                    self.assertEqual(obs[name].shape, (120, 160, 3), name)
                    self.assertEqual(obs[name].dtype, np.uint8, name)
                    self.assertGreater(int(obs[name].std()), 0, f"{name} is a blank frame")
                self.assertFalse(np.array_equal(obs["front"], obs["wrist"]), "both cameras gave the same frame")
                again = robot.get_observation()
                self.assertTrue(np.array_equal(again["front"], obs["front"]), "same pose, different frame")
            finally:
                robot.disconnect()
            self.assertFalse(any(cam.is_connected for cam in robot.cameras.values()))

    def test_lerobot_builds_the_twin_camera_from_its_type_name(self):
        from lerobot.cameras import CameraConfig, make_cameras_from_configs
        from lerobot_robot_scorbot.sim_cameras import ScorbotTwinCamera, ScorbotTwinCameraConfig
        self.assertIs(CameraConfig.get_choice_class("scorbot_twin"), ScorbotTwinCameraConfig)
        cams = make_cameras_from_configs(twin_cameras())
        self.assertIsInstance(cams["front"], ScorbotTwinCamera)

    def test_a_frame_bigger_than_the_offscreen_buffer_is_refused(self):
        from lerobot_robot_scorbot.sim_cameras import ScorbotTwinCameraConfig
        with self.assertRaises(ValueError):
            ScorbotTwinCameraConfig(camera="front", width=1280, height=720)
        with self.assertRaises(ValueError):
            ScorbotTwinCameraConfig(camera="side")

    def test_a_twin_camera_without_the_robot_says_so(self):
        from lerobot_robot_scorbot.sim_cameras import ScorbotTwinCamera
        cam = ScorbotTwinCamera(twin_cameras()["front"])
        with self.assertRaisesRegex(RuntimeError, "scorbot robot"):
            cam.connect()


@unittest.skipUnless(HAVE_LEROBOT, "needs lerobot (examples/lerobot_sim_check.py in its own venv)")
class WithLerobotTests(unittest.TestCase):
    def test_registered_and_built_by_lerobot(self):
        from lerobot.robots import Robot, RobotConfig
        from lerobot.robots.utils import make_robot_from_config
        from lerobot.teleoperators import Teleoperator, TeleoperatorConfig
        self.assertIs(RobotConfig.get_choice_class("scorbot"), ScorbotRobotConfig)
        for name in ("scorbot_joint_keys", "scorbot_joint_scripted"):
            self.assertIsNotNone(TeleoperatorConfig.get_choice_class(name))
        with tempfile.TemporaryDirectory() as tmp:
            robot = make_robot_from_config(sim_config(tmp))
            self.assertIsInstance(robot, ScorbotRobot)
            self.assertIsInstance(robot, Robot)
        from lerobot_robot_scorbot import ScorbotJointScripted
        from lerobot_robot_scorbot.teleop import ScorbotJointScriptedConfig
        teleop = ScorbotJointScripted(ScorbotJointScriptedConfig())
        self.assertIsInstance(teleop, Teleoperator)
        teleop.connect()
        self.assertEqual(teleop.get_action(), {"base.pos": 0.5, "shoulder.pos": 0.0, "elbow.pos": 0.0,
                                               "pitch.pos": 0.0, "roll.pos": 0.0})
        teleop.disconnect()

    @unittest.skipUnless(find_spec("pynput"), "LeRobot's keyboard teleoperator needs pynput even to construct")
    def test_keys_teleop_maps_lerobots_keyboard_action_to_joint_targets(self):
        from unittest import mock
        from lerobot.teleoperators.keyboard import KeyboardEndEffectorTeleop
        from lerobot_robot_scorbot import ScorbotJointKeys
        from lerobot_robot_scorbot.teleop import ScorbotJointKeysConfig
        teleop = ScorbotJointKeys(ScorbotJointKeysConfig())     # no connect: that needs pynput and a keyboard
        self.assertEqual(teleop.action_features, dict.fromkeys(KEYS, float))
        raw = {"delta_x": 1, "delta_y": 0, "delta_z": -1}
        with mock.patch.object(KeyboardEndEffectorTeleop, "get_action", return_value=raw):
            self.assertEqual(teleop.get_action(), {"base.pos": 0.5, "shoulder.pos": 0.0, "elbow.pos": -0.5,
                                                   "pitch.pos": 0.0, "roll.pos": 0.0})
            self.assertEqual(teleop.get_action(), {"base.pos": 1.0, "shoulder.pos": 0.0, "elbow.pos": -1.0,
                                                   "pitch.pos": 0.0, "roll.pos": 0.0})


if __name__ == "__main__":
    unittest.main()
