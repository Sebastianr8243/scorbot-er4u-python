"""Standard-library terminal front end for the guided lab session.

Single keys come from msvcrt.getwch() on a Windows console; anywhere else, or
when input is piped, each answer is one line from input(). There are no global
keyboard hooks: keys only count while this window has focus.
"""

from __future__ import annotations

import contextlib
import signal
import sys
import time

from .operator import CHOICE_ATTEMPTS, ENTER, matches

SPECIAL = "<special>"
RELEASE_POLL_S = 0.02
RELEASE_WARN_S = 3.0


def _virtual_key(char):
    """Windows virtual-key code for a letter or digit key; None for anything else."""
    upper = char.upper()
    if len(upper) == 1 and ("A" <= upper <= "Z" or "0" <= upper <= "9"):
        return ord(upper)
    return None


def quiet_from_settings(delay_setting: int, speed_setting: int) -> float:
    """Seconds without a typed repeat that prove a held key would have repeated.

    Windows keyboard delay 0-3 means about 250-1000 ms before the first repeat;
    speed 0-31 means about 2.5-30 repeats per second. The quiet window is the
    longer of the two plus a 0.1 s margin.
    """
    delay_s = (min(max(delay_setting, 0), 3) + 1) * 0.25
    rate_hz = 2.5 + min(max(speed_setting, 0), 31) * (27.5 / 31)
    return max(delay_s, 1.0 / rate_hz) + 0.1


def _windows_quiet_s() -> float:
    """The quiet window from this PC's keyboard settings; 1.1 s (the slowest) if unknown."""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        delay, speed = ctypes.c_uint(), ctypes.c_uint()
        if (user32.SystemParametersInfoW(0x0016, 0, ctypes.byref(delay), 0)       # KEYBOARDDELAY
                and user32.SystemParametersInfoW(0x000A, 0, ctypes.byref(speed), 0)):  # SPEED
            return quiet_from_settings(delay.value, speed.value)
    except (ImportError, AttributeError, OSError):
        pass
    return quiet_from_settings(3, 0)


def _windows_key_down():
    """Physical key state through user32.GetAsyncKeyState; None off Windows.

    Only ever used to wait for a release (it delays motion, never causes it):
    motion still comes only from a character read from this console.
    """
    try:
        import ctypes
        user32 = ctypes.windll.user32
    except (ImportError, AttributeError, OSError):
        return None

    def key_down(char):
        code = _virtual_key(char)
        return code is not None and bool(user32.GetAsyncKeyState(code) & 0x8000)
    return key_down


def _msvcrt(name):
    try:
        import msvcrt
    except ImportError:
        return None
    return getattr(msvcrt, name)


class TerminalOperator:
    def __init__(self, getwch=None, isatty=None, kbhit=None, key_down=None,
                 sleep=time.sleep, clock=time.monotonic, quiet_s=None):
        self._getwch = getwch if getwch is not None else _msvcrt("getwch")
        self._kbhit = kbhit if kbhit is not None else _msvcrt("kbhit")
        self._isatty = isatty or (lambda: sys.stdin.isatty())
        self._key_down = key_down if key_down is not None else _windows_key_down()
        self._sleep, self._clock = sleep, clock
        self._quiet_s = quiet_s if quiet_s is not None else _windows_quiet_s()
        self._last_key_s = None   # when the last single key was read

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
        self._last_key_s = self._clock()
        return char.lower()

    def can_stop_on_key(self):
        """True only if a key pressed during a move can actually be read."""
        return bool(self._single_keys() and self._kbhit is not None)

    def can_wait_for_release(self):
        return bool(self._single_keys() and self._kbhit is not None
                    and self._key_down is not None)

    def wait_for_release(self, key):
        """Block until `key` is up AND no repeat has arrived for the quiet window.

        Two independent checks: the physical key state, and Windows' own repeat
        timing. GetAsyncKeyState returns 0 ("up") when it fails, for example on
        another desktop or under UI privilege isolation (Microsoft docs), so the
        key state alone could let a held key through; a held key always types a
        repeat within the quiet window, so silence that long proves release.
        Returns the number of queued characters dropped. Only ever delays motion.
        """
        if not self._single_keys() or self._kbhit is None:
            return self.discard_pending_keys()
        started = self._clock()
        last = self._last_key_s if self._last_key_s is not None else started
        warned, dropped = False, 0
        while True:
            while self._kbhit():
                self._getwch()
                dropped += 1
                last = self._clock()
            down = self._key_down is not None and self._key_down(key)
            if not down and self._clock() - last >= self._quiet_s:
                return dropped
            if not warned and self._clock() - started > RELEASE_WARN_S:
                print("  Release the key to continue.", flush=True)
                warned = True
            self._sleep(RELEASE_POLL_S)

    def key_or_tick(self, prompt, tick_s):
        """A key if one arrives within tick_s, else None (lets the caller poll)."""
        if not self._single_keys() or self._kbhit is None:
            return self.key(prompt)
        deadline = self._clock() + tick_s
        while True:
            if self._kbhit():
                return self.key(prompt)
            if self._clock() >= deadline:
                return None
            self._sleep(RELEASE_POLL_S)

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
