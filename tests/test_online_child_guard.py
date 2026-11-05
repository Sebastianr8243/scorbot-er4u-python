"""The pytest backstop in conftest.py: no test may start a hardware tool with --online in a child process.

The scripts named here do not exist, so even without the backstop (plain unittest) nothing could reach the arm.
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


def guarded():
    return getattr(subprocess.Popen.__init__, "__name__", "") == "guarded"


class OnlineChildGuardTests(unittest.TestCase):
    def setUp(self):
        if not guarded():
            self.skipTest("the backstop lives in conftest.py and runs only under pytest")
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.dir = Path(folder.name)

    def test_online_hardware_tools_are_refused_before_they_start(self):
        for argv in ([sys.executable, str(self.dir / "bring_up.py"), "--online", "--logs", "x"],
                     [sys.executable, str(self.dir / "lab_day.py"), "--online"],
                     [sys.executable, "-m", "scorbot.lab", "--online"],
                     [sys.executable, "-m", "scorbot.transport.probe", "--online"],
                     ["scorbot", "lab", "day", "--online"],
                     f'"{sys.executable}" {self.dir / "bring_up.py"} --online'):
            with self.subTest(argv=argv), self.assertRaisesRegex(AssertionError, "conftest.py backstop"):
                subprocess.run(argv, capture_output=True)

    def test_simulated_and_unrelated_children_still_run(self):
        missing = str(self.dir / "bring_up.py")
        self.assertNotEqual(subprocess.run([sys.executable, missing, "--simulate"], capture_output=True).returncode, 0)
        self.assertEqual(subprocess.run([sys.executable, "-c", "pass", "--online"], capture_output=True).returncode, 0)


if __name__ == "__main__":
    unittest.main()
