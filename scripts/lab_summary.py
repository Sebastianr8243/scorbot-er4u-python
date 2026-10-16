"""One page from the lab-day logs: did each step run, and what did the arm do.

    python scripts/lab_summary.py logs/idle-01.jsonl logs/base-01.jsonl ... [--phone base=1.2]

Reads bench_joint, bench_stream, policy_loop and record_raw_state records (it tells them apart itself)
and reuses the reviewers in review_lab_logs.py. Per jog it says which way the counts went for a
positive request, how many counts came back per commanded degree against the vendor prior, and
(with --phone joint=degrees, the angle change you read off the phone level) counts per measured degree.
It never opens USB. A line here is a lead for you to check against what you saw, never a safety or
calibration verdict. Exit 1 if any file has a problem.
"""

import argparse
import glob
from pathlib import Path
import sys

try:
    from scripts import review_lab_logs as review
    from scripts.vendor_check import read_rows
except ImportError:  # Run as a script: scripts/ itself is on sys.path.
    import review_lab_logs as review
    from vendor_check import read_rows

from scorbot.nominal import VENDOR_COUNTS_PER_DEGREE

MOVED_COUNTS = 5            # a joint that changed by fewer counts than this did not move
SCALE_WARN_RATIO = 0.5      # observed / prior outside [0.5, 2] gets a flag


def kind_of(rows):
    session = next((r for r in rows if r.get("type") == "session"), {})
    types = {r.get("type") for r in rows}
    if session.get("procedure") == "stream_trial":
        return "stream"
    if session.get("procedure") == "policy_run":
        return "policy"
    if "motion_preview" in types:
        return "bench"
    if "sample" in types:
        return "idle"
    return "unknown"


def bench_lines(report, phone):
    joint, delta = report["joint"], report["requested_delta_deg"]
    moved = report["count_deltas"].get(joint)
    if moved is None or not delta:
        return [f"  {joint}: no before/after counts in the log"]
    observed = moved["observed"]
    if abs(observed) < MOVED_COUNTS:
        lines = [f"  {joint}: asked {delta:+g} deg, counts changed by {observed:+d}: it did not move"]
    else:
        way = "UP" if observed * delta > 0 else "DOWN"
        per_deg = abs(observed) / abs(delta)
        prior = VENDOR_COUNTS_PER_DEGREE[joint].value
        flag = "" if SCALE_WARN_RATIO <= per_deg / prior <= 1 / SCALE_WARN_RATIO else "  <-- far from the prior"
        lines = [f"  {joint}: asked {delta:+g} deg, counts went {way} by {abs(observed)} "
                 f"(+ request = counts {way})",
                 f"    {per_deg:.1f} counts per commanded degree; vendor prior {prior:.1f}{flag}"]
        if joint in phone and phone[joint]:
            lines.append(f"    {abs(observed) / abs(phone[joint]):.1f} counts per degree you measured "
                         f"({phone[joint]:+g} deg on the phone)")
    others = {m: d["observed"] for m, d in report["count_deltas"].items()
              if m != joint and abs(d["observed"]) >= MOVED_COUNTS}
    if others:
        lines.append("    other joints also changed: " + ", ".join(f"{m} {c:+d}" for m, c in others.items()))
    return lines


def policy_report(path, rows):
    steps = [r for r in rows if r.get("type") == "policy_step"]
    after = next((r for r in rows if r.get("type") == "after_policy"), None)
    failed = next((r for r in rows if r.get("type") == "policy_failed"), None)
    problems = []
    result = (after or failed or {}).get("result") or {}
    if after is None:
        problems.append("run never reached after_policy" + (" (policy_failed)" if failed else ""))
    problems += [str(p) for p in result.get("problems", [])]
    top = {m: max((abs(s["measured_deg"].get(m, 0.0)) for s in steps), default=0.0)
           for m in ("base", "shoulder", "elbow")}
    return {"file": str(path), "kind": "policy", "steps": len(steps),
            "clamped": sum(1 for s in steps if s.get("clamped")), "max_deg": top,
            "policy_errors": result.get("policy_errors", 0), "problems": sorted(set(problems))}


def summarize(path, phone):
    """(lines, problems) for one file."""
    rows = read_rows(path)
    kind = kind_of(rows)
    name = Path(path).name
    if kind == "idle":
        report = review.review_idle(path)
        rest = report["count_range_at_rest"]
        lines = [f"{name}  IDLE  {report['samples']} samples; counts moved at rest by up to "
                 f"{max(rest.values(), default=0)} (should be about 0)"]
    elif kind == "bench":
        report = review.review_bench(path)
        lines = [f"{name}  JOG {report['joint']}"] + bench_lines(report, phone)
    elif kind == "stream":
        report = review.review_stream(path)
        res = report["result"]
        lines = [f"{name}  STREAM {report['motor']} {report['requested_delta_deg']:+g} deg, out and back: "
                 f"{'passed' if res.get('passed') else 'DID NOT PASS'}"]
    elif kind == "policy":
        report = policy_report(path, rows)
        lines = [f"{name}  POLICY {report['steps']} steps, {report['clamped']} clamped, "
                 f"{report['policy_errors']} policy errors; furthest from home "
                 + ", ".join(f"{m} {d:.2f} deg" for m, d in report["max_deg"].items())]
    else:
        return [f"{name}  not a record this tool knows"], ["unknown record type"]
    if report.get("operator_observation"):
        seen = report["operator_observation"].get("direction_and_displacement")
        if seen:
            lines.append(f"  you saw: {seen}")
    for problem in report["problems"]:
        lines.append(f"  PROBLEM: {problem}")
    return lines, report["problems"]


def expand(paths):
    """PowerShell passes logs\*.jsonl through unexpanded; expand it, and leave out the .controller.jsonl event logs."""
    out = []
    for path in paths:
        found = sorted(glob.glob(str(path))) if "*" in str(path) else [str(path)]
        out += [Path(f) for f in found if not f.endswith(".controller.jsonl") or "*" not in str(path)]
    return out


def parse_phone(items):
    out = {}
    for item in items:
        joint, _, value = item.partition("=")
        if joint not in VENDOR_COUNTS_PER_DEGREE or not value:
            raise SystemExit(f"--phone wants joint=degrees, e.g. base=1.2 (got {item!r})")
        out[joint] = float(value)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("logs", nargs="+", type=Path)
    parser.add_argument("--phone", action="append", default=[], metavar="JOINT=DEG",
                        help="angle change you measured for that joint's jog")
    args = parser.parse_args(argv)
    phone = parse_phone(args.phone)
    bad = 0
    logs = expand(args.logs)
    if not logs:
        raise SystemExit("no log files matched")
    for path in logs:
        lines, problems = summarize(path, phone)
        print("\n".join(lines))
        bad += bool(problems)
    print("\nScales are the vendor prior, not measured on this arm. Check every line against what you saw; "
          "this is not a safety verdict.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
