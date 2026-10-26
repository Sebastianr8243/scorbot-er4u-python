"""Keyboard commands for teleoperation: one command per key press (Windows console).

Shared by examples/record_episode.py and examples/teleop.py.
"""

KEYS = {"d": ("dx", 1), "a": ("dx", -1), "w": ("dy", 1), "s": ("dy", -1), "r": ("dz", 1), "f": ("dz", -1)}


def _next_key(getch):
    """The next key, lowercased, or "" for a special key. Windows sends arrows, Insert, Delete and End as a prefix
    plus a scan code that can look like a letter, so both characters are consumed."""
    key = getch().lower()
    if key in ("\x00", "\xe0"):
        getch()
        return ""
    return key


def keyboard_commands(getch, step_mm, pending=lambda: False):
    """Commands from key presses until q. getch() returns one character; pending() says whether more are waiting.

    Keys pressed while a step was running (a held key repeats during the blocking move) are thrown away, so
    letting go stops the arm. A q among them still ends the episode, after the current step."""
    while True:
        key = _next_key(getch)
        if key == "q":
            return
        if key in KEYS:
            axis, sign = KEYS[key]
            command = {axis: sign * step_mm}
        elif key == "o":
            command = {"gripper": True}
        elif key == "c":
            command = {"gripper": False}
        elif key == " ":
            command = {}
        else:
            continue
        yield command
        while pending():
            if _next_key(getch) == "q":         # a quit pressed during the step still ends the episode
                return


def getch():
    import msvcrt
    return msvcrt.getwch()


def kbhit():
    import msvcrt
    return msvcrt.kbhit()
