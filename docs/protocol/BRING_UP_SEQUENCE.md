# Bring-up sequence: what it takes to move the arm, and how sure we are

One page. Each step is what the vendor's SCORBASE does to get the arm from "powered on" to "moving", the bytes involved, where our knowledge comes from, and whether it has ever run on **our** arm. Update the last column when a real log shows it.

Evidence labels: **Arm** = our own real logs (`docs/evidence/`); **Capture** = the ULPGC thesis's captures of SCORBASE on the same `09F1/0007` controller ([ULPGC_THESIS_FINDINGS.md](ULPGC_THESIS_FINDINGS.md)); **DLL** = our disassembly of the vendor's `USBC.dll` ([VENDOR_DLL_PROTOCOL.md](VENDOR_DLL_PROTOCOL.md)); **Legacy** = what `openScorbot/` sends. Everything on the arm is "unverified" in the sense of `CLAUDE.md` unless it says Arm.

## The nine steps

| # | Step | Bytes (from offset 4 of the 64-byte OUT) | Evidence | On our arm? |
|---|---|---|---|---|
| 1 | Open USB | VID `09F1`, PID `0007`, config 1, interface 0, bulk OUT `02`, bulk IN `83`, 64 bytes | Arm, Capture | **Yes** (09-29, 10-06) |
| 2 | Reset handshake and parameter download | `5A`; `4F FF 54`; `73 FF`; `42 FF 00 00`; wait for the queue; `72`, `64`, `61`; `53 00 08 00 10` (host period 16 ms); the per-axis `53` parameter block | DLL (vendor "mode 1"), Legacy matches byte for byte | **Yes** (connect, state reads) |
| 3 | Keep-alive | One numbered OUT after another, `0D` in byte 4, byte 0 +1 each time; read the IN reply every cycle (its byte 0 echoes the number). Stop sending and the controller drops control and the POWER LED goes orange. | Capture, DLL, Arm | **Yes**, for about 45 s at 54 messages/s (18.5 ms each) |
| 4 | Control on (motors on) | `47`; `4F FF 53`; `42 FF 01 00`; `73 20`; `42 20 00 00` | DLL, Legacy (`motorson`) | **Yes** (MOTORS LED lit, 296 ms) |
| 5 | **Home**: find each switch and zero the counts | Vendor: parameter download, then per axis in the order shoulder, elbow, roll, pitch, base, gripper, a controller-side drive (`4D`, mode `H`) until the switch bit, a back-off offset, then `48` (set position 0). Legacy: a host-streamed setpoint ramp of 10 counts per message, no offset, no `48`. | Vendor: **DLL only**, bytes never captured. Legacy: Arm | **No. Legacy failed on 10-06** after 4.2 s |
| 6 | **Move** | `47`; then `0D` messages whose slot `12 + 4(n-1)` holds motor n's absolute setpoint (16-bit little-endian magnitude, then two sign bytes `00 00` or `FF FF`); then `4F 3F 53`, `73 20`, `42 20`. Step size per message is the speed. | Capture (waist, wrist, five-motor moves), DLL, Legacy bytes identical | **Never** |
| 7 | Read-back and safety bits (IN) | byte 2 bit 0 emergency; byte 5 home switches (pressed = bit set; base 1, shoulder 2, elbow 4, pitch 8, roll 16); positions at `19 + 5N`; position error at `22 + 5N`; **byte 59 impact bitmask** (0x01 motor 1, 0x18 motors 4 and 5, 0x20 gripper) | Switches and impact: Capture and DLL. Emergency bit: **DLL only** | Switches, counts, error words decoded and logged. Impact byte recorded since 2026-10-08. Emergency bit not decoded. |
| 8 | Stop | Vendor `Stop('A')`: copy measured positions into the setpoints, then `47`, `4F 3F 53`, `73 20`, `42 20`. Legacy's close lacks the `47`; our `request_stop` sends it. Physical e-stop: motor power cut, controller goes to COFF, stays off until control-on (manual). | DLL, manual | **Never run** |
| 9 | Control off and disconnect | `47`; `73 FF`; `42 FF 00 00`; `4F FF 53`; `73 FF`; `73 20`; `42 20 00 00` | DLL, Legacy (`motorsoff`) | **Yes** (disable 656 ms, exit 906 to 985 ms) |

## What this says

- **The protocol side is mostly done.** Steps 1 to 4 and 9 run on our arm. Steps 6 and 8 have two independent sources and our legacy bytes match both, but they have never moved or stopped our arm.
- **Step 5 is the real gap.** The vendor homes with the controller doing the driving; we have only ever tried the host-streamed way, and it failed. See `docs/evidence/2026-10-06-lab-logs/ANALYSIS.md`.

## The gaps, and how each one closes

| Gap | Why it matters | How it closes |
|---|---|---|
| How to home (bytes of the vendor's `4D` / mode `H` drive, its units and sign) | We cannot establish a reliable zero without it | A USBPcap capture of SCORBASE Home (`docs/lab/S1_CAPTURE_ADDENDUM.md`). The thesis never describes its own home routine; its source is on the thesis CD (`codigo.7z`), not online. |
| What a count means physically: jog direction, counts per degree | Every move depends on it | One 1 degree base jog with a physical measurement; the thesis CD code may also have it |
| What happens when things go wrong: comms-loss timeout, e-stop packets, whether our stop works | Safety | Lab tests with a hand on the stop (`VENDOR_PROTOCOL_LAB_PLAN.md` F2, F3) |
| Smaller: ID wrap (255 to 0 or 1), unexplained reply bytes 3-4, 15-18, 60-63 and OUT bytes 2-3, the mode SCORBASE passes to `Initialization`, whether the lab's `USBC.dll` build matches ours | Completeness | A capture of more than 256 messages; analysing `SCORBASE.exe` itself (we do not have it in this repo); a hash of the lab DLL |

## Before step 5 or 6 on the real arm

- LEDs read as expected after connect and after enable; `home_switch_bits` below 32 (`docs/lab/AGENT_RUNBOOK.md`).
- An idle recording first: counts stable, error words small, impact byte 0.
- A no-motion switch check: press each microswitch by hand and see the right bit change (requirement T7).
- First jogs at speed 2 or 3, not the default 10, and a slow shoulder jog as the lag test (the SDK's speed is the largest step in counts per message; the failed home search stepped 10).
- A hand on the physical stop, the whole arm path clear, and `python scripts/home_trace_report.py` run on every legacy home.

## Keeping this honest

When a real log shows a step working or failing, change its last column and name the log. Do not move a step from "DLL only" to "Capture" unless a capture of ours (or the thesis's) shows the same bytes.
