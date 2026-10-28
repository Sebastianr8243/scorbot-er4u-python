"""Check the Stack 1 adapter against an installed `lerobot`. FakeArm only: no arm, no DLL, no USB, no server.

    python examples/lerobot_install_check.py

Run it in a throwaway venv that has `lerobot` installed (the repo itself does not depend on it). Prints PASS or FAIL
for each check and exits 0 only if all pass; exits 2 with a message if lerobot is not importable. Proves the plugin
fits LeRobot's code; says nothing about the real arm (unverified), policies, or lerobot-record / lerobot-eval.
"""

import sys
import tempfile
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for path in (ROOT, ROOT / "plugins" / "lerobot_robot_scorbot_stack1"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from lerobot_robot_scorbot_stack1 import ActionRefused, Stack1Robot, Stack1RobotConfig  # noqa: E402
from lerobot_robot_scorbot_stack1 import robot as ours  # noqa: E402
from scorbot.bridge import FakeArm  # noqa: E402

BOX = {"x": [250, 350], "y": [-50, 50], "z": [150, 250]}      # the FakeArm starts at 300, 0, 200


def good(dx=0.0, dy=0.0, dz=0.0, grip=0.0):
    return {"dx.mm": dx, "dy.mm": dy, "dz.mm": dz, "gripper.pos": grip}


def lerobot_version():
    try:
        from importlib.metadata import version
        return version("lerobot")
    except Exception:
        return "unknown"


def config(tmp, **kw):
    return Stack1RobotConfig(calibration_dir=tmp, box_mm=BOX, **kw)


def check_real_base_class():
    assert ours.HAVE_LEROBOT, "our module fell back to the stand-in Robot (lerobot import failed inside it)"
    from lerobot.robots import Robot
    assert issubclass(Stack1Robot, Robot), "Stack1Robot is not a subclass of lerobot.robots.Robot"
    return f"lerobot {lerobot_version()}"


def check_registered():
    from lerobot.robots import RobotConfig
    found = RobotConfig.get_choice_class("scorbot_stack1")
    assert found is Stack1RobotConfig, f"registry returned {found!r}"
    return "scorbot_stack1 -> Stack1RobotConfig"


def check_make_robot(tmp):
    for name in ("lerobot.robots.utils", "lerobot.robots"):
        try:
            make = import_module(name).make_robot_from_config
            break
        except (ImportError, AttributeError):
            make = None
    assert make is not None, "make_robot_from_config not found in lerobot.robots.utils or lerobot.robots"
    robot = make(config(tmp))
    assert type(robot) is Stack1Robot, f"got {type(robot).__name__}"
    return "make_robot_from_config built Stack1Robot"


def check_paths(tmp):
    for value in (str(Path(tmp) / "as_str"), Path(tmp) / "as_path"):
        Stack1Robot(Stack1RobotConfig(calibration_dir=value, box_mm=BOX))
        assert Path(value).is_dir(), f"Robot.__init__ did not create {value}"
    return "calibration_dir as str and Path"


def check_fake_arm_drive(tmp):
    robot = Stack1Robot(config(tmp))
    robot.connect(arm=FakeArm(homed=False))
    assert robot.is_connected and robot.is_calibrated and robot.data_source == "simulated"
    try:
        robot.send_action(good(dx=10))
        raise AssertionError("moved before Home")
    except ActionRefused:
        pass
    robot.home()
    obs = robot.get_observation()
    assert set(obs) == set(robot.observation_features), f"observation keys {sorted(obs)}"
    sent = robot.send_action(good(dx=20))
    assert set(sent) == set(robot.action_features), f"action keys {sorted(sent)}"
    after = robot.get_observation()
    assert abs(after["x.mm"] - obs["x.mm"] - 20) < 1e-6, f"x went {obs['x.mm']} -> {after['x.mm']}"
    try:
        robot.send_action(good(dx=60))
        raise AssertionError("60 mm step was not refused")
    except ActionRefused:
        pass
    robot.disconnect()
    assert not robot.is_connected
    return "connect, home, observe, step, refuse, disconnect"


def check_cli_parse():
    import draccus
    from lerobot.robots import RobotConfig

    @dataclass
    class Wrapper:
        robot: RobotConfig

    parsed = draccus.parse(config_class=Wrapper, args=["--robot.type=scorbot_stack1", "--robot.arm_id=arm7", "--robot.port=8771"])
    assert type(parsed.robot) is Stack1RobotConfig, f"parsed {type(parsed.robot).__name__}"
    assert (parsed.robot.arm_id, parsed.robot.port) == ("arm7", 8771)
    return "--robot.type=scorbot_stack1 parsed to Stack1RobotConfig"


def run_checks(tmp):
    """[(name, passed, detail)]. The first check gates the rest: without the real base class nothing else means anything."""
    results = []
    for name, fn, args in (("real base class", check_real_base_class, ()), ("registered by name", check_registered, ()),
                           ("make_robot_from_config", check_make_robot, (tmp,)), ("calibration_dir types", check_paths, (tmp,)),
                           ("drive on FakeArm", check_fake_arm_drive, (tmp,)), ("draccus CLI parse", check_cli_parse, ())):
        try:
            results.append((name, True, fn(*args)))
        except Exception as error:      # a failed check must not hide the others, except the first
            results.append((name, False, f"{type(error).__name__}: {error}"))
            if name == "real base class":
                break
    return results


def main():
    try:
        import lerobot  # noqa: F401
    except ImportError as error:
        print(f"lerobot is not importable ({error}). Install it in a throwaway venv, then run this again.")
        return 2
    with tempfile.TemporaryDirectory() as tmp:
        results = run_checks(tmp)
    for name, passed, detail in results:
        print(f"{'PASS' if passed else 'FAIL'}  {name:<24} {detail}")
    ok = all(passed for _, passed, _ in results)
    print("all checks passed" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
