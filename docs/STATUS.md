# Project status

**As of 2026-10-20, `origin/main` at `fa0d9f2`, plus branch `arm-api`.** One page: what works, how well we know it, what is in the way. Old reports: [`archive/reports/INDEX.md`](archive/reports/INDEX.md). Update this page whenever a result changes a row.

**Goal:** Python control of the Intelitek ScorBot ER-4U through its original controller: home, move to points, grip, and later camera-driven tasks.

**In one paragraph:** there are two stacks (map: root `CLAUDE.md`, "Layers"). **Stack 1** drives Intelitek's own `USBC.dll` from Python (`tools/usbc_*`); its vendor Home ran on the real arm on 2026-10-20 (Home OK in 134 s, order 2, 3, 5, 4, 1, 6; log in the private repo). The arm server and Python API (`tools/usbc_server`, `scorbot/bridge.py`) are built on Stack 1 and ran end to end on the lab DLL against a fake controller, but not yet on the arm. **Stack 2** is our own USB protocol (`scorbot.Scorbot`, `openScorbot`): large and desk-tested, but its one real home attempt (2026-10-06) failed after 4.2 s. It is the long-term no-DLL goal, not the demo path.

## Evidence scale

| Level | Meaning |
|---|---|
| E0 | Idea or note, no design |
| E1 | Designed: a spec or doc exists, no code |
| E2 | Built, with unit tests, no hardware and no outside reference |
| E3 | Checked against an outside reference without the arm (vendor DLL on a fake controller, the ULPGC thesis capture, vendor disassembly) |
| E4 | Ran on the real arm once, result recorded in a log |
| E5 | Ran on the real arm repeatedly and the behaviour is understood |

"Reviewed" is a separate column: yes means Codex and Gemini both reviewed it and the findings were fixed; it never raises the level. A simulator run is E2, never more.

## Stack 1: vendor DLL (the arm path)

| Capability | Level | Reviewed | Proof | What is in the way |
|---|---|---|---|---|
| Vendor Home (`tools/usbc_home`) | E4 | no | 2026-10-20 run log, private repo; lab Par files match `protocol/VENDOR_HOMING_TRACE.md` | Ran once |
| Arm server + Python API (`tools/usbc_server`, `scorbot/bridge.py`) | E3 | no | `tests_extra/test_usbc_server.py`; desk run on the lab DLL build against `tools/usbc_fake`, 2026-10-20 ([`requirements/arm-api.md`](requirements/arm-api.md)) | First lab run; suggest `/codex:review` before merge |
| Relative point move (`move_by`, `tools/usbc_move`) | E3 | no | Desk run above (a 10 mm move read back as 9,996 units) | Physical mm scale unmeasured (`examples/arm_characterise.py`) |
| Absolute move (`move_to`) after a Home | E3 | no | Desk run above | Absolute Teach never ran on the arm |
| Gripper, speed (`OpenGripper`, `CloseGripper`, `Speed`) | E3 | no | Desk run above; names from the lab DLL's export table | Never ran on the arm |
| Joint jog (`tools/usbc_jog`) | E2 | no (Codex only) | `tests_extra/` | Axis numbers not confirmed on the arm |
| Characterise, pick-and-place (`examples/`) | E2 | no | `tests_extra/test_arm_api.py` | Needs the arm and a printed mm grid mat |

## Stack 2: own USB protocol (long-term, not the demo path)

| Capability | Level | Reviewed | Proof | What is in the way |
|---|---|---|---|---|
| Connect, handshake, raw state read | E4 | no | [`archive/evidence/2026-09-29-idle-capture-handoff.md`](archive/evidence/2026-09-29-idle-capture-handoff.md) | Only one idle capture |
| Legacy home | E4 (failed) | no | [`archive/evidence/2026-10-06-lab-logs/ANALYSIS.md`](archive/evidence/2026-10-06-lab-logs/ANALYSIS.md) | Failed at 4.2 s |
| Vendor-home port (`scorbot/vendor_home.py`) | E3 | yes (4 rounds) | [`archive/specs/2026-10-08-vendor-home-port.md`](archive/specs/2026-10-08-vendor-home-port.md), `tests/test_thesis_crosscheck.py` | Needs a USBPcap capture of Stack 1 to replay against |
| Packet codec (`scorbot/transport/`) | E3 | no | `tests/test_transport_codec.py` | Not wired into `Scorbot` |
| Joint jog, streaming, gripper, inch home, software stop | E2 | no | `tests/`, `tests_extra/` | Never ran on the arm |
| Simulator, recording, replay (`scorbot/session/`) | E2 | no | `tests/` | Offline only |

## Critical path

```mermaid
flowchart LR
    M["Review and merge arm-api"] --> L["Lab: server, where, by 10, gripper"]
    L --> C["Characterise on the grid mat (scale, repeatability)"]
    C --> P["Pick-and-place x10, filmed"]
    P --> V["Later: cameras feed Arm.move_to"]
```

Steps and commands: [`lab/RUN_THE_ARM.md`](lab/RUN_THE_ARM.md), "Python API". Later, separately: capture Stack 1 with USBPcap and replay it against Stack 2 (`scripts/replay_vendor_home.py`).

## Owner decisions open

| Decision | Needed before |
|---|---|
| Baseline [`requirements/arm-api.md`](requirements/arm-api.md) | Merging arm-api |
| `/codex:review` on arm-api (motion logic) | Merging arm-api |

Stack 2 decisions (near-home confirmation, shoulder travel bound, BACKLOG 20) wait until that stack is picked up again: [`archive/project/BACKLOG.md`](archive/project/BACKLOG.md).

## Not covered here

The story of each change is in [`archive/project/PROJECT_LOG.md`](archive/project/PROJECT_LOG.md); hazards are in [`archive/design/SAFETY_CASE.md`](archive/design/SAFETY_CASE.md).
