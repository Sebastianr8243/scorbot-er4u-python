"""How far the arm lagged its streamed target, read from a controller event log. Never opens USB.

    python scripts/follow_report.py RUN.controller.jsonl [MORE.jsonl ...] [--json]

Reads each stream in the log (``stream_start`` to its ``stream_trace`` of every step) and, per speed setting and
per motor that moved, prints: the worst lag (commanded minus measured counts on send steps, or the stream's own
lead, whichever is larger) in counts and degrees against the stream's lead limit; the peak measured speed over any
0.25 s against the vendor MaxSpeed at that speed; the overshoot past the last command; how long the arm took to
settle within 5 counts of it after the command stopped moving; and the stream fault, if any.

Lag is sampled once per 24 ms step and includes the one step the command is planned ahead of the reply, so even a
perfect follower shows some: run the same move with ``--simulate`` and compare. Degrees use the vendor prior
(``preview_jog``), uncalibrated. A lead for the lab day sheet, never a safety verdict.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:      # run as a script
    sys.path.insert(0, str(ROOT))

from scorbot.streaming import MOTORS, VENDOR_MAX_SPEED_COUNTS_S  # noqa: E402

try:
    from scripts.vendor_check import read_rows
except ImportError:  # Run as a script: scripts/ itself is on sys.path.
    from vendor_check import read_rows

MOVED_COUNTS = 5        # ours: a motor whose command spans more than this moved (bench_stream's ARRIVED_COUNTS)
SETTLED_COUNTS = 5      # ours: within this of the last command is settled (bench_stream's ARRIVED_COUNTS)
WINDOW_S = 0.25         # ours: speed is measured over at least this long, so one noisy reply is not a peak
STREAM_ENDS = ("stream_complete", "stream_stopped")


def prior_per_degree() -> dict[str, float]:
    """Counts per degree per motor, from the same offline plan the stream uses. Opens nothing."""
    from scorbot import SimulatedScorbot
    with tempfile.TemporaryDirectory() as tmp:
        robot = SimulatedScorbot(log_path=Path(tmp) / "unused.jsonl", robot_id="follow-report")
        return {m: abs(robot.preview_jog(m, 1.0)["motor_count_deltas"][m]) for m in MOTORS}


def _streams(rows):
    """Each stream as {start, speeds [(ns, fraction)], steps, fault}."""
    found, current = [], None
    for row in rows:
        event = row.get("event")
        if event == "stream_start":
            current = {"start": row, "speeds": [(row.get("host_monotonic_ns", 0), row["speed_fraction"])],
                       "steps": [], "fault": None}
            found.append(current)
        elif current is None:
            continue
        elif event == "stream_speed":
            current["speeds"].append((row["host_monotonic_ns"], row["speed_fraction"]))
        elif event == "stream_trace":
            current["steps"] = row.get("steps") or []
        elif event == "stream_fault":
            current["fault"] = current["fault"] or row.get("error") or "fault"
        elif event in STREAM_ENDS:
            current = None
    return found


def _peak_speed(times, values):
    peak, j = 0.0, 0
    for i, t0 in enumerate(times):
        j = max(j, i + 1)
        while j < len(times) and times[j] - t0 < WINDOW_S:
            j += 1
        if j < len(times):
            peak = max(peak, abs(values[j] - values[i]) / (times[j] - t0))
    return peak


def _settle_s(times, commanded, measured):
    final = commanded[-1]
    stop = len(commanded) - 1
    while stop > 0 and commanded[stop - 1] == final:
        stop -= 1
    settled = len(measured)
    while settled > stop and abs(measured[settled - 1] - final) <= SETTLED_COUNTS:
        settled -= 1
    return None if settled == len(measured) else times[settled] - times[stop]


def _segment(steps, motor, fraction, per_degree, lead_limit_deg):
    times = [s["host_monotonic_ns"] / 1e9 for s in steps]
    commanded = [s["commanded"][motor] for s in steps]
    measured = [s["measured"][motor] for s in steps]
    if max(commanded) - min(commanded) <= MOVED_COUNTS:
        return None
    lag = max(max(abs(s["lead"][motor]) for s in steps),
              max((abs(s["commanded"][motor] - s["measured"][motor]) for s in steps if s["action"] == "send"),
                  default=0))
    direction = (commanded[-1] > measured[0]) - (commanded[-1] < measured[0])
    overshoot = max(0, max((m - commanded[-1]) * direction for m in measured)) if direction else 0
    peak, limit = _peak_speed(times, measured), VENDOR_MAX_SPEED_COUNTS_S * fraction
    scale = per_degree[motor]
    return {"motor": motor, "speed_pct": round(fraction * 100, 2), "steps": len(steps),
            "worst_lag_counts": lag, "worst_lag_deg": lag / scale, "lead_limit_deg": lead_limit_deg,
            "peak_counts_s": peak, "limit_counts_s": limit, "peak_pct_of_limit": 100 * peak / limit,
            "peak_deg_s": peak / scale, "overshoot_counts": overshoot, "overshoot_deg": overshoot / scale,
            "settle_s": _settle_s(times, commanded, measured)}


def analyse(rows, per_degree):
    """One dict per stream, speed setting and motor that moved, in log order. Pure."""
    results = []
    for stream in _streams(rows):
        start, steps = stream["start"], stream["steps"]
        bounds = stream["speeds"] + [(float("inf"), None)]
        segments = []
        for (begin, fraction), (end, _) in zip(bounds, bounds[1:]):
            part = [s for s in steps if begin <= s["host_monotonic_ns"] < end]
            if part:
                segments += [_segment(part, m, fraction, per_degree, start.get("lead_limit_deg")) for m in MOTORS]
        segments = [r for r in segments if r is not None]
        for r in segments:
            r.update(simulated=bool(start.get("simulated")), fault=None)
        if segments:
            segments[-1]["fault"] = stream["fault"]
        results += segments
    return results


def _line(r):
    settle = "never" if r["settle_s"] is None else f"{r['settle_s']:.2f} s"
    return (f"{'SIMULATED ' if r['simulated'] else ''}{r['motor']:<8} {r['speed_pct']:>5g}%  "
            f"lag {r['worst_lag_counts']:>4} counts = {r['worst_lag_deg']:.2f} deg (lead limit {r['lead_limit_deg']} deg)  "
            f"peak {r['peak_counts_s']:>5.0f} counts/s = {r['peak_pct_of_limit']:.0f}% of {r['limit_counts_s']:.0f}  "
            f"overshoot {r['overshoot_counts']} counts  settle {settle}  "
            f"fault {r['fault'] or 'none'}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("logs", nargs="+", type=Path, help="controller event JSONL (<run>.controller.jsonl)")
    parser.add_argument("--json", action="store_true", help="print one JSON object per result")
    args = parser.parse_args(argv)
    per_degree, code = prior_per_degree(), 0
    for path in args.logs:
        try:
            results = analyse(read_rows(path), per_degree)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f"{path}: cannot read: {exc}")
            code = 2
            continue
        if not results:
            print(f"{path}: no stream with a moving motor")
            code = 2
            continue
        print(f"{path}:")
        for r in results:
            print(json.dumps({"file": str(path), **r}) if args.json else "  " + _line(r))
    print("Lag includes the one step the command runs ahead; compare with a --simulate run. "
          "Degrees are the vendor prior, uncalibrated. Not a safety verdict.")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
