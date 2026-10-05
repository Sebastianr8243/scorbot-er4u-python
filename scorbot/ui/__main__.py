"""``python -m scorbot.ui --simulate``: the browser page for the simulated arm.

Never opens USB. Needs the ``ui`` and ``planning`` extras.
"""

from __future__ import annotations

import argparse
from importlib.util import find_spec

EXIT_REFUSED = 2


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scorbot.ui", description=__doc__)
    parser.add_argument("--simulate", action="store_true",
                        help="required: the page drives the simulator only")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--mesh-dir", default=None,
                        help="folder of link meshes (default: models/er4u_meshes)")
    args = parser.parse_args(argv)
    if not args.simulate:
        print("The page drives the simulated arm only. Run it with --simulate. "
              "The real arm waits for the lab acceptance run.")
        return EXIT_REFUSED
    for module, extra in (("viser", "ui"), ("ruckig", "planning")):
        if find_spec(module) is None:
            print(f"The browser page needs the {extra} extra: "
                  f"python -m pip install -e \".[{extra}]\"")
            return EXIT_REFUSED

    from ..mover import Mover
    from ..simulated import SimulatedScorbot
    from .app import run
    from .panel import Panel

    robot = SimulatedScorbot()
    robot.connect()
    try:
        robot.enable()
        robot.home(start_position_confirmed=True)
        panel = Panel(Mover(robot), robot)
        try:
            run(panel, port=args.port, mesh_dir=args.mesh_dir)
        finally:
            panel.close()
    finally:
        robot.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
