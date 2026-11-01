# Protocol ledger

One row per claim Stack 2 relies on, with how we know it. Started 2026-10-31. Add a row when a result lands; never delete one, mark it wrong instead.

**Status:** `reversed` (read from the DLL's code), `desk` (the DLL or Stack 2 seen doing it against `tools/usbc_fake`), `arm` (a bench log on the real arm). **Level** is the `docs/STATUS.md` evidence scale: the DLL on the fake or its disassembly is E3, Stack 2 on the fake alone is E2. Nothing here is `arm` yet.

**Two DLL builds.** Addresses are from the SDK Release `USBC.dll` (sha256 `1f7a5fca...390f0`, 471,040 bytes), the one opened in Ghidra. The desk runs use the lab tree's `USBC.dll` (sha256 `6118465d...bd821`). Older docs cite a third, the 2018 build, whose addresses differ again (its gripper routine is `0x1000d943`, this one's `0x1000d693`). The DLL, the decompiled code and the raw recordings stay outside the repo.

## Link and pacing

| ID | Claim | Status | Level | Source |
|---|---|---|---|---|
| PL01 | The DLL opens the driver with a plain exclusive, non-overlapped `CreateFileA` and does blocking 64-byte `WriteFile`/`ReadFile`. It imports no `DeviceIoControl`. Stack 2's `device_file` opens it the same way, so no driver swap is needed | reversed | E3 | Release build import table; USB loop `0x1003bfe8`; `scorbot/transport/device_file.py` |
| PL02 | The DLL's precise wait sleeps for the wait minus about 9 ms, then spins on `QueryPerformanceCounter` | reversed | E3 | `0x10019a09` |
| PL03 | Pacing compares sent ID minus echoed ID with the queue limit (45, 20 while homing, 4 manual, 6 pendant) | reversed | E3 | `VENDOR_DLL_PROTOCOL.md` section 5 |
| PL04 | The handshake, control on and off, and the setpoint region bytes Stack 2 builds equal the DLL's | desk | E3 | `DESK_BYTE_CHECK.md` |
| PL05 | The controller answers on a cadence of about 16 ms | thesis capture | E3 | `BRING_UP_SEQUENCE.md`, "Reply timing" |
| PL06 | At the legacy idle pace (one write and one read per message, 18.8 ms per reply over 505 replies), the echoed ID advanced 329 over 431 replies: at least about a quarter of real replies do not echo a newer ID than the last one | arm log, re-read 2026-10-31 | E4 | `docs/evidence/2026-10-06-lab-logs/idle-01.jsonl` (git history, before `a3af270`): 20 samples' `raw_hex` byte 0 against `packet_index`, which counts reads |
| PL07 | At that pace the lag (sent minus echoed ID) climbs about 7 per 27 replies to about 46, then the echo leaps about 48 ahead in one sample interval (twice in 505 replies). Reading: the controller holds about 47 queued messages and drops the backlog when the host gets further ahead (inference, not reversed) | arm log | E4 (pattern), E0 (reading) | same log |
| PL08 | IN byte 60 equals the echoed ID plus 47 in all 20 samples | arm log | E4 | same log |

## Home

| ID | Claim | Status | Level | Source |
|---|---|---|---|---|
| PL10 | Stack 2's vendor Home sends the same non-`0D` commands, in the same order, as the lab DLL's `Home('A')` on the same fake: 330 commands, with the gripper ending on its stall | desk | E3 | `tests/fixtures/vendor_home_trace05.json`, `tests/test_vendor_home.py` (2026-10-31) |
| PL11 | The lab DLL's Home with gripper end stops equals the 2008 recording (`trace04`) plus one `47` before each of the three gripper `4D 20 0` | desk | E3 | diff of the two fixtures, 2026-10-31 |
| PL12 | Stack 2's whole bring-up (probe, enable, vendor Home, base +1 and -1 degree, 3 s sine stream, disable) runs over `device_file` on the pipe fake and passes every step | desk | E2 | `pipe_ladder.py` run, 2026-10-31 (scratch, not committed) |

## Gripper

