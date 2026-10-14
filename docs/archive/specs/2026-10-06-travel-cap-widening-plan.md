# Plan: widening the travel cap past 10 degrees

Written 2026-10-06 on the lab PC. **Superseded the same day:** the owner lifted the cap straight to 180 degrees without the gate below (see `docs/project/PROJECT_LOG.md`, 2026-10-06), and the elbow and wrist-pitch limits are now checked on the whole pose in `jog_joint` and `start_stream`. The gate and stages below are kept as the way to earn trust in the source model's limits, not as a block on the cap. Nothing here has been run on the arm.

## Why this is allowed, and what it is not

The owner's call: the 10 degree cap is too tight now that the arm has moved. That is already the plan of record. `docs/specs/2026-10-04-streaming-driver-requirements.md` section 5 item 2 says the cap is raised in stages (10, 20, 45 degrees, then the encoder limits) after a clean lab session, with the reason in the project log. This plan only makes the gate for each stage concrete.

"The arm moved" is not the evidence the gate needs. The cap exists because directions and scales are inherited from the vendor files, not measured on this arm. Inside 10 degrees a wrong scale is a small, visible error. At 45 degrees it is a collision. So the gate is measurements, not motion.

## What the cap is and is not (check this first)

| Limit | Where | Raising `TRAVEL_CAP_DEG` changes it? |
|---|---|---|
| Travel from home, 10 degrees, base/shoulder/elbow | `scorbot/limits.py:TRAVEL_CAP_DEG` (aliases in `follow.py`, `lab/session.py`, `Scorbot.STREAM_TRAVEL_CAP_MAX_DEG`) | Yes, this is the one |
| Joint limits of the source model | `scorbot/source_model.py` (`limit_window`, `LIMITS_DEG`) | **No.** The shoulder has under 4 degrees above home. If "going up" was the shoulder, a bigger cap will not give more up-travel: the joint limit refuses first |
| Jog ceiling, 5 degrees per `jog_joint` | `limits.MAX_JOG_DEG` | No, and it stays |
| Lead limit (command may lead the arm by the jog ceiling) | `streaming.py` | No, and it stays: it is what catches a stall at a hard stop |
| `bench_stream.py` own cap, 2 degrees | `examples/bench_stream.py:40` | No. Raise separately, per stage |
| Wrist jogs, Cartesian on the arm | | No, out of scope |

So there are two separate questions: widen the cap (this plan), and move the shoulder's upper joint limit (needs a measurement of where the hard stop really is, a different plan).

## The gate to leave a stage

All of these, written down in `docs/project/PROJECT_LOG.md` with the log file names:

1. **Direction confirmed** per joint (ACCEPTANCE_RUN section 3): `+` goes the way the model says.
2. **Scale within about 5 percent** per joint (ACCEPTANCE_RUN section 4), read with a level or protractor, from the log counts via `signed_count_delta`. At 5 degrees of travel the measurement is good to about 2 percent, so the data at the old cap supports a decision to go to the next one.
3. **A clean stream trial per joint** (`bench_stream.py`, step E): `Trial result: PASSED`, largest lead well under 284 counts, other two motors at most 10 counts, no buzzing or hunting.
4. **Stop trial result known** (step C). It does not have to pass, but we need to know whether the software stop can be relied on at all.
5. **No fault** in the sessions, and `scripts/review_lab_logs.py` clean.
6. **Clearance swept by hand**, motors off: move each joint by hand through the new range and watch for the table, the base, cables, the gripper against the forearm.

If the owner has only measured one joint, say so. The cap is one global constant. Raising it for all three on one joint's evidence is the shortcut that hurts; the alternative is a small per-joint cap (a design choice, see open questions).

## Stages

| Stage | Cap | Entry | First test at the new cap |
|---|---|---|---|
| 1 (today) | 10 | | |
| 2 | 20 | Gate above, from the work at 10 | One motor at a time, base first (no gravity load). Step out in 2 degree increments past 10, level reading at each, back to home between motors. Then shoulder and elbow the same way |
| 3 | 45 | Same gate, repeated from the stage 2 logs, plus a hard-stop check for each joint (below) | Same method, 5 degree increments |
| 4 | vendor encoder limits | Home and signs measured, and a measured calibration (`scorbot/calibration.py`), the only tier allowed to widen past here | Separate design |

Rules for every test at a new stage:

- Someone's hand is on the physical stop. Neither Ctrl-C nor the software stop is an emergency stop.
- One motor per run. A wrong direction is harder to see when three move.
- Increase only if the previous increment's counts matched the model within 5 percent. A mismatch stops the stage: keep the log, correct `source_model.py` from the measurement, do not push on.
- Abort and use the physical stop on: any motion in another joint, a noise change, the MOTORS LED disagreeing with the software, a stream or session fault. Do not retry a faulted command.
- Every command has a new `--output` name.

## Hard stops and the lead limit

The real danger past the cap is not a wrong angle, it is driving into a mechanical stop or the table. The lead limit (command may lead the arm by 5 degrees before the session faults) is the protection, but it has never fired on the arm. Before stage 3, trigger it on purpose in a safe way: command a small target while someone holds the joint lightly by hand with the arm's motors at the lowest speed, and confirm the session faults and disables. Only if the owner decides this test is safe for the hardware; otherwise skip stage 3 until the lead limit has another proof.

## Code and doc changes per stage (small, one commit)

1. `scorbot/limits.py:TRAVEL_CAP_DEG` to the new value. Nowhere else: the other names are aliases.
2. `tests/test_limits.py:13` asserts `10.0`. Change it with the constant, in the same commit, so the change is deliberate. `tests/test_source_model.py` and others read the constant and follow.
3. Update the places that state "10 degrees": root `CLAUDE.md` (jog ceiling row, stream row), `scorbot/CLAUDE.md` (`limits.py` and `lab/` rows), `docs/lab/` cards where they quote it.
4. `docs/project/PROJECT_LOG.md`: the reason, the evidence (log names, numbers), the stage.
5. `scorbot/provenance.py` fingerprints `limits.py`, so the lab logs change their hash. Expected; note it in the log entry.
6. Run `python -m unittest discover -s tests -v`, then the simulated rehearsal (`python -m scorbot.lab --simulate ...`). Simulated results are not evidence about the arm.
7. Run Codex and Gemini review before committing: this is a motion-limit change.
8. Branch, not `main`. No push unless the owner asks.

Do not do the code change before the gate is met. The constant is the only guard on how far the arm travels.

## Open questions for the owner

1. Which joint moved "up", how far (in degrees), and which script or key did it (the guided session, `bench_joint`, a stream)? The log names are what turn this into evidence.
2. Was a level or protractor used, or was the movement judged by eye? If by eye, stage 2 has no evidence yet, and the first job is the acceptance run's scale step.
3. Is it all three joints that need more travel, or one? If one, a per-joint cap is a small change (`limits.py` would hold a dict) and avoids widening the others.
4. Does the shoulder need more than the 3.7 degrees above home? That is a joint-limit question, not a cap question.
