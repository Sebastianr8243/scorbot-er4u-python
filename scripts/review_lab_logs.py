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


IDLE_LED_STEPS = ("after_connect", "after_exit")
BENCH_LED_STEPS = ("after_connect", "after_enable", "after_jog", "after_disable")
STREAM_LED_STEPS = ("after_connect", "after_enable", "after_stream", "after_disable")


def _led_review(rows, session, steps):
    """Operator LED answers, plus problems for mismatches and missing answers.

    Logs written before the LED prompts existed have no ``led_prompts`` flag in
    their session row; for them a missing observation is not a problem.
    """
    observations = [{key: row.get(key) for key in ("step", "motors_led", "power_led")}
                    for row in rows if row.get("type") == "led_observation"]
    problems = [f"LED mismatch {row.get('step')}: {row.get('led')} LED reported "
                f"{row.get('observed')}, software expected {row.get('expected')}"
                for row in rows if row.get("type") == "led_mismatch"]
    if (session or {}).get("led_prompts") is True:
        seen = {row["step"] for row in observations if isinstance(row["step"], str)}
        problems += [f"led observation missing {step}" for step in steps if step not in seen]
        valid_answers = {"motors_led": ("lit", "off"),
                         "power_led": ("green", "orange", "flashing")}
        for row in observations:
            if row["step"] not in steps:
                continue
            for led, valid in valid_answers.items():
                if row[led] not in valid:
                    problems.append(f"LED observation {row['step']}: {led} is unsure or invalid")
    return observations, problems


def review_idle(path):
    rows = read_rows(path)
    session = next((row for row in rows if row.get("type") == "session"), None)
    samples = [row["state"] for row in rows
               if row.get("type") == "sample" and isinstance(row.get("state"), dict)]
    problems = []
    if session is None:
        problems.append("missing session metadata")
    if any(row.get("type") == "session_failed" for row in rows):
        problems.append("session reported failure")
    if any(row.get("type") == "recorder_failed" for row in rows):
        problems.append("MCAP recorder reported failure")
    if len(samples) < 2:
        problems.append("need at least two idle samples")
    problems += _packet_checks(samples)
    if any(state.get("enabled") is not False for state in samples):
        problems.append("idle sample does not report motors disabled")
    ranges = {}
    for joint in JOINTS:
        values = [state.get("encoder_counts", {}).get(joint) for state in samples]
        if values and all(type(value) is int for value in values):
            # Measure against the first sample with the wrap-aware delta: signed
            # counts jump by 65536 when a joint at rest jitters across 0/65535.
            try:
                offsets = [signed_count_delta(value, values[0]) for value in values]
            except ValueError:
                problems.append(f"ambiguous {joint} count change at rest")
                continue
            ranges[joint] = max(offsets) - min(offsets)
    leds, led_problems = _led_review(rows, session, IDLE_LED_STEPS)
    problems += led_problems
    return {
        "file": str(path), "kind": "idle", "robot_id": session.get("robot_id") if session else None,
        "data_source": (session or {}).get("data_source", "real"),
        "motion_source_sha256": session.get("motion_source_sha256") if session else None,
        "samples": len(samples), "count_range_at_rest": ranges,
        "led_observations": leds,
        "problems": sorted(set(problems)),
        "physical_review_required": True,
    }


def _count_deltas(events, before_kind, after_kind, planned):
    """Per-joint planned and observed count change between two recorded states."""
    before = events.get(before_kind, {}).get("state", {}).get("encoder_counts", {})
    after = events.get(after_kind, {}).get("state", {}).get("encoder_counts", {})
    deltas, problems = {}, []
    for joint in JOINTS:
        if joint not in before or joint not in after:
            problems.append(f"missing {joint} before/after count")
            continue
        try:
            observed = signed_count_delta(after[joint], before[joint])
        except ValueError:
            problems.append(f"ambiguous {joint} count difference")
            continue
        predicted = planned.get(joint, 0)
        deltas[joint] = {"planned": predicted, "observed": observed,
                         "difference": observed - predicted}
    return deltas, problems


