# Project log

What happened, when and why, newest first. One entry per piece of work: the
decision, the evidence behind it, and where it lives (commits, docs, tests).
Git history has the diffs; this file has the story. Hardware claims stay
"unverified" unless a bench log exists.

The implementation plans this log cites under `docs/superpowers/plans/` were removed from the tree on 2026-10-04; they are in git history (last present at commit `e5ef11e`).

## 2026-10-07

### Pre-hardware review of the arm-facing code, and what it changed

- **Review:** Codex `max` (Astra, xhigh) over the whole branch since `e99cce9`, and Gemini `deep`
  (3.1 Pro, high) over the same diff in five chunks (its 24k character limit). Earlier rounds used
  Codex `deep` and `standard`. Every finding was checked against the code; Gemini saw one chunk
  at a time and several of its findings were about code in another chunk (the step limit, the
  progress check and the stall error live in `_coupled_jog`; `park_at_home` works in distances
  from the run's start; the session's 2 degree bound is enforced by `pre_home_jog`), and were
  rejected on that evidence.
- **Raised by both reviewers, fixed:** the wrist roll (both wrist motors the same way) is never
  jogged, so a motor that kept landing short built roll up unseen; `joint_move.run` and the park
  now fault past `ROLL_FAULT_COUNTS` (60 counts, about 2 degrees). An unexpected exception in a
  pre-home move, the park or inch homing left the move half done with the motors on; all three now
  switch the motors off and latch (and the tail after the last jog fails closed through
  `_fail_closed`). A stop during the pre-home step used to continue the loop; it now ends the step.
- **Codex only, checked and fixed:** a stop requested between "was one asked for" and the clear was
  wiped (`_stop_lock`, `_consume_stop`, every check-and-clear site); a second Ctrl-C in the
  accounting reads could skip the motor-off (the latch now comes first); the inch search keeps the
  wrist's angle to the forearm, so the wrist is not where home would put it: the session now asks
  whether the gripper points as at home and ends before any jog if not (model-based, unverified).
- **A correction to earlier advice:** the legacy `home()` is not wrist-free. It drives the wrist
  pitch and roll toward their switches (`openScorbot/setHome.py`, BACKLOG 3 and 4). It still has the
  only record on this arm (2026-09-29), so it stays the first thing to try; `SESSION_PROCESS.md`
  now says so and puts a bounded coupled direction check before any inch home.
- **Rejected, with reasons:** keeping the stop set after a raised `MotionStopped` (the raise is the
  acknowledgement; a latched stop would refuse every later jog); removing the wrist pitch follower
  (an owner decision, 2026-10-07); declining `ARM` or `PARK` as a failure (an operator choice, the
  same as answering no to the park question).
- **Tests:** 1106, lint clean. The simulator and the tests share the vendor formulas, so none of
  this shows whether the wrist signs are right on the arm.

## 2026-10-06 (lab PC)

### Coupled joint moves: the pre-home move, inch homing and the park

- **Why:** the vendor's routine is "bring the robot near home, then home" and to
  end a session at home; the vendor's homing moves every motor a joint needs
  together (its manual jog drives one motor, VENDOR_MANUAL_MOVE_TRACE.md). Ours moved one motor at a time, so a long shoulder sweep forced the
  elbow into its stop (the likely cause of the 2026-10-06 failures). The owner
  allowed wrist moves in these three phases only (2026-10-07).
- **What:** `scorbot/joint_move.py` holds the count vectors for moving one joint
  with all others fixed (from the vendor formulas; the vendor's own coupled
  vectors overshoot the wrist 2x, see `VENDOR_COUPLING_TRACE.md`) and a cumulative
  target with a closed loop on measured counts. `Scorbot.pre_home_jog` (2 degrees
  a call, 60 in all per joint, only while not homed), `Scorbot.home_inch` (its
  steps and offset are now coupled joint moves) and `Scorbot.park_at_home` (back to
  the home counts, the whole-pose check applying) use it. Only the wrist pitch
  (orders 10 and 11) is ever jogged for the coupling, never the roll; `jog_joint`
  still refuses every wrist joint. The simulator's elbow switch now follows the
  elbow joint (shoulder plus elbow counts). A bug the tests found: a fresh target
  per pre-home command lost the small coupled amounts and the wrist never moved
  (12 degrees of pitch drift); the target now lives for the whole phase.
