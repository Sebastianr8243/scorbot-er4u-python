"""How the lab session talks to a person, and a scripted stand-in for tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

ENTER = "enter"
CHOICE_ATTEMPTS = 3


@dataclass(frozen=True)
class StatusLine:
    source: str
    homed: bool
    armed: bool
    joint: str
    step_deg: float
    speed: int
    fault: str | None

    def render(self) -> str:
        return (f"{self.source} | {'HOMED' if self.homed else 'HOME NEEDED'} | "
                f"{'ARMED' if self.armed else 'DISARMED'} | {self.joint} | "
                f"step {self.step_deg:g} deg | speed {self.speed} | "
                f"fault: {self.fault or 'none'}")


class Operator(Protocol):
    def confirm(self, prompt: str, expected: str) -> bool: ...
    def choose(self, prompt: str, options: dict[str, str]) -> str: ...
    def key(self, prompt: str) -> str: ...
    def text(self, prompt: str) -> str: ...
    def show(self, message: str, level: str = "info") -> None: ...
    def status(self, line: StatusLine) -> None: ...
    def discard_pending_keys(self) -> int: ...
    def can_stop_on_key(self) -> bool: ...


def matches(typed: str, expected: str) -> bool:
    return typed.strip().upper() == expected.strip().upper()


class ScriptedOperator:
    """Answers from a list; an answer may be a callable returning the string."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.shown: list[str] = []
        self.statuses: list[StatusLine] = []
        self.discards = 0
        self.pending_keys = 0
        self.stop_on_key = True

    def can_stop_on_key(self):
        return self.stop_on_key

    def _next(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.answers:
            raise EOFError("scripted answers exhausted")
        answer = self.answers.pop(0)
        return answer() if callable(answer) else answer

    def confirm(self, prompt, expected):
        try:
            return matches(self._next(prompt), expected)
        except EOFError:
            return False

    def choose(self, prompt, options):
        for _ in range(CHOICE_ATTEMPTS):
            try:
                key = self._next(prompt).strip().lower() or ENTER
            except EOFError:
                return "unsure"
            if key in options:
                return options[key]
        return "unsure"

    def key(self, prompt):
        try:
            # A whole word stays a word, so "quit" can never read as the key q.
            return self._next(prompt).strip().lower() or ENTER
        except EOFError:
            return ""

    def text(self, prompt):
        try:
            return self._next(prompt).strip()
        except EOFError:
            return ""

    def show(self, message, level="info"):
        self.shown.append(message)

    def status(self, line):
        self.statuses.append(line)

    def discard_pending_keys(self):
        self.discards += 1
        count, self.pending_keys = self.pending_keys, 0
        return count
