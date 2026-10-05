# ScorBot lab platform: goals and roadmap

Status: draft brief, 2026-09-29, updated 2026-09-30 with the manual
verification and prior-work research (see "What we learned"). This file
records the goals before any design spec. Nothing below is built yet unless it says so. Hardware claims are
unverified unless a bench log exists.

## Goal

Turn the Intelitek ScorBot ER-4U into a **teaching and research arm** that can
be driven three ways:

1. **Traditional control**: joint and Cartesian trajectories, kinematics,
   calibration and control experiments.
2. **Imitation learning**: record human demonstrations, train a policy
   (for example ACT or Diffusion Policy) and run it on the arm.
3. **Vision-language-action (VLA)**: a camera plus a text instruction drive the
   arm through a VLA model (for example SmolVLA or pi0).

LeRobot (Hugging Face) provides the learning side: dataset format, policies,
training and the Hub. This repo provides the ScorBot side: safe control,
evidence logging and calibration.

## Who it is for

| Order | User | What they need |
|---|---|---|
| First | Our own research team | A flexible toolkit with rigorous evidence. Rough edges are acceptable. |
| Later | Students in a course | Guided workflows, strong guardrails, simple setup |
| Later | Other labs with ScorBots | Packaged install, docs, support for different setups |

Design choices should not block the later users, but the team comes first.

## Where we are (2026-09-29)

| Item | Status |
|---|---|
| SDK, safety gates, fault latch, simulator, JSONL and MCAP recording | Built, tested offline |
| G1: supervised home and a -1 degree base jog on the lab PC | Reported as successful by the operator. Logs are on the lab PC and not yet reviewed. |
| Calibration (degrees per count, directions) | Not measured |
| Multi-joint, streaming or Cartesian motion | Not possible today: one blocking single-joint jog at a time |
| Wrist and gripper | Locked off, never measured |
| Camera | Session schema only, no capture code |
| LeRobot integration | None |

## What we learned (2026-09-30)

Sources: the manual verification ([SCORBOT_Manual_Verification.md](../manual/SCORBOT_Manual_Verification.md),
impact in [MANUAL_VERIFICATION_IMPACT.md](../manual/MANUAL_VERIFICATION_IMPACT.md)),
the deployment plan ([DEPLOYMENT_OPTIONS.md](DEPLOYMENT_OPTIONS.md)) and a
research pass on prior ER-4U work. None of it is a bench measurement.

**Our own code already streams setpoints.** Every legacy jog is a series of
small target steps, one per USB packet: `motion_profile.increments_for_counts`
plans the steps and `libdef` writes them, with a write/read exchange of
0.008 s + 0.012 s (`openScorbot/conf.py`), a ceiling of about 50 exchanges/s
before USB and Python overhead (inferred, not measured). The G1 base jog was
reported to work, so the controller probably followed a host-generated
target stream for one joint and a small move. The 1 s blocking jog is a limit
of our software API, not necessarily of the controller. Unverified: the G1
logs hold only the state before and after each jog, so packet-by-packet
tracking needs a capture or the in-jog packet trace added on 2026-10-01
(action item 14).

**What the manuals settle and what they do not.** The manuals give travel
ranges, link lengths, gear ratios (127.1 or 127.7:1 for motors 1-3, a conflict)
and the 20-slot encoder disk. They do not give counts per degree, joint
zeros or directions, the communication time-out, home retention after
Control Off or an e-stop, or any brake or holding spec. Those come only from
measurement. The manuals also add four hazards (SAFETY_CASE HZ-21 to HZ-24):
the controller can cut motor power without the SDK seeing it, Home can shift
with electrical noise, holding with motors off is unknown, and a stalled
sync thread may trigger the time-out.

**Prior work.**
- USNA (Esposito et al. 2011; the Kutzer ScorBotToolbox) drives the arm
  through the Intelitek DLL. It offers destination moves (`RMoveJoint`,
  `RMoveLinear`) with a speed (1-100 %) or a move time, and callers wait for
  `RIsMotionDone` before the next command. No streaming or retargeting is
  documented, and whether the DLL or the controller plans the trajectory is
  unknown.
