"""Jog through the vendor DLL against the fake controller on the named pipe (32-bit Python, desk only).

    <32-bit python> fake_jog.py DLL_DIR OUT.jsonl units [axis velocity seconds]
    <32-bit python> fake_jog.py DLL_DIR OUT.jsonl rate  [seconds]

``units`` is one usbc_jog jog; the log's position_before/after/delta show the DLL's encoder, joint and xyz arrays
side by side. ``rate`` cycles MoveManual over axes 0, 1, 2 with no sleep and logs the round-trip per call.
Same guards as fake_home.py: USBC.INI must point only at the pipe and no ScorBot USB device may be present.
The fake arm is our model, so this measures the DLL and this PC, not the controller. Start pipe_server.py first.
"""
import importlib.util
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fake_home  # noqa: E402
import pipe_server  # noqa: E402

_spec = importlib.util.spec_from_file_location("usbc_jog", os.path.join(HERE, "..", "usbc_jog", "usbc_jog.py"))
jog = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jog)
home = jog.home


def rate_action(seconds, velocity=1):
    def action(dll, log, console, state):
        state["call_open"] = 0      # jog.call's open-call count
        if not jog.call(dll.enter_manual, state):
            return 4
        durations = []
        end = time.monotonic() + seconds
        try:
            while time.monotonic() < end and not jog.call(dll.emergency, state):
                for axis in range(3):
                    began = time.monotonic()
                    ok = jog.call(lambda a=axis: dll.move_manual(a, velocity), state)
                    durations.append((axis, ok, round((time.monotonic() - began) * 1000, 2)))
        finally:
            jog._close(dll, log, state, [])
            home.request_stop(dll, log, state)
        ms = sorted(d[2] for d in durations)
        log.event("rate_summary", calls=len(ms), refused=sum(not d[1] for d in durations),
                  median_ms=ms[len(ms) // 2] if ms else None, max_ms=ms[-1] if ms else None,
                  full_3_axis_ticks_per_s=round(len(ms) / 3 / seconds, 1))
        log.event("rate_calls", first=durations[:12])
        return 0
    return action


def main():
    dll_dir, out, mode = sys.argv[1:4]
    rest = sys.argv[4:]
    fake_home.require_pipe_link(dll_dir)
    pipe_server.refuse_if_arm_present()
    lib, _path = home.load_library(dll_dir)
    log = home.EventLog(out, "vendor_dll_fake_pipe")
    if mode == "units":
        axis, velocity, seconds = (int(rest[0]), int(rest[1]), float(rest[2])) if rest else (0, 5, 1.0)
        action = jog.make_action(axis, velocity, seconds)
    elif mode == "rate":
        action = rate_action(float(rest[0]) if rest else 3.0)
    else:
        raise SystemExit("mode is units or rate")
    code = home.run(jog.JogDll(lib), log, False, fake_home.Auto(), action=action, word="JOG")
    log.close()
    print("exit", code)
    return code


if __name__ == "__main__":
    sys.exit(main())
