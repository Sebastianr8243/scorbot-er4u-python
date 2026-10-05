# Backlog: known bugs and open work

Everything found in the September 2026 reviews that is not yet fixed. Each item cites the code and says how it was found. Nothing here is verified on hardware; items marked **verify first** need bench data before any code change.

Priority:
- **P0**: must be resolved before motion beyond G1.
- **P1**: fix soon; affects correctness or safety margins.
- **P2**: quality, tooling or research.

Hazard IDs (HZ-nn) refer to [SAFETY_CASE.md](../design/SAFETY_CASE.md). Protocol details are in [PROTOCOL.md](../protocol/PROTOCOL.md).

## P0: before any motion beyond G1

| # | Item | Where | Evidence | Next step |
|---|---|---|---|---|
| 1 | Homing search travel is bounded only by the 30 s per-axis deadline and the controller error word; a wrong start pose can sweep the base 140-200 degrees (HZ-05) | `openScorbot/setHome.py:homing` | Legacy USB review H2 | Add a per-axis travel cap, bounded by the manual's joint span (`scorbot/nominal.py`). **Verify first:** switch position and search direction |
| 2 | Homing "braking" does not ramp: `cont_vel` stays 88, so 12 full-speed steps run past the switch; about 120-240 counts for shoulder, elbow and base and about 720 per wrist motor. The vendor procedure backs off until the switch releases (HZ-06) | `openScorbot/setHome.py:homing`, `libdef.py:incremento` | Legacy review H3; protocol review | Implement a vendor-style back-off. **Verify first:** a USB capture of SCORBASE homing |
| 3 | `home()` drives wrist pitch and roll although wrist jogs are gated; wrist overshoot is the largest | `scorbot/robot.py:Scorbot.home`, `setHome.py:homing` | Safety case HZ-14 | Decide: gate wrist homing, or verify the two-motor mapping first |
| 4 | Pitch homing steps the motors in order 11's direction, the opposite of jog order 10 | `openScorbot/setHome.py:homing` | Protocol review | **Verify first** on the bench: record which way pitch moves during homing |
| 5 | Settle loops are not wrap-aware: `abs(target - media) > 20` never converges across 0/65535, and a good move ends as result 2 after 101 packets | `openScorbot/libcomm.py:move_hips/move_shoulder/move_elbow/move_wrist`, `setHome.py`, `moveXYZ.py` | Property test `tests/test_properties.py::LegacyKnownBugs::test_legacy_settle_check_is_not_wrap_aware` | Use a wrap-aware distance once the wrap convention is confirmed by a trace |
| 6 | Software stop built 2026-10-04, **untested on the arm**: `Scorbot.request_stop` ends a jog early with the vendor's stop sequence (`47` then the normal close). Still open: the lab trial, a key binding in the guided session and teleop, and a stop for homing beyond its own cancel path (HZ-10, HZ-10a) | `openScorbot/libcomm.py` jog loops, `libdef.stopMov`, `scorbot/robot.py:request_stop` | Vendor disassembly (`docs/protocol/VENDOR_DLL_PROTOCOL.md`), `tests/test_software_stop.py` | Lab plan step F3: `bench_joint.py --stop-after-ms` on a 1 degree jog; then wire it into `scorbot.lab` |
| 7 | The controller's "motor power shutdown on communication failure" is stated by the manual but never observed (HZ-16) | Controller-USB manual p. 6 | `docs/manual/HARDWARE_REFERENCE.md` | G2 test: stop packets with the arm at rest, hand on e-stop, time until the MOTORS LED goes off |
| 8 | Jog direction and counts per degree are inherited assumptions (HZ-02) | `openScorbot/motion_profile.py:COUNTS_PER_DEGREE`, `MOTOR_DIRECTIONS` | Manual gives no encoder CPR. Vendor parameter files (priors): base 141.89, shoulder/elbow 113.51, wrist 27.90 counts/°; legacy base and roll agree, legacy pitch 33.8 does not (`scorbot.nominal.VENDOR_COUNTS_PER_DEGREE`) | Measure with an inclinometer or ArUco markers; `scripts/fit_calibration.py` warns on disagreement |

Streaming (2026-10-04): `Scorbot.start_stream` follows a stream of base, shoulder and elbow targets, built and simulator-tested but **never run on the arm**. The supervised first trial is `examples/bench_stream.py` (lab plan F4). Open: pacing on the echoed message ID and the emergency-bit fault, both waiting on lab confirmation; replacing the one-jog-per-action follower in `scorbot/follow.py`, teleop and the LeRobot plugin once the arm has shown it tracks. Requirements: `docs/specs/2026-10-04-streaming-driver-requirements.md`.

## P1: legacy code bugs (openScorbot/)

