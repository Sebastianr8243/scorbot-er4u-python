"""Print PowerShell commands for training and rollout; execute nothing."""

import argparse
import re

from scorbot.cli import ROBOT_SAFETY_FLAGS


def quote(argument):
    if re.fullmatch(r"[A-Za-z0-9_./:=\\-]+", argument):
        return argument
    return "'" + argument.replace("'", "''") + "'"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    train = commands.add_parser("train", help="Print a GPU PC training command")
    train.add_argument("--dataset-root", default="rehearsal/my_first_demo")
    train.add_argument("--repo-id", default="local/my_first_demo")
    train.add_argument("--output-dir", default="outputs/act")
    train.add_argument("--steps", type=int, default=20000)
    train.add_argument("--batch-size", type=int, default=8)
    train.add_argument("--device", default="cuda")
    train.add_argument("--policy", choices=("act", "smolvla"), default="act")
    rollout = commands.add_parser("rollout", help="Print a lab PC rollout command")
    rollout.add_argument("--model", required=True, help="Local pretrained_model directory")
    rollout.add_argument("--sim", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "train":
        policy = "--policy.type=act" if args.policy == "act" else "--policy.path=lerobot/smolvla_base"
        flags = ["lerobot-train", f"--dataset.repo_id={args.repo_id}",
                 f"--dataset.root={args.dataset_root}", policy, f"--policy.device={args.device}",
                 f"--output_dir={args.output_dir}", f"--steps={args.steps}",
                 f"--batch_size={args.batch_size}", "--policy.push_to_hub=false"]
    else:
        flags = ["lerobot-rollout", "--strategy.type=base", f"--policy.path={args.model}",
                 "--robot.type=scorbot", *ROBOT_SAFETY_FLAGS, "--device=cpu",
                 "--return_to_initial_position=false"]
        if args.sim:
            flags.append("--robot.simulate=true")
    print(" `\n  ".join(quote(flag) for flag in flags))
    print("Preview only. Run the command in the separate LeRobot venv after checking its settings.")
    if args.command == "train":
        print(f"Copy dataset to GPU PC: scp -r {quote(args.dataset_root)} '<user>@<gpu-pc>:<path>'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
