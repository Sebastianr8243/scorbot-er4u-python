"""Home, then one point move through the vendor DLL against the fake controller on the named pipe (32-bit Python, desk only).

    <32-bit python> fake_move.py DLL_DIR OUT.jsonl [axis mm]

Runs the vendor Home, then tools/usbc_move's DefineVector, Teach, MoveJoint on the same session, and logs the DLL's
encoder, joint and xyz arrays before and after. Same guards as fake_home.py: USBC.INI must point only at the pipe and
no ScorBot USB device may be present. The fake arm is our model, so this measures what the DLL accepts and in what
order, not what the controller does. Start pipe_server.py first (it must run long enough for Home, about 60 s).
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fake_home  # noqa: E402
import pipe_server  # noqa: E402

_spec = importlib.util.spec_from_file_location("usbc_move", os.path.join(HERE, "..", "usbc_move", "usbc_move.py"))
move = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(move)
home = move.home


class HomingMoveDll(move.MoveDll):
    """usbc_move binds no Home on purpose; the desk run against the fake pipe adds it so the DLL is homed first."""
    signatures = {**move.SIGNATURES, "Home": home.SIGNATURES["Home"]}


def homed_then_move(axis, mm):
    step = move.make_action(axis, mm)

    def action(dll, log, console, state):
        code = home._home(dll, log, console, state)
        if code != 0:
            log.event("move_skipped", reason="home did not finish cleanly", code=code)
            return code
        return step(dll, log, console, state)
    return action


def main():
    dll_dir, out = sys.argv[1:3]
    axis, mm = (sys.argv[3], float(sys.argv[4])) if len(sys.argv) > 4 else ("x", 5.0)
    fake_home.require_pipe_link(dll_dir)
    pipe_server.refuse_if_arm_present()
    lib, _path = home.load_library(dll_dir)
    log = home.EventLog(out, "vendor_dll_fake_pipe")
    code = home.run(HomingMoveDll(lib), log, False, fake_home.Auto(), action=homed_then_move(axis, mm), word="MOVE")
    log.close()
    print("exit", code)
    return code


if __name__ == "__main__":
    sys.exit(main())
