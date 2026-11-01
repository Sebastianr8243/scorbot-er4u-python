# Two stacks and the AI goal

Written 2026-10-26 so the idea is not lost. Everything about the arm here is unverified unless it says a run backs it.

## The two stacks

- **Stack 1 (vendor DLL):** Intelitek's `USBC.dll` and ERUSB driver, with our Python on top (`tools/usbc_*`, `scorbot/bridge.py`). Vendor Home ran on the real arm on 2026-10-20. The DLL plans each move itself and refuses a new target mid-move, so control is "go to this point, tell me when done".
- **Stack 2 (own USB):** our own protocol code (`openScorbot/`, `scorbot/robot.py`) over pyusb. Never moved the arm; its one real home attempt failed on 2026-10-06. Control is streaming: send the next setpoint about every 20 ms.

```
AI / VLA (camera + instruction) -> target -> Robot-shaped class -> Stack 1 or Stack 2 -> arm
```

Only the last link differs. The AI side talks to a `Robot` interface (`get_observation`, `send_action`), so either stack can sit underneath.

## What each is good for with AI

| | Stack 1 | Stack 2 |
|---|---|---|
| Fits | Chunked actions: pick and place, "move to the cup"; the AI decides where, the DLL decides how | Continuous, reactive control at a steady rate |
| Ready | Closest: bridge merged (PR #29), not yet run on the arm | Needs the first successful jog, then driver and timing work |
| Limit | No new target mid-move | Unproven; timing sleeps are load-bearing |

## Decision (2026-10-26, proposed)

> **Superseded 2026-10-31:** Stack 2 is the main path (LeRobot VLAs with no vendor DLL); Stack 1 is the backup. Kept below as it was proposed.

1. Stack 1 first: a LeRobot-style `Robot` class over `bridge.py`, tested with `FakeArm`, then run at the lab with a person present at the arm.
2. Stack 2 step zero in the same visit if time allows: read state and make one 1 degree jog.
3. Stack 2 streaming and speed only after step 2 works.

## Stack 2 notes from this day

- **Done (`stack2-optimize`, f22ed65):** `libdef` reads `MAX_COUNT`, `MSG_LEN`, `TIME_OUT_W` once at import instead of parsing `data.json` per USB message. About 1.7 ms of a ~21 ms cycle (estimate from 0.33 ms per read; the old code was not timed). Bytes and sleeps unchanged. An independent review found nothing; the tests ran: 1434 passed.
- **Bigger speed gain:** the 8 to 19 ms sleeps. The vendor DLL waits half of `PCPeriod` (8 ms) between write and read, and only 1 ms before the next message when the controller's queue is shallow (`VENDOR_DLL_PROTOCOL.md` section 5). An off-by-default adaptive pacing is possible at the desk; turning it on needs a 1 degree jog on the arm and a `scripts/usb_trace.py compare` capture.
- **Driver conflict:** pyusb needs WinUSB (Zadig), which removes ERUSB and breaks Stack 1 (what happened 2026-10-20). `scorbot/transport/device_file.py` opens `\\.\ERUSBDevice0` the way the DLL does, so Stack 2 could run on Intelitek's driver. Tested against a fake only. Read from `ERUSB.sys` since (2026-10-31, `docs/protocol/LEDGER.md` PL30 to PL32): the driver waits about 5 s itself and the cancel cannot cut it short.
- **LeRobot:** branch `lerobot-adapter` has a plugin over `PolicyArm`, simulator only, built on Stack 2 streaming and unverified against an installed LeRobot.
