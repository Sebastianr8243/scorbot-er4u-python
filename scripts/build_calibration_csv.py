"""Turn guided lab session logs plus physical angle readings into the calibration CSV.

The logs give the encoder counts; you give the angle you read off the level or
protractor for each step. The output is the file ``fit_calibration.py`` wants
(see docs/lab/PHYSICAL_CALIBRATION.md). Never opens USB. Standard library only.

Readings file, one line per measurement (``point`` is ``home`` or the step
number the lab session prints as ``Step n``):

    log,point,joint,role,physical_angle_deg,requested_target_deg
    lab-a.jsonl,home,base,home,0.0,
    lab-a.jsonl,3,base,fit,2.9,
    lab-a.jsonl,5,base,move_verify,4.9,5.0

    python scripts\\build_calibration_csv.py --log logs\\lab-a.jsonl --readings readings.csv --output measurements\\arm-1.csv
"""

import argparse
import csv
import json
import math
from pathlib import Path

JOINTS = ("base", "shoulder", "elbow")
ROLES = ("home", "fit", "verify", "move_verify")
NEEDED = {"home": 3, "fit": 4, "verify": 3, "move_verify": 3}      # fit_calibration.fit
COLUMNS = ("robot_id", "joint", "role", "approach", "reference_source", "encoder_count",
           "measured_angle_deg", "requested_target_deg")


class ReadingsError(ValueError):
    """The logs and the readings do not make a trustworthy calibration CSV."""


def load_log(path: Path) -> list[dict]:
    with Path(path).open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def _facts(name: str, rows: list[dict]) -> dict:
    heads = [row for row in rows if row.get("type") == "session"]
    if len(heads) != 1:
        raise ReadingsError(f"{name}: needs exactly one session row, has {len(heads)}; "
                            "one log is one session")
    head = heads[0]
    if head.get("data_source") != "real":
        raise ReadingsError(f"{name}: data_source is {head.get('data_source')!r}; a calibration "
                            "is built from real-arm logs only")
    homes = [row for row in rows if row.get("type") == "home_complete"]
    if len(homes) > 1:
        raise ReadingsError(f"{name}: has {len(homes)} homings; a trial is one homing")
    jogs, order = {}, []
    for row in rows:
        if row.get("type") == "jog_preview":
            jogs[row["n"]] = {"joint": row["joint"], "delta": row["delta_deg"]}
            order.append(row["n"])
        elif row.get("type") == "after_jog" and row["n"] in jogs:
            jogs[row["n"]]["counts"] = row["state"]["encoder_counts"]
    return {"robot_id": head["robot_id"],
            "home": homes[0]["state"]["encoder_counts"] if homes else None,
            "jogs": {n: jog for n, jog in jogs.items() if "counts" in jog},
            "order": [n for n in dict.fromkeys(order) if "counts" in jogs[n]]}


def _commanded_travel(fact: dict, joint: str, step: int) -> float:
    """Degrees commanded for ``joint`` from home up to and including ``step`` (legacy scale)."""
    return sum(fact["jogs"][n]["delta"] for n in fact["order"]
               if n <= step and fact["jogs"][n]["joint"] == joint)


