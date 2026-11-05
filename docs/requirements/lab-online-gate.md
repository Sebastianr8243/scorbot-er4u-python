# Lab session mode requirements
Status: baseline v1 2026-11-04 (issue #160 task brief)

## Goal
Require explicit mode selection before a guided lab session starts. Real controller connections energise the motors and require an operator at the physical stop.

## Requirements
| ID | Type | Statement | Check |
|---|---|---|---|
| ON01 | SHALL NOT | A command without a mode starts an operator, reads a profile, runs preflight or starts a session; it shall exit 2 and explain the hazard and both mode choices. | test: `python -m unittest tests.test_lab_main.LabMainTests.test_no_mode_refuses_before_starting_anything tests.test_lab_main.LabMainTests.test_profile_alone_does_not_choose_real_mode` |
| ON02 | SHALL | An explicit `--online` retains real session wiring and default or explicit log folders. | test: `python -m unittest tests.test_lab_main.LabMainTests.test_online_keeps_real_session_wiring_and_default_logs tests.test_lab_main.LabMainTests.test_online_keeps_explicit_log_folder` |
| ON03 | SHALL | `--simulate` retains simulated data, no real preflight and the rehearsal log folder. | test: `python -m unittest tests.test_lab_main.LabMainTests.test_simulate_keeps_rehearsal_defaults` |
| ON04 | SHALL NOT | Both mode flags, dropped-motor rehearsal online or a fake camera online start a session. | test: `python -m unittest tests.test_lab_main.LabMainTests.test_simulate_and_online_are_mutually_exclusive tests.test_lab_main.LabMainTests.test_dropped_motors_online_still_needs_simulate tests.test_lab_main.LabMainTests.test_fake_camera_online_still_needs_simulate` |

## Out of scope
- Controller behavior, typed terminal confirmations and launcher argument forwarding.
- Hardware trials or any claim of physical verification.

## Assumptions
- Connecting to the real controller energises the motors.
- Tests replace hardware and session entry points with mocks.

## Open questions
- None.

## Change log
- 2026-11-04 ON01-ON04 baselined from the issue #160 task brief.

## Verification (2026-11-04)
| ID | Result | Evidence |
|---|---|---|
| ON01 | pass | Both no-mode tests pass; setup entry points and operator output remain uncalled. |
| ON02 | pass | Both online wiring tests pass with mocked hardware and session entry points. |
| ON03 | pass | Simulated defaults test passes. |
| ON04 | pass | All three invalid-combination tests exit 2 before setup. |

`python -m unittest tests.test_lab_main`: 8 tests, OK after the fix; before the fix, 5 failures and 2 errors. No hardware trial performed.

Full suite and `scripts/check_docs.py`: see the PR.
