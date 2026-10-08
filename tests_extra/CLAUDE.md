# tests_extra/

Tests for code that is off the first-trial path (connect, state, home, small jogs). Moved out of `tests/` on 2026-10-08 so the lab-visit gate (`tests/`) stays small. Same rules as [../tests/CLAUDE.md](../tests/CLAUDE.md): never open USB, never move the arm. CI runs this folder too.

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
| `test_lab_teleop.py`, `test_follow.py` | Teleop mode (key `t` of the guided session) and the one-jog follower |
| `test_camera_*.py` | Webcam capture |
| `test_rerun_view.py`, `test_plot.py`, `test_arm_chain.py`, `test_notes.py` | Viewers, plots, the link chain, the notes sheet |
| `test_build_calibration_csv.py`, `test_kinematics.py` | Offline calibration and kinematics tools |
| `test_usbc_peread.py`, `test_usbc_query.py` | Ghidra dump tools |

Several of these cover code that is still reachable from `robot.py` or the lab session (streaming and gripper gates, teleop). Run this folder before any lab visit that uses them.
