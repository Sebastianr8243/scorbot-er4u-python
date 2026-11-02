"""How fast PolicyArm moves on the simulator, fixed step vs speed step, at 25, 50 and 100 percent. No USB, no arm.

    python examples/policy_arm_speed_sim.py [--hz 10] [--twin]

For each speed, step limit and motor (base, then shoulder) it drives PolicyArm toward a far goal at --hz calls
per second for SECONDS and prints: the fastest commanded stream step (counts/s, and as a percent of the vendor's
MaxSpeed at that speed; whole-count rounding allows 1 count per period over), the fastest measured joint speed
over any 0.25 s (deg/s), whether the stream faulted, and with --twin the MuJoCo twin's peak joint speed and its
largest lag behind the commanded angle when fed the same commands (12 steps of 2 ms per 24 ms period).
The simulated controller follows each setpoint exactly, so its numbers show the planner's limits, not a real
arm's lag. The twin's gains and geometry are unvalidated. Says nothing about the real arm.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import tempfile
import time

from scorbot import SimulatedScorbot
from scorbot.policy_arm import PolicyArm
from scorbot.streaming import DEFAULT_PERIOD_S, VENDOR_MAX_SPEED_COUNTS_S

SPEEDS = (25, 50, 100)
MODES = ("fixed", "speed")
GOALS = {"base": 25.0, "shoulder": -25.0}   # ours: far enough to reach full speed; shoulder down (its joint limit is about 3.7 deg up)
CAP_DEG = 30.0                              # ours: room to reach full speed (the default box is 2 degrees)
SECONDS = 3.0
WINDOW_S = 0.25


def drive(pct, mode, motor, hz, folder):
    arm = PolicyArm(SimulatedScorbot(log_path=Path(folder) / f"{motor}-{mode}-{pct}.jsonl", robot_id="speed-sim"),
                    travel_cap_deg=CAP_DEG, speed_pct=pct, step_limit=mode)
    arm.connect()
    fault = None
    try:
        seen, start = [], time.perf_counter()
        while (now := time.perf_counter()) - start < SECONDS:
            arm.act({motor: GOALS[motor]})
            seen.append((time.perf_counter(), arm.observe()[motor]))
            time.sleep(max(0.0, 1 / hz - (time.perf_counter() - now)))
        steps, fault, per_degree = list(arm._stream.steps), arm._stream.core.fault, arm._per_degree[motor]
    finally:
        try:
            arm.close()
        except Exception as error:      # a faulted stream reports here; the fault is part of the result
            fault = fault or str(error)
    commanded = [s["commanded"][motor] for s in steps if s["action"] == "send"]
    peak_counts = max((abs(b - a) for a, b in zip(commanded, commanded[1:])), default=0) / DEFAULT_PERIOD_S
    peak_deg_s = 0.0
    for i, (t0, d0) in enumerate(seen):
        later = [(t, d) for t, d in seen[i:] if t - t0 >= WINDOW_S]
        if later:
            t1, d1 = later[0]
            peak_deg_s = max(peak_deg_s, abs(d1 - d0) / (t1 - t0))
    return {"peak_counts_s": peak_counts, "limit_counts_s": VENDOR_MAX_SPEED_COUNTS_S * pct / 100,
            "peak_deg_s": peak_deg_s, "limit_deg_s": VENDOR_MAX_SPEED_COUNTS_S * pct / 100 / abs(per_degree),
            "reached_deg": seen[-1][1], "fault": fault, "commanded_deg": [c / per_degree for c in commanded]}


def twin(motor, commanded_deg):
    """Peak joint speed (deg/s) and largest lag (deg) of the MuJoCo twin fed the commanded angles."""
    import mujoco
    from scorbot import mujoco_twin
    model, data = mujoco_twin.load()
    joint, actuator = model.joint(motor).id, model.actuator(motor).id     # joint 0 is the block's free joint
    substeps = round(DEFAULT_PERIOD_S / model.opt.timestep)
    peak = lag = 0.0
    for angle in commanded_deg:
        data.ctrl[actuator] = math.radians(angle)
        for _ in range(substeps):
            mujoco.mj_step(model, data)
            peak = max(peak, abs(math.degrees(data.qvel[model.jnt_dofadr[joint]])))
        lag = max(lag, abs(angle - math.degrees(data.qpos[model.jnt_qposadr[joint]])))
    return peak, lag


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hz", type=float, default=10.0, help="PolicyArm.act calls per second (LeRobot fps)")
    parser.add_argument("--twin", action="store_true", help="also feed the commands to the MuJoCo twin (needs mujoco)")
    args = parser.parse_args(argv)
    print(f"{args.hz:g} Hz, {SECONDS:g} s per run, cap {CAP_DEG:g} deg, simulator only")
    header = "motor     speed% step   cmd counts/s (% of limit)  measured deg/s (limit)  reached  fault"
    print(header + ("  twin deg/s  twin lag deg" if args.twin else ""))
    with tempfile.TemporaryDirectory() as folder:
        for motor in GOALS:
            for pct in SPEEDS:
                for mode in MODES:
                    r = drive(pct, mode, motor, args.hz, folder)
                    line = (f"{motor:<9} {pct:>5}  {mode:<6} {r['peak_counts_s']:>7.0f} ({100 * r['peak_counts_s'] / r['limit_counts_s']:>5.1f}%)"
                            f"       {r['peak_deg_s']:>6.2f} ({r['limit_deg_s']:>5.2f})      {r['reached_deg']:>6.2f}   "
                            f"{'yes: ' + r['fault'] if r['fault'] else 'no'}")
                    if args.twin:
                        peak, lag = twin(motor, r["commanded_deg"])
                        line += f"  {peak:>9.2f}  {lag:>11.3f}"
                    print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
