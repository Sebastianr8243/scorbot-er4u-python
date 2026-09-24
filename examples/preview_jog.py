"""Print an offline jog plan. This script never connects to USB."""

import argparse
import json
from pathlib import Path

from scorbot import Scorbot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--joint", choices=("base", "shoulder", "elbow",
                                           "wrist_pitch", "wrist_roll"), required=True)
    parser.add_argument("--delta", type=float, required=True)
    parser.add_argument("--speed", type=int, default=10)
    parser.add_argument("--state-json", type=Path,
                        help="Saved JSON object with signed_encoder_counts or a nested state")
    args = parser.parse_args()
    counts = None
    if args.state_json is not None:
        data = json.loads(args.state_json.read_text(encoding="utf-8"))
        state = data.get("state", data)
        counts = state.get("signed_encoder_counts")
        if counts is None:
            parser.error("Saved state has no signed_encoder_counts")
    plan = Scorbot().preview_jog(
        args.joint, args.delta, speed=args.speed, starting_signed_counts=counts)
    print(json.dumps(plan, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
