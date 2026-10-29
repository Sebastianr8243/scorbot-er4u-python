"""Move the arm one small step to a point through the vendor's USBC.dll. Lab tool, standard library only.

Spec: specs/2026-10-16-vendor-dll-point-move-plan.md (in git history). Needs a 32-bit Python. The call order is the
USNA ScorBot Toolbox's (Kutzer: RDefineVector, RAddToVecXYZPR, RMoveJoint, then wait), which is
DefineVector, Teach, MoveJoint in USBC.dll (tools/usbc_analysis robotdll.c). Unverified on the arm.

    python32 usbc_move.py --dll-dir DIR --probe
    python32 usbc_move.py --dll-dir DIR --simulate --axis x --mm 5
    python32 usbc_move.py --dll-dir DIR --online --axis x --mm 5

The target is a RELATIVE point (x, y, z in micrometres, as the vendor header says), so a wrong unit makes
the step smaller or larger by a fixed factor rather than sending the arm to an absolute pose. The log has
the position before and after; compare them with the request before you raise --mm. Needs a home first
(the DLL refuses a move before one is a normal outcome; the tool reports it and stops).

Reuses tools/usbc_home (fingerprint, bounded calls, stop bookkeeping, event log, clean-up). --online
energises the motors and moves the arm. Stop (s) here is not an emergency stop; the physical stop is.
The log goes to logs\\ (rehearsal\\ when simulated) unless --output names a file.

Only the functions in SIGNATURES are bound: no Home, SetHome, SetJoint, MoveManual, MoveLinear, torque or
gripper call.
"""

import argparse
import ctypes
import importlib.util
import os
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, *parts):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_HERE, "..", *parts))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


home = _load("usbc_home", "usbc_home", "usbc_home.py")
jog = _load("usbc_jog", "usbc_jog", "usbc_jog.py")

VECTOR = "OSC"                   # the vendor allows up to 16 characters
VECTOR_POINTS = 2
POINT = 1
ABSOLUTE_FLAG, RELATIVE_FLAG = -0x7FFE, -0x7FFF     # RAddToVecXYZPR: 0 absolute, 1 relative (robotdll.c)
MAX_MM = 20.0                    # first-tool bound; raise it only after the log shows the unit
MOVE_TIMEOUT_S = 30.0
SETTLE_S = 0.4
SETTLE_POLLS = 3
SETTLE_TIMEOUT_S = 10.0
AXES = {"x": 0, "y": 1, "z": 2}

_pchar, _long, _short = ctypes.c_char_p, ctypes.c_long, home._short
SIGNATURES = {name: home.SIGNATURES[name] for name in
              ("Initialization", "IsEmergency", "Control", "Stop", "CloseUSBC")}
SIGNATURES.update({
    "GetCurrentPosition": jog.SIGNATURES["GetCurrentPosition"],
    "DefineVector": ("?DefineVector@@YAHEPADF@Z", (home._uc, _pchar, _short), home._int),
    "Teach": ("?Teach@@YAHPADFPAJFJ@Z", (_pchar, _short, ctypes.POINTER(_long * 5), _short, _long), home._int),
    "MoveJoint": ("?MoveJoint@@YAHPADF0F@Z", (_pchar, _short, _pchar, _short), home._int),
})


class MoveDll(home.VendorDll):
    signatures = SIGNATURES

    def define_vector(self, name, points):
        return bool(self._fn["DefineVector"](ord(home.HOME_GROUP), name.encode("ascii"), points))

    def teach_relative(self, name, point, values):
        array = (_long * 5)(*values)
        return bool(self._fn["Teach"](name.encode("ascii"), point, ctypes.pointer(array), 5, RELATIVE_FLAG))

    def move_joint(self, name, point):
        return bool(self._fn["MoveJoint"](name.encode("ascii"), point, None, 0))

    def position(self):
        """The DLL's three position arrays (encoder, joint, XYZ). Units are not stated anywhere."""
        arrays = [(_long * jog.AXES)() for _ in range(3)]
        ok = bool(self._fn["GetCurrentPosition"](*(ctypes.pointer(a) for a in arrays)))
        return {"ok": ok, **dict(zip(("encoder", "joint", "xyz"), (list(a) for a in arrays)))}


def _settle(dll, log):
    """Read the position until it has not changed for SETTLE_POLLS reads. Returns the last reading."""
    last, same, deadline = None, 0, time.monotonic() + SETTLE_TIMEOUT_S
    while time.monotonic() < deadline:
        now = jog.call(dll.position, {"call_open": 0})     # a read after the move: never keeps it busy
        same = same + 1 if last and now["encoder"] == last["encoder"] else 0
        if same >= SETTLE_POLLS:
            return now
        last = now
        time.sleep(SETTLE_S)
    log.event("settle_timeout", seconds=SETTLE_TIMEOUT_S)
    return last


