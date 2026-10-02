"""Resample one checked episode onto a fixed-rate grid (spec section 3).

state: latest robot reading at or before each tick (MCAP states plus the
decoded USB packets recorded during each jog). action: the target the SDK
sent while a jog is in flight or was commanded since the previous tick, else
the current state. image: the latest camera frame at or before the tick.
All counts are from the session home, uncalibrated.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

from .checks import grid
from .load import MOTORS, rel


@dataclass
class EpisodeFrames:
    session: str
    episode: int
    task: str
    times_ns: list
    state: list
    action: list
    frame_seq: list | None


def _latest(times, values, t):
    index = bisect_right(times, t) - 1
    return values[index] if index >= 0 else None


def resample(data, episode, *, fps: int, video: bool) -> EpisodeFrames:
    home = data.home_raw
    samples = sorted([*data.states, *(s for jog in data.jogs for s in jog.trace)],
                     key=lambda sample: sample[0])
    times = [t for t, _ in samples]
    values = [[float(v) for v in rel(raw, home)] for _, raw in samples]
    step = 1e9 / fps
    jogs = [j for j in data.jogs if j.command_ns is not None]
    targets = {j.index: [float(v + j.deltas.get(m, 0))
                         for m, v in zip(MOTORS, rel(j.sdk_start_raw, home))] for j in jogs}
    ticks = grid(episode.start_ns, episode.end_ns, fps)
    state, action = [], []
    for t in ticks:
        current = _latest(times, values, t)
        state.append(current)
        label = current
        for jog in jogs:
            in_flight = jog.command_ns <= t < (jog.result_ns or t)
            just_commanded = t - step < jog.command_ns <= t
            if in_flight or just_commanded:
                label = targets[jog.index]
        action.append(label)
    frame_seq = None
    if video and data.camera is not None:
        stamps = [f["observed_monotonic_ns"] for f in data.camera.frames]
        seqs = [f["seq"] for f in data.camera.frames]
        frame_seq = [_latest(stamps, seqs, t) for t in ticks]
    return EpisodeFrames(data.name, episode.number, episode.task, ticks, state, action,
                         frame_seq)
