# Lab USBC.dll and ERUSB.sys: first pass (2026-10-26)

Static analysis only (Ghidra 12.1.4 headless, method in [tools/usbc_analysis/README.md](../../tools/usbc_analysis/README.md)). Nothing loaded or run. The binaries, Ghidra projects and decompiled output stay outside the repo; only this summary is committed.

## Files
| File | Size | SHA-256 (first 16) | PE build date |
|---|---|---|---|
| lab `USBC.dll` (private repo) | 532,576 | 6118465d2ab2aaa3 | 2014-06-11 |
| 2018 (kutzer) | 539,784 | 6de887a8d6f2ce68 | 2018-11-22 |
| 2008 (MTIS) | 471,040 | 91e99a915c4f5e22 | 2008-05-04 |
| `ERUSB.sys` (x64) | 60,152 | 1da14fca474492a0 | 2009-05-18 |

## 1. Lab DLL vs 2018 build
Method: split decompile by function, strip addresses and FUN_/DAT_ names, hash the body, match across builds. Same function count (2747). Loose on purpose: it ignores which function a call goes to.

- 2729 of 2747 lab functions match a 2018 function. 2413 match the 2008 build.
- Real differences (excluding exception-unwind stubs): 18 functions, of which only 3 are named: `Home`, `ChangeConfig`, `_strncpy`.
- `_strncpy`: CRT, same size, just relocated. Ignore.
- `ChangeConfig`: only global-data layout shifted (struct at 0x1008a290 in 2018, 0x1008a298 in lab, byte vs int casts). Same logic.
- `Home`: lab is IDENTICAL to the 2008 `Home`. The 2018 `Home` adds a preamble the lab build lacks: two pre-checks (`FUN_10003655`, `FUN_10004d89`) that log error 0x215 and return 0 on failure, and, when a flag is set, a buffer resync (`FUN_100405f9`, `1003fac0`, `1003fb6a`, `1003f372`, `10005476`, `100401bb`), then `FUN_1002f69e` (2018 only) before the per-group homing code.
- Message layer (comm thread, message zero/number/fill, pacing helpers, all in 0x1003xxxx and 0x1004xxxx): no unmatched functions. So the OUT message layout, command letters and pacing in `VENDOR_DLL_PROTOCOL.md` hold for the lab DLL.

Reading: the lab DLL is a 2014 build, between 2008 and 2018. Protocol notes written from the 2018 build apply to it, except the Home entry sequence: the lab's homing starts directly at `Control('&',1)` with no pre-check and no resync. The per-group homing body is the same code as 2008, so `VENDOR_HOMING_TRACE.md` (made from the lab DLL on the real arm) is the reference for the lab, not the 2018 Home.

Limit: the hash ignores callee identity, so a changed callee with an unchanged caller would not show. The 18 unmatched functions are the full list of caller-visible changes; the callee check is the next step if it matters.

## 2. ERUSB.sys
- x64 WDM function driver, 135 functions, built on a C++ class library (pool tag `Wdm `, device name `ERUSBDevice`). Not KMDF.
- All major functions (create, close, read, write, device control, PnP, power) route through one stub (`LAB_00010420`) into a vtable, so the dispatch is virtual and not yet resolved.
- The only way it talks to the bus is `IOCTL_INTERNAL_USB_SUBMIT_URB` (0x220003) to the lower stack. The URB helpers I read (`FUN_00016a00`, `FUN_00016be0`) are descriptor requests (URB function 0xB: device and configuration descriptor), not the data path.
- Across `FUN_00016be0` (no memcpy, no bit ops) and `FUN_00016e30` (the biggest URB function: one copy, one bit op) I found no transformation of payload bytes.
- Not shown: the read/write path end to end. Pass-through of the 64 bytes is still "no sign of change", not proven. Needs the vtable resolved and the bulk-transfer URB (function 0x9) located.

## Next
1. Resolve the ERUSB vtable at `PTR_FUN_00018e80`, find IRP_MJ_READ/WRITE and the bulk URB build; confirm the buffer is copied unmodified and the endpoint and size.
2. Re-label `VENDOR_DLL_PROTOCOL.md` items now confirmed by the real-arm Home (V stays "read from code", add "confirmed on arm").
3. Callee-identity check on the 18 unmatched functions if a difference starts to matter.
