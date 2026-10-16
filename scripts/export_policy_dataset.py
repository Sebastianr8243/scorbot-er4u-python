"""Turn policy_loop records into a training dataset: one NPZ of frames plus a JSON of what they mean.

    python scripts/export_policy_dataset.py logs/policy-01.jsonl [logs/policy-02.jsonl ...] --out dataset/run1

One policy run is one episode; one control tick is one frame. Per frame: ``observation_state`` (measured
degrees from the session home, base/shoulder/elbow), ``observation_counts`` (the raw counts behind it),
``action`` (the clamped target position the stream was given, degrees from home), ``clamped`` (the
clamp changed the policy's ask), ``timestamp`` (seconds from the first tick), ``episode_index`` and
``frame_index``. No images yet: the camera is not wired into policy_loop.

Degrees use the vendor's counts-per-degree prior, which is NOT measured on our arm; meta.json says so.
Refuses, with a reason each: runs that did not pass, runs where the policy errored, anything that is not
a policy_run record, and (the whole export) mixed real and simulated data or mixed control rates.
Reloads what it wrote and compares it with the source rows; any difference fails. Plain NumPy, no LeRobot:
a LeRobot writer would be a second step in its own environment.
"""

import argparse
import json
from pathlib import Path
import sys

import numpy as np

MOTORS = ("base", "shoulder", "elbow")
MIN_FRAMES = 2
UNITS = ("degrees from the session home, from the vendor counts-per-degree prior: "
         "UNCALIBRATED, not measured on this arm")


def load_run(path):
    rows = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    session = next((r for r in rows if r.get("type") == "session"), None)
    steps = [r for r in rows if r.get("type") == "policy_step"]
    after = next((r for r in rows if r.get("type") == "after_policy"), None)
    return session, steps, after


def refusal(session, steps, after):
    """Why this run cannot be an episode, or None."""
    if not session or session.get("procedure") != "policy_run":
        return "not a policy_loop record"
    if after is None:
        return "the run never finished (no after_policy row)"
    if not after["result"].get("passed"):
        return "the run failed: " + "; ".join(after["result"].get("problems", []))
    if after["result"].get("policy_errors"):
        return f"the policy errored {after['result']['policy_errors']} time(s)"
    if len(steps) < MIN_FRAMES:
        return f"only {len(steps)} frame(s)"
    return None


def frames(steps):
    return {
        "observation_state": np.array([[s["measured_deg"][m] for m in MOTORS] for s in steps], dtype=np.float64),
        "observation_counts": np.array([[s["measured_counts"][m] for m in MOTORS] for s in steps], dtype=np.int64),
        "action": np.array([[s["target_deg"][m] for m in MOTORS] for s in steps], dtype=np.float64),
        "clamped": np.array([bool(s["clamped"]) for s in steps]),
        "timestamp": np.array([s["t"] for s in steps], dtype=np.float64),
    }


def export(inputs, out):
    """Write the dataset. Returns (meta, refused) where refused is [(path, reason)]. Raises SystemExit
    when nothing can be exported or the inputs cannot share a dataset."""
    kept, refused = [], []
    for path in inputs:
        session, steps, after = load_run(path)
        why = refusal(session, steps, after)
        if why:
            refused.append((str(path), why))
        else:
            kept.append((path, session, steps))
    if not kept:
        raise SystemExit("nothing to export: " + "; ".join(f"{p}: {w}" for p, w in refused))
    for key, label in (("data_source", "real and simulated data"), ("rate_hz", "control rates")):
        if len({s[key] for _, s, _ in kept}) > 1:
            raise SystemExit(f"refusing to mix {label} in one dataset")
    out = Path(out)
    if out.exists():
        raise SystemExit(f"{out} already exists; choose a new folder (nothing is overwritten)")
    parts, episodes = [], []
    for index, (path, session, steps) in enumerate(kept):
        data = frames(steps)
        data["episode_index"] = np.full(len(steps), index, dtype=np.int64)
        data["frame_index"] = np.arange(len(steps), dtype=np.int64)
        parts.append(data)
        episodes.append({"index": index, "source": Path(path).name, "frames": len(steps),
                         "policy": session["policy"], "robot_id": session["robot_id"],
                         "software_commit": session["software_commit"],
                         "motion_source_sha256": session["motion_source_sha256"],
                         "start_pose_note": session["start_pose_note"]})
    arrays = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
    first = kept[0][1]
    meta = {"format": "scorbot-policy-npz-1", "data_source": first["data_source"], "fps": first["rate_hz"],
            "motors": list(MOTORS), "state_unit": UNITS, "action_unit": "commanded target position, " + UNITS,
            "images": "none", "frames": int(len(arrays["timestamp"])), "episodes": episodes,
            "refused": [{"source": p, "reason": w} for p, w in refused]}
    out.mkdir(parents=True)
    np.savez(out / "data.npz", **arrays)
    (out / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta, refused


def verify(out, inputs):
    """Reload the dataset and compare it with the source rows. Returns a list of problems."""
    out, problems = Path(out), []
    data = np.load(out / "data.npz")
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    by_source = {Path(p).name: p for p in inputs}
    start = 0
    for ep in meta["episodes"]:
        _, steps, _ = load_run(by_source[ep["source"]])
        want, stop = frames(steps), start + ep["frames"]
        for key, expected in want.items():
            if not np.array_equal(data[key][start:stop], expected):
                problems.append(f"episode {ep['index']}: {key} differs from the source rows")
        if not np.array_equal(data["frame_index"][start:stop], np.arange(ep["frames"])):
            problems.append(f"episode {ep['index']}: frame_index is not 0..n-1")
        start = stop
    if start != len(data["timestamp"]):
        problems.append("frame count does not match the episode list")
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("inputs", nargs="+", type=Path, help="policy_loop JSONL records")
    parser.add_argument("--out", required=True, type=Path, help="new folder for data.npz and meta.json")
    args = parser.parse_args(argv)
    meta, refused = export(args.inputs, args.out)
    for path, why in refused:
        print(f"refused {path}: {why}")
    problems = verify(args.out, args.inputs)
    if problems:
        print("EXPORT CHECK FAILED:", *problems, sep="\n  ")
        return 1
    print(f"Wrote {meta['frames']} frames in {len(meta['episodes'])} episode(s) "
          f"({meta['data_source']}, {meta['fps']} Hz) to {args.out}; reloaded and matched the source rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
