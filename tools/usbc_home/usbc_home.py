"""Run the vendor's USBC.dll Home and nothing else. Lab tool, standard library only.

Spec (stack2 branch): docs/archive/specs/2026-10-08-vendor-dll-home-plan.md. Needs a 32-bit Python (the
DLL is 32-bit). From disassembly and unverified on the arm.

    python32 usbc_home.py --dll-dir DIR --probe
    python32 usbc_home.py --dll-dir DIR --simulate
    python32 usbc_home.py --dll-dir DIR --online

--probe loads the DLL and checks the exports; it never calls Initialization.
--simulate uses the DLL's own simulation mode (no USB). --online energises the
motors on connect and moves the arm in Home. Stop (s) here is not an emergency
stop; the physical stop is. The log goes to logs\\ (rehearsal\\ when simulated)
unless --output names a file.

Only the functions in ALLOWED are bound; no joint-move export is reachable.
"""

import argparse
import ctypes
import hashlib
import json
import os
import struct
import sys
import threading
import time
from datetime import datetime, timezone

INIT_ONLINE = 1          # force online; the shipped ER4CONF.INI says Simulation = Yes
INIT_SIMULATE = 2
ER4USB_SYSTEM_TYPE = 41
INIT_TIMEOUT_S = 5.0     # Initialization may never call back
HOME_TIMEOUT_S = 800.0   # scorbot.vendor_home_source.TOTAL_SECONDS: every axis' max_time in both passes adds up to about 730 s
STOP_GRACE_S = 10.0
CONTROL_ON_CALL_S = 10.0    # Control(on) energises the motors; a call that never returns leaves them energised
CONTROL_OFF_CALL_S = 10.0   # Control(off) is several lock-step messages; past this the DLL is taken to be stuck
STOP_CALL_S = 3.0        # Stop runs on its own thread: a Stop that blocks must not hide the warning

CALLBACK = ctypes.CFUNCTYPE(None, ctypes.c_void_p)
_uc, _int, _short = ctypes.c_ubyte, ctypes.c_int, ctypes.c_short

# name -> (mangled export, argument types, return type). All __cdecl (read from
# the 2018 and 2008 export tables). IsOnlineOk is not exported by either build.
SIGNATURES = {
    "Initialization": ("?Initialization@@YAHFFP6AXPAX@Z1@Z", (_short, _short, CALLBACK, CALLBACK), _int),
    "IsEmergency": ("?IsEmergency@@YAHXZ", (), _int),
    "Control": ("?Control@@YAHEH@Z", (_uc, _int), _int),
    "Home": ("?Home@@YAHEP6AXPAX@Z@Z", (_uc, CALLBACK), _int),
    "Stop": ("?Stop@@YAHE@Z", (_uc,), _int),
    "CloseUSBC": ("?CloseUSBC@@YAXXZ", (), None),
}
ALLOWED = tuple(SIGNATURES)
GROUPS = ("A", "&")
HOME_GROUP = "A"       # SCORBASE's robot home (Scorbase.exe FUN_0044d4e5)
CONTROL_GROUP = "&"    # SCORBASE and the vendor SDK demo pass '&' to Control and Stop (VENDOR_DLL_PROTOCOL.md section 15)
PROGRESS = {0xFF: "homing started", 0x40: "homing ended"}


class ToolError(Exception):
    pass


def describe_error(pointer):
    """Code and message of the DLL's ErrorInfo (from the decompile: u32 code, then text at +16)."""
    if not pointer:
        return {}
    text = ctypes.string_at(pointer + 16, 128).split(b"\0")[0]
    return {"code": ctypes.c_uint.from_address(pointer).value, "message": text.decode("latin1")}


