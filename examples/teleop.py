"""Drive the arm from the keyboard, one `move_by` per key press. No cameras, no logging.

Needs tools/usbc_server running and homed. The arm moves on every key press, so keep a hand near the red stop.
It asks you to type "go" first. Rehearse with no hardware: `python examples/teleop.py --fake`.

    python examples/teleop.py --step-mm 5

Keys: d/a = x +/-, w/s = y +/-, r/f = z +/-, t/g = wrist pitch +/-, y/h = wrist roll +/-, o = open gripper, c = close,
q = end. Wrist steps are in the DLL's own pitch and roll units (probably 0.001 degree; unverified on the arm): start
small, `--wrist-step 200`, and watch the first one.
A server refusal (a step outside the box) is printed and the loop goes on; any other error ends it.
After every key a status line shows the step count, the last result, the refusals so far and the position in mm.
"""

import argparse
import sys

from scorbot.bridge import Arm, ArmError, FakeArm
from scorbot.keys import getch, kbhit, keyboard_commands

MAX_STEP_MM = 20        # below the server's 50 mm limit: this is a hand-driven script
MAX_WRIST_STEP = 5000   # below the server's 10000 units, for the same reason


def status_line(done, last, refused, position):
    last = last if len(last) <= 60 else last[:57] + "..."       # the full refusal was just printed
    where = f" | x {position.x:.1f} y {position.y:.1f} z {position.z:.1f} mm" if position else ""
    return f"step {done}: {last} | refused {refused}{where}"


def drive(arm, commands, say=print, status=None):
    """Apply each command. Returns the number applied; a server refusal is reported and skipped.

    `status`, when given, gets one line after every key: steps so far, the last result, refusals, the last known position."""
    done = refused = 0
    position = None
    for command in commands:
        delta = {axis: command[axis] for axis in ("dx", "dy", "dz") if axis in command}
        wrist = {axis: command[axis] for axis in ("dpitch", "droll") if axis in command}
        try:
            if delta:
                position = arm.move_by(**delta) or position
            if wrist:
                position = arm.wrist_by(**wrist) or position
            if "gripper" in command:
                position = arm.gripper(open=command["gripper"]) or position
        except ArmError as error:
            if "error" not in error.reply or "code" in error.reply:
                raise                   # not a plain refusal: the arm may have moved, so end here
            say(f"refused: {error}")
            refused += 1
            if status:
                status(status_line(done, f"refused: {error}", refused, position))
            continue
        done += 1
        if status:
            status(status_line(done, "ok", refused, position))
    return done


def main(argv=None, read=input, keys=getch, waiting=kbhit):
    parser = argparse.ArgumentParser(description="Move the arm with the keyboard, one step per key press")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--speed", type=int, default=20, help="percent of top speed, 1 to 100")
    parser.add_argument("--step-mm", type=float, default=5.0, help=f"distance per key press, at most {MAX_STEP_MM}")
    parser.add_argument("--wrist-step", type=float, default=0, help=f"wrist units per t/g/y/h press, at most {MAX_WRIST_STEP}; 0 turns the wrist keys off")
    parser.add_argument("--fake", action="store_true", help="FakeArm: no hardware")
    args = parser.parse_args(argv)
    if not 0 < args.step_mm <= MAX_STEP_MM:
        parser.error(f"--step-mm must be above 0 and at most {MAX_STEP_MM}")
    if not 0 <= args.wrist_step <= MAX_WRIST_STEP:
        parser.error(f"--wrist-step must be from 0 to {MAX_WRIST_STEP}")
    warning = "Rehearsal with no hardware." if args.fake else "The arm will move on every key press. Hand on the red stop."
    if read(f"{warning} Type go: ") != "go":
        print("not started")
        return 1
    print("keys: d/a x, w/s y, r/f z" + (", t/g pitch, y/h roll" if args.wrist_step else "") + ", o open, c close, q end")
    try:
        with FakeArm() if args.fake else Arm(port=args.port) as arm:
            arm.speed(args.speed)
            print(f"{drive(arm, keyboard_commands(keys, args.step_mm, waiting, args.wrist_step), status=print)} steps")
    except ArmError as error:
        print(f"stopped: {error}")
        return 1
    except KeyboardInterrupt:
        print("ended")
    return 0


if __name__ == "__main__":
    sys.exit(main())
