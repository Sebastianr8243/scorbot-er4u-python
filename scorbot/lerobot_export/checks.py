"""Refusal rules for dataset export (spec section 2). Pure functions over loaded data.

A refused episode is never exported; every refusal names its reason so the
operator can see what went wrong in the lab.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
import math

TARGET_TOLERANCE_COUNTS = 20   # the legacy settle band (lab DRIFT_COUNTS)
MAX_CLOCK_RESOLUTION_S = 1e-3
MIN_FRAMES = 2
FAULT_ROWS = ("jog_failed", "counts_drift")


@dataclass(frozen=True)
class Refusal:
    scope: str          # "export", "session" or "episode"
    session: str
    episode: int | None
    reason: str


def grid(start_ns: int, end_ns: int, fps: int) -> list[int]:
    """Tick times from start to end inclusive, one every 1/fps seconds."""
    step = 1e9 / fps
    count = int((end_ns - start_ns) // step) + 1
    return [start_ns + round(k * step) for k in range(count)]


def check_export(sessions, *, video: bool) -> list[Refusal]:
    refusals = []
    sources = {s.data_source for s in sessions}
    if len(sources) > 1:
        refusals.append(Refusal("export", "-", None, "inputs mix real and simulated "
                                                     f"sources {sorted(map(str, sources))}"))
    cameras = {e.camera for s in sessions for e in s.episodes if e.status == "completed"}
    if video and False in cameras:
        refusals.append(Refusal("export", "-", None, "some completed episodes have no "
                                                     "camera; export them with --no-video"))
    if video:
        sizes = {(f["width"], f["height"]) for s in sessions if s.camera
                 for f in s.camera.frames[:1]}
        if len(sizes) > 1:
            refusals.append(Refusal("export", "-", None, f"camera sizes differ: {sizes}"))
    return refusals


def check_session(data, *, video: bool) -> list[Refusal]:
    def refuse(reason):
        return Refusal("session", data.name, None, reason)
    out = [refuse(error) for error in data.load_errors]
    if data.home_raw is None:
        out.append(refuse("no home_complete row: counts cannot be made relative to home"))
    if data.mcap is not None and data.mcap.errors:
        out.append(refuse("MCAP session has integrity errors: "
                          + "; ".join(f.message for f in data.mcap.errors)))
    if any(row.get("type") == "session_failed" for row in data.rows):
        out.append(refuse("lab session_failed row"))
    if data.clock_resolution_s is None or data.clock_resolution_s > MAX_CLOCK_RESOLUTION_S:
        out.append(refuse(f"clock resolution {data.clock_resolution_s} s is coarser than "
                          f"{MAX_CLOCK_RESOLUTION_S} s"))
    if video and data.camera is not None and data.camera.errors:
        out.append(refuse("camera stream has errors: "
                          + "; ".join(f.message for f in data.camera.errors)))
    return out


def _in_window(t, episode):
    return t is not None and episode.start_ns <= t <= episode.end_ns


def check_episode(data, episode, *, fps, max_frame_gap_s, video) -> list[Refusal]:
    def refuse(reason):
        return Refusal("episode", data.name, episode.number, reason)
    if episode.status != "completed":
        return [refuse(f"not completed ({episode.status}: {episode.reason})")]
    out = []
    marks = {(m.get("episode"), m.get("event"), m.get("status")) for m in data.mcap_episodes}
    if ((episode.number, "start", None) not in marks
            or (episode.number, "end", "completed") not in marks):
        out.append(refuse("MCAP lacks matching /session/episode start and completed end"))
    for row in data.rows:
        if row.get("type") in FAULT_ROWS and _in_window(row.get("host_monotonic_ns"), episode):
            out.append(refuse(f"{row['type']} inside the episode"))
    ticks = grid(episode.start_ns, episode.end_ns, fps)
    if len(ticks) < MIN_FRAMES:
        out.append(refuse(f"too short: {len(ticks)} frame(s) at {fps} fps"))
    step = 1e9 / fps
    jogs = [j for j in data.jogs if _in_window(j.command_ns, episode)]
    for jog in jogs:
        if jog.lab_target_signed is None or jog.result_ns is None:
            out.append(refuse(f"jog {jog.index} could not be matched to its SDK record"))
            continue
        if any(abs(jog.lab_target_signed.get(m, 10 ** 9) - jog.sdk_target_signed.get(m, 0))
               > TARGET_TOLERANCE_COUNTS for m in jog.deltas):
            out.append(refuse(f"jog {jog.index}: lab and SDK target differ by more than "
                              f"{TARGET_TOLERANCE_COUNTS} counts"))
    # A jog commanded in (t_k - step, t_k] labels tick k; two jogs may not share one.
    slots = [math.ceil((j.command_ns - episode.start_ns) / step) for j in jogs]
    if len(slots) != len(set(slots)):
        out.append(refuse("two jogs commanded within one grid interval"))
    if video:
        out.extend(refuse(r) for r in _video_problems(data, ticks, step, jogs,
                                                      max_frame_gap_s))
    return out


def _video_problems(data, ticks, step, jogs, max_frame_gap_s) -> list[str]:
    if data.camera is None or not data.camera.frames:
        return ["no camera frames for a video episode"]
    problems = []
    for jog in jogs:
        inside = [t for t in ticks if jog.command_ns < t < (jog.result_ns or t)]
        if not inside:
            continue
        if jog.trace_dropped:
            problems.append(f"jog {jog.index}: trace dropped packets during motion")
            continue
        times = [t for t, _ in jog.trace]
        for t in inside:
            if not any(t - step < s <= t for s in times):
                problems.append(f"jog {jog.index}: no state packet within one grid interval "
                                "of a tick during motion")
                break
    stamps = sorted(f["observed_monotonic_ns"] for f in data.camera.frames)
    limit = max_frame_gap_s * 1e9
    for t in ticks:
        index = bisect_right(stamps, t) - 1
        if index < 0 or t - stamps[index] > limit:
            problems.append(f"frame gap: no frame within {max_frame_gap_s} s before a tick")
            return problems
    # Gaps between frames can hide between ticks; check consecutive frames too.
    first = max(bisect_left(stamps, ticks[0]) - 1, 0)
    last = bisect_right(stamps, ticks[-1])
    window = stamps[first:last + 1]
    if any(b - a > limit for a, b in zip(window, window[1:])):
        problems.append(f"frame gap: more than {max_frame_gap_s} s between two frames")
    return problems


def exportable(data, *, fps, max_frame_gap_s, video):
    """Episodes of one session that pass every rule, and every refusal."""
    refusals = check_session(data, video=video)
    if refusals:
        return [], refusals
    kept = []
    for episode in data.episodes:
        problems = check_episode(data, episode, fps=fps, max_frame_gap_s=max_frame_gap_s,
                                 video=video)
        if problems:
            refusals.extend(problems)
        else:
            kept.append(episode)
    return kept, refusals
