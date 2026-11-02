"""Every module-level constant in ``scorbot/`` has a row in ``docs/protocol/SOURCE_AUDIT.md``, tagged vendor, legacy or ours.

A number added without saying where it came from is how a made-up value reaches the controller. This fails the
moment a constant has no row, a row names a constant that is gone, or a row's tag is not one of the three.
"""

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AUDIT = ROOT / "docs" / "protocol" / "SOURCE_AUDIT.md"
TAGS = {"vendor", "legacy", "ours"}
KEY = re.compile(r"`((?:[a-z_][a-z0-9_]*\.)+_*[A-Z][A-Z0-9_]*)`")


def _is_constant(name: str) -> bool:
    return name == name.upper() and any(c.isalpha() for c in name)


def constants(package: Path = ROOT / "scorbot") -> set[str]:
    """``module.NAME`` (relative to ``package``) for each UPPER_CASE name assigned at module level, tuples unpacked."""
    found = set()
    for path in sorted(package.rglob("*.py")):
        module = ".".join(path.relative_to(package).with_suffix("").parts)
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            else:
                continue
            for target in targets:
                for name in (target.elts if isinstance(target, ast.Tuple) else [target]):
                    if isinstance(name, ast.Name) and _is_constant(name.id):
                        found.add(f"{module}.{name.id}")
    return found


def audit_rows(text: str) -> dict[str, str]:
    """``module.NAME`` -> tag, from every table row whose first cell names constants."""
    rows = {}
    for line in text.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not line.startswith("|") or len(cells) < 3:
            continue
        for key in KEY.findall(cells[0]):
            rows[key] = cells[2]
    return rows


class SourceAuditTests(unittest.TestCase):
    def setUp(self):
        self.rows = audit_rows(AUDIT.read_text(encoding="utf-8"))

    def test_every_constant_has_a_row(self):
        missing = sorted(constants() - set(self.rows))
        self.assertEqual(missing, [], f"tag these in {AUDIT.name} (vendor, legacy or ours): {', '.join(missing)}")

    def test_every_row_names_a_constant_that_exists(self):
        stale = sorted(set(self.rows) - constants())
        self.assertEqual(stale, [], f"remove or rename these rows in {AUDIT.name}: {', '.join(stale)}")

    def test_every_tag_is_vendor_legacy_or_ours(self):
        bad = {key: tag for key, tag in self.rows.items() if tag not in TAGS}
        self.assertEqual(bad, {}, f"tags must be one of {sorted(TAGS)}")

    def test_the_walker_unpacks_tuples_and_skips_lower_case(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp)
            (package / "sub").mkdir()
            (package / "a.py").write_text("A = 1\nB, C = 2, 3\nlower = 4\n_P: int = 5\ndef f():\n    D = 6\n",
                                          encoding="utf-8")
            (package / "sub" / "b.py").write_text("E = 7\n", encoding="utf-8")
            self.assertEqual(constants(package), {"a.A", "a.B", "a.C", "a._P", "sub.b.E"})


if __name__ == "__main__":
    unittest.main()