class VendorDll:
    """The allowlisted exports of one loaded library; nothing else is bound."""

    signatures = SIGNATURES     # tools/usbc_jog binds its own list

    def __init__(self, lib):
        self._fn = {}
        self._keep = []          # callbacks must outlive the call that registers them
        for name, (mangled, argtypes, restype) in self.signatures.items():
            try:
                fn = getattr(lib, mangled)
            except AttributeError:
                raise ToolError(f"export {mangled} ({name}) not found in this DLL build") from None
            fn.argtypes = argtypes
            fn.restype = restype
            self._fn[name] = fn

    def _callback(self, handler):
        callback = CALLBACK(handler)
        self._keep.append(callback)
        return callback

    def initialization(self, mode, on_done, on_error):
        return bool(self._fn["Initialization"](mode, ER4USB_SYSTEM_TYPE,
                                               self._callback(on_done), self._callback(on_error)))

    def emergency(self):
        return bool(self._fn["IsEmergency"]())

    def control(self, group, on):
        return bool(self._fn["Control"](ord(group), 1 if on else 0))

    def home(self, group, on_progress):
        def handler(pointer):
            on_progress(ctypes.c_ubyte.from_address(pointer).value if pointer else None)
        return bool(self._fn["Home"](ord(group), self._callback(handler)))

    def stop(self, group):
        return bool(self._fn["Stop"](ord(group)))

    def close(self):
        self._fn["CloseUSBC"]()


class EventLog:
    """Append-only JSONL. `source` says whether the DLL ran online or simulated."""

    def __init__(self, path, source):
        self.source = source
        self._start = time.monotonic()
        if path and os.path.dirname(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)  # a fresh clone has no logs\ folder
        self._handle = open(path, "x", encoding="utf-8") if path else None

    def event(self, name, **fields):
        record = {"t": round(time.monotonic() - self._start, 4),
                  "wall": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                  "event": name, "source": self.source, **fields}
        line = json.dumps(record, allow_nan=False)
        if self._handle:
            self._handle.write(line + "\n")
            self._handle.flush()
        print(line)

    def close(self):
        if self._handle:
            self._handle.close()


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def file_facts(path):
    stat = os.stat(path)
    return {"path": path, "size": stat.st_size, "sha256": sha256(path),
            "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds")}


def ini_facts(dll_dir):
    """Where the DLL may look for its ini files: next to the DLL and next to the host program."""
    places = {"dll_dir": dll_dir, "python_dir": os.path.dirname(os.path.abspath(sys.executable))}
    return {label: {name: os.path.isfile(os.path.join(folder, name)) for name in ("USBC.INI", "ER4CONF.INI")}
            for label, folder in places.items()}


# USBC.dll builds we have decompiled (docs/protocol/VENDOR_DLL_PROTOCOL.md). The 2008 build is
# also the one SCORBASE 5.3.3.5 ships in BIN\.
KNOWN_DLL_SHA256 = {
    "91e99a915c4f5e22c942b3e8dc757ac6b0f3610c01abdab0c2ea2ad0aa555240": "2008 (SCORBASE 5.3.3.5 BIN)",
    "6de887a8d6f2ce686d377586d4be8981ff643cad13c158cd4603f8b44f20555e": "2018 (Kutzer ScorBot Toolbox)",
}


def check_fingerprint(sha, log):
    if sha not in KNOWN_DLL_SHA256:
        log.event("unknown_dll", sha256=sha)
        print(f"Warning: USBC.dll {sha[:12]}... is not a build we have read; going on anyway")


