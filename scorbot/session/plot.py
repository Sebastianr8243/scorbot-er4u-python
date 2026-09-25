"""Static PNG plots of recorded sessions (optional: pip install -e ".[plot]").

The design follows the dataviz method:
- one validated series hue and neutral chrome;
- small multiples instead of many colours or a second y-axis;
- solid hairline grids, and text in ink colours, never in the series colour;
- the data source (REAL / SIMULATED / SYNTHETIC) always first in the title.

The CSV export is the table view of the same numbers.
"""

from __future__ import annotations

from pathlib import Path

from ..state import JOINTS

# Dataviz reference palette: series slot 1 (validated in light and dark) and chart chrome.
THEMES = {
    "light": {"surface": "#fcfcfb", "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#898781",
              "grid": "#e1e0d9", "axis": "#c3c2b7", "series": "#2a78d6",
              "span": (0.537, 0.529, 0.506, 0.12)},
    "dark": {"surface": "#1a1a19", "ink": "#ffffff", "ink2": "#c3c2b7", "muted": "#898781",
             "grid": "#2c2c2a", "axis": "#383835", "series": "#3987e5",
             "span": (0.765, 0.761, 0.718, 0.14)},
}
DURATION_NOTE = "recorder-observed duration (includes script overhead), not arm motion time"


class PlotUnavailable(RuntimeError):
    """matplotlib is not installed."""


def require_matplotlib():
    try:
        import matplotlib
        matplotlib.use("Agg")  # headless: no display needed, also on CI
        import matplotlib.pyplot as plt
    except ImportError as error:
        raise PlotUnavailable('Plotting needs matplotlib: pip install -e ".[plot]"') from error
    return plt


def _style(ax, theme):
    ax.set_facecolor(theme["surface"])
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(theme["axis"])
    ax.tick_params(colors=theme["muted"], labelcolor=theme["ink2"], labelsize=8)
    ax.grid(axis="y", color=theme["grid"], linewidth=0.8, linestyle="-")
    ax.set_axisbelow(True)


def _figure(plt, theme, rows, width=8.0, row_height=1.9):
    fig, axes = plt.subplots(rows, 1, figsize=(width, 0.9 + row_height * rows),
                             sharex=True, squeeze=False)
    fig.patch.set_facecolor(theme["surface"])
    return fig, [row[0] for row in axes]


HEADER_INCHES = 0.75  # room kept above the plots for the title and subtitle


def _title(fig, theme, source, text, subtitle=None):
    """Title and subtitle at fixed distances from the top, whatever the figure height."""
    height = fig.get_figheight()
    title = f"{str(source).upper()} — {text}"
    fig.text(0.01, 1 - 0.14 / height, title, ha="left", va="top", fontsize=11,
             color=theme["ink"], fontweight="bold")
    if subtitle:
        fig.text(0.01, 1 - 0.42 / height, subtitle, ha="left", va="top", fontsize=8,
                 color=theme["ink2"])
    return title


def _layout(fig):
    fig.tight_layout(rect=(0, 0, 1, 1 - HEADER_INCHES / fig.get_figheight()))


def _integer_counts(ax):
    """Motor counts are whole numbers: integer ticks, and at least ±1 around zero."""
    from matplotlib.ticker import MaxNLocator
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    low, high = ax.get_ylim()
    ax.set_ylim(min(low, -1), max(high, 1))


def _save(plt, fig, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=120, facecolor=fig.get_facecolor())
    plt.close(fig)
    return path


def plot_counts(session, out_path, theme: str = "light") -> dict:
    """Signed encoder counts over time, one small panel per motor that moved."""
    plt, palette = require_matplotlib(), THEMES[theme]
    events = session.events
    base = events[0]["payload"]["_rec"]["logged_monotonic_ns"] if events else 0
    seconds = lambda event: (event["payload"]["_rec"]["logged_monotonic_ns"] - base) / 1e9  # noqa: E731
    series: dict = {}
    for event in events:
        if event["topic"] != "/robot/state":
            continue
        payload = event["payload"]
        counts = payload.get("signed_encoder_counts") or payload.get("encoder_counts")
        if not isinstance(counts, dict):
            continue
        for motor, value in counts.items():
            if isinstance(value, int):
                series.setdefault(motor, ([], []))
                series[motor][0].append(seconds(event))
                series[motor][1].append(value)
    order = [m for m in JOINTS if m in series] + sorted(set(series) - set(JOINTS))
    panels = [m for m in order if len(set(series[m][1])) > 1]

    results = {e["payload"].get("command_id"): e for e in events
               if e["topic"] == "/robot/command_result"}
    spans = [(seconds(e), seconds(results[e["payload"]["command_id"]]))
             for e in events if e["topic"] == "/robot/command"
             and e["payload"].get("command_id") in results]

    fig, axes = _figure(plt, palette, max(1, len(panels)))
    meta = session.metadata
    title = _title(fig, palette, meta.get("data_source", "unknown"),
                   f"encoder counts, session {meta.get('session_id', session.path)}",
                   "shaded: from each command to its result")
    if not panels:
        axes[0].text(0.5, 0.5, "No motor count changed in this session", ha="center",
                     va="center", color=palette["ink2"], transform=axes[0].transAxes)
    for ax, motor in zip(axes, panels):
        _style(ax, palette)
        for start, end in spans:
            ax.axvspan(start, max(end, start + 1e-3), color=palette["span"], linewidth=0)
        times, values = series[motor]
        ax.plot(times, values, color=palette["series"], linewidth=2, marker="o",
                markersize=4, solid_capstyle="round")
        ax.set_title(motor, loc="left", fontsize=9, color=palette["ink"])
        ax.set_ylabel("counts", fontsize=8, color=palette["ink2"])
    axes[-1].set_xlabel("time since first event (s)", fontsize=8, color=palette["ink2"])
    _layout(fig)
    return {"path": _save(plt, fig, out_path), "panels": panels, "title": title,
            "command_spans": len(spans)}


