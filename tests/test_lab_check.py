"""scripts/lab_check.py: the file, port and box checks. USB, tasklist and the 32-bit python need the lab PC."""
import importlib.util
import json
import socket
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("lab_check", ROOT / "scripts" / "lab_check.py")
assert spec and spec.loader
lab_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab_check)


class LabCheckTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name)

    def test_a_complete_bin_folder_passes_and_each_gap_fails(self):
        for name in lab_check.VENDOR_FILES + ("ER4Ax1.ini",):
            (self.path / name).write_text("x")
        (self.path / "Par").mkdir()
        self.assertEqual(lab_check.check_bin(self.path)[0], "PASS")
        (self.path / "ER4Ax1.ini").unlink()
        status, detail = lab_check.check_bin(self.path)
        self.assertEqual(status, "FAIL")
        self.assertIn("ER4Ax", detail)

    def test_a_missing_python_is_a_fail_not_a_crash(self):
        self.assertEqual(lab_check.check_python32(self.path)[0], "FAIL")

    def test_box_missing_or_unmeasured_warns_and_measured_passes(self):
        box = self.path / "arm.json"
        self.assertEqual(lab_check.check_box(box)[0], "WARN")
        box.write_text(json.dumps({"box_mm": {}}))
        self.assertEqual(lab_check.check_box(box)[0], "WARN")
        box.write_text(json.dumps({"measured": True}))
        self.assertEqual(lab_check.check_box(box)[0], "PASS")

    def test_a_free_port_passes_and_a_foreign_listener_fails(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            self.assertEqual(lab_check.check_port(port)[0], "FAIL")      # accepts, never speaks: not the arm server
        self.assertEqual(lab_check.check_port(port)[0], "PASS")

    def test_vendor_and_capture_files_are_recognised_as_untrackable(self):
        for name in ("USBC.dll", "bin/ER4Ax1.ini", "x.pcapng", "ROB_4u.ini"):
            self.assertTrue(lab_check.TRACKED.search(name), name)
        self.assertFalse(lab_check.TRACKED.search("scorbot/bridge.py"))


if __name__ == "__main__":
    unittest.main()
