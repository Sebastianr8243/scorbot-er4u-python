"""Turn an SDK or session error into what it probably means and what to do. Pure.

Rules are checked in order; the first match wins, so a latched "Controller is
faulted: <cause>" still reports its root cause when that cause is known.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Guidance:
    key: str
    title: str
    meaning: str
    steps: tuple[str, ...]


COMMON_STEPS = (
    "If anything is still moving, use the physical stop.",
    "Do not retry in this session.",
    "Note the pose, the LEDs and any sounds; take a photo.",
    "Check the MOTORS LED: the controller may have cut motor power.",
    "Keep hands and objects clear below the arm: whether it holds its pose with "
    "motors off is unverified.",
    "To continue, start a new session. Connecting turns the motors on at the current "
    "pose, so first get the arm back to the known start pose per the lab procedure, "
    "then home again.",
)

_RULES = (
    ("timeout", r"timed out", "Command timed out",
     "The controller did not answer in time. The motion state is unknown; the arm may "
     "still be moving."),
    ("error_too_large", r"error code 1\b", "Joint error too large",
     "Probably a stall, a collision or an impact, or the controller cut motor power "
     "(e-stop, over-current or communication time-out)."),
    ("not_settled", r"error code 2\b", "Joint did not settle",
     "The joint probably did not reach its target within the legacy settle loop; it "
     "may be blocked or overloaded."),
    ("feedback", r"feedback is unavailable|no fresh|stale", "Controller feedback lost",
     "No fresh state arrived from the controller, so the arm's position is unknown."),
    ("worker_crash", r"worker stopped|worker is not running", "USB link lost",
     "A USB worker thread stopped. The controller probably times out and cuts motor "
     "power; when is unverified."),
    ("led_gate", r"led check", "LED check failed",
     "The LEDs did not show the expected state. The motors are probably off or the "
     "controller is not communicating."),
    ("counts_drift", r"counts drift", "Counts moved with nothing commanded",
     "Something probably moved the arm or the readings (electrical noise, a push, or "
     "sagging with motors off), so the logged travel no longer describes the pose."),
    ("faulted", r"controller is faulted", "Session already faulted",
     "An earlier fault latched the session; nothing will move again in this session."),
)
_UNKNOWN = ("unknown", "Unexpected error", "The cause is not recognised.")


def guidance_for(error: str) -> Guidance:
    text = (error or "").lower()
    for key, pattern, title, meaning in _RULES:
        if re.search(pattern, text):
            return Guidance(key, title, meaning, COMMON_STEPS)
    return Guidance(*_UNKNOWN, COMMON_STEPS)


def format_guidance(guidance: Guidance) -> str:
    lines = [f"{guidance.title}: {guidance.meaning}"]
    lines += [f"  {number}. {step}" for number, step in enumerate(guidance.steps, start=1)]
    return "\n".join(lines)