- OpenScorbot (this repo's legacy code) is the only known direct-USB Python
  stack, and it generates every intermediate target on the PC.

**ACT at low rates.** No source shows dependable LeRobot ACT with only 1-5
target updates per second. Evidence exists for a slow policy (about 1 Hz
replanning) on top of a fast trajectory follower (about 50 Hz). Inferred plan
for this arm: the policy on the GPU server predicts action chunks at 1-5 Hz;
the lab PC turns each chunk into small target steps at the packet rate.

## The main technical risk

LeRobot and VLA policies send a new joint target many times a second (often
10 to 50 Hz). Our stack can only do one blocking jog, which takes about 1 s or
more. Whether the controller tracks streamed setpoints *in general* (several
joints, changing targets, longer moves) is **unverified**. Since 2026-09-30 it
looks likely: the legacy jog already streams per-packet targets for one joint
and G1 reported that jog working (see "What we learned"). The answer decides
how far the learning tracks can go:

- **Setpoints work (now the likely case):** a fast target follower on the lab PC
  under a slow policy, and a normal LeRobot integration.
- **Setpoints do not work:** the learning tracks run at a low step rate
  (about 1 Hz). This is still useful for teaching and slow tasks, but it is a
  research question whether policies work well at that rate.
- **Fallback:** replace the controller electronics with a modern motor driver.
  This is a separate hardware project.

## Sub-projects

Each sub-project gets its own spec, plan and build cycle.

| # | Sub-project | Delivers | Needs the arm |
|---|---|---|---|
| S1 | Controller measurement | G1 log review, USB capture of Intelitek software, real cycle time, whether setpoints work | Yes (capture only) |
| S2 | Motion layer | Calibration (G2), then a streaming target follower (the legacy per-packet mechanism, without stopping every degree), multi-joint moves, cancel and watchdog | Yes |
| S3 | Camera | Time-synced video in sessions | Camera only |
| S4 | LeRobot bridge | Session-to-LeRobot dataset exporter, `lerobot_robot_scorbot` plugin, teleop input | Exporter: no. Plugin: yes |
| S5 | Lab workflow | One `scorbot-lab` command (record, review, export, replay, eval), checklists | Partly |

### How the sub-projects serve each goal

| Goal | Needs |
|---|---|
| Traditional control | S1, S2, calibration, then the existing offline `scorbot/kinematics.py` wired in after validation |
| Imitation learning | S2, S3, S4 (teleop, dataset, plugin), S5 |
| VLA | Everything above, plus task text labels and a control rate the model tolerates |

## Suggested order

1. **S1**: review the G1 logs and capture USB traces. This decides the control ceiling.
   The capture card now also covers the manual's open questions (home
   retention, time-out, speed levels, holding with motors off).
2. **S4 exporter** (no arm needed): convert existing sessions to LeRobot format, in parallel with S1.
3. **S2**: calibration, then multi-joint waypoint motion.
4. **S3**: camera.
5. **S4 plugin and teleop**, then **record and replay** a demonstration end to end.
6. First imitation-learning policy (reach or point, no gripper).
7. Gripper and wrist measurement, then pick and place.
8. VLA.
9. **S5** grows alongside, and becomes the student-facing tool at the end.

## Action items (2026-09-29)

"Lab" items need the lab PC or the arm. "Desk" items do not.

