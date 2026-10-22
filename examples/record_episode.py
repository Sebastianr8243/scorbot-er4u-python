"""Record one teleoperated episode for imitation learning: two cameras, the arm's state and the command you gave.

Needs tools/usbc_server running and homed, and two cameras (`pip install .[camera]`). It moves the arm with the
keys below, one `move_by` per key press, so keep a hand near the red stop. It asks you to type "go" first.
Rehearse with no hardware: `python examples/record_episode.py --fake`.

    python examples/record_episode.py --task "pick the block" --step-mm 10

Keys: d/a = x +/-, w/s = y +/-, r/f = z +/-, o = open gripper, c = close, space = look without moving, q = end.
Each step reads both cameras, then the arm, then runs your command. One folder per episode (never overwritten):
    meta.json     task, cameras, units, source (real or simulated), step count, ended_early
    steps.jsonl   per step a row (state before the command, the command, frame paths, timestamps) written before the
                  arm moves, then a line {"step", "outcome": ok|refused|failed|interrupted, ...} once the command ended
    <camera>/000000.jpg ...
Rate is whatever the arm gives (a blocking move per step), not a fixed Hz: use t_s. state.gripper is the last
commanded one (the server cannot read it). Camera frames are stamped at read-return time and may be a few frames old.
Convert to a training format later; this keeps plain files and adds no dependency.
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from scorbot.bridge import Arm, ArmError, FakeArm
from scorbot.camera.recorder import encode_jpeg
from scorbot.camera.source import FakeSource

KEYS = {"d": ("dx", 1), "a": ("dx", -1), "w": ("dy", 1), "s": ("dy", -1), "r": ("dz", 1), "f": ("dz", -1)}


def _next_key(getch):
    """The next key, lowercased, or "" for a special key. Windows sends arrows, Insert, Delete and End as a prefix
    plus a scan code that can look like a letter, so both characters are consumed."""
    key = getch().lower()
    if key in ("\x00", "\xe0"):
        getch()
        return ""
    return key


def keyboard_commands(getch, step_mm, pending=lambda: False):
    """Commands from key presses until q. getch() returns one character; pending() says whether more are waiting.

    Keys pressed while a step was running (a held key repeats during the blocking move) are thrown away, so
    letting go stops the arm. A q among them still ends the episode, after the current step."""
    while True:
        key = _next_key(getch)
        if key == "q":
            return
        if key in KEYS:
            axis, sign = KEYS[key]
            command = {axis: sign * step_mm}
        elif key == "o":
            command = {"gripper": True}
        elif key == "c":
            command = {"gripper": False}
        elif key == " ":
            command = {}
        else:
            continue
        yield command
        while pending():
            if _next_key(getch) == "q":         # a quit pressed during the step still ends the episode
                return


def _write_json(path, data):
    path.write_text(json.dumps(data, indent=2, allow_nan=False), encoding="utf-8")


def record(arm, cameras, commands, out_dir, task="", encode=encode_jpeg, quality=90, clock=time.monotonic):
    """Run each command and log a row per step; cameras is {name: opened FrameSource}. Returns the row count.

    A camera or arm failure stops the episode after the rows already written (meta.json says ended_early).
    meta.json's source is "simulated" for a FakeArm and "real" for anything else, never a caller's choice."""
    source = "simulated" if isinstance(arm, FakeArm) else "real"
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=False)       # evidence is never overwritten
    for name in cameras:
        (out_dir / name).mkdir()
    meta = {"task": task, "source": source, "cameras": list(cameras), "units": {"x,y,z,dx,dy,dz": "mm", "pitch,roll": "DLL units"},
            "started": datetime.now().isoformat(timespec="seconds"), "steps": 0, "ended_early": True}
    _write_json(out_dir / "meta.json", meta)
    gripper, start, steps = None, clock(), 0
    try:
        with open(out_dir / "steps.jsonl", "x", encoding="utf-8") as log:
            for step, command in enumerate(commands):
                images, seen = {}, []
                for name, cam in cameras.items():       # both reads first so encoding one frame never delays the other
                    ok, images[name] = cam.read()
                    seen.append(clock())
                    if not ok:
                        raise RuntimeError(f"camera {name} returned no frame at step {step}")
                here = arm.where()
                read_at = clock()
                frames = {name: f"{name}/{step:06d}.jpg" for name in images}
                for name, image in images.items():
                    (out_dir / frames[name]).write_bytes(encode(image, quality))
                row = {"step": step, "t_s": round(read_at - start, 4), "frames": frames,
                       "frame_skew_s": round(max(seen) - min(seen), 4), "state_lag_s": round(read_at - min(seen), 4),
                       "state": {"x": here.x, "y": here.y, "z": here.z, "pitch": here.pitch, "roll": here.roll,
                                 "encoder": here.encoder, "joint": here.joint, "gripper": gripper}}
                delta = {axis: command.get(axis, 0) for axis in ("dx", "dy", "dz")}
                wanted = command.get("gripper")
                row["action"] = {**delta, "gripper": None if wanted is None else ("open" if wanted else "close")}
                log.write(json.dumps(row, allow_nan=False) + "\n")
                log.flush()                 # the evidence is on disk before the arm moves
                steps += 1
                outcome = {"step": step, "outcome": "ok"}
                try:
                    if any(delta.values()):
                        arm.move_by(**delta)
                    if wanted is not None:
                        arm.gripper(open=wanted)
                        gripper = "open" if wanted else "closed"
                except ArmError as error:
                    # a server refusal has an error and no code; anything else (a DLL code, a lost connection)
                    # may have happened after the arm started moving
                    refused = "error" in error.reply and "code" not in error.reply
                    outcome.update(outcome="refused" if refused else "failed", error=str(error), reply=error.reply)
                    raise
                except BaseException as error:      # Ctrl+C or a dead socket: the arm may have moved
                    outcome.update(outcome="interrupted", error=repr(error))
                    raise
                finally:
                    outcome["act_s"] = round(clock() - read_at, 4)
                    log.write(json.dumps(outcome, allow_nan=False) + "\n")
                    log.flush()
            meta["ended_early"] = False
    finally:
        meta["steps"] = steps
        _write_json(out_dir / "meta.json", meta)
    return steps


def _getch():
    import msvcrt
    return msvcrt.getwch()


def _pending():
    import msvcrt
    return msvcrt.kbhit()


def main(argv=None, read=input, getch=_getch, encode=encode_jpeg, pending=_pending):
    parser = argparse.ArgumentParser(description="Record a teleoperated episode: two cameras, arm state, your command")
    parser.add_argument("--out", help="episode folder (default episodes/<time>; rehearsal/<time> with --fake)")
    parser.add_argument("--task", default="")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--speed", type=int, default=20, help="percent of top speed, 1 to 100")
    parser.add_argument("--step-mm", type=float, default=10.0, help="distance per key press, at most 50")
    parser.add_argument("--cameras", type=int, nargs=2, default=[0, 1], metavar=("TOP", "WRIST"), help="OpenCV indexes")
    parser.add_argument("--fake", action="store_true", help="FakeArm and fake cameras: no hardware")
    args = parser.parse_args(argv)
    if not 0 < args.step_mm <= 50:
        parser.error("--step-mm must be above 0 and at most 50 (the server's step limit)")
    warning = "Rehearsal with no hardware." if args.fake else "The arm will move on every key press. Hand on the red stop."
    if read(f"{warning} Type go: ") != "go":
        print("not started")
        return 1
    folder = "rehearsal" if args.fake else "episodes"      # rehearsals never land beside real episodes
    out = Path(args.out or Path(folder) / f"{datetime.now():%Y%m%d-%H%M%S}")
    if args.fake:
        cameras, fake_arm = {"top": FakeSource(), "wrist": FakeSource()}, FakeArm()
    else:
        from scorbot.camera.source import OpenCVSource
        cameras = {name: OpenCVSource(index) for name, index in zip(("top", "wrist"), args.cameras)}
        fake_arm = None
    opened = []
    try:
        for cam in cameras.values():
            cam.open()
            opened.append(cam)
        if fake_arm is not None:
            return _run(fake_arm, cameras, args, out, getch, encode, pending)
        with Arm(port=args.port) as arm:
            arm.speed(args.speed)
            return _run(arm, cameras, args, out, getch, encode, pending)
    except (ArmError, RuntimeError) as error:
        print(f"stopped: {error}")
        return 1
    finally:
        for cam in opened:
            cam.close()


def _run(arm, cameras, args, out, getch, encode, pending):
    print("keys: d/a x, w/s y, r/f z, o open, c close, space look, q end")
    steps = record(arm, cameras, keyboard_commands(getch, args.step_mm, pending), out, args.task, encode)
    print(f"{steps} steps in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