def build_rows(logs: dict[str, list[dict]], readings: list[dict]) -> list[dict]:
    facts = {name: _facts(name, rows) for name, rows in logs.items()}
    robots = {fact["robot_id"] for fact in facts.values()}
    if len(robots) > 1:
        raise ReadingsError(f"The logs are from different robot ids: {sorted(robots)}")
    if len({name for name in logs}) != len(logs):
        raise ReadingsError("Two logs have the same name")
    home_angles = {}
    for reading in readings:
        if str(reading["point"]).strip() == "home" and reading["role"] == "home":
            try:
                home_angles[(reading["log"], reading["joint"])] = float(reading["physical_angle_deg"])
            except ValueError:
                pass
    out, seen = [], set()
    for number, reading in enumerate(readings, start=2):
        where = f"readings line {number}"
        name, joint, role = reading["log"], reading["joint"], reading["role"]
        point = str(reading["point"]).strip()
        if point != "home":
            if not point.isdigit() or str(int(point)) != point:
                raise ReadingsError(f"{where}: point must be 'home' or a step number like 3, "
                                    f"not {point!r}")
        if name not in facts:
            raise ReadingsError(f"{where}: log {name!r} was not passed with --log "
                                f"(have {sorted(facts)})")
        if joint not in JOINTS or role not in ROLES:
            raise ReadingsError(f"{where}: joint must be one of {JOINTS} and role one of {ROLES}")
        try:
            angle = float(reading["physical_angle_deg"])
        except ValueError:
            angle = math.nan
        if not math.isfinite(angle):
            raise ReadingsError(f"{where}: physical_angle_deg must be a finite number")
        try:
            typed = float(reading.get("requested_target_deg") or "nan")
        except ValueError:
            raise ReadingsError(f"{where}: requested_target_deg must be a number or empty") from None
        if (name, point, joint) in seen:
            raise ReadingsError(f"{where}: {joint} at {point} in {name} is listed twice")
        seen.add((name, point, joint))
        fact = facts[name]
        if (point == "home") != (role == "home"):
            raise ReadingsError(f"{where}: the point 'home' goes with the role 'home', and only that")
        if point == "home":
            if fact["home"] is None:
                raise ReadingsError(f"{where}: {name} has no completed homing")
            counts, approach = fact["home"], ""
        else:
            try:
                jog = fact["jogs"][int(point)]
            except (ValueError, KeyError):
                steps = ", ".join(f"{n}={j['joint']} {j['delta']:+g}" for n, j in fact["jogs"].items())
                raise ReadingsError(f"{where}: {name} has no step {point!r} (steps: {steps})") from None
            if jog["joint"] != joint:
                raise ReadingsError(f"{where}: step {point} of {name} moved {jog['joint']}, "
                                    f"not {joint}")
            counts, approach = jog["counts"], "+" if jog["delta"] > 0 else "-"
        requested = ""
        if role == "move_verify":
            if (name, joint) not in home_angles:
                raise ReadingsError(f"{where}: move_verify needs a home reading for {joint} in "
                                    f"{name}, to work out the target from the logged steps")
            requested = home_angles[(name, joint)] + _commanded_travel(fact, joint, int(point))
            if math.isfinite(typed) and abs(requested - typed) > 0.05:
                raise ReadingsError(f"{where}: requested_target_deg {typed:g} does not match the "
                                    f"logged steps, which commanded {requested:g} "
                                    "(home reading plus the steps up to this one)")
        count = int(counts[joint])
        if not 0 <= count <= 65535:
            raise ReadingsError(f"{where}: count {count} is not unsigned 16-bit; is this a lab log?")
        out.append({"robot_id": fact["robot_id"], "joint": joint, "role": role,
                    "approach": approach, "reference_source": "physical",
                    "encoder_count": count, "measured_angle_deg": angle,
                    "requested_target_deg": requested})
    return out


def summary(rows: list[dict]) -> list[str]:
    """One line per joint: rows held against rows the fitter needs, so a short set shows at the lab."""
    lines = []
    for joint in JOINTS:
        mine = [row for row in rows if row["joint"] == joint]
        if not mine:
            continue
        parts = []
        for role, needed in NEEDED.items():
            group = [row for row in mine if row["role"] == role]
            sides = {row["approach"] for row in group} - {""}
            text = f"{role} {len(group)}/{needed}"
            if role != "home" and sides != {"+", "-"}:
                text += " (needs both approach directions)"
            parts.append(text)
        short = any(len([r for r in mine if r["role"] == role]) < needed
                    for role, needed in NEEDED.items())
        lines.append(f"{joint}: " + ", ".join(parts) + ("  <- not enough yet" if short else ""))
    return lines


def write_csv(rows: list[dict], output: Path) -> None:
    with Path(output).open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--log", action="append", required=True, type=Path,
                        help="a lab session JSONL log (repeat for each homing trial)")
    parser.add_argument("--readings", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path, help="CSV to create (never overwritten)")
    parser.add_argument("--limits", type=Path, help="with --calibration-output: also fit")
    parser.add_argument("--calibration-output", type=Path)
    args = parser.parse_args(argv)
    if bool(args.limits) != bool(args.calibration_output):
        parser.error("--limits and --calibration-output go together")
    names = [path.name for path in args.log]
    if len(set(names)) != len(names):
        print("Not written: two --log files have the same name; rename one so readings can tell them apart")
        return 2
    try:
        logs = {path.name: load_log(path) for path in args.log}
        with args.readings.open(newline="", encoding="utf-8-sig") as stream:
            readings = list(csv.DictReader(stream))
        rows = build_rows(logs, readings)
        write_csv(rows, args.output)
    except (ReadingsError, OSError, KeyError, json.JSONDecodeError) as error:
        print(f"Not written: {error}")
        return 2
    print(f"Wrote {len(rows)} rows to {args.output}")
    for line in summary(rows):
        print(line)
    robot_id = rows[0]["robot_id"] if rows else "ROBOT-ID"
    if args.limits:
        try:
            from fit_calibration import fit, scale_warnings, write_result
        except ImportError:
            from scripts.fit_calibration import fit, scale_warnings, write_result
        try:
            result = fit(args.output, args.limits, robot_id)
            write_result(result, args.calibration_output)
        except (ValueError, OSError) as error:
            print(f"Fit refused: {error}")
            return 3
        print(f"Validated {', '.join(result['joints'])}; wrote {args.calibration_output}")
        for line in scale_warnings(result):
            print(line)
    else:
        print(f"Next: python scripts\\fit_calibration.py --measurements {args.output} "
              f"--limits <limits.json> --robot-id {robot_id} --output calibration\\{robot_id}.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
