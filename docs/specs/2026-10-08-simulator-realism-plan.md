# Simulator realism plan (2026-10-08)

Status: proposal, nothing built. Every simulator behaviour below is a **modeled hypothesis, never evidence about the arm** (evidence ladder rung 0: the simulator can show our software handles a world, not that the arm is that world).

## Why

`SimulatedController` (`scorbot/simulated.py`) answers the command queue with exact, instant, collision-free jogs. That is right for testing gates and fault latching, and wrong for the failures the real arm has shown: the 2026-10-06 legacy home died in about 4 s with "error code 1" (`docs/evidence/2026-10-06-lab-logs/base-first-02.*`), and the simulator cannot even express that story (no hard stops, no coupling between the shoulder, elbow and wrist motors, no lag, no stall, no mid-motion faults).

What the real logs do give the simulator (checked 2026-10-08):

| Observation | Source | Use |
|---|---|---|
| Disable took about 656 ms, enable about 296 ms (log timestamps are quantized, see below) | `base-first-02.controller.jsonl` | Command latency profile; the simulator answers in 0 ms |
| The home command failed 4.2 s after it was sent, code 1, no state rows in between | `base-first-02.controller.jsonl` | A "home dies early" scenario; the log has no per-axis trace, so it cannot say which axis stalled |
| Counts at home start were all near 0 (base 65533, shoulder 1, elbow 65533, wrist 65533/3) | `home_start` row | Power-on counts are relative to power-up, not home: start-pose scenarios |
| `host_monotonic_ns` values are all whole milliseconds and the deltas step by 15.6 ms (500, 516, 484 ms) | every 10-06 log | Looks like the coarse Windows clock; cause unverified (`_record` uses `time.monotonic_ns()`; which Python ran is not in the log) |

## Principles

1. **Opt-in realism.** The default `SimulatorProfile` stays the simple model, so the existing ~1,000 tests do not move. New behaviour arrives as named profiles.
2. **A plant, not more `if`s.** Physics goes in a new pure module (no threads, no sleeps, no USB), the same pattern as `StreamCore`. `SimulatedController` only calls it.
3. **Worlds, not predictions.** Each unverified assumption becomes a switch (`world`): coupling on/off, a wrist sign reversed, a scale off by x percent, a home offset wrong. The test is whether our lab procedure and logs *detect* that world. That measures the lab plan's power, which is what the desk can honestly do.
4. **Label everything.** Every parameter carries `status = "modeled, not measured"` until a lab run fits it. No sim result goes into a doc as arm evidence.
5. **Fingerprint.** `simulated.py` is not in `provenance._SOURCE_FILES`; keep it that way. Any change to `robot.py` (for example the clock) changes `motion_source_sha256`, which is intended.

## Phases

### Phase 0: ground truth from the logs (no simulator code, about 1 hour)

Write `docs/evidence/2026-10-06-lab-logs/ANALYSIS.md`: latencies, the home failure timing, the power-on counts, the clock quantization. Settle which Python ran on the lab PC and whether `time.monotonic_ns` is the coarse clock there. Output decides Phase 3.
Accept: every number in the table above is reproducible by a script in `scripts/`.

### Phase 1: the plant (`scorbot/sim_plant.py`)

State in motor counts; joint angles derived through `source_model` (so the elbow joint follows the shoulder motor, as `_switch_position` already does for the elbow).

- **Hard stops** in joint space, a margin beyond `source_model.LIMITS_DEG` (parameter). A requested motion is clipped so no joint passes its stop; the motor that cannot advance is reported as stalled and its error word grows. Which motor shows the stall is the hypothesis.
- **Coupling**: moving the shoulder motor alone changes the elbow joint and the wrist pitch (vectors from `joint_move.vector`). Switch `coupling=False` gives the old behaviour.
- **Lag**: first-order or rate-limited motion using the vendor profile (`vendor_profile`), so a jog takes time and a stop leaves coast.
- **Backlash / deadband** in counts (default 0).
- **Encoder**: noise, seam wrap (exists), optional lost counts.
- **Switches**: width and bounce; polarity stays the existing hypothesis.

Accept: **the plant's world must differ from what the code believes**, otherwise the test passes by construction (the plant's coupling comes from the same `joint_move.vector` the coupled moves use, so a coupled move "working" proves nothing). Mismatch tests, each asserting that a safeguard faults within N degrees of the error showing:
- plant wrist sign reversed vs. the coupled move's belief: the 40 percent progress check or the readback after each jog faults within about 4 degrees of pitch (existing claim in `tests/test_coupled_moves.py`, now against the plant);
- plant coupling ratio 20 percent off `joint_move`: the pose limit or the progress check faults before a joint reaches its hard stop;
- plant scale off by x percent: `move_joint`'s 2 degree landing check and the stream lead limit fault;
- plant home offset wrong: the inch home's switch-based checks and the pose limits fault.
These measure whether our safeguards detect a wrong assumption, the one honest thing the desk can test. Plant unit tests are pure (counts in, counts out), no threads.
Not a test here: "legacy `home()` from a folded start stalls". The simulator's `home()` is a script (`_model_homing`), not `setHome.py`, so such a test would pass only because it was programmed to. That case belongs to Phase 6, where the real loops run.

