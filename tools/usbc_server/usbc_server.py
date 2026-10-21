"""Keep one vendor USBC.dll session open and run arm commands sent over a local socket. Lab tool, standard library only.

Plan: docs/requirements/arm-api.md. Needs a 32-bit Python. The Python API (scorbot/bridge.py, any Python) is the
client. One session: Initialization, Control on, Home, then serve commands until "quit"; Control off and CloseUSBC
on every exit (tools/usbc_home run()). A client that disconnects leaves the session open for the next one, so the
arm homes once and many scripts can follow.

    python32 usbc_server.py --dll-dir DIR --probe
    python32 usbc_server.py --dll-dir DIR --simulate
    python32 usbc_server.py --dll-dir DIR --online [--no-home] [--port 8765]

Protocol: one JSON object per line, each with an "op" and an optional "id" that the reply echoes.
    ping | where | home | speed {percent} | move_by {dx, dy, dz} | move_to {x, y, z[, pitch, roll]}
    gripper {open: true|false} | stop | quit
Distances are mm (sent to Teach as micrometres, the decided unit). pitch and roll are passed through in the DLL's
own units. Every reply after a DLL call carries the encoder, joint and xyz arrays. move_to is refused until this
session has homed; the DLL's own workspace is the only other limit. A "stop" line during a move calls Stop.
--online energises the motors and moves the arm; Stop here is not an emergency stop, the physical stop is. The log
goes to logs\\ unless --output names a file.
"""

import argparse
import ctypes
import importlib.util
import json
import os
import socket
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("usbc_move", os.path.join(_HERE, "..", "usbc_move", "usbc_move.py"))
move = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(move)
home, jog = move.home, move.jog

PORT = 8765
HOST = "127.0.0.1"              # never another interface: anyone who can connect can move the arm
MAX_STEP_MM = 50.0              # largest move_by on one axis
GRIPPER_CALL_S = 10.0           # whether OpenGripper returns before the jaws stop is unread
IDLE_POLL_S = 0.2
UM_PER_MM = 1000                # Teach's unit, micrometres (RobotDll.h; decided 2026-10-20, unverified on the arm)

SIGNATURES = {**{name: home.SIGNATURES[name] for name in
                 ("Initialization", "IsEmergency", "Control", "Home", "Stop", "CloseUSBC")},
              **{name: move.SIGNATURES[name] for name in ("GetCurrentPosition", "DefineVector", "Teach", "MoveJoint")},
              # read from the lab build's export table (SHA-256 6118465d...), not called before this tool
              "OpenGripper": ("?OpenGripper@@YAHXZ", (), home._int),
              "CloseGripper": ("?CloseGripper@@YAHXZ", (), home._int),
              "Speed": ("?Speed@@YAHEJ@Z", (home._uc, ctypes.c_long), home._int)}


class ServerDll(move.MoveDll):
    signatures = SIGNATURES

    def teach_absolute(self, name, point, values):
        array = (ctypes.c_long * 5)(*values)
        return bool(self._fn["Teach"](name.encode("ascii"), point, ctypes.pointer(array), 5, move.ABSOLUTE_FLAG))

    def gripper(self, opening):
        return bool(self._fn["OpenGripper" if opening else "CloseGripper"]())

    def speed(self, percent):
        return bool(self._fn["Speed"](ord(home.HOME_GROUP), percent))


class Refused(Exception):
    """A command the server will not run; the reply says why and nothing was sent to the DLL."""


class Channel:
    """JSON lines on one client socket. Lines that arrive during a move wait in `pending`."""

    def __init__(self, conn):
        self.conn, self.buffer, self.pending = conn, b"", []

    def _read(self, timeout):
        self.conn.settimeout(timeout)
        try:
            data = self.conn.recv(4096)
        except (BlockingIOError, socket.timeout):
            return True
        except OSError:
            return False
        if not data:
            return False
        self.buffer += data
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            if line.strip():
                self.pending.append(line)
        return True

    def next_line(self, timeout):
        """The next line, None when nothing came in `timeout`; raises EOFError when the client has gone."""
        if not self.pending and not self._read(timeout):
            raise EOFError
        return self.pending.pop(0) if self.pending else None

    def stop_waiting(self):
        """True (and the line consumed) if a stop arrived, or the client went away (Ctrl+C in a script)."""
        if not self._read(0):
            return True
        for index, line in enumerate(self.pending):
            try:
                if json.loads(line).get("op") == "stop":
                    del self.pending[index]
                    return True
            except (ValueError, AttributeError):
                continue
        return False

    def send(self, reply):
        try:
            self.conn.sendall((json.dumps(reply, allow_nan=False) + "\n").encode("utf-8"))
        except OSError:
            pass        # the client went away; the next read ends this connection


