# One-command lab day requirements
Status: baseline v2 2026-11-05 (issue #166 review fixes)

## Goal
Run T, S, P and G in order with a dated index for the record sheet. G includes a supervised low-speed
velocity jog per arm axis; physical-arm behavior remains unverified.

## Requirements
| ID | Type | Statement | Check |
|---|---|---|---|
| LD01 | SHALL | With `--velocity-jog`, bring-up jogs base, shoulder and elbow at manual speed +5 then -5, each for 0.3 s with renewal and stop, after the degree jog and before any stream. | test: `tests/test_bring_up.py::BringUpTests::test_the_velocity_jog_runs_one_step_per_axis_out_and_back` |
| LD02 | SHALL | Real velocity jogs require exact JOG per axis; a decline logs and skips that axis. Simulated jogs ask nothing; without the flag there are no velocity jogs. | test: `tests/test_bring_up.py::BringUpTests::test_each_velocity_jog_waits_for_an_ok_and_declining_skips_it`, `test_without_the_flag_there_is_no_velocity_jog` |
| LD03 | SHALL | G forwards the velocity flag in the selected mode; a day writes flushed UTC start, step and end rows with source labels, options, problems and relative log paths in one dated folder. | test: `tests/test_lab_day.py::SimulatedDayTests::test_simulate_runs_every_step_with_a_velocity_jog_per_axis_into_one_log` |
| LD04 | SHALL | Declining GO skips and continues; failure stops immediately. Exit is 1 for failure, otherwise 3 for a declined GO or a skip inside G, otherwise 0; print the sheet command at a normal end. | test: `tests/test_lab_day.py::RunnerTests` |
| LD05 | SHALL | The sheet loads each indexed log once, reports missing files and mixed sources, and prints step results and signed velocity counts or skipped/missing jogs. For each axis it uses the last file with a positive-speed end row, pairing that first out row only with the first later negative-speed end row in the same file. | test: `tests/test_lab_day.py::SimulatedDayTests::test_the_sheet_says_which_steps_and_jogs_were_skipped`, `test_the_sheet_pairs_out_and_back_within_one_log_by_sign`, `tests_extra/test_lab_sheet.py` |
| LD06 | SHALL NOT | Implementation or verification opens USB, imports `usb`, changes tests, adds dependencies or changes SDK motion behavior. | review: edited files and simulator-only verification commands |
| LD07 | SHALL | T on WinUSB or in simulation, and S in simulation, record n/a with the printed reason as a problem; n/a does not change the exit code, and a day with only n/a and pass still says all steps passed. | test: `tests/test_lab_day.py::StepTests::test_steps_that_cannot_run_here_say_so`, `SimulatedDayTests::test_simulate_runs_every_step_with_a_velocity_jog_per_axis_into_one_log` |
| LD08 | SHALL | A velocity jog whose reason after stop is not stop fails bring-up, naming the axis and reason. | test: `tests/test_bring_up.py::BringUpTests::test_a_velocity_jog_that_ends_by_itself_is_a_fail` |
| LD09 | SHALL | G records a skipped list (empty when none) from step_skipped names in every listed bring-up log and prints SKIPPED inside G when any exist. | test: `tests/test_lab_day.py::RunnerTests::test_a_jog_skipped_inside_g_reaches_the_index_and_the_exit` |
| LD10 | SHALL | Every started index ends with lab_day_end; KeyboardInterrupt or EOFError at a prompt or in a step records exit 130 and interrupted true, then propagates the interruption. | test: `tests/test_lab_day.py::RunnerTests::test_an_interrupt_still_closes_the_index` |
| LD11 | SHALL NOT | The sheet loads an index-listed path whose resolved location is outside the index folder, including parent traversal, absolute paths outside it and symlinks outside it; each refusal reports the index name and listed path as outside the lab-day folder. | test: `tests/test_lab_day.py::SimulatedDayTests::test_the_sheet_refuses_index_paths_outside_its_folder` |

## Out of scope
- Real arm trials, SDK or protocol changes, new dependencies, git operations.

## Assumptions
- Existing SDK jog handles own the motion safeguards and JSONL motion rows.
- Values from logs are operator leads, not physical-motion or calibration claims.

## Open questions
- None for implementation; velocity jogs remain unverified on the arm.

## Change log
- 2026-11-05 Baseline from the approved issue #166 brief and its existing tests.
- 2026-11-05 v2: the review-fix brief (approved through mission control) approves LD04-LD05 revisions and LD07-LD11 for n/a, early jog endings, skips inside G, interrupts and folder-only index loading.

## Verification (2026-11-05)
| IDs | Result | Evidence |
|---|---|---|
| LD01-LD05, LD07-LD11 | pass | Requested four-file pytest run: 71 passed, 13 subtests passed in 57.33s; tests unchanged. Includes the complete simulated day, n/a results, skipped jog propagation, interrupt end rows, folder traversal refusal and per-file jog pairing. Path resolution also covers absolute paths and symlinks outside the folder by inspection. |
| LD06 | pass | Changes confined to lab scripts and documentation; no USB access or imports, SDK changes, dependencies or git operations. Tests use simulated robots or injected runners. |

Requested Ruff check (`scripts tests tests_extra`): All checks passed!

Physical-arm velocity jogs remain unverified.