def default_output(tool, simulate):
    """logs/<tool>-<time>.jsonl, or rehearsal/ for a simulated run."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return os.path.join("rehearsal" if simulate else "logs", f"{tool}-{stamp}.jsonl")


def load_library(dll_dir):
    if struct.calcsize("P") != 4:
        raise ToolError("USBC.dll is 32-bit: run this with a 32-bit Python (see the spec)")
    path = os.path.join(dll_dir, "USBC.dll")
    if not os.path.isfile(path):
        raise ToolError(f"no USBC.dll in {dll_dir}")
    os.chdir(dll_dir)
    os.add_dll_directory(dll_dir)
    return ctypes.CDLL(path), path


def probe(dll_dir, log):
    lib, path = load_library(dll_dir)
    log.event("dll", **file_facts(path), python=sys.version.split()[0], ini=ini_facts(dll_dir))
    VendorDll(lib)
    log.event("exports_resolved", names=list(ALLOWED))
    return 0


def bounded(fn, seconds):
    """Run fn on a daemon thread. Returns (finished, value, error); never raises fn's own exception.

    An interrupt (Ctrl+C) during the wait propagates while the call may still be running, so callers
    mark the session busy before calling this and clear it only from `finished`.
    """
    box = {}

    def target():
        try:
            box["value"] = fn()
        except BaseException as error:    # carried back to the caller's thread
            box["error"] = error

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(seconds)
    return not thread.is_alive(), box.get("value"), box.get("error")


def request_stop(dll, log, state):
    """Call Stop on a bounded thread. Any Stop not proven finished keeps the session busy."""
    state["stop_open"] += 1     # counted from launch, so an interrupt while waiting still leaves it open
    finished, ok, error = bounded(lambda: dll.stop(CONTROL_GROUP), STOP_CALL_S)
    if finished:
        state["stop_open"] -= 1
    log.event("stop_requested", ok=ok if finished else None, returned=finished,
              **({"error": repr(error)} if error else {}))


class Console:
    """Keyboard stop key; replaced in tests."""

    def stop_pressed(self):
        try:
            import msvcrt
        except ImportError:
            return False
        return msvcrt.kbhit() and msvcrt.getwch().lower() == "s"


def run(dll, log, simulate, console, do_home=True, action=None):
    """Initialization, control on, Home, control off, close. Returns an exit code.

    ``action(dll, log, console, state)`` replaces Home (the jog tool passes its own);
    it must keep ``state["busy"]`` true while a
    call that may outlive us is running. If Home is still running when we give up (Stop did not end
    it) nothing is switched off or closed: the process exits, the keep-alive stops
    and the controller drops control (unverified); the physical stop is the answer.
    """
    state = {"control_attempted": False, "busy": False, "stop_open": 0}
    code = 1
    try:
        code = _sequence(dll, log, simulate, console, state, do_home, action)
    finally:
        if state["busy"]:
            log.event("shutdown_skipped", reason=state.get("busy_note") or "Home is still running")
            print(state.get("busy_note") or "HOME IS STILL RUNNING AND STOP DID NOT END IT. USE THE PHYSICAL STOP NOW.")
        else:
            hung = False
            try:
                if state["control_attempted"]:
                    # Bounded like Stop: a DLL that blocks here (a dead link) must not hang the cleanup.
                    finished, off_ok, off_error = bounded(lambda: dll.control(CONTROL_GROUP, False), CONTROL_OFF_CALL_S)
                    if not finished:
                        hung = True
                        log.event("control_off_hang", seconds=CONTROL_OFF_CALL_S)
                        print("CONTROL-OFF DID NOT RETURN: the motor state is unknown. USE THE PHYSICAL STOP NOW.")
                        code = 8
                    else:
                        if off_error is not None:
                            log.event("control_off_error", error=repr(off_error))
                            off_ok = False
                        log.event("control_off", ok=off_ok)
                        if off_ok is False:      # say so before CloseUSBC, which could still raise
                            log.event("control_off_failed")
                            print("CONTROL-OFF FAILED: the motors may still be on. Use the physical stop.")
                            code = 8
            finally:
                if hung:
                    log.event("close_skipped", reason="Control-off is still running in the DLL")
                else:
                    dll.close()
                    log.event("closed")
    return code


def _sequence(dll, log, simulate, console, state, do_home, action=None):
    init_done = threading.Event()
    state["busy"] = True    # the call may outlive us if we are interrupted while waiting
    finished, returned, error = bounded(
        lambda: dll.initialization(INIT_SIMULATE if simulate else INIT_ONLINE,
                                   lambda _p: (log.event("init_done"), init_done.set()),
                                   lambda p: log.event("dll_error_callback", **describe_error(p))),
        INIT_TIMEOUT_S)
    if not finished:        # the DLL call itself is stuck: leave it alone, the process exit ends it
        log.event("init_hang", seconds=INIT_TIMEOUT_S)
        return 2
    state["busy"] = False
    if error:
        raise error
    log.event("init_returned", value=returned)
    if not returned:          # every failure path of the DLL's Initialization returns 0
        log.event("init_refused")
        return 2
    if not init_done.wait(INIT_TIMEOUT_S):
        log.event("init_timeout", seconds=INIT_TIMEOUT_S)
        return 2
    emergency = dll.emergency()
    log.event("emergency", active=emergency)
    if emergency:
        return 3
    state["control_attempted"] = True       # a failed control-on may be half done: always try control-off
    state["busy"] = True                    # the call may outlive us; cleared only once it is proven finished
    finished, ok, error = bounded(lambda: dll.control(CONTROL_GROUP, True), CONTROL_ON_CALL_S)
    if not finished:
        log.event("control_on_hang", seconds=CONTROL_ON_CALL_S)
        state["busy_note"] = "CONTROL-ON DID NOT RETURN: the motors may be energised. USE THE PHYSICAL STOP NOW."
        return 8
    state["busy"] = False
    if error:
        raise error
    log.event("control_on", ok=ok)
    if not ok:
        return 4
    if action:
        return action(dll, log, console, state)
    if not do_home:
        log.event("home_skipped")
        return 0
    return _home(dll, log, console, state)


def _home(dll, log, console, state):
    result = {}
    worker = threading.Thread(
        target=lambda: result.update(ok=dll.home(HOME_GROUP, lambda v: log.event(
            "home_progress", value=v, meaning=PROGRESS.get(v, f"axis {v}")))),
        daemon=True)
    log.event("home_start", group=HOME_GROUP)
    state["busy"] = True    # until the Home call is proven finished
    worker.start()
    deadline = time.monotonic() + HOME_TIMEOUT_S
    stopped = False
    try:
        while worker.is_alive():
            if not stopped and console.stop_pressed():
                request_stop(dll, log, state)
                stopped, deadline = True, time.monotonic() + STOP_GRACE_S
            if time.monotonic() > deadline:
                log.event("home_timeout")
                request_stop(dll, log, state)
                worker.join(STOP_GRACE_S)
                state["busy"] = worker.is_alive() or state["stop_open"] > 0
                return 6
            time.sleep(0.05)
    except BaseException:       # Ctrl+C or an error while supervising: Home may still be running
        log.event("home_interrupted")
        try:
            request_stop(dll, log, state)
            worker.join(STOP_GRACE_S)
        finally:                # busy stays True if anything above raised
            state["busy"] = state["busy"] and (worker.is_alive() or state["stop_open"] > 0)
        raise
    state["busy"] = state["stop_open"] > 0
    log.event("home_returned", ok=result.get("ok"), stopped=stopped)
    return 0 if result.get("ok") and not stopped else 7


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the vendor's USBC.dll Home (lab tool)")
    parser.add_argument("--dll-dir", required=True, help="folder holding USBC.dll and its ini files")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--probe", action="store_true")
    mode.add_argument("--simulate", action="store_true")
    mode.add_argument("--online", action="store_true")
    parser.add_argument("--output", help="JSONL log to create (refuses to overwrite); default logs/home-<time>.jsonl")
    parser.add_argument("--operator", help="initials, written to the log")
    parser.add_argument("--no-home", action="store_true",
                        help="with --online: connect, read emergency, control on then off, close; never call Home")
    args = parser.parse_args(argv)
    if args.no_home and not args.online:
        parser.error("--no-home only applies to --online")
    if not args.probe and not args.output:
        args.output = default_output("home", args.simulate)
    source = {True: "vendor_dll_simulated"}.get(args.simulate, "vendor_dll")
    log = EventLog(args.output, source)
    try:
        log.event("start", mode="probe" if args.probe else "simulate" if args.simulate else "online",
                  operator=args.operator)
        if args.probe:
            return probe(args.dll_dir, log)
        lib, path = load_library(args.dll_dir)
        facts = file_facts(path)
        log.event("dll", **facts, python=sys.version.split()[0], ini=ini_facts(args.dll_dir))
        if args.online:
            check_fingerprint(facts["sha256"], log)
        return run(VendorDll(lib), log, args.simulate, Console(), not args.no_home)
    except ToolError as error:
        log.event("error", message=str(error))
        return 1
    finally:
        log.close()


if __name__ == "__main__":
    sys.exit(main())
