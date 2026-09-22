"""Package the current working tree for the Windows robot PC.

The kit uses source installation with internet access and excludes local
virtual environments, generated configuration, logs, and build artifacts.
"""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "dist" / "scorbot-er4u-windows-bench-kit.zip"
FILES = ("README.md", "START_HERE_WINDOWS.md", "LICENSE", "pyproject.toml")
DIRECTORIES = ("scorbot", "openScorbot", "examples", "docs", "tests", "scripts", "references")
EXCLUDED = {"__pycache__", "data.json"}
ZADIG = ROOT / "dist" / "usb-tools" / "zadig-2.9.exe"


def included(path: Path) -> bool:
    relative = path.relative_to(ROOT)
    return not any(part in EXCLUDED for part in relative.parts) and path.suffix not in {
        ".pyc", ".log", ".jsonl"
    }


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    sources = [ROOT / filename for filename in FILES]
    for directory in DIRECTORIES:
        sources.extend(path for path in (ROOT / directory).rglob("*") if path.is_file())
    with ZipFile(OUTPUT, "w", ZIP_DEFLATED) as archive:
        for path in sorted(sources):
            if included(path):
                archive.write(path, path.relative_to(ROOT).as_posix())
        if ZADIG.is_file():
            archive.write(ZADIG, "usb-tools/zadig-2.9.exe")
    print(OUTPUT)
    print("Zadig 2.9 included" if ZADIG.is_file() else
          "Zadig not found; use the official download linked in the bench guide")


if __name__ == "__main__":
    main()
