"""A self-contained HTML report of an export plan: look at episodes before exporting.

Per kept episode: task, length, camera thumbnails and one plot per arm joint
of position (observation.state) and target (action) over time. Per refused
episode: the reasons. No scripts, fonts, images or links from outside the
file; every text from the logs is HTML-escaped; images are bounded in count,
size and total bytes. Needs no lerobot; thumbnails need OpenCV.
"""

from __future__ import annotations

import base64
import html

from .load import CAMERA_ID, MOTORS
from .sidecar import STEP_JOINTS, UNITS

MAX_EPISODES = 200
MAX_THUMBS = 8
THUMB_WIDTH = 160
MAX_IMAGE_BYTES = 8 * 2 ** 20

_STYLE = """
:root { --bg: #ffffff; --fg: #1d2330; --muted: #5d6675; --line: #d9dde4;
        --state: #2463eb; --action: #d9480f; --bad: #b42318; --banner: #fff4d6; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #14171c; --fg: #e6e9ee; --muted: #a0a8b5; --line: #2c323b;
          --state: #6ea0ff; --action: #ff8a4c; --bad: #ff6b5e; --banner: #3a3216; } }
body { background: var(--bg); color: var(--fg); margin: 0 auto; max-width: 980px;
       padding: 16px; font: 15px/1.45 system-ui, sans-serif; }
h1 { font-size: 22px; } h2 { font-size: 17px; margin-top: 28px; }
.muted { color: var(--muted); } .bad { color: var(--bad); }
.banner { background: var(--banner); padding: 8px 12px; border-radius: 6px; }
.episode { border-top: 1px solid var(--line); padding-top: 8px; }
.thumbs { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0; }
.thumbs img { width: 120px; height: auto; border-radius: 4px; }
svg { width: 100%; max-width: 940px; height: 120px; display: block; }
svg .state { stroke: var(--state); } svg .action { stroke: var(--action); }
svg .axis { stroke: var(--line); } svg text { fill: var(--muted); font-size: 11px; }
.key span { display: inline-block; width: 12px; height: 3px; margin: 0 4px 3px 12px; }
"""


def _plot(joint: str, state, action) -> str:
    """Inline SVG: position and target over time for one joint."""
    values = [*state, *action]
    low, high = min(values), max(values)
    if high - low < 1:
        low, high = low - 1, high + 1
    width, height, pad = 940, 120, 18
    count = max(len(state) - 1, 1)

    def points(series):
        return " ".join(f"{pad + (width - 2 * pad) * i / count:.1f},"
                        f"{height - pad - (height - 2 * pad) * (v - low) / (high - low):.1f}"
                        for i, v in enumerate(series))
    return (f'<svg viewBox="0 0 {width} {height}" role="img" '
            f'aria-label="{joint} position and target">'
            f'<line class="axis" x1="{pad}" y1="{height - pad}" x2="{width - pad}" '
            f'y2="{height - pad}"/>'
            f'<polyline class="state" fill="none" stroke-width="3" '
            f'points="{points(state)}"/>'
            # Target drawn dashed on top: on the simulator it often equals the position.
            f'<polyline class="action" fill="none" stroke-width="1.5" stroke-dasharray="5 4" '
            f'points="{points(action)}"/>'
            f'<text x="{pad}" y="12">{html.escape(joint)}: {high:g} counts</text>'
            f'<text x="{pad}" y="{height - 4}">{low:g}</text></svg>')


def _thumbnails(data, frame_seq, budget: dict) -> str:
    try:
        import cv2
        import numpy as np
    except ImportError:
        return '<p class="muted">No thumbnails: OpenCV is not installed (camera extra).</p>'
    from ..camera.stream import iter_frames
    if not frame_seq:
        return ""
    picks = sorted({frame_seq[round(i * (len(frame_seq) - 1) / max(MAX_THUMBS - 1, 1))]
                    for i in range(min(MAX_THUMBS, len(frame_seq)))})
    images = []
    for seq, frame in enumerate(iter_frames(data.mcap_folder, CAMERA_ID)):
        if seq > picks[-1]:
            break
        if seq not in picks:
            continue
        image = cv2.imdecode(np.frombuffer(frame.data, np.uint8), cv2.IMREAD_COLOR)
        scale = THUMB_WIDTH / max(image.shape[1], 1)
        if scale < 1:
            image = cv2.resize(image, (THUMB_WIDTH, max(1, round(image.shape[0] * scale))))
        ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        data_bytes = encoded.tobytes() if ok else b""
        if budget["left"] < len(data_bytes):
            images.append('<span class="muted">image limit reached; thumbnails stop</span>')
            break
        budget["left"] -= len(data_bytes)
        images.append(f'<img alt="frame {seq}" src="data:image/jpeg;base64,'
                      f'{base64.b64encode(data_bytes).decode("ascii")}">')
    return f'<div class="thumbs">{"".join(images)}</div>'


def render(plan, *, fps: int, video: bool, max_image_bytes: int = MAX_IMAGE_BYTES) -> str:
    sources = sorted({str(s.data_source) for s in plan.sessions})
    simulated = any(source != "real" for source in sources)
    parts = [f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
             f"<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
             f"<title>Episode preview</title><style>{_STYLE}</style></head><body>",
             "<h1>Episode preview</h1>"]
    if simulated:
        parts.append(f'<p class="banner">SIMULATED data ({html.escape(", ".join(sources))}): '
                     "a rehearsal, never lab evidence.</p>")
    parts.append(f'<p class="muted">{len(plan.episodes)} kept, {len(plan.refusals)} refused '
                 f"or skipped. {fps} fps. Units: {html.escape(UNITS)}. Image latency "
                 "unmeasured. Inputs: "
                 + html.escape(", ".join(str(s.jsonl_path) for s in plan.sessions)) + "</p>")
    parts.append('<p class="key muted">Lines: <span style="background: var(--state)"></span>'
                  'position (observation.state) <span style="background: var(--action)">'
                  "</span>target (action), dashed</p>")
    budget = {"left": max_image_bytes}
    for data, frames in plan.episodes[:MAX_EPISODES]:
        seconds = (frames.times_ns[-1] - frames.times_ns[0]) / 1e9
        parts.append(f'<section class="episode"><h2>{html.escape(data.name)} episode '
                     f"{frames.episode}: {html.escape(frames.task)}</h2>"
                     f'<p class="muted">{len(frames.times_ns)} frames, {seconds:.1f} s</p>')
        if video and frames.frame_seq:
            parts.append(_thumbnails(data, frames.frame_seq, budget))
        for joint in STEP_JOINTS:
            index = MOTORS.index(joint)
            parts.append(_plot(joint, [row[index] for row in frames.state],
                               [row[index] for row in frames.action]))
        parts.append("</section>")
    if len(plan.episodes) > MAX_EPISODES:
        parts.append(f'<p class="muted">{len(plan.episodes) - MAX_EPISODES} more episodes '
                     "not shown.</p>")
    if plan.refusals:
        parts.append("<h2>Refused or skipped</h2><ul>")
        for refusal in plan.refusals:
            where = refusal.session + (f" episode {refusal.episode}" if refusal.episode else "")
            parts.append(f'<li><span class="bad">{html.escape(refusal.scope)}</span> '
                         f"{html.escape(where)}: {html.escape(refusal.reason)}</li>")
        parts.append("</ul>")
    parts.append("</body></html>")
    return "".join(parts)
