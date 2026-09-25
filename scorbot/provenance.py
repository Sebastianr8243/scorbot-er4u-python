"""Stable fingerprint for the Python motion path in clone or ZIP installs."""

from hashlib import sha256
from pathlib import Path


_SOURCE_FILES = (
    "openScorbot/libcomm.py",
    "openScorbot/libdef.py",
    "openScorbot/motion_profile.py",
    "scorbot/robot.py",
    "scorbot/state.py",
)


def motion_source_sha256() -> str:
    root = Path(__file__).resolve().parent.parent
    digest = sha256()
    for relative in _SOURCE_FILES:
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update((root / relative).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