### Phase 2: scenario and fault engine

Replace one-shot `inject(kind)` with a seeded schedule (`FaultSchedule`): fire at time t, at the nth packet, or when a joint crosses an angle. Keep `inject` as sugar. New kinds:

- stall at a hard stop; error-word spike
- motors dropped **mid-jog** (today only between commands); e-stop mid-motion with coast
- dropped, duplicated and stale packets; USB timeout mid-motion; ack slower than the 30 s disconnect wait (BACKLOG 18)
- encoder glitch; switch bounce
- world-wrong switches: `reversed_motors` (wrist sign risk; `gripper_reversed` generalized), `scale_error_pct`, `home_offset_error`

Scenarios are small JSON files with a seed, so a failing run replays exactly. Add every kind to `FAULT_KINDS` and to `test_every_fault_kind_latches_the_session` (`scorbot/CLAUDE.md` rule).
Accept: each new kind latches the session or is reported, none leaves a motion queued after a fault.

### Phase 3: time model

- A clock injected into the **controller side only** (`simulated.py`, `sim_plant.py`), with a virtual mode (no real sleeping) for tests and a realistic mode (latencies from Phase 0) for rehearsals. The simulator's hardcoded sleeps (0.3 s worker crash, 0.5 s late answer, 3 s rehearsal homing) are the likely slow tail. Do not touch `robot.py` or `streaming.py` for this before the next lab visit: both are in the motion fingerprint and `Stream.source` paces with real time.
- Cheap and outside the motion path: add Python version, platform and `time.get_clock_info("monotonic")` to the lab session header row in `scorbot/lab`, not `robot.py`. That answers the Phase 0 question on the next visit.
- After the visit, if the coarse clock is confirmed, log `time.perf_counter_ns` beside `time.monotonic_ns` in `_record` (touches `robot.py`, changes `motion_source_sha256`, intended).
Accept: a stream test runs 10 simulated seconds in well under 1 real second, and the stream's pacing is asserted on the virtual clock.

### Phase 4: stateful property tests

Hypothesis `RuleBasedStateMachine` over `SimulatedScorbot` with the plant: rules (enable, disable, home, jog, gripper, start/stop stream, request_stop, inject) and invariants checked after every step:
- no motion accepted after a latched fault
- counts stay inside the hard-stop window
- real and simulated rows never mix
- `homed`/`enabled` match the documented command-history semantics
Hypothesis is an optional dependency in this repo (tests skip without it); keep deadlines off and steps few because the facade uses threads.
Accept: runs under 60 s in CI, and a deliberately broken gate is caught.

### Phase 5: close the loop with the arm

After a lab visit: fit `PlantParams` from the logs (latencies, settle times, stall counts) into a `measured_v1` profile with provenance, and add `scripts/compare_sim_real.py` that replays the same command list against the simulator and prints residuals. Until that exists the simulator has no accuracy number and should not be described as accurate.

### Phase 6 (optional): packet-level controller

Today the simulator replaces the command queue, so `setHome.py`, `libcomm` loops and the legacy settle logic never run in simulation, and that is where the 10-06 failure lives. A fake USB endpoint pair driven by the plant would run the real legacy loops (`tests/test_settle_seam.py` already uses fake endpoints). This is also the only place the "legacy home from a folded start" story can be tested honestly (with the plant's coupling as a hypothesis). Costs: it needs the OUT-packet semantics decoded (`scorbot/transport/codec.py` encodes; decoding is not built), and it adds a second place where unverified protocol assumptions live. Decide after Phase 0 and a one-day spike reading `libcomm.py` and `setHome.py` (not read for this plan).

## Order and size

| Phase | Size | Depends on |
|---|---|---|
| 0 logs analysis | 1 h | none |
| 1 plant | 2 to 3 days | none |
| 2 scenarios | 2 days | 1 |
| 3 time model | 1 to 2 days | 0 |
| 4 stateful tests | 1 day | 1, 2 |
| 5 compare with arm | after a lab visit | a visit |
| 6 packet level | spike 1 day, build unknown | 0, 1 |

## Risks

- The plant encodes the same coupling hypothesis it will be used to "test". It tests our software against that world; it cannot confirm the world.
- A rich simulator invites trusting it. Keep the SIMULATED banner and the "modeled, not measured" status on every output.
- Stateful tests with threads can be flaky; budget time to make them deterministic with the virtual clock first (so Phase 3 may move ahead of Phase 4).

## Not in scope

Cartesian physics, contact and gripper force models, collision, MuJoCo or PyBullet. A full rigid-body engine needs measured masses, friction and geometry that do not exist; the arm's meshes are unvalidated.