def review_stream(path):
    """A stream trial record (examples/bench_stream.py): one motor out and back."""
    rows = read_rows(path)
    events = {row.get("type"): row for row in rows}
    required = ("session", "connected", "home_complete", "home_observation",
                "stream_plan", "before_stream", "after_stream",
                "operator_observation", "disabled")
    problems = [f"missing {kind}" for kind in required if kind not in events]
    for kind, text in (("session_failed", "session reported failure"),
                       ("stream_failed", "stream ended on a fault or an interrupt"),
                       ("recorder_failed", "MCAP recorder reported failure")):
        if kind in events:
            problems.append(text)
    declined = events.get("operator_declined", {}).get("text")
    states = [events[kind]["state"] for kind in
              ("connected", "home_complete", "before_stream", "after_stream", "disabled")
              if isinstance(events.get(kind, {}).get("state"), dict)]
    problems += _packet_checks(states)
    if events.get("home_observation", {}).get("text") in (None, "", "not recorded"):
        problems.append("home observation missing")
    observation = events.get("operator_observation", {})
    for field in ("direction_and_displacement", "other_motion", "controller_indicators", "issue"):
        if observation.get(field) in (None, "", "not recorded"):
            problems.append(f"operator {field} missing")
    result = (events.get("after_stream") or events.get("stream_failed") or {}).get("result") or {}
    if "after_stream" in events and not result.get("passed"):
        problems += [f"stream trial failed: {problem}"
                     for problem in result.get("problems") or ["no verdict recorded"]]
    # Out and back: every motor is planned to end where it started.
    deltas, delta_problems = _count_deltas(events, "before_stream", "after_stream", {})
    if "before_stream" in events and "after_stream" in events:
        problems += delta_problems
    session = events.get("session", {})
    leds, led_problems = _led_review(rows, session, STREAM_LED_STEPS)
    problems += led_problems
    plan = events.get("stream_plan", {})
    return {
        "file": str(path), "kind": "stream", "robot_id": session.get("robot_id"),
        "data_source": session.get("data_source", "real"),
        "motion_source_sha256": session.get("motion_source_sha256"),
        "motor": session.get("motor"), "requested_delta_deg": session.get("requested_delta_deg"),
        "target_counts_from_home": plan.get("target_counts_from_home"),
        "result": result, "count_deltas": deltas,
        "home_observation": events.get("home_observation"),
        "operator_observation": observation, "led_observations": leds,
        "operator_declined": declined,
        "problems": sorted(set(problems)),
        "physical_review_required": True,
    }


def review_bench(path):
    rows = read_rows(path)
    events = {row.get("type"): row for row in rows}
    if events.get("session", {}).get("procedure") == "stream_trial":
        return review_stream(path)    # a stream record given as --bench is still reviewed right
    required = ("session", "connected", "home_complete", "home_observation",
                "motion_preview", "before_jog", "after_jog",
                "operator_observation", "disabled")
    problems = [f"missing {kind}" for kind in required if kind not in events]
    if "session_failed" in events:
        problems.append("session reported failure")
    if "recorder_failed" in events:
        problems.append("MCAP recorder reported failure")
    declined = events.get("operator_declined", {}).get("text")
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
    deltas, delta_problems = _count_deltas(events, "before_jog", "after_jog",
                                           plan.get("motor_count_deltas", {}))
    problems += delta_problems
    session = events.get("session", {})
    leds, led_problems = _led_review(rows, session, BENCH_LED_STEPS)
    problems += led_problems
    return {
        "file": str(path), "kind": "bench", "robot_id": session.get("robot_id"),
        "data_source": session.get("data_source", "real"),
        "motion_source_sha256": session.get("motion_source_sha256"),
        "joint": session.get("joint"), "requested_delta_deg": session.get("requested_delta_deg"),
        "stop_after_ms": session.get("stop_after_ms"),
        "stop_trial_outcome": events.get("after_jog", {}).get("stop_trial_outcome"),
        "stopped_on_request": bool(events.get("after_jog", {}).get("stopped_on_request")),
        "count_deltas": deltas, "home_observation": events.get("home_observation"),
        "operator_observation": observation, "led_observations": leds,
        "operator_declined": declined,
        "problems": sorted(set(problems)),
        "physical_review_required": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--idle", type=Path)
    parser.add_argument("--bench", type=Path, action="append", default=[])
    parser.add_argument("--stream", type=Path, action="append", default=[],
                        help="Stream trial record (examples/bench_stream.py)")
    parser.add_argument("--session", type=Path,
                        help="Guided lab-session log (python -m scorbot.lab)")
    parser.add_argument("--json", action="store_true", help="With --session: print JSON")
    args = parser.parse_args()
    if args.session is not None:
        from scorbot.lab.review import format_session_review, review_session_rows
        report = review_session_rows(read_rows(args.session))
        print(json.dumps(report, indent=2) if args.json else format_session_review(report))
        return 1 if report["problems"] else 0
    if args.idle is None:
        parser.error("--idle is required unless --session is given")
    reports = ([review_idle(args.idle)] + [review_bench(path) for path in args.bench]
               + [review_stream(path) for path in args.stream])
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
    banner = "SIMULATED DATA: rehearsal logs, not evidence from the physical arm."
    if "simulated" in sources:
        print(banner)
    print(json.dumps(reports, indent=2))
    if "simulated" in sources:
        print(banner)  # repeated so it is still on screen after the long report
    return 1 if any(report["problems"] for report in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
