# Stack 1 LeRobot adapter requirements
Status: baseline v1 2026-10-27; v2 2026-10-28 (L18 to L24: `simulate`, cameras, teleoperators, the `lerobot-record` check)

## Goal
A LeRobot-style `Robot` for Stack 1 (`scorbot.bridge.Arm`, vendor DLL through `tools/usbc_server`, xyz in mm), with a safety layer in front of the arm, and a laptop simulation (`python examples/sim_demo.py`) that needs no arm, no DLL and no USB. Users: Sebastian, and anyone trying a policy on one of the ten arms. Code: `plugins/lerobot_robot_scorbot_stack1/`.

## Requirements
Tests are in `tests/test_lerobot_stack1.py` (class::test). All run on `FakeArm`.

| ID | Type | Statement | Check |
|----|------|-----------|-------|
| L01 | SHALL NOT | `send_action` moves the arm when any of dx, dy, dz is above 50 mm in size (exactly 50 is allowed); it raises `ActionRefused` and sends no command. `max_step_mm` in the config cannot be set above 50 | test: `SafetyTests::test_a_step_over_50_mm_on_one_axis_is_refused`, `ConfigTests::test_max_step_cannot_exceed_50` |
| L02 | SHALL NOT | `send_action` moves the arm to a target (current position plus step) outside the calibrated box; no clipping, no command sent | test: `SafetyTests::test_a_target_outside_the_box_is_refused` |
| L03 | SHALL NOT | `send_action` moves the arm when there is no calibrated box (no `box_mm` in the config and no readable `<calibration_dir>/arm.json`) | test: `SafetyTests::test_no_box_means_no_move` |
| L04 | SHALL NOT | The arm moves, or the gripper changes, before the adapter's `home()` has run in this session; observation is allowed | test: `SafetyTests::test_nothing_moves_until_homed` |
| L05 | SHALL | Any error from the arm during `send_action` or `get_observation` calls `arm.stop()` once, latches the adapter stopped, and re-raises as `AdapterStopped`; while latched every `send_action` is refused with no arm command | test: `SafetyTests::test_an_arm_error_stops_and_latches` |
| L06 | SHALL | `reset()` is the only way out of the latch, and it also clears the homed flag so a new Home is needed before any move | test: `SafetyTests::test_reset_unlatches_but_needs_a_new_home` |
| L07 | SHALL NOT | `connect()` opens a socket to an arm server unless `allow_real=True` is in the config; without it a `ValueError` names the flag. With it, `Arm(host, port)` is used | test: `ConnectTests::test_a_real_server_is_refused_without_the_flag`, `ConnectTests::test_the_flag_connects_to_the_configured_port` |
| L08 | SHALL | The config has `arm_id`, `port`, `host`, `calibration_dir` (default `calibration`; one folder per arm, the same folder the bridge CLI takes as `--calibration-dir`), and the box is read from `<calibration_dir>/arm.json` | test: `ConfigTests::test_per_arm_fields_and_box_from_the_calibration_dir` |
| L09 | SHALL | `observation_features` is exactly `x.mm`, `y.mm`, `z.mm`, `gripper.pos`; `action_features` is exactly `dx.mm`, `dy.mm`, `dz.mm`, `gripper.pos`. `get_observation` returns those keys | test: `FeatureTests::test_observation_and_action_keys` |
| L10 | SHALL NOT | A malformed action moves the arm: a missing key, an extra key, a non-number, or a non-finite value is refused | test: `SafetyTests::test_malformed_or_non_finite_actions_are_refused` |
| L11 | SHALL NOT | The tests, the plugin import or the demo open a socket or load a DLL: the test module makes `socket.create_connection` raise, and the demo and tests pass a `FakeArm` | test: `NoHardwareTests::test_nothing_opens_a_socket` plus every other test running with that guard |
| L12 | SHALL | Every step record and every episode `meta.json` carries `data_source`. It is `simulated` for a `FakeArm` and for a server whose `ping` says `simulated: true`, and `real` only when `ping` says `simulated: false` | test: `DataSourceTests::test_source_comes_from_the_arm_not_the_caller`, `DatasetTests::test_every_record_carries_data_source` |
| L13 | SHALL NOT | The dataset writer accepts a record whose `data_source` differs from the dataset's, or opens a dataset root that already holds the other source | test: `DatasetTests::test_mixed_sources_are_refused` |
| L14 | SHALL | The writer saves per frame `observation.state`, `action`, `timestamp`, `frame_index`, `episode_index`, `task`, `next.done`, `data_source` (LeRobot's column names, as JSON lines), and never overwrites an episode | test: `DatasetTests::test_frames_have_lerobot_columns_and_episodes_are_not_overwritten` |
| L15 | SHALL | `python examples/sim_demo.py` exits 0 with FakeArm only, prints each step as plain text (position mm, gripper, action sent), prints a `refused: <reason>` line for each of: step over 50 mm, target outside the box, move before Home, and saves an episode that reloads equal to what was sent. No plotting, no new dependency | test: `DemoTests::test_the_demo_runs_and_saves_an_episode` |
| L16 | SHALL | The plugin imports without the `lerobot` package (a stand-in base class is used) | test: `NoHardwareTests::test_imports_without_lerobot` |
| L17 | SHALL NOT | Any text claim that the adapter works on the arm: docs say "unverified" until a bench log exists. Nothing in this change was run on the arm | review: grep the plugin README and docs for the word "unverified" |
| L18 | SHALL NOT | `connect()` builds an `Arm` or opens a socket when `simulate=True`, even with `allow_real=True`; it builds its own `FakeArm` and `data_source` is `simulated` | test: `SimulateTests::test_simulate_wins_over_allow_real`, `SimulateTests::test_connects_without_an_arm_or_allow_real_and_starts_homed` |
| L19 | SHALL | A `simulate` robot starts homed (`lerobot-record` never calls `home()`), with the gripper closed and a default box (x 200 to 400, y -100 to 100, z 100 to 300 mm) used only when there is no `box_mm` and no `arm.json`; every other safety rule (L01, L02, L06) still applies. An arm passed to `connect()` is used as given, not homed | test: `SimulateTests::test_the_safety_layer_still_applies`, `test_an_explicit_box_replaces_the_default_one`, `test_arm_json_in_the_calibration_dir_replaces_the_default_box`, `test_reset_needs_a_new_home_like_the_real_path`, `test_an_arm_passed_in_is_used_as_before` |
| L20 | SHALL | The robot's `name` (which LeRobot writes as the dataset's `robot_type`) is `scorbot_stack1_sim` when `simulate=True` and `scorbot_stack1` otherwise, because a LeRobot v3 dataset has no `data_source` field | test: `SimulateTests::test_the_dataset_type_says_simulated`; by hand: `examples/lerobot_record_check.py` reads `robot_type` back |
| L21 | SHALL | With `cameras` in the config: `observation_features` has a `(height, width, 3)` entry per camera, `get_observation` returns one frame per camera, `connect()` connects the cameras and a camera that fails leaves the arm closed and nothing open, `disconnect()` disconnects them, and a camera error neither stops nor latches the arm | test: `CameraTests` (a stand-in camera; the factory replaced) |
| L22 | SHALL NOT | A simulated camera (the `scorbot_fake` camera sets `simulated`) is connected next to a real arm: real and simulated data never mix | test: `CameraTests::test_a_fake_camera_is_refused_with_a_real_arm` |
| L23 | SHALL | The teleoperator helpers output exactly the four action keys; the keyboard map scales to `step_mm` (above 0, at most 50), keeps the gripper state on "stay" and maps close, stay and open to 0, last, 1; the script parser refuses a malformed, non-finite or over-50 mm step | test: `tests/test_lerobot_teleop.py` |
| L24 | SHALL | `examples/lerobot_record_check.py` (opt-in, in a throwaway venv with `lerobot`) records 2 episodes with the real `lerobot-record`, once with no cameras and once with two fake cameras and video, and every check passes when the datasets are loaded with `LeRobotDataset`. The command it builds always has `--robot.simulate=true` and `--dataset.push_to_hub=false`, and never `allow_real` | test: `tests/test_lerobot_record_check.py` (the command, in CI); by hand: the run, passed on `lerobot` 0.6.1 on 2026-10-28 |

