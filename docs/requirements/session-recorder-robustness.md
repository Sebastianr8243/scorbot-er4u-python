# Session recorder robustness
Status: baseline v1 2026-11-06 (scope and checks authorized in the task brief)

## Goal
Keep session failure evidence and lab control flow intact when recording fails.
Change only the session recorder, with offline fault injection and no new dependencies.

## Requirements
All test names below are in `tests/test_session_robustness.py`.

| ID | Type | Statement | Check |
|---|---|---|---|
| SR01 | SHALL | A failed finish, final fsync or stream close records ended_utc, event_count and closed_cleanly=false, then re-raises the original error. | test: `test_close_failure_records_end_metadata_and_reraises`; `test_close_preserves_finish_error_when_stream_close_also_fails` |
| SR02 | SHALL | A failed metadata save can be retried by close without finishing the MCAP twice, preserving the original finish error when both fail. | test: `test_close_retries_metadata_without_finishing_twice` |
| SR03 | SHALL | Failed creation releases the MCAP handle and removes only the directory created by that invocation. | test: `test_create_failure_closes_handle_and_removes_new_folder`; `test_create_collision_preserves_existing_session` |
| SR04 | SHALL | A periodic fsync failure after a flushed event allows subsequent recording and appears as a durability error in final metadata. | test: `test_periodic_fsync_failure_keeps_recording_and_reports_durability` |
| SR05 | SHALL NOT | Caller payloads carrying the reserved _rec key produce an event or consume a sequence number. | test: `test_caller_rec_field_is_rejected_without_consuming_sequence` |
| SR06 | SHALL | BestEffortRecorder suppresses close errors, still closes after a prior logging failure, and retains one first failure and one warning across concurrent failures. | test: `test_best_effort_close_never_raises_and_preserves_first_failure`; `test_best_effort_concurrent_failures_warn_once_and_keep_first_error` |
| SR07 | SHALL | Sequential session creations share one code identity computation per process and return independently mutable identity dictionaries. | test: `test_two_creates_run_each_git_subprocess_only_once`; `test_cached_code_identity_cannot_be_mutated_by_session_callers` |
| SR08 | SHALL NOT | This work opens USB, moves the arm, changes the missing-simulated-key guard, edits existing tests, adds dependencies, or pushes. | review: final diff and commands; existing `SourceMixingTests::test_state_without_the_flag_is_accepted_anywhere` passes unchanged |

## Out of scope
- Bug 3: states without a simulated key still bypass the real-versus-simulated guard.
- Hardware, replay behavior, camera recorder changes, and dependency changes.

## Assumptions
- Code identity describes the first recording in the process; restart after editing source to refresh it.
- An fsync failure weakens power-loss durability even when subsequent finalization succeeds.

## Open questions
- None.

## Change log
- 2026-11-06: SR01-SR08 baseline from the user's explicit task and regression checks.

## Verification (2026-11-06)
| IDs | Result | Evidence |
|---|---|---|
| SR01-SR07 | pass | New regressions: 17 failed and 2 passed before the patch; all 19 passed afterward. Recorder suites: 68 passed and 5 subtests passed. |
| SR08 | pass | Final diff changes recorder code, new tests and documentation only; existing tests unchanged, no hardware or push commands run. |
| All | pass | `uv run --frozen pytest tests tests_extra -q -n 4`: 1974 passed, 18 skipped, 15363 subtests passed in 124.99 s. Ruff, compileall, documentation checks and diff whitespace checks passed. |

The full suite reported one thread warning in the unchanged home-stop test:
`test_a_stopped_home_leaves_the_motors_on_and_reports_only_stopped` compares a
mock with an integer in the legacy worker. It was left outside this recorder fix.
The initial full run failed the documentation index before the new test's README
row was added; the final full run above includes that correction.