| # | Item | Where | Evidence |
|---|---|---|---|
| 9 | `get_switch` misdecodes byte 5 when any bit >= 32 is set. Mitigated: `home()` refuses such a byte | `libdef.py:get_switch` | Legacy review H1; `tests/test_python_api.py::CommandTests::test_home_refuses_switch_byte_the_legacy_decoder_misreads`. Fix: `bool(byte & bit)` |
| 10 | `cIn` drops the 16 mm shoulder offset (element-wise `*` where a matrix product was meant), ignores the 145 mm tool length, and returns `[-1, -1, -1]` for unreachable targets, which is a valid angle; shoulder and elbow are off by 4-12 degrees | `libdef.py:cIn`, `moveXYZ.py:controlXYZ` | `tests/test_kinematics.py` (measured disagreement); fix against `scorbot/kinematics.py` after measuring poses |
| 11 | `controlXYZ` success path returns the encoder vector where `execute` expects `posRef` | `moveXYZ.py:controlXYZ`, `libcomm.py:execute` | Protocol review |
| 12 | `suma`/`resta` overflow twice for a step above 65535 and emit a 5-digit or negative hex field. Real steps are 20 or less | `libdef.py:suma`, `resta` | `tests/test_properties.py` (two `expectedFailure` tests) |
| 13 | Gripper `clamp`: the timeout loop condition is inverted (waits while not moved) and puts result 1, not 2 | `libcomm.py:clamp` | Protocol review |
| 14 | `getError` threshold is asymmetric: +40 one way, about -36 the other | `libdef.py:getError` | Protocol review |
| 15 | The pause marker is hard-coded `256` in `syncro` while `libcomm` reads `MAX_COUNT`; an edited `data.json` would allow two token holders | `libsync.py:syncro` | Legacy review H6 |
| 16 | `execute` spins on the pause marker with no sleep | `libcomm.py:execute` | Legacy review H7; add `time.sleep(0.001)` after timing is measured |
| 17 | `conf.readData` re-reads and parses `data.json` on every call (about 8 times per packet); `data.json` is created once and never overwritten, so a stale copy silently wins | `conf.py:readData`, `setup` | Legacy review. Cache only after the packet period is measured in an idle capture |
| 18 | Disconnect handshake waits up to 30 s for ack 13, equal to the command timeout, so a slow ack faults a good run | `libcomm.py:scorbotoff`, `libsync.py:send_wait` | Legacy review H9 |
| 19 | `from numpy import *` shadows `round`, `abs`, `sum`; mixed tabs and spaces across modules | `libdef.py` and others | CLAUDE.md review |

## P1: SDK and lab scripts

| # | Item | Where | Evidence |
|---|---|---|---|
| 20 | Calibration faults set `_fault` directly, bypassing `_latch_fault`, so `enabled` stays true; the same in `SimulatedScorbot.connect` | `scorbot/robot.py:jog_joint`, `get_joint_angles`; `scorbot/simulated.py:SimulatedScorbot.connect` | Simplify and architecture reviews. Decide whether `enabled` should become unknown |
| 21 | `home()` timeout is 180 s against up to 5 x 30 s of search plus braking and transitions | `scorbot/robot.py:Scorbot.home` | SDK review H4. Set from a measured home duration |
| 22 | Ctrl-C may not interrupt `queue.get` on Windows until the timeout expires | `scorbot/robot.py:_command` | SDK review H5, unverified. Test in a Windows rehearsal with `step_delay_s` |
| 23 | `review_bench` counts an operator-declined run's missing steps as problems (exit 1) | `scripts/review_lab_logs.py:review_bench` | Safety case Q11. Decide the intended verdict |
| 27 | Enter presses typed during homing sit in stdin and answer the next prompt (they can only decline) | `examples/bench_joint.py` | SDK review M8 |
| 29 | `examples/python_control.py` connects and moves on run, with no guard or confirmation | `examples/python_control.py` | CLAUDE.md review |
| 30 | `build_bench_kit.py` zips all of `references/`, so local copyrighted Intelitek PDFs go into the kit | `scripts/build_bench_kit.py` | CLAUDE.md review. Fine for the lab PC; never publish the ZIP |
| 31 | `preview_jog` adds deltas to signed counts, which can cross the ±65535 seam (display only) | `scorbot/robot.py:preview_jog` | Altitude review |

## P2: tooling, simulator, research

| # | Item |
|---|---|
| 37 | Verify against real files: the USBPcap header layout and direction inference in `scripts/usb_trace.py`, the Foxglove layout keys, the PlotJuggler path syntax in `docs/design/EXPERIMENT_RECORDING.md` |
| 38 | Capture SCORBASE "Go Home", control on/off and e-stop with USBPcap; if Go Home is a single controller command, use it instead of building one |
| 39 | `return_to_home()`: count-based, bounded jogs, retract first and base last, typed plan confirmation; simulator only until G2 |
| 40 | Simulator: sync-worker crash injection; replay of recorded lab data. (Done 2026-10-01: modeled homing with switch bits and offsets, rest jitter across the 0/65535 seam, simulated LED panel; see `SimulatorProfile`) |
| 41 | Kinematics: model reach is 601 mm against the manual's 610 mm; measure the tool length and home pose, then fix `cIn` (item 10) |
| 43 | Operator UX backlog (plan-based `MOVE` confirmation, observe before the plan is shown, structured observations, readable review verdict, alarm banner, `--config lab.toml`, one-page checklist cards): see [OPERATOR_UX.md](../design/OPERATOR_UX.md) |
| 44 | Hazards with no test yet: HZ-05, HZ-06, HZ-09, HZ-16, HZ-19, HZ-20 (see [SAFETY_CASE.md](../design/SAFETY_CASE.md)) |
| 45 | Later roadmap: URDF from measured geometry, then `ikpy`/MuJoCo; optional ROS 2 driver; LeRobot-compatible dataset export |
