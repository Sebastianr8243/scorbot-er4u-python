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

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

HOST, PORT = "127.0.0.1", 8765          # tools/usbc_server PORT
XYZ_UNITS_PER_MM = 1000     # the DLL's xyz unit: a 10 mm move_by read back as 9,996 on the fake controller (2026-10-20). Physical scale unverified; arm_characterise.py measures it
POSITIONS = Path("calibration") / "positions.json"
BOX = Path("calibration") / "arm.json"
REPLY_TIMEOUT_S = 900.0     # a Home takes about 135 s on the lab arm; the server bounds every call itself
POLL_S = 0.2                # a read that blocks for the whole timeout is not woken by Ctrl+C on Windows (checked 2026-10-26), so wake this often
LINE_END = bytes([10])      # one JSON object per line


# The server's exit codes (tools/usbc_home, tools/usbc_move) as plain words.
FAILURE_CODES = {
    4: "the DLL refused to set the move up",
    6: "it ran past its time limit; Stop was asked for but is not confirmed, so the arm may still be moving: use the physical stop",
    7: "it was refused or stopped (not homed, outside the DLL's workspace, or Stop was pressed)",
    8: "a DLL call did not return: the arm may still be moving, use the physical stop",
}


def describe_failure(op, reply):
    code = reply.get("code")
    return f"{op} failed: {FAILURE_CODES[code]} (code {code})" if code in FAILURE_CODES else f"{op} failed (code {code})"


class ArmError(Exception):
    """The server refused a command or the DLL call failed. `reply` is the server's whole answer."""

    def __init__(self, message: str, reply: dict | None = None):
        super().__init__(message)
        self.reply = reply or {}


@dataclass(frozen=True)
class Position:
    """Where the arm is: x, y, z in mm (DLL task frame), pitch and roll in the DLL's units, and the raw arrays."""

    x: float
    y: float
    z: float
    pitch: int = 0          # the DLL's own units, passed through unchanged
    roll: int = 0
    encoder: list = field(default_factory=list)
    joint: list = field(default_factory=list)
    xyz: list = field(default_factory=list)
    timing: dict = field(default_factory=dict)      # seconds the server spent per step of a move or gripper call

    @classmethod
    def from_reply(cls, reply: dict) -> Position | None:
        """A Position from a server reply, or None when the reply carries no xyz (ping, speed, quit)."""
        xyz = reply.get("xyz")
        if not xyz:
            return None
        x, y, z = (value / XYZ_UNITS_PER_MM for value in xyz[:3])
        return cls(x, y, z, xyz[3], xyz[4], reply.get("encoder", []), reply.get("joint", []), xyz,
                   reply.get("timing_s", {}))


class Arm:
    """A connection to tools/usbc_server. Use as a context manager so the socket closes."""

    def __init__(self, host: str = HOST, port: int = PORT, timeout: float = REPLY_TIMEOUT_S):
        try:
            self._sock = socket.create_connection((host, port), timeout=10)
        except OSError as error:
            raise ArmError(f"no arm server on {host}:{port} ({error}); start tools/usbc_server first") from None
        self._sock.settimeout(POLL_S)
        self._reply_timeout, self._buffer = timeout, b""
        self._next_id = 0

    def close(self):
        self._sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    def _write(self, command):
        self._sock.sendall((json.dumps(command, allow_nan=False) + "\n").encode("utf-8"))

    def _readline(self) -> bytes:
        """The next line from the server. Polls in POLL_S slices so Ctrl+C is seen while a move runs."""
        deadline = time.monotonic() + self._reply_timeout
        while LINE_END not in self._buffer:
            try:
                data = self._sock.recv(4096)
            except socket.timeout:
                if time.monotonic() > deadline:
                    raise ArmError(f"no reply from the arm server in {self._reply_timeout:g} s; the arm may still be moving: use the physical stop") from None
                continue
            if not data:
                raise ArmError("the arm server closed the connection")
            self._buffer += data
        line, self._buffer = self._buffer.split(LINE_END, 1)
        return line

    def send(self, op: str, **fields) -> dict:
        """One command and its reply. Raises ArmError when the reply is not ok."""
        self._next_id += 1
        self._write({"op": op, "id": self._next_id, **fields})
        try:
            while True:
                reply = json.loads(self._readline())
                if reply.get("busy"):       # another client holds the server; the line has no id
                    raise ArmError(reply.get("error") or "the arm server is busy", reply)
                if reply.get("id") == self._next_id:     # anything else answers an earlier stop
                    break
        except KeyboardInterrupt:       # Ctrl+C while the arm moves: stop it before giving up the call
            self.stop()
            raise
        if not reply.get("ok"):
            detail, message = reply.get("error"), describe_failure(op, reply)
            if detail and reply.get("code") not in FAILURE_CODES:
                message = detail
            elif detail:    # keep the mapped warning (code 8 says use the physical stop) and add the server's own words
                message = f"{message}; server said: {detail}"
            raise ArmError(message, reply)
        return reply

    def _position(self, op: str, **fields) -> Position:
        """send() for the ops whose reply carries the arm's position; a reply without it is an error."""
        reply = self.send(op, **fields)
        position = Position.from_reply(reply)
        if position is None:
            raise ArmError(f"{op} reply carried no position", reply)
        return position

    def ping(self):
        return self.send("ping")

    def where(self) -> Position:
        """Current position."""
        return self._position("where")

    def home(self) -> Position | None:
        """Vendor Home: about 135 s on the lab arm, closes the gripper and moves the wrist. The reply may carry no position."""
        return Position.from_reply(self.send("home"))

    def speed(self, percent: int) -> None:
        """Whole percent of top speed, 1 to 100."""
        self.send("speed", percent=percent)

    def move_by(self, dx: float = 0.0, dy: float = 0.0, dz: float = 0.0) -> Position:
        """Relative move in mm; the server refuses more than 50 mm on any axis, or a step that leaves the measured box."""
        return self._position("move_by", dx=dx, dy=dy, dz=dz)

    def wrist_by(self, dpitch: float = 0.0, droll: float = 0.0) -> Position:
        """Relative wrist step in the DLL's own pitch and roll units (probably 0.001 degree; unverified). At most 10000 per axis."""
        return self._position("wrist_by", dpitch=dpitch, droll=droll)

    def move_to(self, x: float, y: float, z: float, pitch: int | None = None, roll: int | None = None) -> Position:
        """Absolute move in mm. Refused before a Home, without a box file, or outside the box."""
        extra = {key: value for key, value in (("pitch", pitch), ("roll", roll)) if value is not None}
        return self._position("move_to", x=x, y=y, z=z, **extra)

    def gripper(self, open: bool) -> Position:        # noqa: A002 - reads as arm.gripper(open=True)
        """Open (True) or close (False) the gripper."""
        return self._position("gripper", open=open)

    def stop(self) -> None:
        """Ask the server to Stop. Safe from another thread while a move waits for its reply."""
        self._write({"op": "stop"})

    def quit(self) -> None:
        """End the server's session: Control off and CloseUSBC."""
        self.send("quit")

    def run(self, commands: list[dict]) -> list[dict]:
        """Send {"op": ..., ...} commands in order; stops at the first failure (ArmError)."""
        return [self.send(**command) for command in commands]

    def teach(self, name: str, positions: Path = POSITIONS, box: Path = BOX) -> Position:
        return teach(self, name, positions, box)

    def go(self, name: str, positions: Path = POSITIONS) -> Position:
        return go(self, name, positions)


