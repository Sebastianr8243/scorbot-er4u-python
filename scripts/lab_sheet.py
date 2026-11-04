"""The lab record sheet (docs/lab/LAB_RECORD_SHEET.md), row by row, from the logs. Run it as
``python scripts/lab_summary.py <logs> --sheet [--phone base=<deg>]``.

Each row prints a value with the record and file it came from, or ``by hand:`` and what to read. Every value is a
lead for the operator to check against what they saw, never a safety or calibration verdict. A file's
``<run>.controller.jsonl`` beside it is read too (stream steps). Never opens USB.
"""

from pathlib import Path

try:
    from scripts import follow_report, lab_summary, review_lab_logs as review
    from scripts.vendor_check import read_rows
except ImportError:  # Run as a script: scripts/ itself is on sys.path.
    import follow_report
    import lab_summary
    import review_lab_logs as review
    from vendor_check import read_rows

from scorbot.calibration import signed_count_delta
from scorbot.state import ERROR_OFFSETS, IMPACT_DETECT_COUNTS, JOINTS, PACKET_MIN_LENGTH, signed_error_word

ARM = review.ARM_JOINTS
NEAR_IMPACT = 15        # the sheet: within about 15 counts of ImpactDetect in a clean move, raise it first
BEFORE_MOTION = ("before_jog", "before_stream", "before_gripper")    # the last state before a move starts

UNKNOWNS = {
    "1": "Parameter folder the lab PC loads",
    "2": "Counts read 0 at the vendor Home pose",
    "3": "Physical pose at zero",
    "4": "Wrist pitch counts per degree",
    "5": "Base counts per degree",
    "6": "Reply count sign and zero match the DLL's",
    "7": "Idle creep with motors on",
    "8": "Gripper stroke",
    "9": "Driver read timeout",
    "10": "Largest following error in a normal move",
    "stream tracking": "Stream lag and overshoot (not on the sheet)",
    "gripper times": "Gripper open and close time (not on the sheet)",
}
ALWAYS_BY_HAND = {
    "1": "CurrentParFolder in ER4CONF.INI beside USBC.dll on the lab PC",
    "3": "photo or protractor at Home",
    "4": "no tool jogs pitch alone (bench_joint takes base, shoulder, elbow; wrist streams go out and back): "
         "jog pitch a known angle, counts moved / degrees",
    "6": "hand-over step X: Stack 1 pose, then the Stack 2 probe; the counts must agree",
    "9": "step T: scripts/read_timeout_check.py prints the time; it writes no log",
}


def _kind_key(row):
    return row.get("type") or row.get("event")


def _source(rows):
    """'simulated', 'real' or None for one file."""
    for row in rows:
        if row.get("data_source") in ("simulated", "real"):
            return row["data_source"]
        if _kind_key(row) == "session" and "data_source" not in row and row.get("procedure") != "policy_run":
            return "real"
    return "simulated" if any(row.get("simulated") is True for row in rows) else None


def _review_problems(path, rows):
    kinds = {_kind_key(row) for row in rows}
    session = next((row for row in rows if row.get("type") == "session"), {})
    if session.get("procedure") == "policy_run":
        return lab_summary.policy_report(path, rows)["problems"]
    if "sample" in kinds:
        return review.review_idle(path)["problems"]
    if session and kinds & {"motion_preview", "stream_plan", "gripper_plan"}:
        return review.review_bench(path)["problems"]
    return []


def load(paths):
    """[(name, rows, controller rows, review problems)] and problems, reading each sibling controller log once."""
    files, problems, seen = [], [], set()
    for path in map(Path, paths):
        if path.resolve() in seen:
            continue
        seen.add(path.resolve())
        try:
            rows = read_rows(path)
        except OSError as exc:
            problems.append(f"{path.name}: cannot read: {exc.strerror or exc}")
            continue
        except ValueError as exc:
            problems.append(f"{path.name}: {str(exc).replace(str(path), path.name)}")
            continue
        controller = []
        sibling = path.with_name(path.name.removesuffix(".jsonl") + ".controller.jsonl")
        if sibling != path and sibling.exists() and sibling.resolve() not in seen:
            seen.add(sibling.resolve())
            try:
                controller = read_rows(sibling)
            except ValueError as exc:
                problems.append(f"{sibling.name}: {str(exc).replace(str(sibling), sibling.name)}")
        flagged = _review_problems(path, rows)
        problems += [f"{path.name}: {p}" for p in flagged]
        files.append((path.name, rows, controller, flagged, sibling.name if controller else None))
    sources = {_source(rows + controller) for _, rows, controller, _, _ in files} - {None}
    if len(sources) > 1:
        problems.append("real and simulated logs mixed in one sheet")
    return files, problems, sources


