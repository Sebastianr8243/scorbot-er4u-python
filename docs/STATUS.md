# Project status

**As of 2026-10-25, `main`.** One page: what works, how well we know it, what is in the way. Update this page whenever a result changes a row.

**Goal:** Python control of the Intelitek ScorBot ER-4U through its original controller: home, move to points, grip, and later camera-driven tasks.

**In one paragraph:** the arm moves through Intelitek's own `USBC.dll` (`tools/usbc_*`). The vendor Home ran on the real arm on 2026-10-20 (Home OK in 134 s, order 2, 3, 5, 4, 1, 6; log in the private repo). The arm server and Python API (`tools/usbc_server`, `scorbot/bridge.py`), the episode recorder and the move-timing replies ran against the lab DLL on a fake controller or in unit tests, but not yet on the arm. Our own USB protocol (no DLL) lives on the `stack2` branch.

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

## Capabilities

| Capability | Level | Reviewed | Proof | What is in the way |
|---|---|---|---|---|
| Vendor Home (`tools/usbc_home`) | E4 | no | 2026-10-20 run log, private repo; lab Par files match the vendor homing trace (`stack2` branch) | Ran once |
| Arm server + Python API (`tools/usbc_server`, `scorbot/bridge.py`) | E3 | no | `tests/test_usbc_server.py`; desk run on the lab DLL build against `tools/usbc_fake`, 2026-10-20 ([`REQUIREMENTS.md`](../REQUIREMENTS.md)) | First lab run; suggest `/codex:review` before merge |
| Relative point move (`move_by`, `tools/usbc_move`) | E3 | no | Desk run above (a 10 mm move read back as 9,996 units) | Physical mm scale unmeasured (`examples/arm_characterise.py`) |
| Absolute move (`move_to`) inside the measured box | E3 | no | Desk run above | Absolute Teach never ran on the arm |
| Gripper, speed (`OpenGripper`, `CloseGripper`, `Speed`) | E3 | no | Desk run above; names from the lab DLL's export table | Never ran on the arm |
| Joint jog (`tools/usbc_jog`) | E2 | no (Codex only) | `tests/` | Axis numbers not confirmed on the arm |
| Characterise, pick-and-place (`examples/`) | E2 | no | `tests/test_arm_api.py` | Needs the arm and a printed mm grid mat |
| Episode recorder (`examples/record_episode.py`) | E2 | Codex (one finding, fixed) | `tests/test_record_episode.py`; `--fake` rehearsal | Never run with real cameras or the arm |
| Lab preflight (`scripts/lab_check.py`) | E2 | no | `tests/test_lab_check.py` | USB, driver and 32-bit Python checks never run on the lab PC |

## Critical path

```mermaid
flowchart LR
    M["Review and merge arm-api"] --> L["Lab: server, where, by 10, gripper"]
    L --> C["Characterise on the grid mat (box, scale, repeatability)"]
    C --> P["Pick-and-place x10, filmed"]
    P --> V["Later: cameras feed Arm.move_to"]
```

Steps and commands: [`lab/RUN_THE_ARM.md`](lab/RUN_THE_ARM.md), "Python API".

## Open

| Item | Needed before |
|---|---|
| Baseline [`REQUIREMENTS.md`](../REQUIREMENTS.md) | The lab run |
| `/codex:review` on the arm API, recorder and `lab_check.py` (motion logic and hardware checks) | The lab run |
