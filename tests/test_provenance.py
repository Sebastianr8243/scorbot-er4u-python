import tempfile
import unittest
from pathlib import Path

from scorbot import provenance


def _write_tree(root: Path, newline: bytes) -> None:
    for index, relative in enumerate(provenance._SOURCE_FILES):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(newline.join([b"line one", f"file {index}".encode(), b""]))


class MotionSourceFingerprintTests(unittest.TestCase):
    def test_crlf_and_lf_checkouts_give_the_same_fingerprint(self):
        with tempfile.TemporaryDirectory() as lf, tempfile.TemporaryDirectory() as crlf:
            _write_tree(Path(lf), b"\n")
            _write_tree(Path(crlf), b"\r\n")
            self.assertEqual(provenance.motion_source_sha256(Path(lf)),
                             provenance.motion_source_sha256(Path(crlf)))

    def test_a_content_change_still_changes_the_fingerprint(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            _write_tree(Path(first), b"\n")
            _write_tree(Path(second), b"\n")
            target = Path(second) / provenance._SOURCE_FILES[0]
            target.write_bytes(target.read_bytes() + b"changed\n")
            self.assertNotEqual(provenance.motion_source_sha256(Path(first)),
                                provenance.motion_source_sha256(Path(second)))

    def test_default_root_is_the_repository(self):
        self.assertEqual(len(provenance.motion_source_sha256()), 64)


if __name__ == "__main__":
    unittest.main()