## Out of scope
- Running anything on the arm; Home on the real arm takes about 135 s and is done by a person at the red stop.
- A real camera (only the fake camera is run), a person at the keyboard teleoperator, and training: none was run.
- Converting old JSON-lines episodes to a `LeRobotDataset`; the JSON-lines writer is unchanged, `lerobot-record` writes the v3 datasets.
- Checking the adapter against an installed `lerobot` in CI: the tests use a stand-in base class. `examples/lerobot_install_check.py` and `examples/lerobot_record_check.py` run it against a real install (passed on `lerobot` 0.6.1, 2026-10-28, FakeArm only) and are opt-in, not part of pytest or CI.
- Wrist pitch and roll.

## Assumptions
- A safety refusal (L01 to L04, L10) is a bad action, not an arm error: it raises `ActionRefused`, does not latch, and the caller may send another action. Only errors from the arm latch (L05).
- `Arm` cannot read the gripper, so `gripper.pos` is the last commanded one (1.0 open, 0.0 closed). After `home()` it is 0.0 because the vendor Home closes the gripper (docs/API.md); unverified on the arm.
- The server also checks the 50 mm and box rules; the adapter checks first so a bad policy never reaches it, and is stricter (target must be inside the box).
- `arm.json` boxes grown only by `teach` are accepted as the box; the adapter does not look at the `measured` flag.

## Open questions
- Whether to move the dataset writer onto `LeRobotDataset` once `lerobot` can be installed on the lab PC.
- What a student should get when the keyboard asks for a move the safety layer refuses: the recording ends (today, if the `record_loop` reading holds), or a clamp, or a zero step. Changing it changes the safety layer, so Sebastian decides.

## Change log
- 2026-10-27 baseline v1, from Sebastian's brief. Same day, scope trim from Sebastian: no live view or plot, demo is plain text, recorder stays small (one JSON-lines file per episode, reloaded in a test).
- 2026-10-28 v2, from Sebastian ("go" on the `lerobot-record` plan, up to two cameras, no training run on the laptop): L18 to L24. A refusal inside `lerobot-record` should end the recording (read from `record_loop`: nothing there catches `ActionRefused`; not run); the scripted teleoperator stays inside the box so the check does not hit it.
