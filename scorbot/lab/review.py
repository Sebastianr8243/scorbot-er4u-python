"""Review a guided lab-session JSONL log. Pure: no printing, no hardware.

A clean review means the log is complete and self-consistent. It never says
the motion was safe or accurate; that is the operator's physical review.
"""

from __future__ import annotations

from ..state import JOINTS

LAB_LED_STEPS = ("after_connect", "after_enable")
UNJOGGED_TOLERANCE = 20   # counts a non-jogged motor may drift before it is flagged


def review_session_rows(rows: list[dict]) -> dict:
    session = next((r for r in rows if r.get("type") == "session"), None)
    problems = [] if session else ["missing session row"]
    by_n: dict[int, dict[str, dict]] = {}
    for row in rows:
        if isinstance(row.get("n"), int):
            by_n.setdefault(row["n"], {})[row.get("type")] = row
        if row.get("type") == "session_failed":
            problems.append(f"session failed: {row.get('error')}")
        elif row.get("type") == "led_mismatch":
            problems.append(f"LED mismatch {row.get('step')}: {row.get('led')} reported "
                            f"{row.get('observed')}, software expected {row.get('expected')}")
        elif row.get("type") == "led_gate_failed":
            problems.append(f"LED gate failed: {row.get('reason')}")
        elif row.get("type") == "plan_complete":
            differences = [d for d in (row.get("count_differences") or {}).values()
                           if isinstance(d, int)]
            if row.get("answer") != "yes" or any(abs(d) > UNJOGGED_TOLERANCE
                                                 for d in differences):
                problems.append(f"plan to {row.get('name')}: arrival answer "
                                f"{row.get('answer')}, count differences "
                                f"{row.get('count_differences')}")
        elif row.get("type") == "counts_drift":
            problems.append(f"counts drift before a step: {row.get('differences')}")
    seen_leds = {r.get("step") for r in rows if r.get("type") == "led_observation"}
    problems += [f"LED observation missing {step}" for step in LAB_LED_STEPS
                 if step not in seen_leds]
    jogs = []
    for n in sorted(by_n):
        rows_n = by_n[n]
        confirmed = rows_n.get("jog_confirmed")
        if confirmed is None:
            failed = rows_n.get("jog_failed")
            if failed:
                problems.append(f"jog {n}: failed before motion: {failed.get('error')}")
            continue
        if "after_jog" not in rows_n:
            reason = rows_n.get("jog_failed", {}).get("error")
            problems.append(f"jog {n}: failed: {reason}" if reason
                            else f"jog {n}: started but has no after_jog")
        result = rows_n.get("jog_result", {})
        planned = result.get("planned") or {}
        measured = result.get("measured") or {}
        for motor in JOINTS:
            value = measured.get(motor)
            if motor not in measured:
                continue
            if value is None:
                problems.append(f"jog {n}: {motor} count change is ambiguous")
            elif motor in planned:
                if planned[motor] * value < 0:
                    problems.append(f"jog {n}: {motor} moved opposite to the plan")
            elif abs(value) > UNJOGGED_TOLERANCE:
                problems.append(f"jog {n}: {motor} moved {value} counts but was not jogged")
        observation = rows_n.get("jog_observation", {})
        jogs.append({"n": n, "move": confirmed.get("move"), "how": confirmed.get("how"),
                     "planned": planned, "measured": measured,
                     "direction": observation.get("direction"),
                     "other_joint_moved": observation.get("other_joint_moved")})
    declined = next((r.get("text") for r in rows if r.get("type") == "operator_declined"), None)
    return {"data_source": (session or {}).get("data_source", "unknown"),
            "robot_id": (session or {}).get("robot_id"),
            "jogs": jogs, "problems": sorted(set(problems)), "declined": declined}


def format_session_review(report: dict) -> str:
    source = str(report["data_source"]).upper()
    lines = [f"{source} lab session, robot {report['robot_id']}"]
    if source != "REAL":
        lines.append(f"{source} DATA: rehearsal logs, not evidence from the physical arm.")
    lines.append(f"{'#':>3}  {'move':<12} {'how':<7} {'planned':>9} {'measured':>9} "
                 f"{'observed':<9} other")
    for jog in report["jogs"]:
        motor = next(iter(jog["planned"]), None)
        planned = jog["planned"].get(motor, "-") if motor else "-"
        measured = jog["measured"].get(motor, "-") if motor else "-"
        lines.append(f"{jog['n']:>3}  {jog['move'] or '-':<12} {jog['how'] or '-':<7} "
                     f"{planned!s:>9} {measured!s:>9} {jog['direction'] or '-':<9} "
                     f"{jog['other_joint_moved'] or '-'}")
    if report["declined"]:
        lines.append(f"Declined: {report['declined']}")
    lines += [f"  - {problem}" for problem in report["problems"]]
    lines.append(f"LOG CHECK: {len(report['problems'])} problems (not a safety verdict)")
    return "\n".join(lines)