def make_action(axis, mm):
    delta = [0, 0, 0, 0, 0]
    delta[AXES[axis]] = round(mm * 1000)        # micrometres, from the vendor header (RobotDll.h)

    def action(dll, log, console, state):
        return move_point(dll, log, console, state, lambda: dll.teach_relative(VECTOR, POINT, delta),
                          {"delta_um": delta, "relative": True}, {"axis": axis, "mm": mm})
    return action


def move_point(dll, log, console, state, teach, taught, start, define=True):
    """DefineVector (unless define is False), teach() point 1, MoveJoint to it, settle. Returns an exit code;
    the position after the move is in state["position"]. Shared with tools/usbc_server."""
    state["busy"] = True        # until every bounded call below is proven finished
    state["call_open"] = 0
    result = {}
    timing = state["timing_s"] = {"define": 0.0}     # seconds per step, for the server's reply
    mark = time.monotonic()

    def lap(step):
        nonlocal mark
        now = time.monotonic()
        timing[step], mark = round(now - mark, 4), now

    try:
        before = jog.call(dll.position, state)
        log.event("position_before", **before)
        lap("read")
        if define:
            if not jog.call(lambda: dll.define_vector(VECTOR, VECTOR_POINTS), state):
                log.event("define_vector_refused")
                state["busy"] = False
                return 4
            state["vector_defined"] = True
            lap("define")
        if not jog.call(teach, state):
            log.event("teach_refused", **taught)
            state["busy"] = False
            return 4
        log.event("point_taught", **taught)
        lap("teach")
        worker = threading.Thread(target=lambda: result.update(ok=dll.move_joint(VECTOR, POINT)), daemon=True)
        log.event("move_start", **start)
        worker.start()
        code = _supervise(dll, log, console, state, worker, result)
        lap("move")
        if not worker.is_alive():
            after = _settle(dll, log)
            lap("settle")
            state["position"] = after
            log.event("position_after", **after)
            log.event("position_delta", **{key: [a - b for a, b in zip(after[key], before[key])]
                                           for key in ("encoder", "joint", "xyz")})
            state["busy"] = state["stop_open"] > 0 or state["call_open"] > 0
        return code
    except jog.Hung:
        log.event("move_call_hang", seconds=jog.CALL_S)
        state["busy_note"] = "A MOVE CALL DID NOT RETURN: the arm may still be moving. USE THE PHYSICAL STOP NOW."
        return 8


def _supervise(dll, log, console, state, worker, result):
    deadline, stopped = time.monotonic() + MOVE_TIMEOUT_S, False
    try:
        while worker.is_alive():
            if not stopped and console.stop_pressed():
                home.request_stop(dll, log, state)
                stopped, deadline = True, time.monotonic() + home.STOP_GRACE_S
            if time.monotonic() > deadline:
                log.event("move_timeout")
                home.request_stop(dll, log, state)
                worker.join(home.STOP_GRACE_S)
                return 6
            time.sleep(0.05)
    except BaseException:       # Ctrl+C while supervising: the move may still be running
        log.event("move_interrupted")
        home.request_stop(dll, log, state)
        worker.join(home.STOP_GRACE_S)
        raise
    log.event("move_returned", ok=result.get("ok"), stopped=stopped)
    if stopped:
        return 7
    if not result.get("ok"):    # a refusal (no home yet, point outside the workspace) is a normal outcome
        return 7
    return 0


def probe(dll_dir, log):
    lib, path = home.load_library(dll_dir)
    log.event("dll", **home.file_facts(path), python=sys.version.split()[0], ini=home.ini_facts(dll_dir))
    MoveDll(lib)
    log.event("exports_resolved", names=list(SIGNATURES))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Move to a point through the vendor's USBC.dll (lab tool)")
    parser.add_argument("--dll-dir", required=True, help="folder holding USBC.dll and its ini files")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--probe", action="store_true")
    mode.add_argument("--simulate", action="store_true")
    mode.add_argument("--online", action="store_true")
    parser.add_argument("--output", help="JSONL log to create (refuses to overwrite); default logs/move-<time>.jsonl")
    parser.add_argument("--operator", help="initials, written to the log")
    parser.add_argument("--axis", choices=sorted(AXES), help="x, y or z of the vendor's task frame (unverified)")
    parser.add_argument("--mm", type=float, default=5.0, help=f"signed step in millimetres, 0 to {MAX_MM:g} in size")
    args = parser.parse_args(argv)
    if not args.probe:
        if args.axis is None:
            parser.error("--axis is required with --simulate and --online")
        if not 0 < abs(args.mm) <= MAX_MM:
            parser.error(f"--mm must be above 0 and at most {MAX_MM:g} in size, either sign")
        if not args.output:
            args.output = home.default_output(f"move-{args.axis}", args.simulate)
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
        return home.run(MoveDll(lib), log, args.simulate, home.Console(),
                        action=make_action(args.axis, args.mm))
    except home.ToolError as error:
        log.event("error", message=str(error))
        return 1
    finally:
        log.close()


if __name__ == "__main__":
    sys.exit(main())
