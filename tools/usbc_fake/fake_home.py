"""Run the vendor DLL's Home against the fake controller on the named pipe (32-bit Python, desk only).

    <32-bit python> fake_home.py DLL_DIR OUT.jsonl [home_timeout_seconds]

DLL_DIR is a COPY of a SCORBASE BIN tree whose USBC.INI has ``DriverLink = \\\\.\\pipe\\scorbot_fake``. This
refuses to run if USBC.INI points anywhere else (the DLL would open the real USB driver) or if a ScorBot
USB device is present. Start ``pipe_server.py`` first. Never commit the DLL tree.
"""
import importlib.util
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pipe_server  # noqa: E402


def load_tool():
    spec = importlib.util.spec_from_file_location("usbc_home", os.path.join(HERE, "..", "usbc_home", "usbc_home.py"))
    assert spec is not None and spec.loader is not None
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return tool


def links_in(path):
    with open(path, encoding="latin-1") as handle:
        return re.findall(r"^\s*DriverLink\s*=\s*(\S+)", handle.read(), re.MULTILINE | re.IGNORECASE)


def require_pipe_link(dll_dir, host_dir=None):
    """The DLL may read USBC.INI next to itself or next to the host program (``usbc_home.ini_facts``):
    both must exist (unless they are the same folder) and name only the pipe."""
    host_dir = os.path.dirname(os.path.abspath(sys.executable)) if host_dir is None else host_dir
    paths = [os.path.join(dll_dir, "USBC.INI")]
    if os.path.abspath(host_dir) != os.path.abspath(dll_dir):
        paths.append(os.path.join(host_dir, "USBC.INI"))      # the DLL may fall back to this one: it must exist
    for path in paths:
        if not os.path.isfile(path):
            raise SystemExit(f"{path} is missing: run Python from the DLL's own folder, or give the host folder "
                             f"a USBC.INI with DriverLink = {pipe_server.PIPE}")
        links = links_in(path)
        if links != [pipe_server.PIPE]:
            raise SystemExit(f"{path}: DriverLink is {links}, not {pipe_server.PIPE}; refusing to run the DLL")


class Auto:
    def stop_pressed(self):
        return False


def main():
    dll_dir, out = sys.argv[1], sys.argv[2]
    require_pipe_link(dll_dir)
    pipe_server.refuse_if_arm_present()
    tool = load_tool()
    tool.HOME_TIMEOUT_S = float(sys.argv[3]) if len(sys.argv) > 3 else 70
    lib, _path = tool.load_library(dll_dir)
    log = tool.EventLog(out, "vendor_dll_fake_pipe")
    code = tool.run(tool.VendorDll(lib), log, False, Auto())
    log.close()
    print("exit", code)
    return code


if __name__ == "__main__":
    sys.exit(main())
