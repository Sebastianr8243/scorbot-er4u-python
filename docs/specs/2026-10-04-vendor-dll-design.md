# Learning from the vendor DLL: design

Date: 2026-10-04. Status: direction agreed (do not run the DLL); awaiting review of this text. Nothing here is hardware-validated.

## Purpose

Intelitek's `USBC.dll` already knows how to do things our own USB code cannot yet do: stop a move, keep the controller's queue short for jogging, stream setpoints, read emergency state and position error. We use it as a **teacher, not a driver**. The arm keeps running on our pyusb path.

## Decision

**We read the DLL; we do not run it.**

1. **Read** the DLL's code by static analysis at a desk. Done for two builds: [VENDOR_DLL_PROTOCOL.md](../protocol/VENDOR_DLL_PROTOCOL.md), method in [tools/usbc_analysis](../../tools/usbc_analysis/README.md).
2. **Confirm** each finding on the lab's controller: first from the packets our own code already logs and from small supervised trials (the fast path), and from USB captures of SCORBASE only for what that cannot show: [VENDOR_PROTOCOL_LAB_PLAN.md](../lab/VENDOR_PROTOCOL_LAB_PLAN.md).
3. **Build** on a finding only after its capture agrees, inside our own driver, under the existing gates.

Why not run the DLL ourselves:

| Running the DLL would need | Reading plus SCORBASE captures needs |
|---|---|
| A 32-bit Python on the lab PC | Nothing new on the lab PC beyond Wireshark, already planned |
| A new tool that can energise the arm outside the `Scorbot` gates | No new path to the arm |
| Signatures confirmed with `undname`, callbacks kept alive, a message pump | Nothing |
| The Intelitek driver active | The same driver, already required for the SCORBASE captures |

What running it would add is exact control over which call is on the wire in each capture. SCORBASE's buttons map closely enough to the calls we care about (connect, home, control on/off, stop, go-to, manual jog), and the S1 lab card already captures each of them.

## What exists

| Piece | Where | State |
|---|---|---|
| Analysis tools (Ghidra export script, query tool, how-to) | `tools/usbc_analysis/` | Done, tested |
| Findings | `docs/protocol/VENDOR_DLL_PROTOCOL.md` | From disassembly, unverified |
| Lab verification plan | `docs/lab/VENDOR_PROTOCOL_LAB_PLAN.md` | Plan |
| ctypes bindings for the DLL | `tools/usbc_probe/bindings.py` | Parked. One getter bound, never run against the real DLL. Kept outside `scorbot/` so the SDK has one path to the arm |

## Rules

1. The vendor binaries, the Ghidra projects and the decompiled output are never committed. They are Intelitek's code and the repository is public.
2. A finding from disassembly is labelled "from disassembly, unverified" wherever it is written, until a capture confirms it.
3. Packet construction in `openScorbot/` changes on the strength of disassembly only for sequences built from command bytes the legacy code already sends, and such a change is tried first on a 1 degree jog. Bytes the legacy code never sends need a capture.
4. Anything built from a confirmed finding goes through `Scorbot` and its gates. A stop built from the vendor's stop sequence is still not an emergency stop.
5. Before comparing captures with the findings, fingerprint the lab's own `USBC.dll` (size, date, SHA-256) and check it against the builds that were read.

## Order of work

1. Done: read both builds, write the findings.
2. Done: `scripts/vendor_check.py` checks an exported trace against the findings, one verdict line per claim, tested on synthetic traces.
3. Lab visit: the captures in the lab plan.
4. After the visit: record a verdict per claim, correct the findings doc, and open one piece of work per confirmed finding (stop, flow control, emergency fault, connect without motors on).

## If we ever do need to run the DLL

Only if a claim cannot be settled through SCORBASE. The parked bindings are the starting point. It would need its own spec first: a 32-bit Python, an explicit allowlist of exports with confirmed signatures, one call per run, a first run with the controller off, the same supervision as any motion, and no movement calls at all (`MoveManual`, `SetJoint`, `MoveTorque`) without a further design.

## Open risks

- A misread layout looks as convincing as a correct one. Rule 2 exists for this.
- The lab's DLL and firmware may be older than the builds read. Rule 5 and the captures exist for this.
- SCORBASE may not expose a call cleanly (for example which `Initialization` mode it uses is only visible as the resulting messages).
