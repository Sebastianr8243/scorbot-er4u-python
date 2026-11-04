# Lab record sheet from the logs requirements
Status: baseline v1 2026-11-04 (issue #134, approved through mission control)

## Goal
After a lab run, `python scripts/lab_summary.py <logs> --sheet` prints every row of
`docs/lab/LAB_RECORD_SHEET.md` the logs can answer, in sheet order, and says what to read by hand for the rest.
Checks run on simulator logs only; every printed number is a lead, and physical-arm values stay unverified.

## Requirements
| ID | Type | Statement | Check |
|---|---|---|---|
| LS01 | SHALL | Print rows 1 to 10 in sheet order, each with a value plus its source record and file, or `by hand:` and what to read; rows 1, 3, 4, 6 and 9 are always by hand. | test: `tests_extra/test_lab_sheet.py::SheetTests::test_rows_come_out_in_sheet_order_and_each_says_value_or_by_hand`, `test_cli_sheet_mode_prints_rows_and_exit_code` |
| LS02 | SHALL | Fill row 2 only from a `home_complete` with `method` `vendor`. | test: `test_row_2_counts_right_after_home`, `test_row_2_needs_a_vendor_home`, `test_row_2_keeps_the_largest_count_over_every_home` |
| LS03 | SHALL | Fill row 5 only from a base jog with a phone angle (`--phone base=<deg>`); without it, say the commanded counts are the prior and ask for the angle. | test: `test_row_5_is_measured_only_with_a_phone_angle` |
| LS04 | SHALL | Fill row 7 from the motors-on stretch between `home_complete` and the first `before_*` state, with its length in seconds. | test: `test_row_7_drift_with_motors_on_between_home_and_the_move` |
| LS05 | SHALL | Fill row 8 from the largest open move in `bench_gripper`, and the gripper times from `before_gripper` to `after_gripper`. | test: `test_row_8_gripper_stroke_closed_to_open`, `test_row_8_takes_the_largest_opening_over_every_file`, `test_extra_rows_stream_tracking_and_gripper_times` |
| LS06 | SHALL | Fill row 10 from motors-on states and traced IN packets (the `<run>.controller.jsonl` beside each log), signed error words on arm axes against `IMPACT_DETECT_COUNTS`, flagged within 15 counts. | test: `test_row_10_largest_following_error_reads_the_stream_trace` |
| LS07 | SHALL | Take stream lag and overshoot from `scripts/follow_report.py`, not a second calculation. | test: `test_extra_rows_stream_tracking_and_gripper_times` |
| LS08 | SHALL | Report a missing file, an invalid JSON line, reviewer problems and mixed real/simulated logs as problems (exit 1), keep filling rows from the other files, and mark rows filled from a flagged log. | test: `test_missing_logs_leave_rows_by_hand_with_what_to_read`, `test_a_corrupt_line_is_a_problem_and_the_other_files_still_count`, `test_a_flagged_run_says_so_on_the_rows_it_fills`, `test_a_run_that_failed_before_its_plan_is_still_reviewed`, `test_a_simulated_controller_log_beside_a_real_log_is_a_mix`, `test_simulated_logs_get_a_banner_and_mixing_with_real_is_a_problem` |
| LS09 | SHALL | Print the SIMULATED banner first and last when any log is simulated. | test: `test_simulated_logs_get_a_banner_and_mixing_with_real_is_a_problem` |
| LS10 | SHALL NOT | Open USB, move the arm, or give a safety or calibration verdict; change `scorbot/` or `openScorbot/`. | review: final diff; tests run the simulator only |

## Out of scope
- New recording tools (a motors-on 60 s idle log, a pitch jog). Rows 4 and 7 note the gap.

## Assumptions
- Real logs have the same record shapes as the simulator's (unverified: no real bench log exists yet).
- On a real run, `stream_trace.packets` holds the IN replies; the simulator leaves it empty.

## Open questions
- None.

## Change log
- 2026-11-04 Baseline from issue #134.
- 2026-11-04 After the Codex review: largest value over every file (rows 2, 8), runs that failed before their plan are reviewed, each controller log classified real/simulated on its own.
