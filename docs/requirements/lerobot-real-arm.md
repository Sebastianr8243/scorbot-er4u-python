# LeRobot real-arm opt-in requirements
Status: baseline v1 2026-11-03 (authorized task brief)

## Goal
Prepare the existing LeRobot plugin for a supervised real-arm trial after lab checks pass.
Physical-arm behaviour and motor-off delivery are unverified; desk checks use fakes only.

## Requirements
| ID | Type | Statement | Check |
|---|---|---|---|
| LR01 | SHALL NOT | Defaults or simulate=false alone construct hardware. | test: RobotShapeTests.test_the_real_arm_is_refused; RealArmGateTests.test_connect_rechecks_the_opt_in_before_hardware |
| LR02 | SHALL NOT | Real-arm opt-in bypass any of the three Home confirmations. | test: RealArmGateTests.test_opt_in_still_requires_each_home_confirmation_before_hardware |
| LR03 | SHALL | Opt-in selects the real class with the existing PolicyArm configuration. | test: RealArmCleanupTests.test_real_class_atexit_and_disconnect_disable_exactly_once (class patched to simulator) |
| LR04 | SHALL | Disconnect, errors or Ctrl+C inside plugin calls, and interpreter exit (atexit) close the arm idempotently without raising from cleanup; no SIGINT or excepthook handler is installed. | test: RealArmCleanupTests in tests_extra/test_lerobot_scorbot_plugin.py |
| LR05 | SHALL NOT | Desk verification open USB, move hardware, use the network or add dependencies. | review: tests patch Scorbot to SimulatedScorbot or mocks; targeted pytest and ruff only |
| LR06 | SHALL | Documentation mark the switch unverified and require lab Home, jog, stream and gripper checks first. | review: plugins/lerobot_robot_scorbot/README.md |

## Out of scope
- Physical-arm verification, new motion logic, LeRobot changes, forced process termination recovery.
- Commits, pushes and full-suite verification (orchestrator owns the full verify).

## Assumptions
- One supervised real-arm connection owns process hooks on the main Python thread.
- Existing PolicyArm Home validation and close sequence remain authoritative.
- Normal Python exit runs atexit; process kill and loss of power cannot run Python cleanup.

## Open questions
- None for desk implementation; hardware remains unverified.

## Change log
- 2026-11-03 Baseline from the authorized task brief.

## Desk verification (2026-11-03)

Python: `C:/Users/sebas/startup/openScorbot/.venv/Scripts/python.exe`, `PYTHONPATH` set to this worktree, `PYTEST_ADDOPTS='-o addopts='` to disable the repository's automatic xdist workers.

- `python -m pytest -q -p no:cacheprovider tests_extra/test_lerobot_scorbot_plugin.py`: **36 passed, 9 skipped, 31 subtests passed** (after removing the signal and excepthook handlers). Skips require the separate LeRobot environment.
- `python -m ruff check .`: **All checks passed!**
- `git diff --check`: exit 0; every changed file has CRLF and no bare LF.
- Full verify: not run, per task; owned by the orchestrator. No USB, network, new dependencies, commits or pushes.

| ID | Result | Evidence |
|---|---|---|
| LR01 | pass | Default refusal and connect-time recheck tests passed. |
| LR02 | pass | Each missing Home confirmation refused before the patched hardware constructor. |
| LR03 | pass | Patched real class built the simulator; robot identity, speed, step limit and wrist option checked. |
| LR04 | pass | Cleanup tests checked motor-off once, original errors preserved, prior hooks restored, and interrupts during Home and cleanup. Physical delivery remains unverified. |
| LR05 | pass | Real class replaced with simulator/mocks throughout; only targeted pytest and ruff executed for verification. |
| LR06 | pass | Plugin README names both switches, all three Home confirmations and all four prerequisite lab steps; real-arm behaviour marked unverified. |

Failing before implementation, passing afterward (in `tests_extra/test_lerobot_scorbot_plugin.py`):

- `RobotShapeTests.test_the_real_arm_is_refused`
- `RealArmGateTests.test_connect_rechecks_the_opt_in_before_hardware`
- `RealArmGateTests.test_opt_in_still_requires_each_home_confirmation_before_hardware`
- `RealArmCleanupTests.test_real_class_atexit_and_disconnect_disable_exactly_once`
- `RealArmCleanupTests.test_disconnect_then_atexit_disable_exactly_once`
- `RealArmCleanupTests.test_failed_camera_connect_stops_and_cleans_other_cameras`
- `RealArmCleanupTests.test_arm_close_error_never_raises_or_repeats_cleanup`

The first red run also exposed a test cleanup omission on the mutable-config case (a leftover simulated worker); the test now always registers disconnect. The first implementation run exposed a camera mock that accidentally advertised twin-camera attachment; it now has the real camera surface explicitly specified. No hardware was involved.