- **Keyboard session (`python -m scorbot.lab --inch-home`):** the vendor routine. After
  the start pose question, "Bring the arm near home first?" enables the motors (same LED
  check) and takes typed moves (`SHOULDER +1`, `elbow -2`, `base 1`; the SDK refuses a wrist
  joint or more than 2 degrees), showing the switch line after each; `HOME` then runs the
  inch home; after the keys "Return the arm to its home pose before the motors go off?"
  and `PARK` run `park_at_home` and say NOT parked, and why, when it cannot. The default
  flow is unchanged. Reviewed by Codex and an Opus agent; their findings (a stale pre-home
  target after a stopped home that drove the shoulder about 9 degrees, a stop lost during
  a wrist jog or in the park, the travel cap reopening on disable, the park calling a
  displaced roll parked, a mid-move gate refusal not latching) were fixed with tests.
- **Not proven:** the signs and sizes on the arm. A wrong wrist sign doubles the
  pitch swing. The first trial must be a visual direction check: an elbow move of
  2 degrees should leave the gripper aligned with the forearm, then a shoulder move
  of 2 degrees should leave the elbow angle unchanged, before any homing.

### Keyboard session: `--inch-home`, and a failed inch home names the joint

- **Why:** on the next arm run the owner used the keyboard session
  (`python -m scorbot.lab`), which homes with the legacy search: the shoulder
  rose a few degrees, stopped with a noise, and the session reported only
  "error code 1" (the same failure as `base-first-02`). That fits the coupling
  finding below (the elbow is dragged into its stop from a folded start). No
  log reached the repo; the owner was away from the lab afterwards.
- **What:** `python -m scorbot.lab --inch-home` (`LabSession(inch_home=True)`)
  homes with `Scorbot.home_inch`, with the same typed words and prompts, the
  same near-home warning and the switch line (`state.switch_summary`, moved from
  the bench script into the SDK). A jog or read that fails inside `home_inch`
  now raises "Home search failed on the shoulder on jog 7 (+6.0 degrees toward
  its switch before it): ..." and the log row says the same, so the terminal line
  names the joint, the jog and the distance. The legacy home stays the default.
  Tests: `tests/test_lab_session.py`, `tests/test_inch_home.py`. Never run on
  the arm.

### The two legacy arithmetic bugs fixed

- **What:** `libdef.suma` and `resta` wrapped a counter only once, so a step of
  a full turn (65,536 counts) or more left a value above 65,535 or below 0 and
  the 4-hex-digit position field came out misaligned or negative. Pinned since
  the property tests as the suite's only two `expectedFailure` tests
  (BACKLOG 12). The arm never reached it: real steps are at most about 100.
- **Fix:** the `if` became a `while` in both, five lines in `libdef.py`. For
  every real step the loop runs once, so no byte sent for a real move changes;
  `tests/test_legacy_properties.py` pins that against a copy of the old logic
  for every step up to one full turn around both seams, and the two known-bug
  tests were flipped to ordinary tests. The suite now reports no expected
  failures. Editing `libdef.py` changes `motion_source_sha256` in lab logs, as
  intended.

### Inch homing (`Scorbot.home_inch`): a home that needs no start pose

- **Why:** the legacy `home()` failed on 2026-10-06 (`base-first-02`, code 1
  after about 4 s) and only works from a fixed start pose; the owner does not
  want SCORBASE in the loop. The vendor DLL's homing was traced
  (`docs/protocol/VENDOR_HOMING_TRACE.md`, from disassembly): the controller
  runs the search, it reverses after a stall or `MaxDistance`, it backs off by an
  offset, then `48` zeroes the counter. `4D` and `48` have never been sent, so
  they need a capture; everything else the vendor does uses legacy bytes.
- **What:** the owner chose the vendor's offsets and a plain build. The joint is
  inched with the legacy jog (at most 1 degree a step) and the switch bit read
  after each step: toward the switch up to 30, 30 and 100 degrees (shoulder,
  elbow, base; see the coupling finding below), back and the other way if not found,
  then off the switch and back to its near edge (at most 6 degrees), then the vendor
  offset (shoulder -190, elbow +45, base 0 counts), then the counts are recorded
  as the session home. Shoulder, elbow and base only; no wrist; no `48`; no new
  command byte. Polarity (bit set = on the switch) and directions are the
  vendor's, from disassembly. Edge accuracy is about a quarter of a degree (the
  legacy jog settles within 20 counts).
- **Where:** `scorbot/inch_home.py` (pure), `Scorbot.home_inch` and
  `Scorbot._jog_joint(..., homing=True)` in `robot.py`, `--inch-home` on
  `examples/bench_joint.py` and `bench_stream.py`, `tests/test_inch_home.py`
  (36 tests, simulator with modeled switches). **Never run on the arm.**
