"""python -m scorbot.camera check, with synthetic frames only."""

import contextlib
import io
from pathlib import Path
import tempfile
import unittest


class CameraCheckTests(unittest.TestCase):
    def test_fake_check_records_a_clean_stream(self):
        try:
            import cv2  # noqa: F401  (the default encoder needs it)
        except ImportError:
            self.skipTest("opencv not installed (pip install .[camera])")
        from scorbot.camera.__main__ import main
        from scorbot.camera.stream import scan_stream
        with tempfile.TemporaryDirectory() as folder:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["check", "--fake", "--seconds", "0.5", "--record", folder])
            self.assertEqual(code, 0, out.getvalue())
            self.assertIn("requested", out.getvalue())
            [session] = [p for p in Path(folder).iterdir() if p.is_dir()]
            index = scan_stream(session, "check")
            self.assertEqual(index.errors, [])
            self.assertGreater(len(index.frames), 0)

    def test_fake_check_without_record(self):
        from scorbot.camera.__main__ import main
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["check", "--fake", "--seconds", "0.3"]), 0)


if __name__ == "__main__":
    unittest.main()
