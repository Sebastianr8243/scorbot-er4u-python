"""The Python API for the arm: a client of tools/usbc_server, which holds the vendor-DLL session. Standard library only.

    from scorbot.bridge import Arm
    with Arm() as arm:              # the server must be running (docs/lab/RUN_THE_ARM.md)
        arm.move_by(dz=20)
        arm.gripper(open=False)
        print(arm.where())

Positions are mm in the DLL's own task frame. Its origin and axis directions are whatever the DLL uses after
Home; examples/arm_characterise.py measures them. Commands are checked by the server (the trust boundary), not
here. The same calls work on FakeArm, which needs no server.

    python -m scorbot.bridge where | by DX DY DZ | to X Y Z | open | close | speed N | home | teach NAME | go NAME | stop | quit
"""

import argparse
import json
import socket
import sys
from dataclasses import dataclass, field
from pathlib import Path

HOST, PORT = "127.0.0.1", 8765          # tools/usbc_server PORT
XYZ_UNITS_PER_MM = 1000     # unverified: the unit of GetCurrentPosition's xyz array; arm_characterise.py measures it
POSITIONS = Path("calibration") / "positions.json"
BOX = Path("calibration") / "arm.json"
REPLY_TIMEOUT_S = 900.0     # a Home takes about 135 s on the lab arm; the server bounds every call itself


class ArmError(Exception):
    """The server refused a command or the DLL call failed. `reply` is the server's whole answer."""

    def __init__(self, message, reply=None):
        super().__init__(message)
        self.reply = reply or {}


@dataclass(frozen=True)
class Position:
    x: float
    y: float
    z: float
    pitch: int = 0          # the DLL's own units, passed through unchanged
    roll: int = 0
    encoder: list = field(default_factory=list)
    joint: list = field(default_factory=list)
    xyz: list = field(default_factory=list)

    @classmethod
    def from_reply(cls, reply):
        xyz = reply.get("xyz")
        if not xyz:
            return None
        x, y, z = (value / XYZ_UNITS_PER_MM for value in xyz[:3])
        return cls(x, y, z, xyz[3], xyz[4], reply.get("encoder", []), reply.get("joint", []), xyz)


class Arm:
    def __init__(self, host=HOST, port=PORT, timeout=REPLY_TIMEOUT_S):
        try:
            self._sock = socket.create_connection((host, port), timeout=10)
        except OSError as error:
            raise ArmError(f"no arm server on {host}:{port} ({error}); start tools/usbc_server first") from None
        self._sock.settimeout(timeout)
        self._reader = self._sock.makefile("rb")
        self._next_id = 0

    def close(self):
        self._reader.close()
        self._sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    def _write(self, command):
        self._sock.sendall((json.dumps(command, allow_nan=False) + "\n").encode("utf-8"))

    def send(self, op, **fields):
        """One command and its reply. Raises ArmError when the reply is not ok."""
        self._next_id += 1
        self._write({"op": op, "id": self._next_id, **fields})
        while True:
            line = self._reader.readline()
            if not line:
                raise ArmError("the arm server closed the connection")
            reply = json.loads(line)
            if reply.get("id") == self._next_id:     # anything else answers an earlier stop
                break
        if not reply.get("ok"):
            raise ArmError(reply.get("error") or f"{op} failed (code {reply.get('code')})", reply)
        return reply

    def ping(self):
        return self.send("ping")

    def where(self):
        return Position.from_reply(self.send("where"))

    def home(self):
        return Position.from_reply(self.send("home"))

    def speed(self, percent):
        self.send("speed", percent=percent)

    def move_by(self, dx=0.0, dy=0.0, dz=0.0):
        return Position.from_reply(self.send("move_by", dx=dx, dy=dy, dz=dz))

    def move_to(self, x, y, z, pitch=None, roll=None):
        extra = {key: value for key, value in (("pitch", pitch), ("roll", roll)) if value is not None}
        return Position.from_reply(self.send("move_to", x=x, y=y, z=z, **extra))

    def gripper(self, open):        # noqa: A002 - reads as arm.gripper(open=True)
        return Position.from_reply(self.send("gripper", open=open))

    def stop(self):
        """Ask the server to Stop. Safe from another thread while a move waits for its reply."""
        self._write({"op": "stop"})

    def quit(self):
        """End the server's session: Control off and CloseUSBC."""
        self.send("quit")

    def run(self, commands):
        """Send {"op": ..., ...} commands in order; stops at the first failure (ArmError)."""
        return [self.send(**command) for command in commands]

    def teach(self, name, positions=POSITIONS, box=BOX):
        return teach(self, name, positions, box)

    def go(self, name, positions=POSITIONS):
        return go(self, name, positions)


