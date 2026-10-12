"""Every module, test file and script has a row in the index of its folder (the CLAUDE.md beside it).

These indexes are how a person, or an agent, finds out what a file is for. They drift quietly: a file is
added in a hurry and nobody writes its row. This fails the moment that happens, and names the file.
"""

import fnmatch
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP = {"__init__.py", "__main__.py"}

# (folder, the index that must describe its files, which files count)
INDEXES = (
    ("scorbot", "scorbot/CLAUDE.md", "*.py"),
    ("scorbot/session", "scorbot/CLAUDE.md", "*.py"),
    ("scorbot/lab", "scorbot/CLAUDE.md", "*.py"),
    ("scorbot/camera", "scorbot/CLAUDE.md", "*.py"),
    ("scorbot/transport", "scorbot/CLAUDE.md", "*.py"),
    ("tests", "tests/CLAUDE.md", "test_*.py"),
    ("tests_extra", "tests_extra/CLAUDE.md", "test_*.py"),
    ("scripts", "scripts/CLAUDE.md", "*.py"),
    ("examples", "examples/CLAUDE.md", "*.py"),
)


def undocumented(folder: str, index: str, pattern: str, root: Path = ROOT) -> list[str]:
    """Names of the files in ``folder`` that ``index`` never mentions.

    A file counts as mentioned when its full name (``plot.py``) appears in the index as a whole word, or when a
    row names a pattern it matches (`test_camera_*.py`). The bare stem is not enough (``plot`` would match any
    sentence that uses the word), and neither is a longer name that merely ends the same way
    (``vendor_home_source.py`` does not document ``source.py``).
    """
    text = (root / index).read_text(encoding="utf-8")
    patterns = re.findall(r"`([^`]*\*[^`]*\.py)`", text)
    missing = []
    for path in sorted((root / folder).glob(pattern)):
        if path.name in SKIP or re.search(rf"(?<![A-Za-z0-9_]){re.escape(path.name)}", text):
            continue
        if any(fnmatch.fnmatch(path.name, glob) for glob in patterns):
            continue
        missing.append(path.name)
    return missing


class IndexTests(unittest.TestCase):
    def test_every_file_has_a_row_in_its_folders_index(self):
        for folder, index, pattern in INDEXES:
            with self.subTest(folder=folder):
                missing = undocumented(folder, index, pattern)
                self.assertEqual(missing, [], f"add a row to {index} for: {', '.join(missing)}")

    def test_every_indexed_folder_exists_and_has_files(self):
        # a renamed folder would otherwise make the check above pass on an empty glob
        for folder, index, pattern in INDEXES:
            with self.subTest(folder=folder):
                self.assertTrue((ROOT / index).is_file(), f"{index} is missing")
                self.assertTrue(list((ROOT / folder).glob(pattern)), f"{folder} has no files matching {pattern}")

    def test_the_matcher_is_strict_about_names_and_honours_pattern_rows(self):
        # the matcher itself, against a throwaway folder, so a loosened rule fails here and not silently
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            for name in ("plot.py", "notes.py", "test_camera_a.py", "unlisted.py", "source.py", "__init__.py"):
                (folder / name).write_text("", encoding="utf-8")
            (folder / "INDEX.md").write_text(
                "The plot of the data is drawn elsewhere. `notes.py` keeps the sheet. `test_camera_*.py` camera tests. "
                "`vendor_home_source.py` is the adapter.",
                encoding="utf-8")
            self.assertEqual(undocumented(".", "INDEX.md", "*.py", root=folder), ["plot.py", "source.py", "unlisted.py"])


if __name__ == "__main__":
    unittest.main()