class ServerConsole(home.Console):
    """The stop key, or a "stop" line from the client, while a move or Home runs."""

    def __init__(self, keys):
        self.keys, self.channel = keys, None

    def stop_pressed(self):
        return self.keys.stop_pressed() or bool(self.channel and self.channel.stop_waiting())


def _number(command, key, default=None):
    value = command.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or abs(value) == float("inf"):
        raise Refused(f"{key} must be a finite number")
    return float(value)


class Server:
    def __init__(self, dll, log, console, state, homed):
        self.dll, self.log, self.console, self.state = dll, log, console, state
        self.homed = homed
        state.setdefault("call_open", 0)

    def handle(self, command):
        """Run one command. Returns (reply fields, exit code or None to keep serving)."""
        op = command.get("op")
        if op == "ping":
            return {"homed": self.homed}, None
        if op == "quit":
            return {}, 0
        if op == "stop":
            home.request_stop(self.dll, self.log, self.state)
            return {}, None
        if op == "where":
            return self._position(), None
        if op == "home":
            code = home._home(self.dll, self.log, self.console, self.state)
            self.homed = self.homed or code == 0    # a failed re-home leaves the frame as the last good Home set it
            return self._result(code), None
        if op == "speed":
            percent = _number(command, "percent")
            if not 1 <= percent <= 100 or percent != int(percent):
                raise Refused("percent must be a whole number from 1 to 100")
            return {"ok": jog.call(lambda: self.dll.speed(int(percent)), self.state)}, None
        if op == "gripper":
            if not isinstance(command.get("open"), bool):
                raise Refused("gripper needs open: true or false")
            return self._gripper(command["open"]), None
        if op == "move_by":
            return self._move_by(command), None
        if op == "move_to":
            return self._move_to(command), None
        raise Refused(f"unknown op {op!r}")

    def _position(self):
        return jog.call(self.dll.position, self.state)

    def _result(self, code):
        reply = {"ok": code == 0, "code": code}
        if self.state.get("position"):
            reply.update({key: self.state["position"][key] for key in ("encoder", "joint", "xyz")})
        return reply

    def _check_emergency(self):
        if jog.call(self.dll.emergency, self.state):
            raise Refused("the emergency stop is active")

    def _move(self, teach, taught, start):
        self._check_emergency()
        self.state["position"] = None
        code = move.move_point(self.dll, self.log, self.console, self.state, teach, taught, start,
                               define=not self.state.get("vector_defined"))    # once per session: a second define is unread
        return self._result(code)

    def _move_by(self, command):
        delta = [_number(command, key, 0) for key in ("dx", "dy", "dz")]
        if not any(delta):
            raise Refused("move_by needs a non-zero dx, dy or dz")
        if max(abs(d) for d in delta) > MAX_STEP_MM:
            raise Refused(f"each of dx, dy, dz must be at most {MAX_STEP_MM:g} mm")
        values = [round(d * UM_PER_MM) for d in delta] + [0, 0]
        return self._move(lambda: self.dll.teach_relative(move.VECTOR, move.POINT, values),
                          {"delta_um": values, "relative": True}, {"op": "move_by", "mm": delta})

    def _move_to(self, command):
        if not self.homed:
            raise Refused("move_to needs a Home in this session first")
        target = {axis: _number(command, axis) for axis in "xyz"}
        now = jog.call(self.dll.position, self.state)
        if not now["ok"]:
            raise Refused("could not read the current pitch and roll")
        pitch = round(_number(command, "pitch", now["xyz"][3]))
        roll = round(_number(command, "roll", now["xyz"][4]))
        values = [round(target[axis] * UM_PER_MM) for axis in "xyz"] + [pitch, roll]
        return self._move(lambda: self.dll.teach_absolute(move.VECTOR, move.POINT, values),
                          {"point_um": values, "relative": False}, {"op": "move_to", "mm": target})

    def _gripper(self, opening):
        self._check_emergency()
        self.state["busy"] = True
        self.state["call_open"] += 1
        finished, ok, error = home.bounded(lambda: self.dll.gripper(opening), GRIPPER_CALL_S)
        if not finished:
            self.log.event("gripper_call_hang", seconds=GRIPPER_CALL_S)
            self.state["busy_note"] = "A GRIPPER CALL DID NOT RETURN: the gripper may still be moving. USE THE PHYSICAL STOP NOW."
            return {"ok": False, "code": 8}
        self.state["call_open"] -= 1
        if error is not None:
            raise error
        self.log.event("gripper", open=opening, ok=ok)
        self.state["position"] = move._settle(self.dll, self.log) if ok else None
        self.state["busy"] = self.state["stop_open"] > 0 or self.state["call_open"] > 0
        return self._result(0 if ok else 7)


