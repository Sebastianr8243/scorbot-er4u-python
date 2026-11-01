# tests_extra/

Tests for code that is off the first-trial path (connect, state, home, small jogs). Moved out of `tests/` on 2026-10-08 so the lab-visit gate (`tests/`) stays small. Same rules as [../tests/README.md](../tests/README.md): never open USB, never move the arm. CI runs this folder too.

```powershell
python -m pytest -n 2 tests tests_extra       # everything
python -m pytest -n 2 tests_extra             # only these
python -m unittest discover -s tests_extra
```

Run from the repo root: these files import helpers as `tests.<name>`.

| Files | Covers |
|---|---|
| `test_streaming_core.py`, `test_streaming.py`, `test_bench_stream.py` | The streaming driver (`start_stream`): core against a small arm model, legacy loop against fake endpoints, the stream trial rehearsed |
| `test_gripper.py`, `test_bench_gripper.py` | The gripper: legacy `clamp` loop, `move_gripper`, the bench trial |
| `test_camera_*.py` | Webcam capture |
| `test_rerun_view.py`, `test_plot.py`, `test_arm_chain.py`, `test_notes.py` | Viewers, plots, the link chain, the notes sheet |
| `test_mujoco_twin.py` | The MuJoCo twin's tool tip, joint spans and pose hold against `arm_chain` and the manual; skips without `mujoco` |
| `test_build_calibration_csv.py`, `test_kinematics.py` | Offline calibration and kinematics tools |
| `test_usbc_peread.py`, `test_usbc_query.py` | Ghidra dump tools |
| `test_usbc_fake.py` | `tools/usbc_fake`: the fake arm's switch bands and drives, and the guards that refuse a `DriverLink` that is not the pipe |
| `test_policy_arm.py` | `scorbot/policy_arm.py` on the simulated controller: starts at home, one clamped step, travel cap, bad values hold, close twice, act after close refused |
| `test_policy_loop.py`, `test_export_policy_dataset.py` | `examples/policy_loop.py` (the clamp on its own, then the supervised policy run rehearsed in the simulator, including a policy that crashes) and `scripts/export_policy_dataset.py` (episodes reload identically; failed, errored and mixed-source runs are refused) |
| `test_lab_summary.py` | `scripts/lab_summary.py` on hand-made records: direction, scale against the prior, phone angle, other joints, policy runs, problems and exit code |
| `test_usbc_server.py` | `tools/usbc_server` through `scorbot.bridge.Arm` on a real localhost socket, against a fake library: refusals, stop during a move, reconnect, a hung call |
| `test_arm_api.py` | `scorbot.bridge.FakeArm`, the `Arm` client against a stand-in server (replies matched by id, refusals, closed connection, no server), taught points and the box, `examples/arm_characterise.py` math and run, `examples/pick_place.py` |
| `test_record_episode.py` | `examples/record_episode.py` against `FakeArm` and `FakeSource`: both frames, state before the command, action, timing, no overwrite, a dead camera, an arm refusal or an interrupt ending the episode (the row is on disk before the arm moves), the key map (Windows extended keys swallowed), the saved `source`, the `--fake` run |
| `test_usbc_home.py`, `test_usbc_jog.py`, `test_usbc_move.py` | `tools/usbc_home/usbc_home.py`, `tools/usbc_jog/usbc_jog.py` and `tools/usbc_move/usbc_move.py` against a fake library (no DLL, no USB) |

Several of these cover code that is still reachable from `robot.py` (streaming and gripper gates). Before any lab visit, run everything (`tests` and `tests_extra`), not only the gate.
