"""Standard-library terminal front end for the guided lab session.

Single keys come from msvcrt.getwch() on a Windows console; anywhere else, or
when input is piped, each answer is one line from input(). There are no global
keyboard hooks: keys only count while this window has focus.
"""

from __future__ import annotations

import contextlib
import signal
import sys

from .operator import CHOICE_ATTEMPTS, ENTER, matches

SPECIAL = "<special>"


def _msvcrt(name):
    try:
        import msvcrt
    except ImportError:
        return None
    return getattr(msvcrt, name)


class TerminalOperator:
    def __init__(self, getwch=None, isatty=None, kbhit=None):
        self._getwch = getwch if getwch is not None else _msvcrt("getwch")
        self._kbhit = kbhit if kbhit is not None else _msvcrt("kbhit")
        self._isatty = isatty or (lambda: sys.stdin.isatty())

    def _single_keys(self):
        return self._getwch is not None and self._isatty()

    def key(self, prompt):
        if not self._single_keys():
            try:
                line = input(prompt).strip().lower()
            except EOFError:
                return ""
            # Only a single character is a key; "quit" or "exit" is an unknown key,
            # never q (base minus) or e (elbow minus).
            return line or ENTER
        print(prompt, end="", flush=True)
        char = self._getwch()
        if char == "\x03":
            raise KeyboardInterrupt
        if char in ("\x00", "\xe0"):
            self._getwch()               # arrow and function keys send two characters
            print()
            return SPECIAL
        if char in ("\r", "\n"):
            print()
            return ENTER
        print(char)
        return char.lower()

    def can_stop_on_key(self):
        """True only if a key pressed during a move can actually be read."""
        return bool(self._single_keys() and self._kbhit is not None)

    def discard_pending_keys(self):
        """Drop keys typed while the arm was moving, so they never answer a question."""
        if not self._single_keys() or self._kbhit is None:
            return 0
        count = 0
        while self._kbhit():
            self._getwch()
            count += 1
        return count

    def choose(self, prompt, options):
        for _ in range(CHOICE_ATTEMPTS):
            answer = self.key(prompt)
            if answer == "":
                return "unsure"
            if answer in options:
                return options[answer]
            print(f"  Press one of: {', '.join(k for k in options if k != ENTER)}"
                  + (" or Enter" if ENTER in options else ""))
        return "unsure"

    def confirm(self, prompt, expected):
        try:
            return matches(input(prompt), expected)
        except EOFError:
            return False

    def text(self, prompt):
        try:
            return input(prompt).strip()
        except EOFError:
            return ""

    def show(self, message, level="info"):
        prefix = {"alarm": "!!! ", "warn": "! "}.get(level, "")
        for line in str(message).splitlines() or [""]:
            print(f"{prefix}{line}")

    def status(self, line):
        print(f"[ {line.render()} ]")


@contextlib.contextmanager
def termination_as_interrupt():
    """Treat SIGTERM and Windows SIGBREAK (console close, unverified) like Ctrl-C."""
    def interrupt(signum, _frame):
        raise KeyboardInterrupt(f"stopped by signal {signum}")

    previous = {}
    for name in ("SIGTERM", "SIGBREAK"):
        number = getattr(signal, name, None)
        if number is not None:
            try:
                previous[number] = signal.signal(number, interrupt)
            except ValueError:
                break
    try:
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
