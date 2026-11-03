"""Desk only: what the vendor DLL's ShowXYZ reports for chosen encoder counts (made tests/fixtures/dll_lab_xyz.json).

The fake controller runs in this process and reports fixed counts; the DLL is only initialised and asked for its
position (ShowXYZ callback and GetCurrentPosition). No Home and no Control: nothing is driven.

    <32-bit python> fake_xyz.py DLL_DIR OUT.json

Same guards as fake_home.py: USBC.INI must name only the pipe, and no ScorBot USB device may be present. Do not
start pipe_server.py separately. OUT.json must not exist.
"""
import ctypes
import json
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fake_home  # noqa: E402
import pipe_server  # noqa: E402

# Counts for base, shoulder, elbow, wrist motor 1, wrist motor 2: zero, each motor alone, then combinations.
POINTS = [[0, 0, 0, 0, 0]]
for motor, values in ((0, (5000, -5000, 15000, -15000)), (1, (3000, -3000, -10000, 400)),
                      (2, (5000, -5000, 12000, -12000)), (3, (1000, -1000, 3000)), (4, (1000, -1000, -3000))):
    for value in values:
        POINTS.append([value if n == motor else 0 for n in range(5)])
POINTS += [[1500, 0, 0, 1500, 1500], [0, 0, 0, 1500, -1500], [8000, -4000, 6000, 700, -300],
           [-12000, 2000, -9000, -2000, 2500], [3000, -8000, 10000, 1200, 1200], [-6000, -6000, -6000, -900, 400],
           [17000, -15000, 14000, 3000, -3000], [-18000, 3500, -15000, -3000, 3000]]


class FixedCounts(pipe_server.Plant):
    counts = [0] * 6

    def reported(self, n):
        return self.counts[n] if n < 6 else 0


def main():
    dll_dir, out = sys.argv[1:3]
    fake_home.require_pipe_link(dll_dir)
    pipe_server.refuse_if_arm_present()
    plant = FixedCounts()
    threading.Thread(target=pipe_server.serve, args=(out + ".pipe.jsonl", 600, plant), daemon=True).start()
    time.sleep(12)                                  # serve() checks for the arm again before "listening"
    tool = fake_home.load_tool()
    lib, _path = tool.load_library(os.path.abspath(dll_dir))
    dll = tool.VendorDll(lib)
    ready = threading.Event()
    if not (dll.initialization(tool.INIT_ONLINE, lambda _p: ready.set(), lambda _p: None) and ready.wait(30)):
        raise SystemExit("Initialization failed")
    array = ctypes.c_long * 8
    seen = []
    callback = tool.CALLBACK(lambda p: seen.append(list(array.from_address(p)) if p else None))
    show = getattr(lib, "?ShowXYZ@@YAHP6AXPAX@Z@Z")
    show.argtypes, show.restype = [tool.CALLBACK], ctypes.c_int
    close_xyz = getattr(lib, "?CloseXYZ@@YAHXZ")
    close_xyz.argtypes, close_xyz.restype = [], ctypes.c_int
    position = getattr(lib, "?GetCurrentPosition@@YAHPAY07J00@Z")
    position.argtypes, position.restype = [ctypes.POINTER(array)] * 3, ctypes.c_int
    rows = []
    for counts in POINTS:
        plant.counts = counts + [0]
        time.sleep(0.6)                             # several monitoring ticks (USBC.INI Monitoring = 100 ms)
        encoder, joint, xyz = array(), array(), array()
        position(ctypes.byref(encoder), ctypes.byref(joint), ctypes.byref(xyz))
        first = len(seen)
        show(callback)                              # calls back at once with the xyz array
        rows.append({"counts": list(encoder)[:5], "xyz": seen[first][:5], "position_xyz": list(xyz)[:5],
                     "joint": list(joint)[:5]})
        print(json.dumps(rows[-1]), flush=True)
    close_xyz()
    dll.close()
    with open(out, "x", encoding="utf-8") as handle:
        json.dump(rows, handle, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
