# Roadmap: from "the arm has never moved" to a VLA driving it

Date: 2026-10-15. Status: plan only, nothing built. Owner goal: run a vision-language-action (VLA) policy on the ER-4U.

## Goal in one line

A camera image plus a text instruction goes into a policy; a small joint-delta action comes out at a few Hz; our code applies it to the arm inside hard limits; every run is recorded in a format the policy can train on.

## What a VLA needs, and where we stand

| Need | Have | Gap |
|---|---|---|
| 1. Arm moves at all, reliably | Vendor-DLL jog and home tools, never run on the arm | Visit A and B. Everything below waits on this. |
| 2. Read joint state | Raw state read worked once (2026-09-29) | Counts to joint angles unverified (axis numbers, signs, scale) |
| 3. Command small joint steps at a steady rate | `usbc_jog`: one axis, velocity, at most 5 s. Own streaming path: E2, never run | A VLA needs several joints, 2 to 10 Hz, position or delta targets. The DLL jog is velocity-only and one axis. |
| 4. Gripper | `move_gripper` E2, never run; not offered in `usbc_jog` | A VLA task without a gripper is a pointing demo. Needed before anything useful. |
| 5. Wrist | Blocked by design (two coupled motors, unmeasured) | Needed for any grasp that is not straight down. Decide later; 3-DOF plus gripper is enough for a first task. |
| 6. Camera | `scorbot/camera/` records streams next to a session | Mounting, fixed pose, latency not measured |
| 7. Recorded demos in a training format | `scorbot/session` MCAP; `scorbot/lerobot_export/` exists only as an empty folder (just `__pycache__`) | The exporter is not written |
| 8. Safety layer between policy and arm | Fault latch, travel limits, physical stop | Per-step clamp and a "policy off, arm holds" switch; nothing of this is tested on the arm |
| 9. A policy | None | Pick after steps 1 to 7 give real data |

## Hard problem to settle early: which path commands the arm

Two ways to drive the controller:

- **Vendor DLL** (`USBC.dll`): the controller gets exactly the packets it expects; this is how we avoid the 2026-10-06 `error code 1`. But it needs 32-bit Python, so our 64-bit SDK and a VLA runtime cannot import it. It needs a small 32-bit helper process that the main program talks to over a local socket. The DLL also has `SetJoint` (a direct joint target, unbound and untraced for use) and `MoveManual` (velocity, one axis).
- **Our own USB bytes** (`scorbot/`, streaming): runs in our Python, already has limits and recording, but the home bytes have never been accepted by the real controller.

Pick: **start on the DLL, put it behind a tiny 32-bit server, and keep our own path as the later replacement.** Decide to switch only on evidence: the visit A capture replayed through our port matches the vendor's bytes (`scripts/replay_vendor_home.py`). Until then the DLL is the only path that is known to be accepted.

## Stages

Each stage ends with a check you can read off a log. Do not start the next stage until it passes.

| Stage | Outcome | Done means | Needs the arm |
|---|---|---|---|
| 0. Now | Runbook ready, tests green | `docs/lab/GET_ARM_MOVING.md` committed; 1327 tests pass | no |
| 1. Visit A/B | Arm connects, jogs 3 axes at velocity 1, vendor Home completes | Logs show before/after position delta per axis; axis table filled in; Home exit code 0 | yes, you present |
| 2. Know the joints | Counts to degrees for base, shoulder, elbow, with phone-level checks | Measured scale and sign per joint in `docs/manual/ER4U_MODEL.md`; replaces "inherited" | yes |
| 3. Gripper | Open and close, empty jaws, then a soft object | Gripper position read back before and after; no fault | yes |
| 4. Step API | One function: "move these joints by these small deltas, return the new state", over a 32-bit helper, with a clamp and a hold | Fake-DLL tests for clamp, hold, timeout; then the arm does a 3-joint 1 degree step and reports the delta | build offline, then yes |
| 5. Camera and recording | A teleoperated episode (you drive with keys or a gamepad through the step API) recorded as image, state, action at fixed rate | Exporter writes a LeRobot-style dataset; a replay script reads it back and the numbers match the log | camera yes |
| 6. Demos | About 50 episodes of one simple task (for example: touch or push a block to a mark) | Dataset passes the replay check; episode count and success rate written down | yes |
| 7. Policy | Fine-tune a small open VLA (pick: SmolVLA through LeRobot, because it is small and trains on one GPU) on the demos, run it open loop in the simulator view first, then on the arm with the clamp at its tightest | Policy produces actions in range; on the arm it completes the task at least once with a hand on the stop | yes |

The tool and model names in stage 7 are a recommendation from general knowledge, not checked against current releases. Re-check before committing to them.

## Cut list (what we skip on purpose)

- Cartesian motion and inverse kinematics: already removed; a VLA outputs joint deltas, so it is not needed.
- Wrist roll and a calibrated wrist: stage 2 is base, shoulder and elbow only.
- Our own USB streaming, until the DLL path works and the capture replay says our bytes match.
- More simulator realism and more code-health splits: nothing here blocks stages 1 to 3.
- Anything that needs a second arm, a leader arm or a force sensor.

## Next two steps

1. Run `docs/lab/GET_ARM_MOVING.md` at the lab (stages 1 and 2).
2. While waiting for a lab date, build offline only what is cheap and safe: the LeRobot exporter (stage 5, currently an empty folder) and the 32-bit helper server design with fake-DLL tests (stage 4). Both are testable without the arm.

## Open owner decisions

- Task for the first policy (pick: touch or push a block to a mark; no gripper dependency).
- Camera: which one, where mounted, fixed or wrist.
- Whether the DLL's `SetJoint` may be bound in a new tool (it would need the same kind of rule change as the jog tool got on 2026-10-13).