- **Reviews (Codex and an Opus agent; Gemini returned 402):** fixed the same day:
  a measured-progress check (a coarse step that moves under 40 percent faults
  before the next jog); a stop request that arrives as a step ends is kept for
  the next step and the end; a switch read needs two agreeing readings (one
  false reading had produced a wrong home about 1 degree high); back-off and
  creep are capped at 6 degrees; a jog ceiling under 2 degrees is refused; an
  interrupt or a failed read latches and switches the motors off, and says which
  joint; the offset uses the jog's own count scale. **Left open (owner's call,
  stated in the CLAUDE.md row):** the source model's joint limits are not checked
  during the search because there is no home to measure them from, so a missed
  shoulder switch drives it toward its upper stop (about 1 to 2 jogs before the
  progress check or the controller's error word); the elbow moves without the
  vendor's coupled wrist drive, so in the model a long elbow sweep takes the
  wrist pitch past its limit; the wrist is not homed and the pose checks assume it
  stands where it would at home; the simulator has no hard stops or coupling and
  its switch positions and the approach directions come from the same sources as
  the code, so the tests cannot catch a sign error that exists on the arm; the
  switch width is unmeasured (a switch narrower than the 1 degree coarse step
  could be skipped).
- **Not copied from the vendor:** the coupled motors that move with the shoulder
  and elbow, wrist homing, stall detection by the controller, the parameter
  download, `48`.

- **Coupling finding (after the reviews; changes the advice):** the elbow and
  wrist motors are mechanically coupled to the shoulder, and the vendor homes with
  them moving together. By the source model (the vendor's own counts-to-angles
  formulas), shoulder +1000 counts alone changes the elbow joint angle by -8.81
  degrees (pitch 0); elbow +1000 alone changes elbow -8.81 and pitch +8.81. So
  with the other motors held, a shoulder sweep of N degrees swings the elbow joint
  by N degrees, and the elbow's range is 135 degrees: a 150 degree sweep from a
  folded pose drives the elbow into its stop. This is the likely cause of the
  legacy home's failure (code 1 after about 4 s from the arm's lowest point), and
  it means `home_inch` from a folded pose would fail the same way. The default
  search caps are therefore small (shoulder 30, elbow 30, base 100): the arm must
  start within a few tens of degrees of its home pose. Compensating only the elbow
  just moves the swing to the wrist pitch (model: pitch -8.81 per +1000 shoulder).
  The vendor's offset vectors (shoulder -190 -> elbow +190, m1 +95, m2 -95) and the
  model disagree by a factor 2 on the wrist part (the model's cancelling vector is
  about -Ds/4 on m1 and +Ds/4 on m2). **Settled by a trace of the DLL**
  (`docs/protocol/VENDOR_COUPLING_TRACE.md`, from disassembly): the model is right
  and the vendor's offset vectors overshoot the wrist by about 2x (they are
  hand-picked integer halves with no geometric basis, and every coupled counter is
  re-zeroed at the end of a full home, so it did not matter to the vendor). The
  count change that moves only the shoulder by Ds keeping the elbow, pitch and roll
  angles fixed is (base 0, shoulder Ds, elbow -Ds, m1 -0.246 Ds, m2 +0.246 Ds);
  elbow by De: (m1 +0.246 De, m2 -0.246 De). Real arbitrary-pose homing needs the
  coupled motors moved with each shoulder or elbow step, which means wrist motor
  moves (disabled by the owner's rule) and, ideally, the multi-motor stream;
  neither has run on the arm. Not built.

### Travel cap lifted from 10 to 180 degrees, with a whole-pose limit check

- **Why:** the owner wanted real tests and judged the 10 degree cap too tight.
  This skips the staged widening (10, 20, 45, ...) of
  `docs/specs/2026-10-04-streaming-driver-requirements.md` and the evidence
  gate of `docs/specs/2026-10-06-travel-cap-widening-plan.md`. **The owner's
  decision; no lab evidence behind it.** Nothing in the logs of that day shows
  a jog: `idle-01` is idle only, `base-first-01` was declined before homing,
  `base-first-02` enabled the motors and `home()` failed after about 4 s with
  `Legacy controller returned error code 1` (a joint error word at or above
  40; which joint is not in the log). The documented way to home is SCORBASE
  first, then the legacy search from the pose it leaves
  (`docs/lab/ARM_CONTROL_BENCH.md`); not yet tried.
- **What changed:** `limits.TRAVEL_CAP_DEG` is 180, past every joint's range,
  so the source model's joint limits (`source_model.LIMITS_DEG`: base -132 to
  174, shoulder -28.28 to 124, elbow -140.8 to -5.16, pitch -109.65 to 113;
  narrower of the toolbox's and the vendor INI's, unmeasured) are the travel
  bound. The motion fingerprint in lab logs changes.
