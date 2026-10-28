"""scripts/check_docs.py finds each known problem in a small bad tree and passes a clean one."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from check_docs import check  # noqa: E402


def tree(files):
    root = Path(tempfile.mkdtemp())
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root, list(files)


class CheckDocsTests(unittest.TestCase):
    def test_clean_tree_passes(self):
        root, files = tree({
            "README.md": "# T\n\nSee [guide](docs/guide.md) and [site](https://example.com).\n",
            "docs/guide.md": "Run `.\\.venv\\Scripts\\python.exe x.py`.\n\n| ID | S |\n|---|---|\n| A01 | x |\n",
        })
        self.assertEqual(check(root, files), [])

    def test_every_known_problem_is_found(self):
        root, files = tree({
            "README.md": "Written by Sebastian. [gone](docs/missing.md)\n",
            "CLAUDE.md": "notes\n",
            "docs/a.md": "run `.\\venv\\Scripts\\python.exe x`\nbad\ttab\n\n| ID | S |\n|---|---|\n| L01 | x |\n",
            "docs/b.md": "| L01 | y |\n",
        })
        found = "\n".join(check(root, files))
        for expected in ("contains 'Sebastian'", "link to missing file docs/missing.md", "local-notes file is tracked",
                         "without the dot", "control character or tab", "L01 is also defined"):
            self.assertIn(expected, found)

    def test_links_inside_code_blocks_are_ignored(self):
        root, files = tree({"README.md": "```\n[x](nowhere.md)\n```\n~~~\n[y](nowhere.md)\n~~~\n"})
        self.assertEqual(check(root, files), [])

    def test_a_longer_fence_is_not_closed_by_a_shorter_one(self):
        root, files = tree({"README.md": "````\n```\nx\n```\n[x](nowhere.md)\n````\n[bad](missing.md)\n"})
        self.assertEqual(check(root, files), ["README.md:7: link to missing file missing.md"])

    def test_vertical_tab_and_form_feed_are_found(self):
        root, files = tree({"a.md": "bad\x0bpath\n", "b.md": "bad\x0cpath\n"})
        self.assertEqual(len(check(root, files)), 2)

    def test_a_link_with_a_title_is_still_checked(self):
        root, files = tree({"README.md": '[g](missing.md "Guide")\n'})
        self.assertIn("link to missing file missing.md", check(root, files)[0])

    def test_banned_words_match_whole_words_only(self):
        root, files = tree({"a.md": "Use the ownership flag.\n", "b.md": "Ask the owner.\n"})
        self.assertEqual(check(root, files), ["b.md: contains 'the owner'"])


if __name__ == "__main__":
    unittest.main()
