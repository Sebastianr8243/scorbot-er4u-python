"""Stable fingerprint for the Python motion path in clone or ZIP installs."""

from hashlib import sha256
from pathlib import Path


_SOURCE_FILES = (
    "openScorbot/libcomm.py",
    "openScorbot/libdef.py",
    "openScorbot/motion_profile.py",
    "scorbot/calibration.py",
    "scorbot/inch_home.py",
    "scorbot/joint_move.py",
    "scorbot/limits.py",
    "scorbot/robot.py",
    "scorbot/state.py",
    "scorbot/streaming.py",
    "scorbot/vendor_profile.py",
)


def motion_source_sha256(root: Path | None = None) -> str:
    """Hash the motion-path sources with line endings normalised to LF.

    A Windows CRLF checkout, a Linux LF checkout and a ZIP of the same code
    give the same value. Logs written before this normalisation (BACKLOG #46)
    hashed raw bytes, so their values only match a checkout with the same
    line endings.
    """
    if root is None:
        root = Path(__file__).resolve().parent.parent
    digest = sha256()
    for relative in _SOURCE_FILES:
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update((root / relative).read_bytes().replace(b"\r\n", b"\n"))
        digest.update(b"\0")
    return digest.hexdigest()