class FakeArm:
    """Arm without a server: positions in memory, for laptops and tests. Never talks to a DLL."""

    def __init__(self, start=(300.0, 0.0, 200.0), homed=True):
        self.x, self.y, self.z = start
        self.homed, self.open, self.commands = homed, True, []

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        pass

    def where(self):
        return Position(self.x, self.y, self.z)

    def home(self):
        self.commands.append(("home",))
        self.homed = True
        return self.where()

    def speed(self, percent):
        self.commands.append(("speed", percent))

    def move_by(self, dx=0.0, dy=0.0, dz=0.0):
        self.commands.append(("move_by", dx, dy, dz))
        self.x, self.y, self.z = self.x + dx, self.y + dy, self.z + dz
        return self.where()

    def move_to(self, x, y, z, pitch=None, roll=None):
        if not self.homed:
            raise ArmError("move_to needs a Home in this session first")
        self.commands.append(("move_to", x, y, z))
        self.x, self.y, self.z = x, y, z
        return self.where()

    def gripper(self, open):        # noqa: A002
        self.commands.append(("gripper", open))
        self.open = open
        return self.where()

    def stop(self):
        self.commands.append(("stop",))

    def quit(self):
        self.commands.append(("quit",))

    def teach(self, name, positions=POSITIONS, box=BOX):
        return teach(self, name, positions, box)

    def go(self, name, positions=POSITIONS):
        return go(self, name, positions)


def _load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def _save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def widen_box(path, points):
    """Grow the box in `path` to hold every point (x, y, z in mm). The box is where the arm has been driven
    under supervision; the server refuses move_to outside it."""
    data = _load(path)
    box = data.get("box_mm", {})
    for point in points:
        for axis, value in zip("xyz", point):
            low, high = box.get(axis, [value, value])
            box[axis] = [min(low, value), max(high, value)]
    data["box_mm"] = box
    _save(path, data)
    return box


def teach(arm, name, positions=POSITIONS, box=BOX):
    """Save the arm's current position under `name`; the box grows to include it (the arm is already there)."""
    here = arm.where()
    data = _load(positions)
    data[name] = {"x": here.x, "y": here.y, "z": here.z, "pitch": here.pitch, "roll": here.roll}
    _save(positions, data)
    widen_box(box, [(here.x, here.y, here.z)])
    return here


def go(arm, name, positions=POSITIONS):
    data = _load(positions)
    if name not in data:
        raise ArmError(f"no taught position {name!r} in {positions}; known: {sorted(data)}")
    point = data[name]
    return arm.move_to(point["x"], point["y"], point["z"], point.get("pitch"), point.get("roll"))


def main(argv=None):
    parser = argparse.ArgumentParser(description="One command to the arm server")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("command", choices=("where", "by", "to", "open", "close", "speed", "home",
                                            "teach", "go", "stop", "quit"))
    parser.add_argument("args", nargs="*")
    args = parser.parse_args(argv)
    needs = {"by": 3, "to": 3, "speed": 1, "teach": 1, "go": 1}.get(args.command, 0)
    if len(args.args) != needs:
        parser.error(f"{args.command} takes {needs} value(s)")
    numbers = args.args if args.command in ("teach", "go") else [float(value) for value in args.args]
    try:
        with Arm(port=args.port) as arm:
            result = {
                "where": lambda: arm.where(),
                "by": lambda: arm.move_by(*numbers),
                "to": lambda: arm.move_to(*numbers),
                "open": lambda: arm.gripper(open=True),
                "close": lambda: arm.gripper(open=False),
                "speed": lambda: arm.speed(int(numbers[0])),
                "home": lambda: arm.home(),
                "teach": lambda: arm.teach(numbers[0]),
                "go": lambda: arm.go(numbers[0]),
                "stop": lambda: arm.stop(),
                "quit": lambda: arm.quit(),
            }[args.command]()
    except ArmError as error:
        print(f"refused: {error}")
        return 1
    if isinstance(result, Position):
        print(f"x {result.x:.1f}  y {result.y:.1f}  z {result.z:.1f} mm   pitch {result.pitch}  roll {result.roll}")
        print(f"encoder {result.encoder}")
    else:
        print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
