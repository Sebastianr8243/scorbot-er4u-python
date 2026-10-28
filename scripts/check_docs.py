"""Fail on documentation problems that have happened here before. Standard library only.

Checks every git-tracked text file:
  - names and tool references that do not belong in the public repo
  - tracked local-notes files (they are git-ignored on purpose)
  - control characters or tabs in Markdown (a backslash before a letter once turned into one)
  - a mistyped venv path: the folder is `.venv`
  - a requirement ID defined in two files
  - a relative Markdown link to a file that does not exist

Usage: python scripts/check_docs.py        (exit 1 when anything is wrong)
"""
import re
import subprocess
import sys
from pathlib import Path

# shortcut: a word list, so a legitimate use of one of these words needs an edit here; add words as they show up.
BANNED = re.compile(r"sebastian|codex|gemini|claude|copilot|anthropic|professor|the owner", re.I)
LOCAL_ONLY = {"CLAUDE.md", "AGENTS.md", "LESSONS.md", "copilot-instructions.md"}
SKIP_BANNED = {".gitignore", "uv.lock", "LICENSE", "scripts/check_docs.py", "tests/test_check_docs.py"}
BAD_VENV = re.compile(r"(?<![.\w])[\\/]?venv[\\/]Scripts")
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]|\t")
ID_ROW = re.compile(r"^\|\s*([A-Z]{1,3}\d{2})\s*\|")
LINK = re.compile(r"\]\(([^)\s]+)\)")


def check(root, files):
    """Return a list of 'path: problem' strings for the given tracked files under root."""
    root = Path(root)
    problems, ids = [], {}
    for rel in files:
        path = root / rel
        if Path(rel).name in LOCAL_ONLY:
            problems.append(f"{rel}: local-notes file is tracked; it belongs in .gitignore")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary file
        if rel not in SKIP_BANNED:
            m = BANNED.search(text)
            if m:
                problems.append(f"{rel}: contains '{m.group(0)}'")
        if not rel.endswith(".md"):
            continue
        in_code = False
        for n, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("```"):
                in_code = not in_code
            if CONTROL.search(line):
                problems.append(f"{rel}:{n}: control character or tab")
            if BAD_VENV.search(line):
                problems.append(f"{rel}:{n}: 'venv' path without the dot (.venv)")
            m = ID_ROW.match(line)
            if m:
                other = ids.setdefault(m.group(1), rel)
                if other != rel:
                    problems.append(f"{rel}:{n}: requirement ID {m.group(1)} is also defined in {other}")
            if in_code:
                continue
            for target in LINK.findall(line):
                if re.match(r"[a-z][a-z0-9+.-]*:|#", target):
                    continue  # URL, mailto or in-page anchor
                if not (path.parent / target.split("#")[0]).exists():
                    problems.append(f"{rel}:{n}: link to missing file {target}")
    return problems


def main():
    root = Path(__file__).resolve().parent.parent
    out = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True).stdout
    problems = check(root, out.splitlines())
    print("\n".join(problems) if problems else "docs ok")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
