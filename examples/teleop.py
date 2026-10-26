"""Drive the arm from the keyboard, one `move_by` per key press. No cameras, no logging.

Needs tools/usbc_server running and homed. The arm moves on every key press, so keep a hand near the red stop.
It asks you to type "go" first. Rehearse with no hardware: `python examples/teleop.py --fake`.

    python examples/teleop.py --step-mm 5

Keys: d/a = x +/-, w/s = y +/-, r/f = z +/-, o = open gripper, c = close, q = end.
A server refusal (a step outside the box) is printed and the loop goes on; any other error ends it.
"""

import argparse
import sys

from scorbot.bridge import Arm, ArmError, FakeArm
from scorbot.keys import getch, kbhit, keyboard_commands

MAX_STEP_MM = 20        # below the server's 50 mm limit: this is a hand-driven script


def drive(arm, commands, say=print):
    """Apply each command. Returns the number applied; a server refusal is reported and skipped."""
    done = 0
    for command in commands:
        delta = {axis: command[axis] for axis in ("dx", "dy", "dz") if axis in command}
        try:
            if delta:
                arm.move_by(**delta)
            if "gripper" in command:
                arm.gripper(open=command["gripper"])
        except ArmError as error:
            if "error" not in error.reply or "code" in error.reply:
                raise                   # not a plain refusal: the arm may have moved, so end here
            say(f"refused: {error}")
            continue
        done += 1
    return done


def main(argv=None, read=input, keys=getch, waiting=kbhit):
    parser = argparse.ArgumentParser(description="Move the arm with the keyboard, one step per key press")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--speed", type=int, default=20, help="percent of top speed, 1 to 100")
    parser.add_argument("--step-mm", type=float, default=5.0, help=f"distance per key press, at most {MAX_STEP_MM}")
    parser.add_argument("--fake", action="store_true", help="FakeArm: no hardware")
    args = parser.parse_args(argv)
    if not 0 < args.step_mm <= MAX_STEP_MM:
        parser.error(f"--step-mm must be above 0 and at most {MAX_STEP_MM}")
    warning = "Rehearsal with no hardware." if args.fake else "The arm will move on every key press. Hand on the red stop."
    if read(f"{warning} Type go: ") != "go":
        print("not started")
        return 1
    print("keys: d/a x, w/s y, r/f z, o open, c close, q end")
    try:
        with FakeArm() if args.fake else Arm(port=args.port) as arm:
            arm.speed(args.speed)
            print(f"{drive(arm, keyboard_commands(keys, args.step_mm, waiting))} steps")
    except ArmError as error:
        print(f"stopped: {error}")
        return 1
    except KeyboardInterrupt:
        print("ended")
    return 0


if __name__ == "__main__":
    sys.exit(main())
