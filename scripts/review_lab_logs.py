"""Review saved first-visit JSONL logs without connecting to the robot.

The report checks log structure and count evidence. It never declares physical
motion safe or calibrated; an operator must review the actual observations.
"""

import argparse
import json
from pathlib import Path

from scorbot.calibration import signed_count_delta
from scorbot.state import JOINTS


def read_rows(path):
    rows = []
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON") from exc
    return rows


def _packet_checks(states):
    problems = []
    indices = [state.get("packet_index") for state in states]
    if any(type(index) is not int for index in indices):
        problems.append("missing packet index")
    elif any(later <= earlier for earlier, later in zip(indices, indices[1:])):
        problems.append("packet indices did not increase")
    for state in states:
        if state.get("fault"):
            problems.append("controller fault in recorded state")
        signs = state.get("encoder_sign_bytes")
        signed = state.get("signed_encoder_counts")
        if not isinstance(signs, dict) or not isinstance(signed, dict):
            problems.append("missing signed encoder fields")
            continue
        if any(signs.get(joint) not in (127, 128) or type(signed.get(joint)) is not int
               for joint in JOINTS):
            problems.append("invalid or missing encoder sign/count field")
    return sorted(set(problems))


def review_idle(path):
    rows = read_rows(path)
    session = next((row for row in rows if row.get("type") == "session"), None)
    samples = [row["state"] for row in rows
               if row.get("type") == "sample" and isinstance(row.get("state"), dict)]
    problems = []
    if session is None:
        problems.append("missing session metadata")
    if len(samples) < 2:
        problems.append("need at least two idle samples")
    problems += _packet_checks(samples)
    if any(state.get("enabled") is not False for state in samples):
        problems.append("idle sample does not report motors disabled")
    ranges = {}
    for joint in JOINTS:
        values = [state.get("signed_encoder_counts", {}).get(joint)
                  for state in samples]
        if values and all(type(value) is int for value in values):
            ranges[joint] = max(values) - min(values)
    return {
        "file": str(path), "kind": "idle", "robot_id": session.get("robot_id") if session else None,
        "data_source": (session or {}).get("data_source", "real"),
        "motion_source_sha256": session.get("motion_source_sha256") if session else None,
        "samples": len(samples), "count_range_at_rest": ranges,
        "problems": sorted(set(problems)),
        "physical_review_required": True,
    }


def review_bench(path):
    rows = read_rows(path)
    events = {row.get("type"): row for row in rows}
    required = ("session", "connected", "home_complete", "home_observation",
                "motion_preview", "before_jog", "after_jog",
                "operator_observation", "disabled")
    problems = [f"missing {kind}" for kind in required if kind not in events]
    if "session_failed" in events:
        problems.append("session reported failure")
    states = [events[kind]["state"] for kind in
              ("connected", "home_complete", "before_jog", "after_jog", "disabled")
              if isinstance(events.get(kind, {}).get("state"), dict)]
    problems += _packet_checks(states)
    if events.get("home_observation", {}).get("text") in (None, "", "not recorded"):
        problems.append("home observation missing")
    observation = events.get("operator_observation", {})
    for field in ("direction_and_displacement", "other_motion", "controller_indicators", "issue"):
        if observation.get(field) in (None, "", "not recorded"):
            problems.append(f"operator {field} missing")
    plan = events.get("motion_preview", {}).get("plan", {})
    expected = plan.get("motor_count_deltas", {})
    before = events.get("before_jog", {}).get("state", {}).get("encoder_counts", {})
    after = events.get("after_jog", {}).get("state", {}).get("encoder_counts", {})
    deltas = {}
    for joint in JOINTS:
        if joint not in before or joint not in after:
            problems.append(f"missing {joint} before/after count")
            continue
        try:
            observed = signed_count_delta(after[joint], before[joint])
        except ValueError:
            problems.append(f"ambiguous {joint} count difference")
            continue
        predicted = expected.get(joint, 0)
        deltas[joint] = {
            "planned": predicted, "observed": observed,
            "difference": observed - predicted,
        }
    session = events.get("session", {})
    return {
        "file": str(path), "kind": "bench", "robot_id": session.get("robot_id"),
        "data_source": session.get("data_source", "real"),
        "motion_source_sha256": session.get("motion_source_sha256"),
        "joint": session.get("joint"), "requested_delta_deg": session.get("requested_delta_deg"),
        "count_deltas": deltas, "home_observation": events.get("home_observation"),
        "operator_observation": observation,
        "problems": sorted(set(problems)),
        "physical_review_required": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--idle", type=Path, required=True)
    parser.add_argument("--bench", type=Path, action="append", default=[])
    args = parser.parse_args()
    reports = [review_idle(args.idle)] + [review_bench(path) for path in args.bench]
    ids = {report["robot_id"] for report in reports}
    fingerprints = {report["motion_source_sha256"] for report in reports}
    if len(ids) != 1 or None in ids:
        for report in reports:
            report["problems"].append("robot ID differs or is missing across files")
    if len(fingerprints) != 1 or None in fingerprints:
        for report in reports:
            report["problems"].append("motion source fingerprint differs or is missing")
    sources = {report["data_source"] for report in reports}
    if len(sources) != 1:
        for report in reports:
            report["problems"].append("real and simulated logs mixed in one review")
    if "simulated" in sources:
        print("SIMULATED DATA: rehearsal logs, not evidence from the physical arm.")
    print(json.dumps(reports, indent=2))
    return 1 if any(report["problems"] for report in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
