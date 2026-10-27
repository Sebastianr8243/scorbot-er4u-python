# examples/

Scripts that drive the arm through the arm server (`tools/usbc_server`) with `scorbot.bridge.Arm`. Read the root [CLAUDE.md](../CLAUDE.md) first. All of them need the server running and homed ([docs/lab/RUN_THE_ARM.md](../docs/lab/RUN_THE_ARM.md)); a person with a hand near the red stop is required for every one.

| Script | Moves the arm | What it does |
|---|---|---|
| `arm_characterise.py` | yes, `move_by` steps of at most 50 mm | Measures the xyz scale per axis, repeatability and a 3x3 grid against a printed mm mat. Writes `calibration/arm_report-<time>.json` and grows the box in `calibration/arm.json`, which `move_to` needs |
| `pick_place.py` | yes | Scripted pick and place between four taught points (`above_a`, `at_a`, `above_b`, `at_b`), `--runs 10`, success and mat error per run to `logs/` |
| `arm_latency.py` | yes, up and down along z | Times `where`, `move_by` and the gripper to find how many actions per second a policy gets |
| `record_episode.py` | yes, one `move_by` per key | Teleoperation that logs two cameras, the arm state and each command to `episodes/<time>/`. `--fake` rehearses with `FakeArm` and fake cameras under `rehearsal/` |
| `sim_demo.py` | no, `FakeArm` only (needs no server, DLL or USB) | Plain-text simulation of the Stack 1 LeRobot adapter (`plugins/lerobot_robot_scorbot_stack1`): a scripted policy, `refused: <reason>` for bad actions, an episode saved under `rehearsal/` and reloaded. Test: `tests/test_lerobot_stack1.py` |
| `teleop.py` | yes, one `move_by` per key | Keyboard control with no cameras or logging: d/a, w/s, r/f for x, y, z, t/g and y/h for wrist pitch and roll (only with `--wrist-step`), o/c for the gripper, q to end. Steps at most 20 mm or 5000 wrist units, a server refusal is printed and the loop goes on. `--fake` rehearses with `FakeArm` |

## Rules

- Each script asks for a typed `go` before it moves the arm, and refuses a step above the server's 50 mm limit; keep both.
- Output folders are created, never overwritten (`open("x")`, `mkdir(exist_ok=False)`).
- `record_episode.py` takes the `source` in `meta.json` from the server's `ping` (`simulated: false` means real), never from the caller.
- Rehearsal output goes to `rehearsal/`, never beside real data in `logs/` or `episodes/`.
- Tests: `tests/test_arm_api.py` (characterise and pick-place against `FakeArm`) and `tests/test_record_episode.py` and `tests/test_teleop.py`.
