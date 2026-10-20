# First motion plan: from "connects" to "arm moves under our code"

Date: 2026-10-11. Status: proposal, nothing built or run. Nothing here is hardware-validated.

## Premise

The arm has run for years under SCORBASE, so the controller accepts a known message stream. Our unknowns are not "can this arm be driven" but "does our byte stream match the one SCORBASE/`USBC.dll` sends, and do we read the replies the same way". The cheapest way to close that is **ground truth from the working vendor stack on this exact arm**, captured at the wire, then replayed against our port offline. Every lab minute goes to collecting evidence first and to our own bytes second.

## The one thing that unblocks most of the rest

**A single lab visit that captures the vendor stack homing and jogging the real arm, with USBPcap running the whole time.** `tools/usbc_home` (built, desk-probed) runs the vendor's own `Home('A')`; USBPcap records the real OUT bytes *and the real controller replies*. That turns the following from "inference from disassembly" into "seen on the wire":

| Open item | Closed by the capture |
|---|---|
| Does the real controller accept the vendor-home bytes (`4D`, `48`, `4F <mask> 48`, `4C`)? | Yes if the vendor DLL's own home completes and the capture shows them |
| Sign of `4D` against the counts; drive units | Counts in the IN replies against the OUT drive values |
| Real switch bands, real reply timing, controller version (`72` reply) | The IN stream; replaces the model in `tests/vendor_home_plant.py` |
| Byte 60 minus byte 0 = queue depth? (stop latency) | OUT ID, echoed ID and byte 60 per message |
| Does the home survive our `5A` Reset? | Read-back with `record_raw_state.py` after swapping to WinUSB (Phase 4 of the DLL plan) |
| Collision flag (IN offset 59) behaviour; emergency bit | Capture E (e-stop at rest) |
| Is the lab's `USBC.dll` a known build? | `--probe` hash |

## Plan

### Phase 0, desk, now (no arm)

| Step | Work | Done when |
|---|---|---|
| 0.1 | Run the gate (`.venv\Scripts\python.exe -m pytest -n 2 tests`) and `tests_extra`. Record the real result | Pass counts written down; any failure triaged |
| 0.2 | Step 1b is already in the working tree, uncommitted (`Scorbot.vendor_home` in `robot.py`, `libcomm.vendor_home_run`, `VendorHomeSource`, `--vendor-home`, four `test_vendor_home_*` files). Audit it against the five items in `2026-10-08-vendor-home-port.md` and list what is missing | A checklist with file:line for each item |
| 0.3 | **Capture analyzer.** `scripts/` tool that reads a USBPcap `.pcapng` (`scripts/usb_trace.py` exists) and, per message, prints OUT ID, command byte, IN echoed ID, byte 60, counts, error words, switch bits, collision byte. Test-first against a synthetic capture and against `tests/fixtures/vendor_home_trace04.json` | Runs on the fixture; output format agreed |
| 0.4 | **Replay comparison.** Given a real capture, compare its OUT command sequence to what `scorbot/vendor_home.py` generates when fed the *real* replies. Report the first divergence | Test with the fixture passes; ready for real data |
| 0.5 | Make the lab-visit card: one page, ordered, with the stop conditions (below) | Reviewed by Codex and Gemini |

### Phase 1, lab visit A: vendor stack on the wire (we send nothing of our own)

Everything already specified in `docs/archive/specs/2026-10-08-vendor-dll-home-plan.md` ("Plan from here", rows 2-6) and `docs/archive/lab/S1_CAPTURE_LAB_CARD.md` captures A, D, E, F, G. Order:

