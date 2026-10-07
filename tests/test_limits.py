"""scorbot.limits: one home for the limits that several modules must agree on."""

import unittest

from scorbot import follow, limits, streaming
from scorbot.lab import session as lab_session
from scorbot.lerobot_export import checks, load, sidecar
from scorbot.robot import Scorbot


class OneHomeTests(unittest.TestCase):
    def test_every_module_uses_the_same_travel_cap(self):
        self.assertEqual(limits.TRAVEL_CAP_DEG, 180.0)
        for name, value in (("follow", follow.TRAVEL_CAP_DEG),
                            ("lab session", lab_session.TRAVEL_CAP_DEG),
                            ("stream", Scorbot.STREAM_TRAVEL_CAP_MAX_DEG)):
            self.assertIs(value, limits.TRAVEL_CAP_DEG, name)

    def test_every_module_uses_the_same_motor_lists(self):
        self.assertEqual(limits.ARM_MOTORS, ("base", "shoulder", "elbow"))
        self.assertEqual(limits.RECORDED_MOTORS,
                         ("base", "shoulder", "elbow", "wrist_motor_1", "wrist_motor_2"))
        for name, value in (("stream", streaming.MOTORS), ("follow", follow.STEP_JOINTS),
                            ("sidecar", sidecar.STEP_JOINTS)):
            self.assertIs(value, limits.ARM_MOTORS, name)
        for name, value in (("follow", follow.MOTORS), ("export", load.MOTORS)):
            self.assertIs(value, limits.RECORDED_MOTORS, name)

    def test_every_module_uses_the_same_count_bands(self):
        self.assertEqual((limits.DRIFT_COUNTS, limits.STABLE_COUNTS), (20, 2))
        for name, value in (("follow drift", follow.DRIFT_COUNTS),
                            ("follow wrist", follow.WRIST_TOLERANCE_COUNTS),
                            ("lab drift", lab_session.DRIFT_COUNTS),
                            ("export target", checks.TARGET_TOLERANCE_COUNTS),
                            ("gripper arm", Scorbot.GRIPPER_ARM_TOLERANCE_COUNTS)):
            self.assertIs(value, limits.DRIFT_COUNTS, name)
        for name, value in (("lab stable", lab_session.STABLE_COUNTS),
                            ("stop settle", Scorbot.STOP_SETTLE_COUNTS)):
            self.assertIs(value, limits.STABLE_COUNTS, name)

    def test_the_jog_ceiling_is_the_documented_five_degrees(self):
        self.assertEqual(limits.MAX_JOG_DEG, 5.0)
        self.assertEqual(Scorbot().max_jog_degrees, limits.MAX_JOG_DEG)
        with self.assertRaises(ValueError):
            Scorbot(max_jog_degrees=limits.MAX_JOG_DEG + 0.1)

    def test_limits_are_in_the_motion_fingerprint_and_need_nothing_else(self):
        import subprocess
        import sys
        from scorbot import provenance
        self.assertIn("scorbot/limits.py", provenance._SOURCE_FILES)
        code = ("import sys, importlib.util; "
                "spec = importlib.util.spec_from_file_location('limits', sys.argv[1]); "
                "module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); "
                "print(sorted(m for m in sys.modules if m.split('.')[0] in "
                "('scorbot', 'numpy', 'usb')))")
        out = subprocess.run([sys.executable, "-c", code, limits.__file__],
                             capture_output=True, text=True, check=True)
        self.assertEqual(out.stdout.strip(), "[]")


if __name__ == "__main__":
    unittest.main()
