# examples/

Runnable scripts. Two of them are the supervised lab procedures; the rest are offline. Root rules in [../CLAUDE.md](../CLAUDE.md) apply.

| Script | Talks to the robot? | Notes |
|---|---|---|
| `record_raw_state.py` | Real connect unless `--simulate` | Idle capture, 1-30 s, 0.2-10 Hz. Requires `--acknowledge-connect-handshake` |
| `bench_joint.py` | Real connect unless `--simulate` | One home and one jog of base/shoulder/elbow, `--delta` nonzero and <= 1 degree, `--speed` 1-20. Requires `--acknowledge-supervised-motion` |
| `python_control.py` | Yes, always. Runs at import (no `main` guard) | Illustrative first session. Never run or import it in tests, CI or as an agent. `compileall` only compiles it |
| `preview_jog.py` | No | `Scorbot().preview_jog`; prints the plan |
| `kinematics_check.py` | No | Nominal model vs legacy `cIn`; unvalidated geometry banner |
| `make_synthetic_session.py` | No | Writes a `synthetic` MCAP session and replays it; used by CI |

## Rules

- A lab script and its rehearsal differ only in `robot_class, data_source = SimulatedScorbot, "simulated"` versus `Scorbot, "real"`. Keep it that way: every new prompt, log row or check must run identically under `--simulate`, so `tests/test_simulated.py` can rehearse it. Do not add behavior that branches on simulation beyond that line.
- Every run you make as an agent uses `--simulate`, an output under `rehearsal/` (git-ignored) or a temp dir, and never `logs/`. A command without `--simulate` energises motors.
- Outputs are created with `open("x")` and refused if they exist (`--output` and `<stem>.controller.jsonl`). Do not switch to overwrite.
- Order matters and is load-bearing: preflight (`run_checks`, real only), `SessionWriter.create` (before the controller connects, so a recorder failure happens before the motor-on handshake), then `BestEffortRecorder` after connect. The JSONL row is written before the matching recorder call so a recorder failure never loses primary evidence.
- The operator flow is `HOME`, `HOME_OK`, `MOVE` typed confirmations plus one-key LED checks (`observe_leds`). A wrong or empty typed confirmation declines (exit code 3); a contradictory or unsure required LED check fails the session before sampling or motion (exit code 1). Completed runs exit 0. The LED expectation is never shown before the answer. After-connect checks in both scripts and after-enable checks in the bench script require the expected answers; later checks warn and record the observation. Do not change prompts casually: `docs/OPERATOR_UX.md`, `docs/G1_LAB_CHECKLIST.md` and `scripts/review_lab_logs.py` (LED step names `IDLE_LED_STEPS`, `BENCH_LED_STEPS`) depend on the text and step names.
- `motion_source_sha256` and the git commit are written into the session row; keep them.
- The 1 degree ceiling here is stricter than the SDK's 5. Do not relax it in the script; there is no measured basis.
- `record_raw_state.py` imports `observe_leds` from `bench_joint` (package path first, then bare, so it works run as a script). Keep both import forms.
- `make_synthetic_session.py` inserts the repo root into `sys.path`; it needs no hardware and must keep working with only the base dependencies (CI runs it).

## Testing changes

`tests/test_bench_joint.py`, `tests/test_calibration_capture.py`, `tests/test_simulated.py` (full G1 rehearsal, LED mismatch, recorder failure, interrupt during jog) patch `builtins.input` with scripted answers. Extend those when you add a prompt or row. Manual smoke check:

```powershell
python examples\bench_joint.py --output rehearsal\t.jsonl --robot-id r --arm-label x --controller-label x --driver none --operator XX --start-pose-note t --joint base --delta 1 --simulate --acknowledge-supervised-motion
```

(answer `HOME`, LED keys, `HOME_OK`, `MOVE`, then observation text). Review with `scripts/review_lab_logs.py`; the report repeats SIMULATED.

## Do not

- Do not add a script that connects without the acknowledgment flag, an `--simulate` path, and a `SessionWriter`.
- Do not add motion beyond one bounded relative jog, or wrist or Cartesian targets.
- Do not put lab-specific paths, robot IDs or operator names in defaults.