def _row(number, value=None, source=None, file=None, by_hand=None, flagged=()):
    if value and flagged:
        value += f" (that log has review problems: {'; '.join(flagged)})"
    return {"row": number, "unknown": UNKNOWNS[number], "value": value, "source": source, "file": file,
            "by_hand": None if value else by_hand}


def _error_words(rows):
    """(record kind, joint, raw error word) from motors-on states and from every IN packet a trace kept."""
    for row in rows:
        state = row.get("state")
        if isinstance(state, dict) and state.get("enabled") is True:
            for joint in ARM:
                raw = (state.get("controller_error_counts") or {}).get(joint)
                if type(raw) is int:
                    yield _kind_key(row), joint, raw
        for packet in row.get("packets") or []:
            if not isinstance(packet, dict) or packet.get("direction") != "in":
                continue
            try:
                data = bytes.fromhex(packet.get("hex") or "")
            except ValueError:
                continue
            if len(data) >= PACKET_MIN_LENGTH:
                for joint, offset in zip(JOINTS, ERROR_OFFSETS):
                    if joint in ARM:
                        yield f"{_kind_key(row)} packets", joint, int.from_bytes(data[offset:offset + 2], "little")


def row_2(files):
    best = None
    for name, rows, _, flagged, _ in files:
        for row in rows:
            if _kind_key(row) == "home_complete" and row.get("method") == "vendor" and "state" in row:
                counts = row["state"].get("signed_encoder_counts") or {}
                if counts:
                    joint = max(counts, key=lambda j: abs(counts[j]))
                    if best is None or abs(counts[joint]) > abs(best[0][joint]):
                        best = (counts, joint, name, flagged)
    if best is None:
        return _row("2", by_hand="no vendor Home in these logs: bring_up log, first state after a vendor Home")
    counts, joint, name, flagged = best
    return _row("2", f"largest {abs(counts[joint])} counts ({joint}) right after Home; "
                     + " ".join(f"{j} {c}" for j, c in counts.items()), "home_complete", name, flagged=flagged)


def row_5(files, phone):
    for name, rows, _, flagged, _ in files:
        session = next((row for row in rows if row.get("type") == "session"), {})
        events = {_kind_key(row): row for row in rows}
        if session.get("joint") != "base" or "before_jog" not in events or "after_jog" not in events:
            continue
        try:
            moved = signed_count_delta(events["after_jog"]["state"]["encoder_counts"]["base"],
                                       events["before_jog"]["state"]["encoder_counts"]["base"])
        except (KeyError, ValueError):
            continue
        if phone.get("base"):
            return _row("5", f"{abs(moved) / abs(phone['base']):.1f} counts per measured degree "
                             f"({abs(moved)} counts for {phone['base']:+g} deg on the phone)",
                        "bench_joint before_jog/after_jog", name, flagged=flagged)
        return _row("5", by_hand=f"{name} reached {abs(moved)} counts for {session.get('requested_delta_deg'):+g} "
                                 f"deg commanded, which is the vendor prior, not a measurement: read the jog on a "
                                 f"phone level and pass --phone base=<deg>")
    return _row("5", by_hand="no base bench_joint log: jog base against a mark, counts moved / degrees "
                             "(or bench_joint --joint base, then --phone base=<deg>)")


def row_7(files):
    worst = None
    for name, rows, _, flagged, _ in files:
        home = next((row for row in rows if _kind_key(row) == "home_complete" and "state" in row), None)
        if home is None:
            continue
        later = rows[rows.index(home) + 1:]
        quiet = [row for row in later if _kind_key(row) in BEFORE_MOTION and isinstance(row.get("state"), dict)]
        if not quiet or quiet[0]["state"].get("enabled") is not True:
            continue
        start, end = home["state"].get("encoder_counts", {}), quiet[0]["state"].get("encoder_counts", {})
        drift = {}
        for joint in ARM:
            try:
                drift[joint] = abs(signed_count_delta(end[joint], start[joint]))
            except (KeyError, ValueError):
                pass
        if not drift:
            continue
        joint = max(drift, key=drift.get)
        seconds = (quiet[0].get("host_monotonic_ns", 0) - home.get("host_monotonic_ns", 0)) / 1e9
        if worst is None or drift[joint] > worst[0]:
            worst = (drift[joint], joint, seconds, _kind_key(quiet[0]), name, flagged)
    if worst is None:
        return _row("7", by_hand="motors on after Home, no command, 60 s: drift of any axis in counts "
                                 "(record_raw_state logs motors off, so it does not answer this)")
    counts, joint, seconds, until, name, flagged = worst
    return _row("7", f"largest {counts} counts ({joint}) over {seconds:.1f} s with motors on and no command "
                     f"(the sheet asks for 60 s)", f"home_complete to {until}", name, flagged=flagged)


