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
x64 WDM function driver, 135 functions, built on a C++ class library (pool tag `Wdm `, device name `ERUSBDevice`). Not KMDF. Addresses are Ghidra addresses (image base 0x10000).

How a write or read reaches the code (all read from the listing, V):
- `DriverEntry` (`FUN_0001c6a0`) points every major function at one stub, `FUN_00010420`. The stub calls a virtual method on the device object, or, when a flag is set, goes through a per-major-function table at `0x1a7a0`.
- That table sends READ to a thunk at `0x12190` and WRITE to `0x120b0`. Each thunk is a single jump through the device object's vtable: READ through slot `+0x38`, WRITE through `+0x48`.
- The device class vtable is at `0x18ec0` (77 entries). Its READ slot is `FUN_00010b20`; its WRITE slot is `FUN_00010c80`.

What the handlers do:
- WRITE (`FUN_00010c80`): length is the IRP's write length; the buffer is `Irp->AssociatedIrp.SystemBuffer`. It calls `FUN_00016550` to build a URB, then `FUN_00016610` to submit it to the lower USB stack with a 5000 ms timeout. Bytes written reported back = the URB's `TransferBufferLength`.
- `FUN_00016550` writes a `_URB_BULK_OR_INTERRUPT_TRANSFER`: header length `0x80`, function `0x9`, `TransferBufferLength` = the length, `TransferBuffer` = the same pointer as the caller's buffer, `MDL` = null, `PipeHandle` from the pipe object, `TransferFlags` = 0 (OUT). No copy at all, no arithmetic on the bytes.
- READ (`FUN_00010b20`): same builder, `TransferFlags` = 3 (IN plus short-transfer-OK). The requested length is capped to the pipe's `MaximumTransferSize` (`USBD_PIPE_INFORMATION +0x10`); a 64-byte request can return fewer bytes. The bytes land straight in the system buffer.
- Endpoints are fixed in the device constructor (`FUN_000117e0`): write pipe = endpoint `0x02` (OUT), read pipe = endpoint `0x83` (IN), both registered with `0x40` (64). Each is matched against the configured interface's pipe list (`FUN_000174d0`) to get its handle; the configuration comes from `USBD_CreateConfigurationRequestEx` in `FUN_00016e30`.

## Verdicts against the claims list (claims list kept with the analysis workspace)
| # | Claim | Verdict |
|---|---|---|
| C1 | WRITE builds a bulk/interrupt OUT URB (function 0x9) | proved: `10c80` to `16550` |
| C2 | The URB buffer is the caller's 64 bytes, unchanged | proved: same pointer, same length, no copy, no arithmetic |
| C3 | READ builds the matching IN URB, reply unchanged | proved, with one caveat: the length is capped to the pipe's MaximumTransferSize, and a short reply is allowed |
| C4 | Endpoints and transfer type | endpoints proved from the driver: OUT `0x02`, IN `0x83`, 64 bytes. The driver does not set the transfer type (function 0x9 serves both), but the ULPGC thesis's device dump of the same `09F1/0007` controller lists two BULK endpoints, `83h` in and `02h` out, 64-byte packets (p. 105, see `ULPGC_THESIS_FINDINGS.md`), and `BRING_UP_SEQUENCE.md` step 1 records bulk OUT `02` / IN `83` running on our own arm (2026-09-29 and 10-06). Transfer type: bulk, from those two sources, not from the driver |
| C5 | No header, trailer, padding or sequence number added | proved on both paths: the URB length equals the user length |

Not covered: what the USB stack and the controller do below the URB (outside these files); the device-control IOCTL path (the DLL uses ReadFile and WriteFile, per `VENDOR_DLL_PROTOCOL.md`).

## Independent check
Codex (read-only, given only `erusb.c` and `erusb.asm` and the five questions, no conclusions from me) reached the same transfer-path result on its own: function 9, direct buffer, no framing, read capped to MaximumTransferSize, endpoints `0x02` and `0x83`. It could not resolve the dispatch chain, because the export it was given held no stub body or vtable data; I resolved that from the listing and file bytes (table at `0x1a7a0`, vtable at `0x18ec0`). One wording note: it labelled C5 "contradicted"; it means it found no framing.

## Gemini cross-check (research, 2026-10-26)
Gemini was asked for the driver framework and Microsoft's URB layout (citation checker: 11 cited, 8 reachable, 2 dead).
- URB layout: Microsoft's x64 `_URB_BULK_OR_INTERRUPT_TRANSFER` is 0x80 bytes with `Hdr.Function` +0x02, `PipeHandle` +0x18, `TransferFlags` +0x20, `TransferBufferLength` +0x24, `TransferBuffer` +0x28, `TransferBufferMDL` +0x30. This matches what `FUN_00016550` writes, so my offset reading holds. Function codes 0x0 (select configuration), 0x9 (bulk or interrupt), 0xB (get descriptor from device) also match; `FUN_00016a00` and `FUN_00016be0` are the 0xB descriptor requests (URB size 0x88).
- Framework: Gemini says Compuware DriverWorks (C++ KDriver/KDevice classes, default pool tag `Wdm `). Treat as a lead: the sources that carry it are dead or do not contain the claim. It does not change any verdict above.

## Next
1. (Optional) Read the device descriptor once with a USB tree viewer to confirm bulk and 64 bytes first-hand; passive, arm not moved. Already settled by the thesis dump and our own arm runs.
2. Wire proof at the lab (needs you): a USBPcap capture while the vendor software runs, compared with what `USBC.dll` says it sends. This is what turns "read from code" into "seen on the cable".
3. Re-label `VENDOR_DLL_PROTOCOL.md` items the real-arm Home has confirmed.
4. Callee-identity check on the 18 differing DLL functions, only if a difference starts to matter.
5. Desk byte check (done 2026-10-26): Stack 2's messages against the lab DLL's, no arm. Result and the two differences it found: [DESK_BYTE_CHECK.md](DESK_BYTE_CHECK.md).
