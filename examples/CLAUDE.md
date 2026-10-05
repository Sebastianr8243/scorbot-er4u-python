# examples/

Runnable scripts. Four of them are the supervised lab procedures; the rest are offline. Root rules in [../CLAUDE.md](../CLAUDE.md) apply.

| Script | Talks to the robot? | Notes |
|---|---|---|
| `record_raw_state.py` | Real connect unless `--simulate` | Idle capture, 1-30 s, 0.2-10 Hz. Requires `--acknowledge-connect-handshake`. Each `sample` row carries `raw_hex`, the undecoded reply, for `scripts/vendor_check.py` |
| `bench_joint.py` | Real connect unless `--simulate` | One home and one jog of base/shoulder/elbow, `--delta` nonzero and <= 1 degree, `--speed` 1-20. Requires `--acknowledge-supervised-motion`. `--stop-after-ms N` is the software-stop trial: it calls `request_stop()` N ms after the jog call, prints that this is not an emergency stop, and records `stop_trial_outcome` (`completed`, `not_started`, `stopped_early`, `stopped_at_full_travel`) and `stopped_on_request` (true only for `stopped_early`, judged from the counts) in the `after_jog` row. Prompts and step names are unchanged |
| `bench_stream.py` | Real connect unless `--simulate` | The first streaming trial (lab plan F4): home, then `Scorbot.start_stream` for one `--motor` to `--delta` (nonzero, <= 1 degree; the count target is what a jog of that joint would plan, so the sign matches `bench_joint.py`), hold `--hold-s`, back to home, close. Travel cap fixed at 2 degrees. Requires `--acknowledge-supervised-motion` and the `planning` extra (refused before preflight if Ruckig is missing); typed confirmations are `HOME`, `HOME_OK`, `STREAM`. Rows: `stream_plan`, `before_stream`, `after_stream` (with `result`: reached, returned, largest lead, other motors, step gaps) or `stream_failed`. It refuses to stream unless all three motors are within 5 counts of home, and refuses a `--delta` that plans 10 counts or fewer. `result` carries `passed` and `problems`: the trial fails if the motor did not reach the target or return, went more than 20 counts past it, or another motor moved more than 10 counts. A failed trial is not an SDK fault at this size (one degree is inside the lead limit): the script still finishes its prompts and disables, prints `Trial result: FAILED`, logs the command as `faulted` and exits 1. Built on the shared helpers in `bench_joint.py` (imported as `bench`). Never run on the arm |
| `bench_gripper.py` | Real connect unless `--simulate` | The first gripper trial (lab day card step F): connect, LED check, `ENABLE`, LED check, then `Scorbot.move_gripper` once per `--moves` entry (`open`/`close`, at most 4), asking `GRIP` before each. It does not home and commands no arm joint. Requires `--acknowledge-supervised-motion`. Rows: `gripper_plan`, `before_gripper` and `after_gripper` per move (with `result`: direction, planned and moved counts, `full_travel`). Stopping short is printed, not treated as a failure: an object and the end of travel look the same. Built on the shared helpers in `bench_joint.py`. No force limit; never run on the arm |
| `preview_jog.py` | No | `Scorbot().preview_jog`; prints the plan |
| `kinematics_check.py` | No | Nominal model vs legacy `cIn`; unvalidated geometry banner |
| `make_synthetic_session.py` | No | Writes a `synthetic` MCAP session and replays it; used by CI |

## Rules

- A lab script and its rehearsal differ only in `robot_class, data_source = SimulatedScorbot, "simulated"` versus `Scorbot, "real"`. Keep it that way: every new prompt, log row or check must run identically under `--simulate`, so `tests/test_simulated.py` can rehearse it. Do not add behavior that branches on simulation beyond that line.
- Every run you make as an agent uses `--simulate`, an output under `rehearsal/` (git-ignored) or a temp dir, and never `logs/`. A command without `--simulate` energises motors.
- Outputs are created with `open("x")` and refused if they exist (`--output` and `<stem>.controller.jsonl`). Do not switch to overwrite.
- Order matters and is load-bearing: preflight (`run_checks`, real only), `SessionWriter.create` (before the controller connects, so a recorder failure happens before the motor-on handshake), then `BestEffortRecorder` after connect. The JSONL row is written before the matching recorder call so a recorder failure never loses primary evidence.
- The operator flow is `HOME`, `HOME_OK`, `MOVE` typed confirmations plus one-key LED checks (`observe_leds`). A wrong or empty typed confirmation declines (exit code 3); a contradictory or unsure required LED check fails the session before sampling or motion (exit code 1). Completed runs exit 0. The LED expectation is never shown before the answer. After-connect checks in both scripts and after-enable checks in the bench script require the expected answers; later checks warn and record the observation. Do not change prompts casually: `docs/design/OPERATOR_UX.md`, `docs/lab/G1_LAB_CHECKLIST.md` and `scripts/review_lab_logs.py` (LED step names `IDLE_LED_STEPS`, `BENCH_LED_STEPS`, `STREAM_LED_STEPS`, `GRIPPER_LED_STEPS`) depend on the text and step names.
- `motion_source_sha256` and the git commit are written into the session row; keep them.
- The 1 degree ceiling here is stricter than the SDK's 5. Do not relax it in the script; there is no measured basis.
- The home-then-move procedure is written once, in `bench_joint.py`: `add_session_arguments`, `check_session_labels`, `new_output_paths`, `choose_backend`, `software_revision`, `open_recorder`, `row_writer`, `confirmation_prompt`, `OpenCommand`, `connect_and_home`, `observe_and_disable` and the three `report_*` functions. `bench_stream.py` uses all of them. Each script passes its own `Scorbot`, `SimulatedScorbot` and `run_checks` to `choose_backend`, because tests replace those names on the script's module; a helper that imported them itself would ignore the replacement. `record_raw_state.py` keeps its own flow (different rows, no homing) and shares only `observe_leds`, `reject_example_values` and `termination_as_interrupt`.
- Both scripts import `bench_joint` by package path first, then bare, so they work run as a script. Keep both import forms.
- `make_synthetic_session.py` inserts the repo root into `sys.path`; it needs no hardware and must keep working with only the base dependencies (CI runs it).

## Testing changes

`tests/test_bench_joint.py`, `tests/test_calibration_capture.py`, `tests/test_simulated.py` (full G1 rehearsal, LED mismatch, recorder failure, interrupt during jog) patch `builtins.input` with scripted answers. Extend those when you add a prompt or row. Manual smoke check:

```powershell
python examples\bench_joint.py --output rehearsal\t.jsonl --robot-id r --arm-label x --controller-label x --driver none --operator XX --start-pose-note t --joint base --delta 1 --simulate --acknowledge-supervised-motion
```

(answer `HOME`, LED keys, `HOME_OK`, `MOVE`, then observation text). Review with `scripts/review_lab_logs.py`; the report repeats SIMULATED.

## Do not

- Do not add a script that connects without the acknowledgment flag, an `--simulate` path, and a `SessionWriter`.
- Do not add motion beyond one bounded relative jog, the one-motor, one-degree stream trial in `bench_stream.py` and the open/close moves of `bench_gripper.py`, or wrist or Cartesian targets. Do not relax that script's 1 degree and 2 degree limits; they are tighter than the SDK's on purpose.
- Do not put lab-specific paths, robot IDs or operator names in defaults.
