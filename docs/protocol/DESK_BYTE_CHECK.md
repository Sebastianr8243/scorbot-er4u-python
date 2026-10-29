# Desk byte check: Stack 2 against the lab DLL (2026-10-26)

What it is: the lab `USBC.dll` ran against a fake controller on a named pipe (`tools/usbc_fake`) and every 64-byte OUT message it wrote was recorded. The real `Scorbot` (legacy handshake and workers) then ran connect, enable, disable and disconnect against a fake `usb` package, and its OUT messages were compared with the DLL's. No arm, no USB. The test is `tests/test_desk_byte_check.py`; the golden file is `tests/fixtures/dll_lab_open_loop.json`, made by `scripts/make_open_loop_fixture.py`.

What it proves: Stack 2 builds the same messages the DLL builds, for the steps below. What it does not prove: that the controller accepts them (the fake arm is our model), timing, or that anything moves. The `0D` keep-alive count is pacing and is not compared. Everything here is unverified on the arm until the USBPcap capture at the lab.

| Step | Result | Detail |
|---|---|---|
| Handshake (reset, parameter download: `5A`, `4F FF 54`, `73 FF`, `42 FF`, `72`, `64`, `61`, the per-axis `53` blocks) | **match** | 88 messages after collapsing repeats |
| Handshake closing `0D` with header `00 0C 00` | **match** | the only message with a non-zero header, same in both |
| Control on (`47`, `4F FF 53`, `42 FF 01 00`, `73 20`, `42 20`) | **match** | in the handshake's tail and in `enable()` |
| Control off, `disable()` | **match** | `47`, `73 FF`, `42 FF 00 00`, `4F FF 53`, `73 FF`, `73 20`, `42 20`, then `64`/`61` three times |
| Close, `disconnect()` | **differs** | Stack 2 sends the DLL's stop group, then one extra short group (`47`, `4F FF 53`, `73 20`, `42 20`, `64`, `61`), then the DLL's closing `42 FF 00 00`, `4F FF 54`. Whether the controller needs the extra group is unverified; pinned in the test so a change shows |
| Encoder region layout (OUT bytes 12 to 35) | **match** | all 599 distinct regions the DLL wrote decode into six (count, sign word) fields and the codec rebuilds each exactly. The DLL only uses sign words `00 00` and `FF FF` |
| Sequence byte at 255 | **differs** | the DLL goes 255 to 0, the legacy code and the codec 255 to 1. The controller accepted both on our 10-06 sessions (`BRING_UP_SEQUENCE.md`) |
| Home | **match** (DLL against DLL) | the lab DLL's Home sent exactly the 327 commands of the earlier Home recording (`tests/fixtures/vendor_home_trace04.json`), so the lab Home equals the 2008 one at run time as well as in the disassembly. `tests/test_vendor_home.py` pins the port against that recording |
| Move | **not testable here** | the DLL ramps its setpoints by its own profile (steps up to 8 counts per message in this run); that is policy, and Stack 2's move code was not run in this check. Only the region layout is compared |

Method note: the recording used the fake's switch model with the roll switch 800 counts wide, `fake_move.py` x 5 mm, 6,804 messages. The raw recording stays outside the repo (`C:\Users\sebas\tools\usbc_desk\rec\`), like the DLL.
