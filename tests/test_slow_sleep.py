"""Windows before Python 3.11 rounds every time.sleep up to the 15.6 ms tick: connect warns."""
import unittest

from scorbot.robot import slow_sleep_warning


class SlowSleepWarningTests(unittest.TestCase):
    def test_windows_before_3_11_is_named(self):
        for version in ((3, 10, 11), (3, 9, 0)):
            with self.subTest(version=version):
                message = slow_sleep_warning("win32", version)
                self.assertIn("15.6 ms", message)
                self.assertIn(f"{version[0]}.{version[1]}", message)

    def test_3_11_and_newer_or_other_systems_are_quiet(self):
        for platform, version in (("win32", (3, 11, 0)), ("win32", (3, 13, 5)),
                                  ("linux", (3, 10, 12)), ("darwin", (3, 10, 0))):
            with self.subTest(platform=platform, version=version):
                self.assertIsNone(slow_sleep_warning(platform, version))


if __name__ == "__main__":
    unittest.main()
