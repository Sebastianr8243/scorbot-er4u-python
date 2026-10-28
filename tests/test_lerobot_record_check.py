"""examples/lerobot_record_check.py without lerobot: what it refuses and the command it would run. The real run needs
lerobot in a throwaway venv and is done by hand; the command it builds must never reach an arm server or the hub."""

import io
import sys
import unittest
from contextlib import redirect_stdout
from importlib.util import find_spec
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from examples import lerobot_record_check  # noqa: E402


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.args = lerobot_record_check.command("ds", "cal")
        self.with_cameras = lerobot_record_check.command("ds", "cal", cameras=True)

    def test_it_can_only_run_the_simulated_robot_and_never_the_hub(self):
        for args in (self.args, self.with_cameras):
            for flag in ("--robot.simulate=true", "--dataset.push_to_hub=false", "--dataset.no_stamp=true"):
                self.assertIn(flag, args)
            self.assertFalse([arg for arg in args if "allow_real" in arg or "push_to_hub=true" in arg])

    def test_video_and_cameras_are_on_together_and_only_fake_cameras_are_used(self):
        self.assertIn("--dataset.video=false", self.args)
        self.assertFalse([arg for arg in self.args if "cameras" in arg])
        self.assertIn("--dataset.video=true", self.with_cameras)
        cams = [arg for arg in self.with_cameras if arg.startswith("--robot.cameras=")]
        self.assertEqual(len(cams), 1)
        self.assertEqual(cams[0].count("type: scorbot_fake"), 2)
        self.assertNotIn("opencv", cams[0])

    def test_it_uses_the_scripted_teleoperator_so_no_keyboard_is_needed(self):
        self.assertIn("--teleop.type=scorbot_scripted", self.args)
        self.assertFalse([arg for arg in self.args if "keyboard" in arg or "scorbot_keys" in arg])

    def test_the_script_returns_to_the_start_so_a_long_run_stays_in_the_box(self):
        steps = [[float(v) for v in step.split(",")] for step in lerobot_record_check.STEPS]
        self.assertEqual([sum(step[axis] for step in steps) for axis in range(3)], [0.0, 0.0, 0.0])


@unittest.skipIf(find_spec("lerobot") is not None, "a real lerobot is installed here; the missing-lerobot path is not reachable")
class NoLerobotTests(unittest.TestCase):
    def test_main_says_so_and_returns_2(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = lerobot_record_check.main()
        self.assertEqual(code, 2)
        self.assertIn("not importable", out.getvalue())


if __name__ == "__main__":
    unittest.main()
