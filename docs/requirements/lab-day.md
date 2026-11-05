# One-command lab day requirements
Status: baseline v1 2026-11-05 (issue #166 task brief)

## Goal
Run T, S, P and G in order with a dated index for the record sheet. G includes a supervised low-speed
velocity jog per arm axis; physical-arm behavior remains unverified.

## Requirements
| ID | Type | Statement | Check |
|---|---|---|---|
| LD01 | SHALL | With `--velocity-jog`, bring-up jogs base, shoulder and elbow at manual speed +5 then -5, each for 0.3 s with renewal and stop, after the degree jog and before any stream. | test: `tests/test_bring_up.py::BringUpTests::test_the_velocity_jog_runs_one_step_per_axis_out_and_back` |
| LD02 | SHALL | Real velocity jogs require exact JOG per axis; a decline logs and skips that axis. Simulated jogs ask nothing; without the flag there are no velocity jogs. | test: `tests/test_bring_up.py::BringUpTests::test_each_velocity_jog_waits_for_an_ok_and_declining_skips_it`, `test_without_the_flag_there_is_no_velocity_jog` |
| LD03 | SHALL | G forwards the velocity flag in the selected mode; a day writes flushed UTC start, step and end rows with source labels, options, problems and relative log paths in one dated folder. | test: `tests/test_lab_day.py::SimulatedDayTests::test_simulate_runs_every_step_with_a_velocity_jog_per_axis_into_one_log` |
| LD04 | SHALL | Declining GO skips and continues; failure stops immediately. Exit is 1 for failure, otherwise 3 for any skipped step, otherwise 0; print the sheet command at the end. | test: `tests/test_lab_day.py::RunnerTests` |
| LD05 | SHALL | The sheet loads each indexed log once, reports missing files and mixed sources, and prints step results and signed velocity counts or skipped/missing jogs. | test: `tests/test_lab_day.py::SimulatedDayTests::test_the_sheet_says_which_steps_and_jogs_were_skipped`, `tests_extra/test_lab_sheet.py` |
| LD06 | SHALL NOT | Implementation or verification opens USB, imports `usb`, changes tests, adds dependencies or changes SDK motion behavior. | review: edited files and simulator-only verification commands |

## Out of scope
- Real arm trials, SDK or protocol changes, new dependencies, git operations.

## Assumptions
- Existing SDK jog handles own the motion safeguards and JSONL motion rows.
- Values from logs are operator leads, not physical-motion or calibration claims.

## Open questions
- None for implementation; velocity jogs remain unverified on the arm.

## Change log
- 2026-11-05 Baseline from the approved issue #166 brief and its existing tests.

## Verification (2026-11-05)
| IDs | Result | Evidence |
|---|---|---|
| LD01-LD05 | pass | Requested four-file pytest run: 65 passed, 11 subtests passed; tests unchanged. The complete simulated day includes child probe and bring-up processes and sheet output. |
| LD06 | pass | Changes confined to lab scripts and documentation; no USB access or imports, SDK changes, dependencies or git operations. Tests use simulated robots or injected runners. |

Physical-arm velocity jogs remain unverified.
