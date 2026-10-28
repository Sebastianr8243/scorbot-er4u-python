# examples

Scripts that drive the arm through the arm server with `scorbot.bridge.Arm`.

## Before you start

- The arm server must be running and homed: [docs/lab/RUN_THE_ARM.md](../docs/lab/RUN_THE_ARM.md).
- **WARNING:** every script except `sim_demo.py` and the two `lerobot_*_check.py` scripts moves the arm. Keep a hand near the red stop. The red stop is the only emergency stop.
- Each moving script asks you to type `go` before its first move. Do not remove that prompt.

## Scripts

| Script | Moves the arm | What it does |
|---|---|---|
| `teleop.py` | yes | Keyboard control, one `move_by` per key press. Keys: `d`/`a` x, `w`/`s` y, `r`/`f` z, `o`/`c` gripper, `q` ends. With `--wrist-step N`: `t`/`g` pitch, `y`/`h` roll. Steps are capped at 20 mm and 5000 wrist units. `--fake` runs without an arm |
| `arm_characterise.py` | yes | Measures the xyz scale per axis, repeatability and a 3x3 grid against a printed mm mat. Writes `calibration/arm_report-<time>.json` and the working box in `calibration/arm.json`, which `move_to` needs |
| `pick_place.py` | yes | Scripted pick and place between four taught points (`above_a`, `at_a`, `above_b`, `at_b`). `--runs 10` records success and mat error per run to `logs/` |
| `arm_latency.py` | yes, up and down along z | Times `where`, `move_by` and the gripper, to find how many commands per second the arm accepts |
| `record_episode.py` | yes | Keyboard teleoperation that logs two cameras, the arm state and each command to `episodes/<time>/`. `--fake` rehearses with `FakeArm` and fake cameras under `rehearsal/` |
| `sim_demo.py` | no | Plain-text simulation of the [LeRobot adapter](../plugins/lerobot_robot_scorbot_stack1/README.md) on `FakeArm`. Needs no server, DLL or USB |
| `lerobot_install_check.py` | no | Runs the adapter against an installed `lerobot` on `FakeArm`. Use a throwaway venv; exits 2 if `lerobot` is missing |
| `lerobot_record_check.py` | no | Runs the real `lerobot-record` on the adapter (`--robot.simulate=true`, scripted teleoperator, never the hub) twice, with no cameras and with two fake cameras and video, then loads each dataset with `LeRobotDataset` and compares values. Throwaway venv with `lerobot`, and the plugin installed with `pip install --no-deps -e`; exits 2 if either is missing |

## Conventions

- Output folders are created, never overwritten.
- `record_episode.py` labels an episode `real` only when the server's `ping` reports `simulated: false`.
- Rehearsal output goes to `rehearsal/`, never beside real data in `logs/` or `episodes/`.
- Every call and its units: [docs/API.md](../docs/API.md).

## Tests

`tests/test_arm_api.py` (characterise and pick-and-place), `tests/test_record_episode.py`, `tests/test_teleop.py`, `tests/test_lerobot_stack1.py`, `tests/test_lerobot_teleop.py`, `tests/test_lerobot_install_check.py`, `tests/test_lerobot_record_check.py`.
