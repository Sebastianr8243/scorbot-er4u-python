# ScorBot lab platform: goals and roadmap

Status: draft brief, 2026-09-29. This file records the goals before any design
spec. Nothing below is built yet unless it says so. Hardware claims are
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

## The main technical risk

LeRobot and VLA policies send a new joint target many times a second (often
10 to 50 Hz). Our stack can only do one blocking jog, which takes about 1 s or
more. Whether the controller accepts streamed setpoints (the per-joint target
bytes in every USB packet) is **unknown**. The answer decides how far the
learning tracks can go:

- **Setpoints work:** smooth control at a few Hz or more, and a normal LeRobot integration.
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
| S2 | Motion layer | Calibration (G2), multi-joint waypoint moves from safe steps, cancel and watchdog; streaming if S1 allows | Yes |
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
| 7 | Done: [S1 capture lab card](S1_CAPTURE_LAB_CARD.md) for items 1, 3-5 | Desk | Maintainer | Next visit |
| 8 | Design the S4 dataset exporter (sessions to LeRobot v3.0); spec, then build against simulated sessions | Desk | Maintainer | Imitation learning |
| 9 | Prototype a LeRobot plugin on `SimulatedScorbot` in a separate Python 3.12 venv | Desk | Maintainer | Record and replay |
| 10 | Pick a camera and mount; pick a teleop device | Desk | Team | S3, S4 |
| 11 | Find out whether a course or deadline shapes the teaching goal | Desk | Team | S5 priority |

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

The safety invariants in [CLAUDE.md](../CLAUDE.md) apply to every sub-project.
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
- The lab PC records and runs the arm. Training happens on another machine or in the cloud.
- The first learned task avoids the gripper.
- One arm and one camera to start.

Open:
- Do the controller's target bytes accept streamed setpoints? (S1)
- What control rate can the arm hold? (S1)
- Which camera and mount? (S3)
- Which teleop device: keyboard, gamepad, or a leader arm? (S4)
- Is there a course or deadline the teaching use must meet?
