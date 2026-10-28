"""examples/lerobot_install_check.py without lerobot: it must say so and never count the stand-in as a pass."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from examples import lerobot_install_check as check  # noqa: E402

try:
    import lerobot  # noqa: F401
    HAVE_REAL = True
except ImportError:
    HAVE_REAL = False


@unittest.skipIf(HAVE_REAL, "a real lerobot is installed: run the script itself")
class WithoutLerobotTests(unittest.TestCase):
    def test_stand_in_base_class_is_a_failure_and_stops_the_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = check.run_checks(tmp)
        self.assertEqual([(name, passed) for name, passed, _ in results], [("real base class", False)])

    def test_main_exits_2_and_says_lerobot_is_missing(self):
        with mock.patch("builtins.print") as shown:
            self.assertEqual(check.main(), 2)
        self.assertIn("not importable", shown.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