def row_8(files):
    for name, rows, _, flagged, _ in files:
        opens = [row["result"] for row in rows if row.get("type") == "after_gripper"
                 and (row.get("result") or {}).get("direction") == "open"]
        if opens:
            best = max(opens, key=lambda r: abs(r.get("moved_counts") or 0))
            travel = "full travel" if best.get("full_travel") else "NOT full travel"
            return _row("8", f"{abs(best.get('moved_counts') or 0)} counts closed to open ({travel})",
                        "bench_gripper after_gripper", name, flagged=flagged)
    return _row("8", by_hand="no bench_gripper log with an open move: counts from closed to open")


def row_10(files):
    worst, where = None, set()
    for name, rows, controller, flagged, controller_name in files:
        for label, rows_ in ((name, rows), (controller_name, controller)):
            for kind, joint, raw in _error_words(rows_):
                where.add(label)
                size = abs(signed_error_word(raw))
                if worst is None or size > worst[0]:
                    worst = (size, joint, kind, label, flagged)
    if worst is None:
        return _row("10", by_hand="no motors-on states in these logs: the largest abs error word per axis "
                                  "in every jog, vendor move and stream log")
    size, joint, kind, label, flagged = worst
    limit = IMPACT_DETECT_COUNTS[joint]
    near = f"; within {NEAR_IMPACT} of {limit}: raise it before going faster" if size >= limit - NEAR_IMPACT else ""
    return _row("10", f"largest {size} counts ({joint}) of {limit} ImpactDetect{near}; read from motors-on "
                      f"states and traced IN packets only, not every reply of every move", kind,
                ", ".join(sorted(where)),
                flagged=flagged)


def row_stream(files):
    found, names, sources = [], [], set()
    per_degree = None
    for name, rows, controller, flagged, controller_name in files:
        if not any(row.get("event") == "stream_trace" for row in rows + controller):
            continue
        per_degree = per_degree or follow_report.prior_per_degree()
        results = follow_report.analyse(rows + controller, per_degree)
        found += [f"{r['motor']} {r['speed_pct']:g}% lag {r['worst_lag_counts']} counts, "
                  f"overshoot {r['overshoot_counts']}" for r in results]
        if results:
            names.append(controller_name or name)
            sources.update(flagged)
    if not found:
        return _row("stream tracking", by_hand="no stream trace: bench_stream or bring_up --stream, "
                                               "then scripts/follow_report.py on its controller log")
    return _row("stream tracking", "; ".join(found), "stream_trace (follow_report)", ", ".join(names),
                flagged=sorted(sources))


def row_gripper_times(files):
    for name, rows, _, flagged, _ in files:
        befores = {row.get("move"): row for row in rows if row.get("type") == "before_gripper"}
        times = []
        for row in rows:
            before = befores.get(row.get("move"))
            if row.get("type") == "after_gripper" and before and "host_monotonic_ns" in row:
                seconds = (row["host_monotonic_ns"] - before.get("host_monotonic_ns", 0)) / 1e9
                times.append(f"{(row.get('result') or {}).get('direction')} {seconds:.2f} s")
        if times:
            return _row("gripper times", ", ".join(times) + " (record to record, includes host time)",
                        "bench_gripper before_gripper/after_gripper", name, flagged=flagged)
    return _row("gripper times", by_hand="no bench_gripper log: time from first setpoint to stall")


def sheet(paths, phone):
    """(rows in sheet order, problems)."""
    return _sheet(paths, phone)[:2]


def _sheet(paths, phone):
    files, problems, sources = load(paths)
    rows = [_row(n, by_hand=ALWAYS_BY_HAND[n]) for n in ("1",)] + [row_2(files)]
    rows += [_row(n, by_hand=ALWAYS_BY_HAND[n]) for n in ("3", "4")] + [row_5(files, phone)]
    rows += [_row("6", by_hand=ALWAYS_BY_HAND["6"]), row_7(files), row_8(files),
             _row("9", by_hand=ALWAYS_BY_HAND["9"]), row_10(files), row_stream(files), row_gripper_times(files)]
    return rows, problems, sources


def print_sheet(paths, phone):
    """Print the sheet; exit code 1 if any file had a problem."""
    rows, problems, sources = _sheet(paths, phone)
    banner = "SIMULATED DATA: rehearsal logs, not evidence from the physical arm."
    if "simulated" in sources:
        print(banner)
    print("Record sheet rows (docs/lab/LAB_RECORD_SHEET.md):")
    for row in rows:
        if row["value"]:
            print(f"{row['row']:<3} {row['unknown']}: {row['value']}  [{row['source']}; {row['file']}]")
        else:
            print(f"{row['row']:<3} {row['unknown']}: by hand: {row['by_hand']}")
    for problem in problems:
        print(f"PROBLEM: {problem}")
    print("Each value is a lead to check against what you saw, not a safety or calibration verdict.")
    if "simulated" in sources:
        print(banner)
    return 1 if problems else 0