0. **Before anything:** back up the SCORBASE install folder and run `usbc_home` from an isolated *copy* of it (the DLL's `Initialization` rewrites `SCBS.INI` and `USBC.INI`). Install Wireshark with USBPcap and reboot at the start of the visit. Start USBPcap on the controller's root hub **before** the controller is plugged in or powered, so the connect handshake and the `72` version reply are in the capture.
1. Fingerprint the lab `USBC.dll` (`--probe`), `--simulate` on the copied tree (arm unpowered).
2. **Intelitek driver stays bound for the whole vendor block** (one swap, not several). No code of ours runs in this block: our connect sends `5A` and energises the motors, which would contaminate the vendor capture.
3. Capture: `usbc_home --online --no-home` (connect, control on/off).
4. Capture: `usbc_home --online` with `HOME` (arm near home, jaws empty, wrist path clear, hand on the stop). Optionally SCORBASE capture A (same bytes from the vendor's own program), a 1 degree SCORBASE base jog, and e-stop capture E with the arm at rest.
5. **Then, once, swap to WinUSB.** Idle recording; `record_raw_state.py`: did the home survive our connect and Reset? Then the **hand sweep** (`LAB_DAY_CARD.md` A2, `scripts/hand_sweep_report.py`): motors off, each joint moved by hand while recording. It gives the direction of the counts, travel, counts per degree and the switch bands in counts, and it doubles as the no-motion switch check (press each microswitch, see the right bit). It only reads, so it is the safest measurement of the visit. Do it before any run of our own motion code.

Stop conditions (any one ends the visit, logs kept): arm moves unexpectedly, MOTORS LED disagrees with the software, any fault, emergency bit, a joint near a hard stop. The physical stop is the only emergency stop.

### Phase 2, desk: turn the capture into our code's evidence

- Replace the plant's modeled switch bands, timing and `72` reply with the real ones; re-run the 24 port tests.
- Replay comparison (0.4): the port against the real replies. Divergences are bugs in the port, fixed test-first.
- Update `BRING_UP_SEQUENCE.md` step 5/6/8 with named logs. Move claims from "DLL only" to "Capture" only where our own capture shows the same bytes.
- Decide byte-60 pacing and stop budget from the real data.

### Phase 3, lab visit B: our code, smallest possible step

Only if Phase 2's replay matches. Order, each a separate typed-confirm run with a hand on the stop:
1. The **1 degree step comes before the full home**: `Scorbot.vendor_home` is all-or-nothing (wrist pitch about 37 degrees, gripper close and open), so it cannot itself be the 1 degree step. Use the existing pre-home single-motor jog (`pre_home_jog`, one motor, like the vendor's manual jog) at 1 degree, speed 2, to see our bytes move the arm the expected way. If visit A showed the vendor home survives our connect, a normal `bench_joint.py` 1 degree base jog also works.
2. Then `Scorbot.vendor_home` (full home, hand on the stop, empty jaws, clear wrist path).
3. 1 degree base jog at speed 2 (direction and counts per degree with a phone level or marker), then the stop trial (`--stop-after-ms`).
4. Shoulder jog with phone level; stream trial; gripper trial: the existing `LAB_DAY_CARD.md` B-G.

## What each AI does

| Who | Job | Gate |
|---|---|---|
| Claude (me) | Phase 0 build, test-first; plan and card | Gate green before each lab visit |
| Codex | Design check on this plan and on the capture analyzer; review of the replay-comparison logic; `codex-rescue` if stuck twice | Before Phase 0.3 code and before visit B |
| Gemini | Independent review of this plan; desk research on `Scorbase.exe` init mode (optional, off critical path) | Same |
| `/graph` | Run Phase 0.3 + 0.4 as a dependency graph if the owner types it | Owner decision |

## Risks the plan accepts or manages

- **The DLL home moves the arm outside our gates.** Vendor routine, hand on the stop, arm near home, empty jaws; it is the evidence-gathering step, so this is the accepted risk.
- **Driver swaps (Intelitek vs WinUSB).** One at a time; photograph the Driver Provider each time; the 2009 `ERUSB` installer is in `C:\Users\sebas\tools\intelitek`, untested on the lab Windows.
- **USBPcap needs Wireshark with USBPcap installed and a reboot**, and must be started before the controller connects or the handshake is missed.
- **The DLL rewrites `SCBS.INI`/`USBC.INI`.** Run from a copy of the install folder; back up the original first.
- **A capture may show the vendor sequence differs from the recording.** Then the port is wrong; fix it before visit B. That is the point of doing A first.

## Not in this plan

Cartesian motion, new gripper force control, anything off the first-trial path, the USB optimization plan (`2026-10-08-optimization-action-plan.md`).

## Review log

- 2026-10-11 Gemini (`standard`): revise. Accepted: driver-swap ordering, install-folder backup and isolated copy, the 1 degree step cannot be `vendor_home` itself, start USBPcap before connect. Reworded: the step 0.2 line (step 1b exists in the tree). Codex: failed, usage limit until 15:04 local; rerun then.
