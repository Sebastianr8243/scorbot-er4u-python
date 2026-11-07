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
| `test_streaming_core.py`, `test_streaming.py`, `test_bench_stream.py` | The streaming driver (`start_stream`): core against a small arm model, legacy loop against fake endpoints (three or five setpoints), the opt-in wrist stream (both wrist motors followed, their pitch-plus-roll bound, the pitch limit on the streamed wrist, their error words), the stream trial rehearsed |
| `test_gripper.py`, `test_bench_gripper.py` | The gripper: legacy `clamp` loop (GUI), `move_gripper` after a vendor Home (order 23), the bench trial (vendor Home, then the gripper) |
| `test_camera_*.py` | Webcam capture and network camera URL forwarding without device property writes |
| `test_setup_lerobot.py` | Separate environment setup, mocked subprocess calls, dry-run, failure summaries and Windows symlink warning |
| `test_mjpeg_camera.py` | LeRobot MJPEG camera registration, RGB/BGR frames, resizing, fresh reads, stream failures and cleanup against loopback HTTP; skips without LeRobot |
| `test_arm_chain.py`, `test_notes.py` | The link chain, the notes sheet |
| `test_mujoco_twin.py` | The MuJoCo twin's tool tip, joint spans and pose hold against `arm_chain` and the manual; skips without `mujoco` |
| `test_build_calibration_csv.py`, `test_kinematics.py` | Offline calibration and kinematics tools |
| `test_usbc_peread.py`, `test_usbc_query.py` | Ghidra dump tools |
| `test_usbc_fake.py` | `tools/usbc_fake`: the fake arm's switch bands and drives, and the guards that refuse a `DriverLink` that is not the pipe |
| `test_policy_arm.py` | `scorbot/policy_arm.py` on the simulated controller: starts at home, one clamped step, travel beyond the old cap, bad values hold, close twice, act after close refused; with `wrist=True`: pitch and roll signs on the two wrist motors, measured back within a count, an arm-only action never moves the wrist motors, pitch and roll together beyond the old cap (waits for measured count arrival with a delayed worker before reversing), the lead margin with a stuck wrist, the speed step shared on one motor |
| `test_policy_arm_step_limit.py` | The opt-in `step_limit="speed"`: `speed_step_deg` (vendor MaxSpeed x speed over the elapsed time, at most the lead margin, time up to the hold timeout), a per-motor step in `clamp`, the lead correction never beating it, PolicyArm wiring with a patched clock; and a frozen copy of the old `clamp` swept against the default path (passes before and after: it guards, it does not test a feature) |
| `test_policy_arm_vendor_home.py` | `PolicyArm.connect` sends the vendor Home, never the legacy one: the same packets byte for byte as a direct `vendor_home`, and the DLL's recorded sequence (`tests/fixtures/vendor_home_trace05.json`), and leaves the gripper homed; without all three confirmations nothing is sent |
| `test_policy_arm_gripper.py` | Binary PolicyArm gripper: simulated open/close, disconnected/unhomed refusals, stop handoff and failed-move handling, stream restart with targets/speed/wrist preserved; real SDK workers on the desk fake compared to the existing DLL gripper fixture (same commands, endpoints and eight-count ramp tolerance). Physical-arm behaviour unverified; `PolicyArm.jaw()` is the DLL's `GetJaw` whole percent and mm (LEDGER PL101) |
| `test_lerobot_scorbot_plugin.py` | `plugins/lerobot_robot_scorbot` without lerobot (stand-in base class): the default real-arm refusal, explicit opt-in and Home gates, real class patched to the simulator, motor-off once across disconnect/atexit, Ctrl+C and exception hooks, cleanup faults, feature dicts, a simulated connect, act, disconnect (needs Ruckig), the two teleoperator maps (keys and scripted walk, step through `clamp`, vendor travel guarded by the robot), the `examples/lerobot_sim_check.py` record command (simulated, local, unattended). With lerobot importable: registration and `make_robot_from_config`; skipped otherwise |
| `test_policy_loop.py`, `test_export_policy_dataset.py` | `examples/policy_loop.py` (the clamp on its own, then the supervised policy run rehearsed in the simulator, including a policy that crashes) and `scripts/export_policy_dataset.py` (episodes reload identically; failed, errored and mixed-source runs are refused) |
| `test_lab_summary.py` | `scripts/lab_summary.py` on hand-made records: direction, scale against the prior, phone angle, other joints, policy runs, problems and exit code |
| `test_lab_sheet.py` | `scripts/lab_summary.py --sheet` (`scripts/lab_sheet.py`) on simulated bench_joint, bench_gripper, bench_stream and idle logs: each record-sheet row filled or by hand, vendor Home only, phone angle required, idle drift, error words from traced packets, missing and corrupt logs, flagged runs, the SIMULATED banner |
| `test_usbc_server.py` | `tools/usbc_server` through `scorbot.bridge.Arm` on a real localhost socket, against a fake library: refusals, stop during a move, reconnect, a hung call |
| `test_arm_api.py` | `scorbot.bridge.FakeArm`, the `Arm` client against a stand-in server (replies matched by id, refusals, closed connection, no server), taught points and the box, `examples/arm_characterise.py` math and run, `examples/pick_place.py` |
| `test_record_episode.py` | `examples/record_episode.py` against `FakeArm` and `FakeSource`: both frames, state before the command, action, timing, no overwrite, a dead camera, an arm refusal or an interrupt ending the episode (the row is on disk before the arm moves), the key map (Windows extended keys swallowed), the saved `source`, the `--fake` run |
| `test_usbc_home.py`, `test_usbc_jog.py`, `test_usbc_move.py` | `tools/usbc_home/usbc_home.py`, `tools/usbc_jog/usbc_jog.py` and `tools/usbc_move/usbc_move.py` against a fake library (no DLL, no USB) |

Several of these cover code that is still reachable from `robot.py` (streaming and gripper gates). Before any lab visit, run everything (`tests` and `tests_extra`), not only the gate.

`test_policy_vendor_limits.py` checks vendor base +170 degrees, both travel windows, shoulder headroom, roll beyond 360, coupled angles, session-home offsets and act-before-Home refusal; no USB or physical arm.
