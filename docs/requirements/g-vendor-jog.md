# Lab step G vendor degree moves
Status: baseline v1 2026-11-08 (authorized task brief)

## Goal
Pin G's 1 degree out-and-back moves to the existing DLL-matched joint profile after vendor Home. All verification is desk-only; physical behavior remains unverified on the arm.

## Requirements
| ID | Type | Statement | Check |
|---|---|---|---|
| GV01 | SHALL | Move the selected base, shoulder or elbow joint by +1 degree in vendor radians from the current vendor target, then return to the saved starting target exactly. | test: `tests/test_bring_up.py::BringUpTests::test_degree_moves_use_vendor_targets_and_return_to_start`, `test_degree_moves_start_at_the_current_vendor_target` |
| GV02 | SHALL NOT | Call jog_joint or preview_jog for G's two degree moves. | test: `tests/test_bring_up.py::BringUpTests::test_degree_moves_use_vendor_targets_and_return_to_start` |
| GV03 | SHALL | Use a low fixture-covered speed and record that speed and the resulting duration in milliseconds for each degree step. | test: `tests/test_bring_up.py::BringUpTests::test_degree_moves_use_vendor_targets_and_return_to_start` |
| GV04 | SHALL | Check every motor's signed encoder delta against vendor target counts minus vendor start counts, including coupled wrist motors, and retain the final back-to-origin check. | test: `tests/test_bring_up.py::BringUpTests::test_degree_moves_use_vendor_targets_and_return_to_start`, `test_degree_move_rejects_unexpected_motor_counts`, `test_degree_return_still_checks_the_original_encoder_counts` |
| GV05 | SHALL | Fail the active degree step and end the ladder on a stop during either move, without later motion, with a fault latched and the session disconnected. | test: `tests/test_bring_up.py::BringUpTests::test_stop_during_a_vendor_degree_move_fails_the_step` |
| GV06 | SHALL | Preserve EMPTY, CLEAR, HOME and per-axis JOG prompts and the simulated lab-day path. | test: `tests/test_bring_up.py::BringUpTests::test_vendor_typed_empty_clear_home`, `test_each_velocity_jog_waits_for_an_ok_and_declining_skips_it`; `tests/test_lab_day.py::SimulatedDayTests::test_simulate_runs_every_step_with_a_velocity_jog_per_axis_into_one_log` |
| GV07 | SHALL NOT | Open a real device, run online commands, change SDK packet generation or add dependencies during this task. | script: requested desk verification commands; `git diff -- scorbot openScorbot pyproject.toml uv.lock` is empty |

## Out of scope
- Physical-arm validation, USB access, commits, pushes, merges and pull requests.
- Changes to optional velocity jogs, streaming, SDK motion profiles or other bench scripts.

## Assumptions
- A completed vendor Home leaves the SDK's empty-tuple home target; subsequent vendor moves leave five vendor joint radians.
- Speed 1 is recorded in fixture c for base +30 degrees and back (4572 ms each); it is a DLL speed setting, not 1 percent of physical maximum speed.
- Vendor count signs and coupled motion remain unverified on the arm.

## Open questions
None for this desk-only change.

## Change log
- 2026-11-08 Initial baseline from the supplied outcome, constraints and verification commands.

## Verification (2026-11-08)
| ID | Result | Evidence |
|---|---|---|
| GV01 | pass | All three selectable joints and a non-home current vendor target pass; the return call receives the saved tuple exactly. |
| GV02 | pass | Degree-move tests make jog_joint and preview_jog raise if reached; the simulated lab-day log contains two vendor_joint_start rows and no legacy motion_start. |
| GV03 | pass | Both step_motion rows record speed 1 and total_ms matching the SDK's vendor_joint_start; fixture c records speed 1 out/back. |
| GV04 | pass | Signed encoder deltas match the pinned base/shoulder/elbow motor deltas; unexpected wrist drift fails, and two tolerated 15-count gripper drifts fail the final 30-count origin check. |
| GV05 | pass | A stop injected during either vendor move fails that step, latches a fault, disconnects and prevents later velocity/stream motion. |
| GV06 | pass | Full bring-up and lab-day tests pass, including typed confirmations, per-axis declines and simulated child processes. |
| GV07 | pass | Only desk tests and static checks run; SDK, legacy code and dependency diff is empty. |

Before the code change, the new targeted tests reported 7 failed checks (including subtests): legacy preview reached, no vendor calls, and stops not exercised. After the change, the focused modules passed 49 tests and 18 subtests.

Requested verification, using the shared Python environment with PYTHONPATH set to this worktree:
- `python -m pytest -n 2 -q tests`: 1421 passed, 1 warning, 14376 subtests passed in 300.42 s.
- `python -m pytest -q tests_extra/test_lab_summary.py`: 11 passed in 9.46 s.
- `python scripts/check_docs.py`: docs ok; also checked this untracked requirements file explicitly.
- `ruff check .`: All checks passed.
- Repo compileall and `git diff --check`: exit 0.

The warning is the unchanged legacy home-stop test worker comparing a MagicMock with an integer. The initial targeted pytest startup could not access the shared temporary directory; subsequent checks used worktree-local TEMP/TMP. No real device or online command was used. Physical behavior remains unverified on the arm. Changes are uncommitted for the caller.