class FakeArm:
    """Arm without a server: positions in memory, for laptops and tests. Never talks to a DLL.

    Same calls as Arm. No server-side limits are applied, except that move_to needs a Home.
    """

    def __init__(self, start=(300.0, 0.0, 200.0), homed=True):
        self.x, self.y, self.z = start
        self.pitch = self.roll = 0
        self.homed, self.open, self.commands = homed, True, []

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        pass

    def where(self):
        return Position(self.x, self.y, self.z, self.pitch, self.roll)

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

    def wrist_by(self, dpitch=0.0, droll=0.0):
        self.commands.append(("wrist_by", dpitch, droll))
        self.pitch, self.roll = self.pitch + dpitch, self.roll + droll
        return self.where()

    def move_to(self, x, y, z, pitch=None, roll=None):
        if not self.homed:
            raise ArmError("move_to needs a Home in this session first")
        self.commands.append(("move_to", x, y, z))
        self.x, self.y, self.z = x, y, z
        self.pitch = self.pitch if pitch is None else pitch
        self.roll = self.roll if roll is None else roll
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


def widen_box(path, points, measured=False):
    """Grow the box in `path` to hold every point (x, y, z in mm). The box is where the arm has been driven
    under supervision; the server refuses move_to outside it. measured=True (arm_characterise.py) also makes
    the server refuse a move_by that leaves it; a box grown only by teach does not."""
    data = _load(path)
    box = data.get("box_mm", {})
    for point in points:
        for axis, value in zip("xyz", point):
            low, high = box.get(axis, [value, value])
            box[axis] = [min(low, value), max(high, value)]
    data["box_mm"] = box
    data["measured"] = bool(data.get("measured")) or measured
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
    parser.add_argument("--calibration-dir", default="calibration",
                        help="folder for positions.json and arm.json used by teach and go (one per arm; default calibration)")
    parser.add_argument("command", choices=("where", "by", "wrist", "to", "open", "close", "speed", "home",
                                            "teach", "go", "stop", "quit"))
    parser.add_argument("args", nargs="*")
    args = parser.parse_args(argv)
    needs = {"by": 3, "wrist": 2, "to": 3, "speed": 1, "teach": 1, "go": 1}.get(args.command, 0)
    if len(args.args) != needs:
        parser.error(f"{args.command} takes {needs} value(s)")
    positions, box = Path(args.calibration_dir) / POSITIONS.name, Path(args.calibration_dir) / BOX.name
    numbers = args.args if args.command in ("teach", "go") else [float(value) for value in args.args]
    try:
        with Arm(port=args.port) as arm:
            result = {
                "where": lambda: arm.where(),
                "by": lambda: arm.move_by(*numbers),
                "wrist": lambda: arm.wrist_by(*numbers),
                "to": lambda: arm.move_to(*numbers),
                "open": lambda: arm.gripper(open=True),
                "close": lambda: arm.gripper(open=False),
                "speed": lambda: arm.speed(int(numbers[0])),
                "home": lambda: arm.home(),
                "teach": lambda: arm.teach(numbers[0], positions, box),
                "go": lambda: arm.go(numbers[0], positions),
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
