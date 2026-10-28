# scorbot

The Python side of the vendor-DLL path. Public calls and units: [docs/API.md](../docs/API.md).

| File | Contract |
|---|---|
| `bridge.py` | `Arm` is a standard-library client of `tools/usbc_server` (JSON lines on 127.0.0.1, request ids, `ArmError` on any refusal). `Position` holds x, y, z in mm (the DLL's xyz divided by `XYZ_UNITS_PER_MM`, unverified on the arm) plus the raw encoder, joint and xyz arrays. `FakeArm` needs no server. `teach` and `go` keep named points in `calibration/positions.json`; `widen_box` grows `calibration/arm.json`, which the server needs before any `move_to`. Command line: `python -m scorbot.bridge`. It never imports USB or ctypes; validation is the server's job |
| `keys.py` | `keyboard_commands`: one command per key press; keys pressed during a move are dropped |
| `camera/source.py` | Frame sources: `FakeSource` and `OpenCVSource` (OpenCV is the optional `camera` extra) |
| `camera/recorder.py` | `encode_jpeg` |

The own-USB-protocol work (no DLL) lives on the `stack2` branch.
