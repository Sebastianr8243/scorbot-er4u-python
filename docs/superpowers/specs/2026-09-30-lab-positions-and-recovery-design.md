# Lab session: back to start, marked positions, fault guidance

Date: 2026-09-30. Status: design choices delegated by the user; awaiting
written-spec review.

## Goal

Three additions to the guided session (`python -m scorbot.lab`), built only
from what already works on the arm (legacy jog orders 4-9 through
`Scorbot.jog_joint`):

1. **Back to start:** return every joint to where it was right after homing.
2. **Marked positions:** mark up to 9 poses during a session and go back to one.
3. **Fault guidance:** when something fails, say what it probably means and
   what to do next, instead of only an error string.

These give the practical equivalent of SCORBASE's Go Home and Record/Go To
Position without new controller commands. The legacy code has no Go Home,
stop, reset or position table (see the legacy survey of 2026-09-30); a real
Go Home and Stop wait for the USB captures.

## Non-goals

No new controller commands, no coordinated or simultaneous multi-joint
motion (joints move one at a time), no wrist or gripper, no larger steps,
no positions saved across sessions (encoder home is re-established each
session, so old positions would be unverified), no automatic retry after a
fault, no clearing of the SDK fault latch.

## Safety rules

Everything in the guided-session spec still applies. In addition:
- A multi-step move (back to start, go to mark) is shown in full first and
  needs one typed confirmation (`BACK` or `GOTO P2`), and the session must be armed.
- Every step is an ordinary bounded jog (1 or 0.5 degree) through the same
  gates, travel cap and logging as a keyed jog.
- **Any key pressed while a multi-step move runs stops it after the current
  step** and disarms. This is a software pause, not an emergency stop; the
  screen says so and names the physical stop.
- Joints move one at a time in the order elbow, shoulder, base (retract
  before swinging the base, backlog item 39).
- A failure latches the session exactly like a keyed jog.

## Design

### New pure module `scorbot/lab/moves.py`

No robot, no I/O; fully unit-tested.

| Name | Contract |
|---|---|
| `Move(joint: str, delta_deg: float)` | frozen; `label` property gives `"BASE -1"`, `"ELBOW +0.5"` |
| `RETURN_ORDER = ("elbow", "shoulder", "base")` | joint order for multi-step moves |
| `plan_moves(current: dict[str, float], target: dict[str, float]) -> list[Move]` | for each joint in `RETURN_ORDER`, whole 1 degree steps toward the target, then one 0.5 step if needed. Raises `ValueError` if a difference is not a multiple of 0.5 or a joint is unknown. Empty list when already there |
| `apply(travel, move) -> dict` | new travel dict after a move (pure) |
| `MarkedPosition(name: str, travel: dict[str, float], counts: dict[str, int])` | frozen snapshot |
| `MAX_MARKS = 9` | names `P1`..`P9` |

### New pure module `scorbot/lab/faults.py`

| Name | Contract |
|---|---|
| `Guidance(key: str, title: str, meaning: str, steps: tuple[str, ...])` | frozen |
| `guidance_for(error: str) -> Guidance` | matches known SDK/legacy error texts, in order: command timeout; legacy result 1 (joint error word too large: stall, collision or impact); legacy result 2 (did not settle); feedback unavailable or stale; worker crashed; LED gate; controller already faulted; otherwise `unknown` |
| `COMMON_STEPS` | the steps every guidance ends with: physical stop if anything moves; do not retry in this session; note pose, LEDs and sounds (photo); check the MOTORS LED; to continue, start a new session and home again |

The texts say "probably" wherever the cause is inferred, per the repo rule.

### Engine changes (`scorbot/lab/session.py`)

- Extract `_execute(move, how, observe=True) -> bool` from `_jog`: the one path
  for every motion after homing (travel cap, `preview_jog`, `jog_joint`, rows,
  fault handling). `_jog` becomes key handling plus confirmation around it.
  `how` is `typed`, `repeat`, `back` or `goto`.
- The session keeps `home_counts` (encoder counts at `home_complete`) and
  `marks: list[MarkedPosition]`.
- New keys in the jog loop:

| Key | Action |
|---|---|
| `m` | mark the current pose as the next `P<n>` (refused after 9); row `position_marked` |
| `g` | choose a mark (`1`-`9`), show its plan, type `GOTO P<n>`, run it |
| `b` | back to start: plan to all-zero travel, type `BACK`, run it |

- Multi-step run (`_run_plan(name, moves, how)`): requires armed; empty plan
  says "already there"; shows every step; one typed confirmation; before each
  step, `operator.discard_pending_keys()` greater than 0 stops the plan
  ("stopped by a key press") and disarms; each step `_execute(..., observe=False)`;
  after the last step asks "Is the arm at <name>? [y/n/u]" and writes
  `plan_complete` with the answer and the counts difference from the target
  (from `home_counts` or the mark's counts, via `signed_count_delta`).
- Rows: `plan_shown` (name, moves), `plan_declined`, `plan_stopped` (reason,
  steps done), `plan_complete`. Jog rows keep their existing shape with the
  new `how` values, so the review table shows them unchanged.
- On `jog_failed` and `session_failed`, the engine shows `guidance_for(error)`
  and writes `fault_guidance` (key, title).
- Help text lists `b`, `m`, `g`; `docs/LAB_SESSION.md` documents them.

### Operator protocol

`discard_pending_keys()` already exists. `ScriptedOperator` gains
`pending_keys: int`, which the next `discard_pending_keys()` returns and
resets, so tests can simulate a key pressed during a move.

## Testing

- `tests/test_lab_moves.py`: plan ordering (elbow, shoulder, base), 1 and 0.5
  steps, mixed signs, already-at-target, non-multiple and unknown-joint errors,
  `apply`, labels.
- `tests/test_lab_faults.py`: each known error text maps to its key; unknown
  falls back; every guidance ends with the common steps and says "physical stop".
- `tests/test_lab_session.py` additions (simulated arm, scripted answers):
  back to start after mixed jogs returns travel to zero, joints in order,
  `plan_complete` with 0-count differences; declined `BACK` moves nothing;
  a key during the plan stops it after one step and disarms; mark then go to
  it; `g` with no marks and `m` after 9 marks are refused; `b` while disarmed
  does nothing; a fault mid-plan latches, shows guidance, still finishes.
- The review test gains a `how="back"` row.

## Risks

- Returning by reversing commanded jogs relies on the legacy scale being
  consistent between directions; the measured count difference at the end
  shows any drift, and the operator confirms the pose.
- Key-to-stop is checked between steps only; a step in progress always
  completes (legacy jogs cannot be interrupted, backlog item 6).
