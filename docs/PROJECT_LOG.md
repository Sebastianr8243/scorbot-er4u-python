# Project log

What happened, when and why, newest first. One entry per piece of work: the
decision, the evidence behind it, and where it lives (commits, docs, tests).
Git history has the diffs; this file has the story. Hardware claims stay
"unverified" unless a bench log exists.

## 2026-10-01

### Semester goal decided

- Both, in order: M1 teleop plus LeRobot datasets (committed), then M2 an ACT
  policy moving the arm under supervision (stretch). M1's demonstrations are
  M2's training data. Recorded in [LAB_PLATFORM_VISION.md](LAB_PLATFORM_VISION.md).

### Lab PC install pinned with uv

- Branch `chore/uv-setup`: `uv.lock` (78 packages), `.python-version` 3.12,
  [START_HERE_WINDOWS.md](../START_HERE_WINDOWS.md) option A
  (`uv sync --locked --extra windows --extra test`), CI check
  `uv lock --check`.
- Evidence: a fresh locked sync into an empty environment passed the full
  offline suite (380 tests at the time).

### Lab positions, drift check and fault guidance (built)

- Plan: [plans/2026-10-01-lab-positions-and-recovery.md](superpowers/plans/2026-10-01-lab-positions-and-recovery.md),
  spec: [specs/2026-09-30-lab-positions-and-recovery-design.md](superpowers/specs/2026-09-30-lab-positions-and-recovery-design.md).
- What the operator gets in `python -m scorbot.lab`: `b` back to start, `m`
  mark a pose (P1-P9, this session only), `g` go to a mark. Plans move one
  joint at a time (elbow, shoulder, base) in the same 1 or 0.5 degree jogs,
  need a typed `BACK` / `GOTO P<n>`, and stop on any key between steps (a
  software pause; the physical stop is the stop). Before every step the
  counts are compared with the last step; beyond 20 counts the step is
  refused. Failures show plain-language guidance (`scorbot/lab/faults.py`).
- How it was built: seven tasks with disjoint file ownership, run by
  parallel subagents in git worktrees in three waves, each task reviewed,
  then one whole-branch review on the strongest model.
- Decisions:
  - Drift limit 20 counts, not 2: the legacy settle loop accepts a joint
    within 20 counts of its target (`openScorbot/libcomm.py`), so 2 would
    refuse after normal jogs.
  - The final review found that drift during a typed confirmation was
    absorbed into the new baseline (reproduced in the simulator); the jog
    now re-reads and re-checks counts after the confirmation.
  - Reviews now flag `plan_complete` answers other than yes or count
    differences beyond 20, and failures before motion.
  - If the terminal cannot read keys during a move, the screen says only
    the physical stop stops it, and on real hardware `b`/`g` are refused.
- Tests: 402 passed, 1 skipped, 2 expected failures; simulated rehearsal of
  jog, mark, go to, back gave `LOG CHECK: 0 problems`. Unverified on
  hardware, including whether the lab console reports key presses during a
  move.

### Clean packet codec, USB upgrade phase A (built)

- Spec: [specs/2026-10-01-packet-codec-design.md](superpowers/specs/2026-10-01-packet-codec-design.md).
- Built: `scorbot/transport/codec.py` (pure, stdlib only, builds every OUT
  packet from named fields), `tests/test_transport_codec.py` (golden tests),
  [PROTOCOL.md](PROTOCOL.md) section 11. Not wired into `Scorbot`, the
  simulator or scripts.
- Evidence: byte-identical to the legacy functions for every `libhex` table
  entry at every sequence byte, every count, `suma`/`resta` at seams and at
  random, and the legacy procedures (`msg_start` handshake, `openMov`/
  `closeMov`, motors on/off, `scorbotoff`, `builder` jog steps) captured
  through fake endpoints. `motion_source_sha256` unchanged.
- Spec deviations: `build_out` takes a `header` keyword because legacy
  `get_msg2(84)` sets byte 2 to 0x0C; signs are the IN sign bytes 128/127,
  as `decode_state` reports them; `step_count` takes a signed step (suma /
  resta) and rejects `|step| > 65535`; `next_sequence(0)` is allowed because
  `msg_start` starts from 0; `scorbot.transport` added to the `pyproject.toml`
  package list.
- Unverified against the controller until phase B compares with captures.
- Why: the legacy USB code (about 3,200 lines) blocks on every jog, has no
  software stop, and has known bugs (BACKLOG 5, 6, 9, 12, 15-17, 19). Editing
  it in place risks the only path proven on the arm (G1). Instead: a clean
  twin, proven byte-identical by golden tests, then checked against captures
  (phase B), then a new driver loop behind a backend switch (phase C).

### Every jog logs its packets

- Commit `3a6243c`. The SDK copies each USB packet in both directions while
  a jog runs and logs a `motion_trace` event; `scripts/usb_trace.py from-log`
  feeds the existing `setpoints` analysis.
- Why: the G1 logs hold only the state before and after each jog, so they
  cannot show whether the arm follows host-streamed targets.

### Platform vision updated

- Commit `331f132`, [LAB_PLATFORM_VISION.md](LAB_PLATFORM_VISION.md).
- Finding (inferred, unverified): the legacy jog already streams one target
  step per packet (write 8 ms + read 12 ms, about 50 per second at most), and
  the G1 jog worked, so the "1 Hz forever" risk is probably smaller than
  framed. Prior work (USNA, Kutzer toolbox) uses Intelitek's DLL with
  wait-until-done destination moves. ACT evidence supports a slow policy over
  a fast follower.

## 2026-09-30

### Lab tool safety from the manual verification

- Commits `e8ca23d`, `32654ed`, `a39fe8b`.
- MOTORS LED asked before every arming; reconnect warning; simulator fault
  `motors_dropped` and `python -m scorbot.lab --simulate --rehearse-motors-dropped`;
  positions spec revised (drift check, back to start is not a re-home);
  S1 capture card gained the manual's open questions (capture G: close
  SCORBASE with motors on).

### Numbers and docs aligned with the manuals

- Commits `a56cd85`, `bab6e04`; analysis in
  [MANUAL_VERIFICATION_IMPACT.md](MANUAL_VERIFICATION_IMPACT.md).
- `BASE_HEIGHT_MM` renamed `SHOULDER_AXIS_HEIGHT_MM` (364 mm is to the
  shoulder axis; the pedestal is 190 mm). Gear ratio 127.1 vs 127.7 recorded
  as unresolved. Wrist joints refuse the counts-per-degree prior. Hazards
  HZ-21 to HZ-24 added (motors off unseen, home shift, holding with motors
  off, stalled sync worker).

### Manual verification added

- Commit `e3ce4c6`: `SCORBOT_Manual_Verification.md` and
  `SCORBOT_Numeric_Facts.yaml`, a source-cited reading of the ER-4u and
  Controller-USB manuals. Result: the manuals settle geometry and travel,
  leave counts per degree, zeros, time-out and holding to measurement.

### Deployment options

- PR #5 (`ad1b2c6`): lab PC native Windows with `uv`, training and inference
  on the GPU server, NiceGUI later, Rerun for review.