def serve(server, listener, log):
    """Accept clients one at a time until quit. Returns the exit code for home.run()."""
    while True:
        try:
            conn, peer = listener.accept()
        except socket.timeout:
            if server.console.keys.stop_pressed():       # s with no client: nothing moving, nothing to stop
                log.event("stop_key_idle")
            continue
        log.event("client_connected", peer=list(peer))
        channel = Channel(conn)
        server.console.channel = channel
        try:
            code = _serve_client(server, channel, log)
        finally:
            server.console.channel = None
            conn.close()
        log.event("client_disconnected")
        if code is not None:
            return code


def _serve_client(server, channel, log):
    while True:
        try:
            line = channel.next_line(IDLE_POLL_S)
        except EOFError:
            return None
        if line is None:
            continue
        request_id = None
        log.event("command", line=line.decode("utf-8", "replace"))     # raw: client fields never reach event()'s kwargs
        try:
            command = json.loads(line)
            if not isinstance(command, dict):
                raise Refused("each line must be a JSON object")
            request_id = command.get("id")
            reply, code = server.handle(command)
        except Refused as error:
            log.event("command_refused", reason=str(error))
            reply, code = {"ok": False, "error": str(error)}, None
        except ValueError as error:
            reply, code = {"ok": False, "error": f"not JSON: {error}"}, None
        except jog.Hung:
            server.state["busy_note"] = "A DLL CALL DID NOT RETURN: the arm may still be moving. USE THE PHYSICAL STOP NOW."
            reply, code = {"ok": False, "code": 8, "error": "a DLL call did not return"}, 8
        reply.setdefault("ok", True)
        if request_id is not None:
            reply["id"] = request_id
        channel.send(reply)
        if server.state["busy"]:        # a call may still run inside the DLL: stop serving, run() skips shutdown
            log.event("serve_ended_busy")
            return 8
        if code is not None:
            return code


def make_action(port, do_home, ready=None):
    def action(dll, log, console, state):
        state["call_open"] = 0
        homed = False
        if do_home:
            code = home._home(dll, log, console, state)
            if code != 0:
                return code
            homed = True
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.bind((HOST, port))
            listener.listen(1)
            listener.settimeout(IDLE_POLL_S)
            log.event("serving", host=HOST, port=listener.getsockname()[1], homed=homed)
            if ready:
                ready(listener.getsockname()[1])
            server = Server(dll, log, ServerConsole(console), state, homed)
            return serve(server, listener, log)
        finally:
            listener.close()
    return action


def probe(dll_dir, log):
    lib, path = home.load_library(dll_dir)
    log.event("dll", **home.file_facts(path), python=sys.version.split()[0], ini=home.ini_facts(dll_dir))
    ServerDll(lib)
    log.event("exports_resolved", names=list(SIGNATURES))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Serve arm commands through the vendor's USBC.dll (lab tool)")
    parser.add_argument("--dll-dir", required=True, help="folder holding USBC.dll and its ini files")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--probe", action="store_true")
    mode.add_argument("--simulate", action="store_true")
    mode.add_argument("--online", action="store_true")
    parser.add_argument("--output", help="JSONL log to create (refuses to overwrite); default logs/server-<time>.jsonl")
    parser.add_argument("--operator", help="initials, written to the log")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--no-home", action="store_true",
                        help="skip the Home at start; move_to stays refused until a home command")
    args = parser.parse_args(argv)
    if not args.probe and not args.output:
        args.output = home.default_output("server", args.simulate)
    log = home.EventLog(args.output, "vendor_dll_simulated" if args.simulate else "vendor_dll")
    try:
        log.event("start", mode="probe" if args.probe else "simulate" if args.simulate else "online",
                  operator=args.operator)
        if args.probe:
            return probe(args.dll_dir, log)
        lib, path = home.load_library(args.dll_dir)
        facts = home.file_facts(path)
        log.event("dll", **facts, python=sys.version.split()[0], ini=home.ini_facts(args.dll_dir))
        if args.online:
            home.check_fingerprint(facts["sha256"], log)
        return home.run(ServerDll(lib), log, args.simulate, home.Console(),
                        action=make_action(args.port, not args.no_home))
    except home.ToolError as error:
        log.event("error", message=str(error))
        return 1
    finally:
        log.close()


if __name__ == "__main__":
    sys.exit(main())