| # | Item | Where | Owner | Unblocks |
|---|---|---|---|---|
| 1 | Copy the G1 `logs\` folder (with `sessions\` and `.controller.jsonl`) off the lab PC | Lab | Operator | G1 review |
| 2 | Review the G1 logs: planned vs observed counts, faults, timing between packets | Desk | Maintainer | S1, first measured cycle time |
| 3 | Install Wireshark with USBPcap on the lab PC and reboot; note the bound driver | Lab | Operator | Captures |
| 4 | Capture Intelitek software: A basic (idle, home, 1 degree base), B go-to between two taught positions, C same move slow and fast | Lab | Operator | Setpoint question |
| 5 | Switch back to WinUSB and capture our code doing A | Lab | Operator | Protocol comparison |
| 6 | Analyze captures with `scripts/usb_trace.py`; write the S1 findings | Desk | Maintainer | S1 spec, S2 design |
| 7 | Done: [S1 capture lab card](../lab/S1_CAPTURE_LAB_CARD.md) for items 1, 3-5 | Desk | Maintainer | Next visit |
| 8 | Design the S4 dataset exporter (sessions to LeRobot v3.0); spec, then build against simulated sessions | Desk | Maintainer | Imitation learning |
| 9 | Prototype a LeRobot plugin on `SimulatedScorbot` in a separate Python 3.12 venv | Desk | Maintainer | Record and replay |
| 10 | Pick a camera and mount; pick a teleop device | Desk | Team | S3, S4 |
| 11 | Find out whether a course or deadline shapes the teaching goal | Desk | Team | S5 priority |

### Before the next lab visit (added 2026-09-30, all desk work)

| # | Item | Owner | Unblocks |
|---|---|---|---|
| 12 | Review and merge `feat/lab-positions-and-recovery` (MOTORS LED check before every arming, reconnect warning, `--rehearse-motors-dropped`) so the lab PC runs it | Team, Maintainer | Safer next visit |
| 13 | Done 2026-10-01: back to start (`b`), marks (`m`, `g`), counts drift check and fault guidance in `python -m scorbot.lab` | Maintainer | Recovery at the bench |
| 14 | Done 2026-10-01: every jog logs the packets it exchanged both ways (`motion_trace` event; `usb_trace.py from-log` then `setpoints`). SDK side only, no change to `openScorbot/` | Maintainer | Streaming evidence without a capture |
| 15 | Rehearse the whole visit with `--simulate` and `--rehearse-motors-dropped` in a Windows console | Operator | Fewer surprises |
| 16 | Decided 2026-10-01: both, in order (see "Semester goal") | Team | Order of S2-S4 |
| 17 | Prepare the analysis for capture B: does a SCORBASE go-to send one destination or a stream of targets? | Maintainer | S2 design |

## Semester goal (decided 2026-10-01)

Both, as two milestones on one path. The demonstrations recorded for the
first milestone are the training data for the second.

| Milestone | Done when | Needs |
|---|---|---|
| **M1 (committed): teleop and datasets** | An operator drives the arm by keyboard or gamepad with the webcam recording, and the sessions export as a LeRobot dataset that replays on the arm, with logs | S1 captures, S2 calibration and smooth motion, S3 camera, S4 exporter and teleop |
| **M2 (stretch): a policy moves the arm** | An ACT policy trained on M1 data on the GPU server reaches a camera-visible target under supervision, through the same SDK gates | M1, plus the server-to-lab-PC action link (DEPLOYMENT_OPTIONS decision 1) |

M1 does not depend on M2. If S1 shows the controller cannot follow
streamed targets, M1 still works at a low step rate; M2 then becomes a
research question about slow policies.

## Success criteria

| Milestone | Done when |
|---|---|
| Control baseline | Measured cycle time and a documented answer on setpoint streaming |
| Traditional control | A calibrated multi-joint path is executed and matches physical measurements within a stated error |
| Record and replay | A teleoperated episode is saved as a LeRobot dataset and replayed on the arm, with logs |
| Imitation learning | A trained policy reaches a camera-visible target in a stated fraction of trials |
| VLA | A text instruction selects and reaches one of several targets |
| Teaching | A student follows a written lab and completes it without the maintainers' help |

## Rules that stay in force

The safety invariants in [CLAUDE.md](../../CLAUDE.md) apply to every sub-project.
In particular:

- Tests and CI never open USB.
- Any fault latches.
- `disable()` is not an emergency stop.
- Real and simulated data never mix.
- Calibration needs physical measurements.
- Wrist and gripper stay locked until they are measured.

LeRobot's `send_action` must go through the same gates as the SDK, not around them.

## Assumptions and open questions

Stated by the team: the goals (traditional control, imitation learning, VLA),
teaching and research use, and the team as first user.

Assumed, to confirm:
- LeRobot is the learning framework. Needs Python 3.12 or newer and PyTorch.
- The lab PC records and runs the arm. It has no GPU (2026-10-01), so training
  and policy inference run on the team GPU server ([DEPLOYMENT_OPTIONS.md](DEPLOYMENT_OPTIONS.md)).
- The first learned task avoids the gripper.
- One arm and one camera to start.

Open:
- Does the controller track streamed setpoints for several joints and longer
  moves? Likely, from the legacy jog; unverified. (S1)
- What control rate can the arm hold? Code ceiling about 50 exchanges/s,
  unmeasured. (S1)
- Does a SCORBASE go-to send one destination or a stream? (S1, capture B)
- Counts per degree, joint zeros and directions (S2, measurement only)
- Does home survive Control Off or an e-stop, and does the arm hold with
  motors off? (S1 captures D and E)
- Which camera and mount? (S3)
- Which teleop device: keyboard, gamepad, or a leader arm? (S4)
- Is there a course or deadline the teaching use must meet?
