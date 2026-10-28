# tests

`unittest` tests run by pytest. They never open USB, load the real `USBC.dll` or move the arm.

```powershell
python -m pytest -n 2 tests          # about 15 s
python -m unittest discover -s tests
```

| File | Covers |
|---|---|
| `test_usbc_server.py` | `tools/usbc_server` through `scorbot.bridge.Arm` on a real localhost socket against a fake library: refusals, stop during a move, reconnect, a hung call, the `simulated` flag in `ping`, every reply written to the log, a second client told the server is busy |
| `test_arm_api.py` | `FakeArm`, the `Arm` client against a stand-in server, taught points and the box, `examples/arm_characterise.py`, `examples/pick_place.py` |
| `test_record_episode.py` | `examples/record_episode.py` against `FakeArm` and `FakeSource` |
| `test_teleop.py` | `examples/teleop.py` against `FakeArm` and a scripted key stream |
| `test_lerobot_stack1.py` | `plugins/lerobot_robot_scorbot_stack1` and `examples/sim_demo.py` on `FakeArm`; a socket guard makes any connection attempt fail |
| `test_camera_source.py` | `FakeSource` and `OpenCVSource` with OpenCV replaced by a fake module |
| `test_usbc_home.py`, `test_usbc_jog.py`, `test_usbc_move.py` | The one-shot vendor-DLL tools against a fake library |
| `test_usbc_fake.py` | `tools/usbc_fake`: the fake arm's switch bands and drives, and the guards that refuse anything but the named pipe |
| `test_lab_check.py` | `scripts/lab_check.py` |
| `test_summarize_log.py` | `scripts/summarize_log.py` on hand-made logs |
| `test_check_docs.py` | `scripts/check_docs.py` on a known-bad tree |