- **The gap this opened, and its fix:** the elbow's limit was enforced only
  in the simulator's `Mover`; on the real arm the 10 degree cap was the only
  thing bounding it. `jog_joint` and `start_stream` now check the whole pose
  (`source_model.pose_limit_excess`; `StreamCore(pose_check=...)` on every
  target and every setpoint): the elbow's angle follows the shoulder's count,
  and the wrist pitch follows both while the wrist motors stand still. Alone
  from home that gives (counts, `source_model.home_window`): base -24688 to
  18729, shoulder -10200 to 422, elbow -2366 to 5196. In the simulator a
  positive elbow jog gives negative counts, which reach the pitch limit after
  20.8 degrees; a negative jog reaches the elbow's own limit after 45.8. An
  arm already past a limit may be jogged back in, never further out.
- **Tests:** `test_limits`, `test_source_model` (new `PoseLimitTests`),
  `test_arm_control`, `test_streaming_core`, `test_streaming` updated or added;
  `test_follow`, `test_lab_session`, `test_lab_teleop`, `test_lab_replay` pin
  the cap to 10 inside the test so they still exercise the mechanism;
  `test_mover`, `test_toolbox`, `test_ui_*` now use targets past a joint
  limit. Docs that said "10 degrees" updated.
- **Still open (from a read-only review of the limits against the sources):**
  the 11.6 percent shoulder/elbow scale disagreement (113.51 against 101.68
  counts per degree) is unmeasured, and at 180 degrees a scale error is tens of
  degrees, not a few; the shoulder has only 3.72 degrees (about 422 counts) of
  margin above home against a 190 count homing offset; real hard-stop
  positions, table, cable and gripper clearance are not measured; the lead
  limit has never fired on the arm. Start every new motion small.
- **Reviews (Codex and an Opus agent; Gemini returned 402, credits depleted):**
  found and fixed the same day: the stream pose check assumed the wrist at
  home (now the measured wrist counts at stream start); a per-motor window
  refused jogs that moved back toward a limit; the jog check failed open or
  raised without latching when home counts were missing or the arm could not
  be placed. **Left open, owner's call:** the limits assume the legacy home is
  the vendor home, but the vendor backs off its switches by shoulder -190,
  elbow +45, pitch +850 counts; 850 pitch counts moves the computed pitch about
  30 degrees, more than the 20.8 degrees the elbow has on that side, so the
  limits can be wrong by more than the travel they allow, in either direction.
  Codex advised keeping a conservative cap until the home offset is measured.
  A multi-axis stream path may also touch a coupled limit between two valid
  poses and fault (fails closed, not reproduced). The stream reads the wrist
  once, at start.

## 2026-10-05 (desk work for the lab visit)

### Three front doors, simulator only

- **Why:** the owner wants a teaching tool and a research tool for VLA. One arm
  model, three ways in: `scorbot.toolbox.Arm` (MATLAB-style names from the USNA
  toolbox), a browser page (`python -m scorbot.ui --simulate`, Viser, `ui`
  extra) and the existing LeRobot plugin. Spec:
  `docs/specs/2026-10-05-three-front-doors-design.md`.
- **Where:** `scorbot/mover.py` (the one owner of `start_stream`; degree and
  millimetre targets, closes a stream nobody drives), `scorbot/toolbox.py`,
  `scorbot/ui/` (`panel.py` logic with no Viser import, `app.py` the page).
- **Owner decisions:** Viser as an optional extra, never on the lab PC;
  millimetre targets allowed in the simulator only (the "no Cartesian motion"
  rule now says "on the arm"). Every new class refuses a robot that is not a
  `SimulatedScorbot`.
- **Reviewed by Codex three times** (design, code, then the page). Found and
  fixed: stale feedback leaving a stream open, a failed stop reported as a
  timeout, Stop waiting behind a stream close, abandoned targets resuming, Stop
  lost between a stream ending and the gripper starting, `is_moving` hiding a
  fault, `scorbot.ui` missing from the package list, late slider events
  restarting the arm after Stop, a gripper left running when the last tab
  closed, the drag handle fighting the user, one failed read ending the page.
  Rejected: a watchdog thread (over-engineering) and moving Home onto a worker
  thread (it would not make Stop faster; pinned by a test instead).