def _strip(ax, palette, groups, labels, median=False):
    """One dot per value per group; a deterministic spread keeps equal values visible."""
    points = 0
    for x, values in enumerate(groups):
        n = len(values)
        offsets = [((i % 7) - 3) * 0.05 for i in range(n)]
        ax.scatter([x + o for o in offsets], values, s=30, color=palette["series"],
                   edgecolors=palette["surface"], linewidths=1.5, zorder=3)
        points += n
        if median and values:
            middle = sorted(values)[n // 2] if n % 2 else sum(sorted(values)[n // 2 - 1:n // 2 + 1]) / 2
            ax.hlines(middle, x - 0.28, x + 0.28, color=palette["ink"], linewidth=2, zorder=4)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, fontsize=8, color=palette["ink2"])
    ax.set_xlim(-0.6, len(labels) - 0.4)
    return points


def plot_count_error(runs, source, out_path, theme: str = "light") -> dict:
    """Observed minus planned motor counts for every command with a plan, across runs."""
    plt, palette = require_matplotlib(), THEMES[theme]
    by_motor: dict = {}
    commands = 0
    for _session_id, records in runs:
        for record in records:
            if record.count_error:
                commands += 1
            for motor, value in record.count_error.items():
                by_motor.setdefault(motor, []).append(value)
    motors = [m for m in JOINTS if m in by_motor] + sorted(set(by_motor) - set(JOINTS))
    fig, (ax,) = _figure(plt, palette, 1, row_height=3.2)
    _style(ax, palette)
    title = _title(fig, palette, source, "count error per motor (observed − planned)",
                   f"{commands} commands from {len(runs)} run(s); "
                   "0 means the motor moved exactly as planned")
    ax.axhline(0, color=palette["axis"], linewidth=1.2, zorder=2)
    points = _strip(ax, palette, [by_motor[m] for m in motors], motors) if motors else 0
    if not motors:
        ax.text(0.5, 0.5, "No command had a plan plus states before and after it",
                ha="center", va="center", color=palette["ink2"], transform=ax.transAxes)
    ax.set_ylabel("counts", fontsize=8, color=palette["ink2"])
    _integer_counts(ax)
    _layout(fig)
    max_abs = max((abs(v) for values in by_motor.values() for v in values), default=None)
    return {"path": _save(plt, fig, out_path), "points": points, "max_abs": max_abs,
            "title": title}


def plot_durations(runs, source, out_path, theme: str = "light") -> dict:
    """Recorder-observed command durations per command kind, across runs, with medians."""
    plt, palette = require_matplotlib(), THEMES[theme]
    by_kind: dict = {}
    for _session_id, records in runs:
        for record in records:
            if record.recorded_duration_ms is not None:
                by_kind.setdefault(record.kind, []).append(record.recorded_duration_ms)
    kinds = sorted(by_kind)
    fig, (ax,) = _figure(plt, palette, 1, row_height=3.2)
    _style(ax, palette)
    title = _title(fig, palette, source, "command durations by kind",
                   f"{DURATION_NOTE}; bar = median")
    if kinds:
        _strip(ax, palette, [by_kind[k] for k in kinds], kinds, median=True)
        ax.set_ylim(bottom=0)
    else:
        ax.text(0.5, 0.5, "No command has a logged result", ha="center", va="center",
                color=palette["ink2"], transform=ax.transAxes)
    ax.set_ylabel("ms", fontsize=8, color=palette["ink2"])
    _layout(fig)
    return {"path": _save(plt, fig, out_path), "kinds": kinds, "title": title,
            "note": DURATION_NOTE}
