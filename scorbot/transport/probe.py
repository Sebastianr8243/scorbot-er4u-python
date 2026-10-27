"""Read one decoded state through the full legacy connection handshake."""

import argparse
from dataclasses import replace

from ..robot import Scorbot, ScorbotError
from ..state import decode_state
from .device_file import DeviceLinkError

CONNECT_WARNING = ("Connecting energises then releases the motors briefly; "
                   "the operator must be present and clear of the arm.")


def read_status(robot):
    """Connect if needed and decode one snapshot; the caller owns disconnect."""
    if robot._device is None:
        robot.connect()
    observed, packet = robot.get_state_and_packet()
    state = decode_state(packet, connected=True, enabled=observed.enabled,
                         homed=observed.homed, fault=observed.fault)
    return replace(state, simulated=observed.simulated), packet


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", choices=("auto", "device_file", "pyusb"), default="auto")
    parser.add_argument("--simulate", action="store_true")
    parser.add_argument("--latency-ms", type=float, default=0, help="SIMULATED reply latency only")
    parser.add_argument("--online", action="store_true", help="real controller (default is refusing)")
    args = parser.parse_args(argv)
    label = "SIMULATED" if args.simulate else "REAL"
    print(f"{label} {CONNECT_WARNING}")
    robot = None
    try:
        if not args.simulate:
            if not args.online:
                raise ScorbotError("Real mode needs --online (or use --simulate)")
        if args.simulate:
            from ..lab.dry_link import DryScorbot
            robot = DryScorbot(latency_ms=args.latency_ms)
        else:
            robot = Scorbot(transport=args.transport)
        try:
            state, packet = read_status(robot)
            print(f"{label} raw {packet.hex()}")
            print(f"{label} counts {state.encoder_counts}; switches {state.home_switch_bits:#04x}")
        finally:
            robot.disconnect()
    except (ScorbotError, DeviceLinkError, OSError, ValueError, EOFError) as exc:
        print(f"{label} FAIL {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
