"""Jog ONE axis through the vendor's USBC.dll and nothing else. Lab tool, standard library only.

Spec: docs/specs/2026-10-13-vendor-dll-jog-plan.md. Needs a 32-bit Python. From the SDK
header, the vendor description and the vendor demo; unverified on the arm.

    python32 usbc_jog.py --dll-dir DIR --probe
    python32 usbc_jog.py --dll-dir DIR --simulate --axis 0
    python32 usbc_jog.py --dll-dir DIR --online --axis 0 --velocity 5 --seconds 1

Reuses tools/usbc_home (fingerprint, bounded calls, stop bookkeeping, event log, clean-up). --online
energises the motors and moves the arm. Stop (s) here is not an emergency stop; the physical stop is.
The log goes to logs\\ (rehearsal\\ when simulated) unless --output names a file.

Only the functions in SIGNATURES are bound: no Home, SetHome, SetJoint, Impact, Move*, torque or gripper.
"""

import argparse
import ctypes
import importlib.util
import os
import sys
import time

_HOME = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "usbc_home", "usbc_home.py")
_spec = importlib.util.spec_from_file_location("usbc_home", _HOME)
home = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(home)

MANUAL_TYPE_ENC = 0          # USBCDEF.h:22
MOVE_PERIOD_S = 0.3          # the vendor demo re-sends MoveManual every 300 ms (ManualMoveDlg.h:19)
POLL_S = 0.05
CALL_S = 3.0                 # a jog call that does not return in this long is taken to be stuck
MAX_SECONDS = 5.0
MAX_VELOCITY = 100           # percent of the axis's INI speed, sign = direction (VENDOR_DLL_PROTOCOL.md, from disassembly)
AXES = 8                     # RobotData is long[TOTAL_AXES] (USBCDEF.h:13)

_arrays = ctypes.POINTER(ctypes.c_long * AXES)
SIGNATURES = {name: home.SIGNATURES[name] for name in
              ("Initialization", "IsEmergency", "Control", "Stop", "CloseUSBC")}
SIGNATURES.update({
    "EnterManual": ("?EnterManual@@YAHF@Z", (home._short,), home._int),
    "MoveManual": ("?MoveManual@@YAHEJ@Z", (home._uc, ctypes.c_long), home._int),
    "CloseManual": ("?CloseManual@@YAHXZ", (), home._int),
    "GetCurrentPosition": ("?GetCurrentPosition@@YAHPAY07J00@Z", (_arrays, _arrays, _arrays), home._int),
})


class JogDll(home.VendorDll):
    signatures = SIGNATURES

    def enter_manual(self):
        return bool(self._fn["EnterManual"](MANUAL_TYPE_ENC))

    def move_manual(self, axis, velocity):
        return bool(self._fn["MoveManual"](axis, velocity))

    def close_manual(self):
        return bool(self._fn["CloseManual"]())

    def position(self):
        """The DLL's three position arrays (encoder, joint, XYZ). Units are not stated anywhere."""
        arrays = [(ctypes.c_long * AXES)() for _ in range(3)]
        ok = bool(self._fn["GetCurrentPosition"](*(ctypes.pointer(a) for a in arrays)))
        return {"ok": ok, **dict(zip(("encoder", "joint", "xyz"), (list(a) for a in arrays)))}


class Hung(Exception):
    """A DLL call did not return: leave the session busy and tell the operator."""


def call(fn, state):
    state["call_open"] += 1     # counted from launch, so a Ctrl+C while waiting leaves it open (as home.request_stop)
    finished, value, error = home.bounded(fn, CALL_S)
    if finished:
        state["call_open"] -= 1
    if not finished:
        raise Hung()
    if error is not None:
        raise error
    return value


def _jog_loop(dll, log, console, state, errors, axis, velocity, seconds):
    """Re-send MoveManual every 300 ms until time is up. Returns (exit code, why it ended)."""
    started = time.monotonic()
    next_move = started
    while time.monotonic() - started < seconds:
        if console.stop_pressed():
            return 7, "stop_key"
        if errors:
            return 7, "dll_error"
        if time.monotonic() >= next_move:
            if call(dll.emergency, state):
                return 7, "emergency"
            moved = call(lambda: dll.move_manual(axis, velocity), state)
            log.event("move_manual", ok=moved)
            if not moved:       # the DLL refuses a move while one runs (911) or with control off (903)
                return 7, "move_refused"
            next_move = time.monotonic() + MOVE_PERIOD_S
        time.sleep(POLL_S)
    if errors:      # an error callback during the last move or sleep, after the loop's own check
        return 7, "dll_error"
    return 0, "time"


