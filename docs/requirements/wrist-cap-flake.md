# Wrist cap test requirements
Status: baseline v1 2026-11-02 (task brief)

## Goal
Diagnose the intermittent combined pitch/roll cap test on the desk and remove its cause. Physical arm behaviour is unverified.

## Requirements
| ID | Type | Statement | Check |
|---|---|---|---|
| W01 | SHALL | The corner test observes arrival before reversing, including when the simulated stream falls behind policy calls. | test: `tests_extra/test_policy_arm.py::PolicyArmWristTests::test_pitch_and_roll_at_the_cap_together_stay_inside_every_limit` with a delayed simulator |
| W02 | SHALL NOT | A proposed wrist setpoint past the existing pitch/roll cap is sent. | test: `tests_extra/test_streaming.py::WristReversalTests` |
| W03 | SHALL NOT | This fix changes driver limits, timeouts, dependencies or packet bytes. | script: `git diff -- scorbot openScorbot pyproject.toml uv.lock` prints nothing |
| W04 | SHALL | The complete desk verification passes. | script: task verify command (lint, compileall, pytest tests and tests_extra with two workers, docs check) exits zero |

## Out of scope
- Changing the planner's existing stop-on-unsafe-trajectory behaviour.
- USB access, physical arm motion, DLL analysis or hardware validation.

## Assumptions
- The task's simulator-only brief authorizes this baseline.
- Wall-clock sleeps do not guarantee progress of the stream worker.

## Open questions
- None.

## Change log
- 2026-11-02 Baseline from the task brief; preserve the existing safety guard and prove the scheduler race before fixing the test.
