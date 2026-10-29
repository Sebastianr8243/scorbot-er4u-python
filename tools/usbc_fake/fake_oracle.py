"""Desk only: Home, then a fixed list of small moves through the vendor DLL against the fake controller.

Logs the DLL's own encoder / joint / xyz arrays before and after every move, to answer: which motor moves for each
axis and which way, counts per mm, and how pitch and roll map onto the two wrist motors. The fake arm follows the
DLL's setpoints exactly, so this shows what the DLL asks for, not how the real arm responds.

    <32-bit python> fake_oracle.py DLL_DIR OUT.json

Start pipe_server.py first and wait for its "listening" line. OUT.json must not exist.
"""
import ctypes
import importlib.util
import json
import os
import sys

dll_dir, out = sys.argv[1:3]
here = os.path.dirname(os.path.abspath(__file__))
repo = os.path.join(here, "..", "..")
sys.path.insert(0, here)
import fake_home  # noqa: E402
import pipe_server  # noqa: E402

spec = importlib.util.spec_from_file_location("usbc_move", os.path.join(repo, "tools", "usbc_move", "usbc_move.py"))
move = importlib.util.module_from_spec(spec)
spec.loader.exec_module(move)
home, jog = move.home, move.jog


class OracleDll(move.MoveDll):
    signatures = {**move.SIGNATURES, "Home": home.SIGNATURES["Home"]}

    def teach_absolute(self, name, point, values):
        array = (ctypes.c_long * 5)(*values)
        return bool(self._fn["Teach"](name.encode("ascii"), point, ctypes.pointer(array), 5, move.ABSOLUTE_FLAG))


# (name, [dx, dy, dz um, dpitch, droll]); relative moves, DLL units for pitch and roll
STEPS = []
for label, base in (("A", []), ("B", [("to B", [100000, 0, -40000, 0, 0])]),
                    ("C", [("to C", [0, 80000, 60000, 0, 0])])):
    STEPS += base
    for axis, index in (("x", 0), ("y", 1), ("z", 2)):
        for sign in (1, -1):
            v = [0] * 5
            v[index] = sign * 10000
            STEPS.append((f"{label} {axis}{'+' if sign > 0 else '-'}10mm", v))
    for name, index in (("pitch", 3), ("roll", 4)):
        for size in (100, 500):
            for sign in (1, -1):
                v = [0] * 5
                v[index] = sign * size
                STEPS.append((f"{label} {name}{'+' if sign > 0 else '-'}{size}", v))


def action(dll, log, console, state):
    code = home._home(dll, log, console, state)
    if code != 0:
        return code
    rows = []
    state.setdefault("call_open", 0)
    here = jog.call(dll.position, state)
    rows.append({"name": "after home", "after": here})
    for name, delta in STEPS:
        before = state.get("position") or here
        state["position"] = None
        code = move.move_point(dll, log, console, state, lambda d=delta: dll.teach_relative(move.VECTOR, move.POINT, d),
                               {"delta_um": delta, "relative": True}, {"step": name},
                               define=not state.get("vector_defined"))
        after = state.get("position")
        rows.append({"name": name, "request": delta, "code": code, "before": before, "after": after})
        if code != 0 or not after:
            log.event("oracle_stop", name=name, code=code)
            break
    with open(out, "x", encoding="utf-8") as handle:
        json.dump(rows, handle, indent=1)
    return 0


fake_home.require_pipe_link(dll_dir)
pipe_server.refuse_if_arm_present()
lib, _path = home.load_library(dll_dir)
log = home.EventLog(out + ".events.jsonl", "vendor_dll_fake_pipe")
rc = home.run(OracleDll(lib), log, False, fake_home.Auto(), action=action)
log.close()
print("exit", rc)
sys.exit(rc)