def make_action(axis, velocity, seconds):
    def action(dll, log, console, state):
        state["busy"] = True        # until every bounded call below is proven finished
        state["call_open"] = 0
        seen = {"errors": [], "stop_failed": False}
        original = log.event

        def watch(name, **fields):      # the DLL's error callback and a Stop that returned False reach us as log events
            if name == "dll_error_callback":
                seen["errors"].append(name)
            elif name == "stop_requested" and (fields.get("ok") is False or "error" in fields):
                seen["stop_failed"] = True
            original(name, **fields)
        log.event = watch
        code, reason, before, unsafe_out, cleaned = 8, "aborted", None, [], False   # "aborted" until the loop says otherwise
        try:
            try:
                before = call(dll.position, state)
                log.event("position_before", **before)
                if not before["ok"]:        # zeros from a failed read are not a baseline
                    code, reason = 4, "position_read_failed"
                elif call(dll.enter_manual, state):
                    log.event("jog_start", axis=axis, velocity=velocity, seconds=seconds)
                    code, reason = _jog_loop(dll, log, console, state, seen["errors"], axis, velocity, seconds)
                else:
                    code, reason = 4, "enter_manual_refused"
            finally:    # every end path: CloseManual and Stop (an abort stops first, it discards the queued setpoints)
                log.event("jog_end", reason=reason)
                try:
                    if reason == "time":
                        _close(dll, log, state, unsafe_out)
                    else:
                        home.request_stop(dll, log, state)
                finally:
                    if reason == "time":
                        home.request_stop(dll, log, state)
                    else:
                        _close(dll, log, state, unsafe_out)
                cleaned = True      # the arm is told to stop; reading the position again must not block shutdown
                if before and before["ok"]:
                    try:
                        after = call(dll.position, {"call_open": 0})     # a read after the stop: never keeps it busy
                        log.event("position_after", **after)
                        if after["ok"]:
                            log.event("position_delta", **{key: [a - b for a, b in zip(after[key], before[key])]
                                                           for key in ("encoder", "joint", "xyz")})
                        elif code == 0:     # no measurement: the run cannot say what moved
                            code = 4
                    except Hung:
                        log.event("position_after_hang", seconds=CALL_S)
        except Hung:
            cleaned = False     # a call may still be running inside the DLL: stay busy
            log.event("jog_call_hang", seconds=CALL_S)
            state["busy_note"] = "A JOG CALL DID NOT RETURN: the arm may still be moving. USE THE PHYSICAL STOP NOW."
            return 8
        finally:
            log.event = original
            if cleaned:     # also on Ctrl+C, so the caller can still switch control off
                if state["call_open"] > 0:
                    state["busy_note"] = "A JOG CALL WAS INTERRUPTED: the arm may still be moving. USE THE PHYSICAL STOP NOW."
                elif unsafe_out:
                    state["busy_note"] = "CLOSEMANUAL FAILED: the arm may still be moving. USE THE PHYSICAL STOP NOW."
                elif seen["stop_failed"]:
                    state["busy_note"] = "STOP FAILED: the arm may still be moving. USE THE PHYSICAL STOP NOW."
                elif state["stop_open"] > 0:
                    state["busy_note"] = "STOP DID NOT RETURN: the arm may still be moving. USE THE PHYSICAL STOP NOW."
                state["busy"] = (bool(unsafe_out) or seen["stop_failed"] or state["stop_open"] > 0
                                 or state["call_open"] > 0)
        if code == 0 and seen["errors"]:    # an error callback during the clean-up still fails the run
            code = 7
        return 8 if state["busy"] else code
    return action


def _close(dll, log, state, unsafe_out):
    """CloseManual, bounded. A False return is appended to unsafe_out; a hang raises Hung."""
    closed = call(dll.close_manual, state)
    log.event("close_manual", ok=closed)
    if not closed:
        unsafe_out.append(1)


def probe(dll_dir, log):
    lib, path = home.load_library(dll_dir)
    log.event("dll", **home.file_facts(path), python=sys.version.split()[0], ini=home.ini_facts(dll_dir))
    JogDll(lib)
    log.event("exports_resolved", names=list(SIGNATURES))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Jog one axis through the vendor's USBC.dll (lab tool)")
    parser.add_argument("--dll-dir", required=True, help="folder holding USBC.dll and its ini files")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--probe", action="store_true")
    mode.add_argument("--simulate", action="store_true")
    mode.add_argument("--online", action="store_true")
    parser.add_argument("--output", help="JSONL log to create (refuses to overwrite); default logs/jog-<time>.jsonl")
    parser.add_argument("--operator", help="initials, written to the log")
    parser.add_argument("--axis", type=int, choices=range(3),
                        help="0 base, 1 shoulder, 2 elbow (the notes' numbering; wrist and gripper are not offered)")
    parser.add_argument("--velocity", type=int, default=10, help=f"signed, 1 to {MAX_VELOCITY} (unit not stated)")
    parser.add_argument("--seconds", type=float, default=1.0, help=f"0 to {MAX_SECONDS:g}")
    args = parser.parse_args(argv)
    if not args.probe:
        if args.axis is None:
            parser.error("--axis is required with --simulate and --online")
        if not 1 <= abs(args.velocity) <= MAX_VELOCITY:
            parser.error(f"--velocity must be 1 to {MAX_VELOCITY} in size, either sign")
        if not 0 < args.seconds <= MAX_SECONDS:
            parser.error(f"--seconds must be above 0 and at most {MAX_SECONDS:g}")
        if not args.output:
            args.output = home.default_output(f"jog-axis{args.axis}", args.simulate)
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
        return home.run(JogDll(lib), log, args.simulate, home.Console(),
                        action=make_action(args.axis, args.velocity, args.seconds))
    except home.ToolError as error:
        log.event("error", message=str(error))
        return 1
    finally:
        log.close()


if __name__ == "__main__":
    sys.exit(main())
