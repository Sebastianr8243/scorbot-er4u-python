"""python -m scorbot.lerobot_export: lab sessions to a local LeRobot dataset.

Run in .venv-lerobot to write (see docs/LEROBOT_EXPORT.md); --dry-run works in
any environment and writes nothing. Never uploads.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .checks import check_export, exportable
from .frames import resample
from .load import load_lab_session


def plan_export(paths, *, fps, max_frame_gap_s, video):
    from .write import ExportPlan   # write imports numpy only at module level, not lerobot
    sessions = [load_lab_session(p) for p in paths]
    plan = ExportPlan(sessions, refusals=check_export(sessions, video=video))
    if plan.refusals:
        return plan
    for data in sessions:
        kept, refusals = exportable(data, fps=fps, max_frame_gap_s=max_frame_gap_s,
                                    video=video)
        plan.refusals.extend(refusals)
        plan.episodes.extend((data, resample(data, e, fps=fps, video=video)) for e in kept)
    return plan


def _print_plan(plan, fps):
    simulated = any(s.data_source != "real" for s in plan.sessions)
    prefix = "SIMULATED " if simulated else ""
    for data, frames in plan.episodes:
        print(f"{prefix}KEEP   {data.name} episode {frames.episode}: "
              f"{len(frames.times_ns)} frames at {fps} fps, task {frames.task!r}")
    for refusal in plan.refusals:
        kind = "SKIP" if refusal.scope == "episode" else "REFUSE"
        where = refusal.session + (f" episode {refusal.episode}" if refusal.episode else "")
        print(f"{prefix}{kind:<6} {where}: {refusal.reason}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scorbot.lerobot_export",
                                     description=__doc__)
    parser.add_argument("lab_logs", nargs="+", type=Path, help="lab session JSONL files")
    parser.add_argument("--out", type=Path, required=True, help="new dataset folder")
    parser.add_argument("--repo-id", required=True, help="dataset name, e.g. local/scorbot-reach")
    parser.add_argument("--fps", type=int, default=10)
    parser.add_argument("--max-frame-gap", type=float, default=0.2,
                        help="seconds without a camera frame that refuse an episode")
    parser.add_argument("--no-video", action="store_true",
                        help="export state and action only (robot-only dataset)")
    parser.add_argument("--dry-run", action="store_true", help="check and list, write nothing")
    parser.add_argument("--preview", type=Path, metavar="HTML",
                        help="also write an HTML report of every episode (no lerobot needed)")
    args = parser.parse_args(argv)
    video = not args.no_video
    if args.out.exists():
        print(f"REFUSE {args.out} already exists; choose a new --out")
        return 1
    plan = plan_export(args.lab_logs, fps=args.fps, max_frame_gap_s=args.max_frame_gap,
                       video=video)
    _print_plan(plan, args.fps)
    if args.preview is not None:
        from .preview import render
        try:
            # Exclusive create: a report must never overwrite a file (a lab log is evidence).
            with open(args.preview, "x", encoding="utf-8") as stream:
                stream.write(render(plan, fps=args.fps, video=video))
        except FileExistsError:
            print(f"REFUSE preview {args.preview} already exists; choose a new file")
            return 1
        print(f"Preview written to {args.preview}")
    if any(r.scope == "export" for r in plan.refusals):
        print("Export refused; nothing written.")
        return 1
    if not plan.episodes:
        print("No episode can be exported; nothing written.")
        return 1
    if args.dry_run:
        print(f"Dry run: {len(plan.episodes)} episode(s) would be exported; nothing written.")
        return 0
    from .write import write_dataset
    try:
        out = write_dataset(plan, args.out, args.repo_id, args.fps, video)
    except Exception as error:
        print(f"Export failed, nothing published: {type(error).__name__}: {error}")
        return 1
    print(f"Wrote {len(plan.episodes)} episode(s) to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
