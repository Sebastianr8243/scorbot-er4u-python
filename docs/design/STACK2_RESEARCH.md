# Stack 2 research: what is known before the first lab run

Written 2026-10-26. A Gemini pass over `docs/protocol/*.md` and a Codex claim check of its five key claims against the source files. Both worked from our own protocol docs, not from the Ghidra export, the GPL source or the manuals, so this is a summary of what the repo already knew, checked, not new evidence. Nothing here is verified on the arm.

## Established (documented disassembly or configuration)

- **Vendor pacing.** After each 64-byte write the DLL waits half of `PCPeriod` (16, so about 8 ms), then reads 64 bytes (`VENDOR_DLL_PROTOCOL.md` lines 26, 427; `PROTOCOL.md` line 360). Between messages it waits 1 ms when (sent ID minus echoed IN byte 0) is below the queue limit, otherwise about 8 ms. Limits from the INI: Buffers 45, HomingBuffers 20, TPBuffers 6, ManualBuffers 4 (`PROTOCOL.md` lines 362-363). This is what the vendor does, not proof that our code needs the same waits.
- **Vendor home.** Pre-pass, then axes 2, 3, 5, 4, 1, 6, then opcode `48` zeroing for every axis (`VENDOR_HOMING_TRACE.md` lines 61, 73). `scorbot/vendor_home.py` already implements it as a pure generator.
- **Driver access.** `USBC.dll` opens `\\.\ERUSBDevice0` with `CreateFileA`, share mode 0, no overlapped I/O, and has no `DeviceIoControl` or `CancelIo` (`VENDOR_DLL_PROTOCOL.md` line 427).

## Not established

- **Whether `ERUSB.sys` honours `CancelSynchronousIo`.** Unknown (line 429). The research pass said "no native driver timeout"; the source says only that any bound lives in the unexamined driver. `DeviceLink`'s timeout depends on this.
- **Reply latency 15.9 to 17.6 ms (mean 16.9).** Attributed to Figure 18 of the ULPGC thesis's SCORBASE capture (`BRING_UP_SEQUENCE.md` line 45). No raw capture is in the repo and `references/` is absent, so it cannot be recomputed. It is a secondary-source number; the interpretation that it is the controller's 16 ms cadence is an inference.
- **What the controller does when the stream stops.** The thesis reports control loss and the POWER LED going green to orange (`ULPGC_THESIS_FINDINGS.md` line 21, "qualitative support", no timeout). The research pass called this capture-observed; it is not. The host must therefore stop on its own (hold, then stop) and must not rely on a controller watchdog.
- **6500 counts per second.** The source leaves speed units unresolved (`VENDOR_DLL_PROTOCOL.md` line 317). Treat it as a prior.
- **IN byte 60 as a queue tail, the exact comms-loss timeout, the `4D` PWM scale.** Unproven.

## Second pass: raw thesis and legacy source (Gemini, 2026-10-26)

Read from the thesis PDF (`references/`, not committed) and `openScorbot/`. Unchecked by a second model; the figure numbers below are Gemini's reading.

- **Timing is real after all, but one capture.** Thesis Figure 18 (p. 25, USBTrace, idle after Home): OUT packets 29.3 to 31.3 ms apart, OUT-to-IN latency 15.9 to 17.6 ms. So the 16.9 ms mean is a figure in the thesis, from a single idle capture, not a motion trace.
- **Stream loss.** The thesis (pp. 25, 26, 30, 110) says control is lost, the LED goes green to orange, and a new `control-on` is needed. No timeout figure, and the word "watchdog" is not used.
- **IN byte 0.** Gemini says Figure 18 refutes "IN byte 0 echoes the OUT ID" (OUT 0x20 pairs with IN 0xF3). The gap is 45 on the 256 ring, which matches the Buffers limit (45), so it reads as a lagged echo. That is our reading, not Gemini's; the lag measure in `vendor_home_source.py` already assumes it.
- **Our sleeps.** Write 8 ms, read 5 ms idle, 12 ms base/shoulder/elbow, 13 ms wrist, 19 ms gripper (`conf.py`), about 20 to 27 ms per message against a ~16 ms controller cadence. `countByte1` wraps 255 to 1; the vendor and thesis wrap 255 to 0, and both are reported to be accepted.
- **Possible gaps in `docs/protocol/PROTOCOL.md`** (to check, not yet fixed): the IN byte 59 collision flag, the 8-motor setpoint layout, the `0x48` counter reset, and I/O opcodes `0x44` and `0x41`. Also a claimed `FF FF F7` versus `FF FF 7F` mismatch in `ULPGC_THESIS_FINDINGS.md`.

## What this means for the build

- The fake controller's latency model is a thesis number plus the vendor's own pacing, not a measurement. Timing from it is not evidence about the real controller, and must never be compared with a real session (repo rule: real and simulated data never share a comparison).
- `tests/fixtures/vendor_home_trace04.json` is the vendor DLL recorded against a fake (`vendor_home_model.Plant`). Byte matching against it is valid; its timing is not.
- Adaptive pacing stays off by default. The echoed-ID lag is already measured by `vendor_home_source.py` (`lag_first`, `lag_max`); reuse that.
- Safe-stop is host-side. Do not design around the controller dropping power.
