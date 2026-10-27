# tests/

`unittest` tests run by pytest. No hardware: they never open USB, load the real `USBC.dll` or move the arm. Read the root [CLAUDE.md](../CLAUDE.md) first.

```powershell
python -m pytest -n 2 tests      # about 15 s
python -m unittest discover -s tests
```

| File | Covers |
|---|---|
| `test_usbc_server.py` | `tools/usbc_server` through `scorbot.bridge.Arm` on a real localhost socket against a fake library: refusals, stop during a move, reconnect, a hung call, the `simulated` flag in `ping`, every reply written to the log, a second client told the server is busy (and not disturbing the first or a running move) |
| `test_arm_api.py` | `FakeArm`, the `Arm` client against a stand-in server (replies matched by id, refusals, closed connection, no server), taught points and the box, `examples/arm_characterise.py` and `examples/pick_place.py` |
| `test_record_episode.py` | `examples/record_episode.py` against `FakeArm` and `FakeSource`: both frames, state before the command, timing, no overwrite, a dead camera, an arm refusal, the key map, the saved `source` |
| `test_teleop.py` | `examples/teleop.py` against `FakeArm` and a scripted key stream: keys to moves, a refusal goes on, a DLL error or a busy reply ends and says to check `where`, the step cap, the status line |
| `test_lerobot_stack1.py` | `plugins/lerobot_robot_scorbot_stack1` and `examples/sim_demo.py` on `FakeArm`: step cap, box, Home, error latch, real-server flag, features, data_source, dataset writer and reload, the demo. A socket guard makes any connection attempt fail |
| `test_camera_source.py` | `FakeSource` and `OpenCVSource` with OpenCV replaced by a fake module |
| `test_usbc_home.py`, `test_usbc_jog.py`, `test_usbc_move.py` | The one-shot vendor-DLL tools against a fake library |
| `test_usbc_fake.py` | `tools/usbc_fake`: the fake arm's switch bands and drives, and the guards that refuse anything but the named pipe |
| `test_lab_check.py` | `scripts/lab_check.py`: the file, port, box and tracked-file checks |
| `test_summarize_log.py` | `scripts/summarize_log.py` on hand-made logs: table and totals, events between a command and its reply, a cut-off last line, long reasons, read-only and standard library only |