| ID | Claim | Status | Level | Source |
|---|---|---|---|---|
| PL20 | Open and close both call one routine: gripper motor on (`42 20 01`), mode T (`4F 20 54`), then `4D 20` with the gripper's `[Homing] Velocity` (open -165, close +165 with the shipped files), and set a countdown of 120 | reversed | E3 | `0x1000d693`, called by `OpenGripper` `0x100185b8`, `CloseGripper` `0x1001868c` and the type 17 homing step |
| PL21 | The countdown drops by one for each controller reply the USB loop processes, so a gripper move lasts at most about 120 messages (about 2 s at PL05's cadence) | reversed, desk | E3 | per-reply handler `0x1002539c`, called from the USB loop; the 2026-10-26 lab-DLL recording (no gripper stops) shows drives of 120 to 124 messages |
| PL22 | **Stall rule.** With 109 to 4 ticks left, every `ImpactCondTicks` ticks, the gripper's count is compared with the last sample. Motion of at most `ImpactCondEnc` counts is a stall: the DLL sends `47`, then `4D 20 0`, `73 20`, `42 20 0`. The gripper's values are 2 and 2 in all five lab parameter sets | reversed, desk | E3 | `0x1002539c` reads record offsets `+0x14C` and `+0x150` (the `[Homing]` fields `ImpactCondEnc`, `ImpactCondTicks`); `er4u/*/ER4Ax6.ini`; lab DLL on the stop fake ends 2 to 4 messages after the stop, with `47` first, every time |
| PL23 | With no stall the move ends at the last ticks with the same messages minus the `47`. The type 17 homing step only waits for the countdown, so a gripper that never stalls is not an error: it fails only on emergency (300) or a user abort (569) | reversed, desk | E3 | the type 17 case of the per-axis homing routine `0x10007fbd`; the 2026-10-26 recording |
| PL24 | After a close, the routine itself sends `48 20 0` (zero the gripper count) at the last tick, when a per-axis flag word (record `+0x83e`, not decoded) is not all set. It never does after an open | reversed, desk | E3 | `0x1002539c`; `48 20` follows only the closes in both recordings |
| PL25 | The stall check runs only while the DLL's online flag is set (written by the USB loop) and its mode word is 1. Both hold on the fake, since the stall path ran | reversed, desk | E3 | flags `0x1007bfc4`, `0x1007bfbc` |
| PL26 | Stack 2 ends its gripper drives on the same check (PL22) and sends the same `47` first. Three differences: the check starts 10 fresh replies plus the measured lag after the drive (vendor: 10); it must be quiet twice in a row (vendor: once); and replies that repeat the last echoed ID are not counted | desk | E3 | `scorbot/vendor_home.py` `gripper_drive`; on the pipe its three gripper drives last 44, 82 and 82 messages against the DLL's 42, 80 and 80 |
| PL27 | Deviation, on purpose: Stack 2 stops the Home with error 574 when the gripper has not stalled within the vendor's 120 replies (plus the lag), stalls before it travelled 200 counts, sees 10 repeated replies in a row, or closes (homing type 17) more than 5 % shorter than it opened. The vendor carries on and zeroes the count, mid-travel if need be | design | E2 | `scorbot/vendor_home.py` docstring; an outside review (2026-10-31) reproduced a mid-travel zero from two repeated replies, then from counts held for 6 replies under advancing echo IDs, and a 10 s push against a jittering stop, before these limits |
| PL29 | Known limit, not closed: an object in the jaws shortens the open and the close alike, so Stack 2, like the vendor, zeroes the gripper at the object. Today's guards are the operator's typed EMPTY before the Home and the close-versus-open check (PL27). The closing guard is a per-arm minimum full stroke, which needs the real stroke first (lab step G logs the three gripper travels) | design | E2 | outside review round 3, 2026-10-31, reproduced it on the arm model |
| PL28 | Once the arm is homed, an ER-4u `OpenGripper` can take `JawMetric` (a 70 mm position move) instead of the drive, when the same per-axis flag word is all set. A lead for a position-controlled gripper action later | reversed | E3 | `OpenGripper` `0x100185b8`, check `0x10023259` |

## Driver (`ERUSB.sys`, sha256 `1da14fca...bdad3`)

| ID | Claim | Status | Level | Source |
|---|---|---|---|---|
| PL30 | READ and WRITE are served inside the dispatch routine: the driver sends the URB down on its own IRP and waits for it, non-alertable, for at most 5000 ms. On a timeout it cancels its own IRP, waits for that, and completes our request with `STATUS_TIMEOUT` (`0x102`, a success status) and the URB's byte count | reversed | E3 | `FUN_00010b20` (read), `FUN_00010c80` (write), `FUN_00016610` (submit and wait) |
| PL31 | Our request is never marked pending and gets no cancel routine, so `CancelSynchronousIo` from user mode cannot end a blocked read; it ends when the controller answers or after the driver's 5 s. Inference: `ReadFile` then returns success with no bytes, and `device_file` now raises that as a timeout instead of retrying ten times (up to 50 s) | reversed, inference | E3 | PL30; `scorbot/transport/device_file.py` |
| PL32 | Inference: the handle is opened for synchronous I/O, so Windows runs one request at a time on it. A stop written from another thread waits behind a blocked read, up to 5 s. The red stop is the emergency stop for this reason too | inference | E1 | PL30, Windows synchronous file objects |

## Open

| ID | Question | How it closes |
|---|---|---|
| PL90 | What the drive value of `4D` means physically | Arm: the gripper's count rate at -165 |
| PL91 | Desk half answered (PL30, PL31). Still open: what `ReadFile` really returns after the driver's 5 s, and how long a stop waits behind a blocked read | Lab, no motion: open `device_file`, read once without writing first, time it and log the result |
| PL92 | How far behind the real controller answers while homing (at idle, PL07 shows a sawtooth up to about 46). Also how long the runs of non-advancing replies are under the homing pace: the gripper drive stops at 10 in a row (PL27) | Arm: the Stack 2 probe's echo lag and `repeats`; the Home log in step G |
| PL93 | Whether the controller accepts Stack 2's Home at all | Arm, after the gates in the lab plan |
| PL94 | What the per-axis flag word at `+0x83e` holds (PL24, PL28) | Ghidra: its writers |