- **Not done:** recording from the browser (the exporter only understands jog
  sessions) and a policy-loop document. Nothing here has touched the arm.

### Wrap-aware settle check (backlog 5)

- **Change:** the nine `abs(target - media) > 20` checks in `libcomm.py` and
  `setHome.py` now call `motion_profile.count_distance`, which equals `abs`
  away from the 0/65535 seam. `moveXYZ.py` is left (order 19 is never sent).
- **Evidence:** `tests/test_settle_seam.py` drives the real `libcomm.move_hips`
  against fake endpoints; a 1 degree jog back to zero with the arm reading just
  below zero ended in result 2 before the change and ends cleanly after it.
- **Found while building:** `SimulatedScorbot` has its own jog logic and never
  runs the legacy loops, so no simulator test (including seam jitter, which
  stops during moves) can show this bug. The fake-endpoint harness of
  `tests/test_software_stop.py` can.
- **Prior art for the method:** difference modulo the counter size, treated as
  signed ([PMUL rollover](https://industrialmonitordirect.com/blogs/knowledgebase/troubleshooting-logix-pmul-block-encoder-rollover),
  [24-bit wrap to signed](https://industrialmonitordirect.com/blogs/knowledgebase/resolving-24-bit-encoder-wrap-around-to-signed-mm-in-tia-portal)).
- Changes `motion_source_sha256`, as intended. Never run on the arm.

### Lab session log to calibration CSV

- `scripts/build_calibration_csv.py` takes counts, robot id and approach from
  lab session logs and only the physical angles from the operator, writes the
  CSV `fit_calibration.py` wants, and can run the fit. The lab session now
  prints `Step n:` after each jog so a reading can name its step.
- **Codex found four ways to a wrong calibration, all fixed:** a log with two
  sessions or two homings (simulated counts labelled physical), a move target
  typed by the operator (now derived from the logged steps and the home
  reading), `1`/`01`/`+1` counting one jog three times, and two logs with the
  same file name replacing each other.
- Runs on synthetic logs in the real row shapes only. The new lab card is
  `docs/lab/ACCEPTANCE_RUN.md` (1 degree steps; `bench_joint` refuses more, and
  a phone level cannot tell the two shoulder scales apart from one degree).
- The two implementation plans for this day's work were dropped once built, as
  on 2026-10-04; what lasts is in the spec, the backlog and these notes.

## 2026-10-04

### Vendor DLL read by static analysis

- Two builds of Intelitek's `USBC.dll` (2008 and 2018, from the public USNA
  and ROS repositories) were decompiled with Ghidra and read. Nothing was
  loaded or run, and the arm was not involved.
- Result: `docs/protocol/VENDOR_DLL_PROTOCOL.md`. The vendor's 64-byte message layout,
  its command-letter table, the connect, control and stop sequences, and the
  reply layout. Every command byte the legacy code sends is in the vendor's
  table. All of it is from disassembly and unverified until a capture agrees.
- Findings that matter most: the reply echoes the message ID (flow control we
  do not use), the arm stop is `47` + `4F 3F 53` (the legacy `closeMov` lacks
  the `47`), motion is a stream of `0D` setpoint messages, and the encoder
  count is a 24-bit number offset by `0x7FFFFF`.
- Method and tools: `tools/usbc_analysis/`. The DLLs and decompiled output
  stay outside the repository.
- Decided with the user: read the DLL, do not run it. Findings get confirmed
  with SCORBASE captures (`docs/lab/VENDOR_PROTOCOL_LAB_PLAN.md`, one row per
  claim). The ctypes bindings were parked in `tools/usbc_probe/` (removed 2026-10-05; in git history), outside the
  SDK. Spec: `docs/specs/2026-10-04-vendor-dll-design.md`.
- Rule change, decided by the user: packet sequences built only from command
  bytes the legacy code already sends may now be changed on the strength of
  the disassembly, tried first on a 1 degree jog. New bytes still need a
  capture. Root `CLAUDE.md` and `openScorbot/CLAUDE.md` carry the wording.
- Reviewed by Codex (adversarial, against the decompiled code): two wording
  errors about the stop sequence found and corrected. Gemini unavailable.

### Owner decisions, 2026-10-05: build from sources, confirm at the lab

- **Why:** lab time is short and the arm is not ours. The ER-4U is well
  described already: the vendor's DLL, the USNA ScorBot Toolbox, a ROS 2
  description, the manuals, the original thesis. The plan is to build one
  model from those, run everything on it in the simulator, and use the lab
  only to confirm it with a short acceptance run.
- **Rule changed:** a second calibration tier, "sources"
  (`scorbot/source_model.py`), may drive motion inside the travel cap until
  the acceptance run confirms or corrects it. Measured calibration stays the
  only tier that can widen travel.
- **Gripper allowed** through `Scorbot.move_gripper` (legacy sequence).
- **Policy:** ACT first, by the easiest route; SmolVLA after.
- **Found the same day:** the vendor zeroes each encoder at the end of homing
  (a "set position" message with 0), so "all counts zero" is its home pose.
  The legacy homing does not, and stops a little past the switch, so our
  home differs from the vendor's by a small amount per joint. That offset is
  one of the things the acceptance run reads.

### Streaming driver (built in the simulator, not yet on the arm)

- Why now: reading the vendor planner showed it refuses a new target while
  moving ("motion in progress"). There is nothing to copy; streaming with
  retargeting is ours to design. The owner chose to stop reading the vendor
  lifecycle and build.
- Requirements and the owner's four decisions:
  `docs/specs/2026-10-04-streaming-driver-requirements.md`. The
  "no coordinated motion" rule was narrowed: base, shoulder and elbow may
  move together as count targets inside the travel cap.
- Built as a USB-free core (`scorbot/streaming.py`, Ruckig online for the
  speed, acceleration and jerk limits) and a thin hook into the legacy
  worker (`libcomm.stream_targets`, order 21), so every byte is one the arm
  has accepted from a jog. `Scorbot.start_stream` returns a `Stream`.
- Starting values are vendor priors: 24 ms, a quarter of 6500 counts/s.
  Travel is capped at 10 degrees from home and is to be widened in stages
  with lab evidence.
- Off by default until the lab confirms the bytes: pacing on the echoed
  message ID, and the emergency-bit fault.

### Raw reply bytes and a software stop (built, not yet on the arm)

- Design and plan: `docs/specs/2026-10-04-software-stop-design.md`.
  Built on the owner's instruction to plan and do it while he was away.
- Idle recordings keep the raw reply (`raw_hex` per sample row, from
  `Scorbot.get_state_and_packet`), so one e-stop press at rest can confirm the
  emergency bit. `RobotState` is unchanged.
- `Scorbot.request_stop` ends a jog early (BACKLOG 6). The legacy loops for
  base, shoulder and elbow check a stop event after each step and in the
  settle loop, then send `47` and the three close commands, the vendor's arm
  stop, all carrying the measured position instead of the jog target (the
  vendor's stop copies measured positions over its setpoints first). Only
  bytes the legacy code already sends. `jog_joint` raises `MotionStopped`; a
  clean stop is not a fault, an arm that does not settle is.
- "Has it stopped" is taken from the USNA ScorBot Toolbox for MATLAB
  (`ScorWaitForMove`): successive readings 0.05 s apart that no longer change,
  giving up after 8 s; we ask for three such pairs in a row.
- Lab trial: `examples/bench_joint.py --stop-after-ms N` on the usual 1 degree
  jog (lab plan step F3).
- It is not an emergency stop and has never run on the arm. Evidence so far:
  fake endpoints (exact command sequence) and the simulator.
- Reviewed by Codex (`review` and `adversarial-review`; Gemini had no
  credits). Five findings, all accepted and fixed: a bench trial that could
  report success with nothing sent, a last-step stop that kept the full
  target, a request lost in the settle loop, one quiet reading pair taken for
  rest, and an interrupted jog stored as completed. The design doc lists
  each with its fix.

## 2026-10-02

### Replay and preview (M1 finish)

- Spec `docs/specs/2026-10-02-replay-and-preview-design.md`,
  plan `docs/superpowers/plans/2026-10-02-replay-and-preview.md`. A Codex
  adversarial review of the first design found LeRobot's replay cannot
  verify which dataset drives the arm, connects outside its cleanup and has
  no stop key; real-arm replay therefore became lab-tool key `p`, reusing
  every gate, with a dataset preflight, a start-pose plan and an arrival
  check. The LeRobot plugin is simulator-only until M2.
- Datasets now carry `scorbot_episodes.jsonl` (verified value for value
  against LeRobot's stored actions; sha256 in the provenance).
- Verified: LeRobot's own `replay()` drives the simulated arm through the
  plugin to the recorded final position. That run also showed a tick can
  land inside a simulated jog; the simulator changes counts at the end of a
  jog, so the state-during-motion rule now applies to real data only.
- `--preview report.html`: self-contained episode report, checked in a
  browser.
- M1 is complete in software; it needs one real run in the lab (record,
  export, replay with key `p`) to be done.

### Final review of steps 3-5 (camera, teleop, exporter)

- Codex review and adversarial review of everything not yet on GitHub,
  plus web research (OpenCV low-latency capture practice, LeRobot recording
  conventions, Microsoft's GetAsyncKeyState documentation).
- Fixed: camera file close could exceed stop()'s deadline; frames queued at
  stop were not counted; the exporter held whole episodes of decoded images
  in memory; unclean (crashed) recordings could be exported; robot-only
  episodes skipped the state-during-motion rule; episodes with no motion or
  a failed attempt counted as demonstrations (stopping now asks "Task
  done?"); and GetAsyncKeyState's silent failure (returns 0) could reopen
  the held-key double step, so the release gate also waits out this PC's
  key-repeat timing. The webcam now requests a one-frame driver buffer.
- Left open, by decision: webcam image latency is unmeasured until the lab
  stopwatch check; real video datasets carry `image_latency: unmeasured`.

### LeRobot exporter (M1 step 5)

- Spec `docs/specs/2026-10-02-lerobot-exporter-design.md`, plan
  `docs/superpowers/plans/2026-10-02-lerobot-exporter.md`, guide
  `docs/design/LEROBOT_EXPORT.md`. Decided with the user: LeRobot 0.6.1 in a
  separate CPU environment `.venv-lerobot`, 10 fps, robot-only datasets only
  with `--no-video`, local folder only.
- A Codex adversarial review found the 10 fps grid could erase short jogs,
  held states could contradict the video during motion, the target check
  compared two values from the same source, image latency is unknown, and a
  failed build could publish a partial dataset. Fixed: every jog labels at
  least one frame, state during a jog comes from the recorded USB packets,
  the action is the SDK's own target, publication is atomic; image latency
  is recorded as unmeasured with a stopwatch procedure in the guide.
- Gemini was not used: its API key's prepaid credits ran out mid-review
  (the earlier Gemini reviews this session were billed to them).
- No `lerobot` extra in `pyproject.toml`: its OpenCV pin conflicts with the
  `camera` extra in one universal lock.
- Verified: the main suite, and a real round trip in `.venv-lerobot` (a
  simulated teleop session with camera frames exports, reopens with
  `LeRobotDataset`, and its actions match the SDK's jog targets).

### Keyboard teleop (M1 step 4)

- Spec `docs/specs/2026-10-02-keyboard-teleop-design.md`, plan
  `docs/superpowers/plans/2026-10-02-keyboard-teleop.md`. Decided with the
  user: keyboard only for now, arm once then each press moves (no per-step
  questions), episodes by key with a task text, 10 degree cap kept until
  calibration (G2).
- Codex and Gemini adversarial reviews both found that a quiet gap in
  console input cannot prove a key was released; teleop now waits on the
  physical key state (`GetAsyncKeyState` via ctypes, no new dependency).
  Codex found a stalled camera can still report ok health; episodes now need
  a frame written in the last second, checked on every key and every 0.2 s.
  Gemini's 15 s teleop idle disarm and log-every-key were adopted; its
  suggestion to disarm on a camera fault was rejected (the camera records
  data, the operator watches the arm).
- `scorbot/lab/teleop.py`, `scorbot/lab/camera.py`, `--camera`, the
  `/session/episode` topic, and the full commanded target
  (`target_signed_counts`) on every jog command (the dataset action).
- Not yet run in a real Windows console; rehearse with
  `python -m scorbot.lab --simulate --camera fake` before the lab.

## 2026-10-01

### M1 roadmap and foundations

- Roadmap for the M1 desk work (`docs/specs/2026-10-01-m1-roadmap-design.md`):
  foundations, simulator realism, camera, teleop, LeRobot export, with
  shared contracts for clock, action, units and evidence order. An
  independent review found teleop bypassing the lab session gates, no action
  record in the MCAP, an undefined camera file contract and a loosened elbow
  span; all fixed in the plan before any code.
- Lab PC moves to Python 3.13: before 3.13 the Windows monotonic clock ticks
  about every 15.6 ms. `uv.lock` unchanged (every lab dependency has a cp313
  Windows wheel).
- BACKLOG #46 fixed: `motion_source_sha256` normalises CRLF to LF before
  hashing, so Windows, Linux and ZIP copies of the same code match. Logs
  written before this fix hashed raw bytes; their values only match a
  checkout with the same line endings.
- Manual contradictions: one verdict each in `nominal.CONTRADICTIONS`
  (adopted, both kept, measure). No bound loosened: the elbow gate stays at
  the manual's 260 degrees although the vendor files allow 275. Vendor joint
  and encoder limits and datasheet joint speeds added as priors.
- Every jog's `motion_preview` row now carries `vendor_limit_report`:
  where the target, in wrap-aware counts from the session home, sits against
  the vendor encoder limits under both sign hypotheses. Diagnostic only, never a guard; it collects evidence on
  whether our homing zero matches the vendor's.
- Offline trajectory planner `scorbot/planning.py` (Ruckig, optional
  `[planning]` extra, also in `[dev]` so CI runs its tests): synchronised,
  jerk-limited point-to-point plans in signed counts, sampled at the vendor
  16 ms host period. Velocity priors from datasheet speeds; acceleration and
  jerk have no defaults because no source gives them. Wired into nothing
  until S1 shows the controller can follow streamed targets.

### Camera capture (M1 step 3)

- Spec `docs/specs/2026-10-01-camera-capture-design.md`, plan
  `docs/superpowers/plans/2026-10-01-camera-capture.md`. Each camera writes
  `camera-<id>.mcap` plus a close-time sidecar and shares nothing with the
  robot recording; an independent review removed a shared manifest that had
  race and crash windows and corrected how DirectShow settings can be
  verified (raw read-back is a backend flag; verify frame size and fps).
- `scorbot/camera/`: `CameraStream`, `scan_stream` / `iter_frames`,
  `FakeSource` / `OpenCVSource`, `CameraRecorder` (reader and writer
  threads, byte-bounded queue, latency limit, owner-drained health, bounded
  `stop()`), `python -m scorbot.camera check`. `log_frame` now requires the
  capture time. OpenCV is the optional `[camera]` extra (also in `[dev]`).
- Not yet tried on a real webcam; run `python -m scorbot.camera check` on
  the lab PC with the webcam before recording training data.

### Realistic rehearsals (M1 step 2, BACKLOG #40 in part)

- `SimulatorProfile` in `scorbot/simulated.py`: modeled homing that drives
  each motor onto its switch and back off in the legacy `setHome.py` order,
  with vendor INI offsets and a 200-count switch width (from a replacement
  controller project, weak prior); a never-found switch fails with the
  legacy time-out code 2; rest jitter that crosses the 0/65535 seam; and
  front-panel LEDs (`leds()`). Default profile unchanged, so existing tests
  keep the instant model. It uses the legacy axis order but not the legacy
  search itself, so it rehearses what the operator sees, not the code path.
- `python -m scorbot.lab --simulate` uses `REHEARSAL_PROFILE` (homing about
  3 s, jitter 1 count so the idle check stays quiet) and shows a
  `SIMULATED panel:` line before every LED
  question. Decided with the user: show the modeled panel rather than make
  the operator guess, and fast homing rather than a 30-60 s wait.

### Semester goal decided

- Both, in order: M1 teleop plus LeRobot datasets (committed), then M2 an ACT
  policy moving the arm under supervision (stretch). M1's demonstrations are
  M2's training data. Recorded in [LAB_PLATFORM_VISION.md](../design/LAB_PLATFORM_VISION.md).

### Lab PC install pinned with uv

- Branch `chore/uv-setup`: `uv.lock` (78 packages), `.python-version` 3.12,
  [START_HERE_WINDOWS.md](../../START_HERE_WINDOWS.md) option A
  (`uv sync --locked --extra windows --extra test`), CI check
  `uv lock --check`.
- Evidence: a fresh locked sync into an empty environment passed the full
  offline suite (380 tests at the time).

### Lab positions, drift check and fault guidance (built)

- Plan: `plans/2026-10-01-lab-positions-and-recovery.md`,
  spec: [specs/2026-09-30-lab-positions-and-recovery-design.md](../specs/2026-09-30-lab-positions-and-recovery-design.md).
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

- Spec: [specs/2026-10-01-packet-codec-design.md](../specs/2026-10-01-packet-codec-design.md).
- Built: `scorbot/transport/codec.py` (pure, stdlib only, builds every OUT
  packet from named fields), `tests/test_transport_codec.py` (golden tests),
  [PROTOCOL.md](../protocol/PROTOCOL.md) section 11. Not wired into `Scorbot`, the
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

- Commit `331f132`, [LAB_PLATFORM_VISION.md](../design/LAB_PLATFORM_VISION.md).
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
  [MANUAL_VERIFICATION_IMPACT.md](../manual/MANUAL_VERIFICATION_IMPACT.md).
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
