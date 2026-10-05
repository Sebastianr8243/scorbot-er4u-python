"""Run ``python -m scorbot.session`` inside the test process.

Starting a fresh interpreter for every command line was the largest single
cost in the suite (each one imports numpy and mcap again). The CLI's ``main``
takes its arguments as a list, so most tests can call it directly.
``session_cli_subprocess`` is kept for the few that are about the real
process: its exit code, its output encoding, what it imports.
"""

import contextlib
import io
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent


def session_cli(*args):
    """``scorbot.session`` with these arguments; returncode, stdout, stderr."""
    from scorbot.session.__main__ import main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main([str(arg) for arg in args])
        except SystemExit as exc:      # argparse errors and explicit exits
            if exc.code is None or isinstance(exc.code, int):
                code = exc.code or 0
            else:
                print(exc.code, file=sys.stderr)
                code = 1
    return SimpleNamespace(returncode=code or 0, stdout=out.getvalue(), stderr=err.getvalue())


def session_cli_subprocess(*args):
    """The same command as a real child process."""
    return subprocess.run([sys.executable, "-m", "scorbot.session", *map(str, args)],
                          cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=120)
