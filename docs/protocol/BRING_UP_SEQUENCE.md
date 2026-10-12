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
| What byte 60 minus byte 0 means (a queue depth would make stops late; the DLL sizes its queue 4 to 45, see above) | Stop latency | The home and jog traces of the first lab run (they hold sent ID, echoed ID and byte 60); a run paced slower |
| Smaller: OUT bytes 2-3, the mode SCORBASE passes to `Initialization`, whether the lab's `USBC.dll` build matches ours | Completeness | Analysing `SCORBASE.exe` itself (we do not have it in this repo); a hash of the lab DLL |

## Settled or narrowed at the desk (2026-10-08, from data we already had)

Sources: the 10-06 idle packets (`raw_hex`, 20 samples), the legacy code, and the thesis figures. V = read straight from the data, I = inference.

| Unknown | Result |
|---|---|
| **ID wrap: 255 to 0 or to 1?** | **Settled in practice.** The legacy counter goes 1 to 255 and back to 1 (`countByte1`, `MAX_COUNT` 256); the thesis's driver went 255 to 0. The controller accepted both: our 10-06 sessions sent more than a thousand messages (several wraps) and never lost control, and the thesis driver ran the same way (p. 27). No need to change the legacy counter. |
| **Emergency bit baseline** | At idle with the e-stop released, IN byte 2 is `0x08` in all 20 samples and in the thesis's packets (V). Bit 0 is clear. A press should make it `0x09` (I, never seen). |
| **Unexplained reply bytes at idle** | Bytes 3-4 and 15-18 are 0, bytes 61-63 are `FF FF FF`, and the only bytes that ever change are 0 and 60 (V). |
| **Reply timing** | In the thesis's SCORBASE capture the reply arrives **15.9 to 17.6 ms after each OUT** (mean 16.9) and the OUT-to-OUT period is about 30 ms (Fig. 18, V). That matches `PCPeriod = 16`. **I: the controller answers on a 16 ms cadence, and our 18.5 ms per message is that cadence plus about 2.5 ms, not Python overhead.** Our 13 ms of write and read sleeps are shorter than 16 ms, so they do not set the pace; shortening them would not speed anything up. |
| **SCORBASE's own speed** | Its slowest waist jog, speed 1, is 750 counts/s (p. 52), about 23 counts per message at its 30 ms cycle (I). Our SDK's whole speed range (1 to 20 counts per message) is at or below that. |
| **Unit of the vendor's `4D` drive value** | The thesis's conveyor takes 0x64 at speed 5 and 0xC8 at speed 10 (p. 62): about 20 per speed step, full scale about 200. The vendor's homing velocities are +90 to +110 (arm) and -165 (gripper), so homing drives at about half of full scale and the gripper at about 80 percent (I). |

**Narrowed later the same day, from the DLL's communication thread (`FUN_1003ce38`, read in Ghidra):**

- **The controller has a message queue, and the vendor sizes it on purpose (V, from disassembly).** After every reply the thread computes `sent ID - echoed ID` (mod 256) and sends the next message after 1 ms while that is below a limit; at the limit it waits `PCPeriod/2` (8 ms). The limit is 45 normally, 20 while homing, 4 for manual jogs, 6 for the teach pendant. A small limit for manual jogs only makes sense if queued messages execute late, so **queue depth is command latency** (I).
- **The DLL never reads reply byte 60 (V: no accessor in the dump).** It is a controller-side field. Reading it as "newest ID the controller has received" is still inference.
- **What that means for us (I).** The legacy code does not pace by the queue; it sends one message per cycle after a handshake that sends many back to back. If byte 60 is the queue tail, our 47 means about 47 messages waiting, about 0.9 s at 18.5 ms each; SCORBASE jogs run at 4, about 0.1 s. The thesis session shows 6.
- **A usable safety number even if the hypothesis is only half right.** After a stop request the arm may still execute every queued step: about `depth x speed` counts (47 x 10 = 470 counts at the default jog speed, 47 x 2 = 94 at speed 2). That is the argument for speed 2 or 3 on the first trials, and why neither `request_stop` nor `disable()` is an emergency stop.
- **Still needs the arm:** whether byte 60 minus byte 0 tracks the sent ID minus the echoed ID (the new home and jog traces hold all three), and how late a stop really lands (the echo offset alone does not give the feedback age, the command-to-motion delay or the stop distance; a timed stop does). The idle logs hold only IN packets, so the desk cannot settle it.

**Decision (owner asked for one, 2026-10-08): plan as if byte 60 is the newest ID the controller has received, so byte 60 minus byte 0 is the number of queued messages.** It is the only reading that fits all the data (rises with the ID, constant above the echo, 47 here against 6 in the thesis, matches the DLL's 4 to 45 limits), and the two ways to be wrong are not equal: assuming it and being wrong costs slow first jogs; ignoring it and being right means a stop lands about a second late. So: stop distance is budgeted as `depth x speed` counts, first jogs run at speed 2 or 3, and a depth above about 10 is noted in the lab log. Still unproven; the first trace settles it, and this paragraph is replaced by the result.

**Since 2026-10-11 the vendor home measures this itself.** `VendorHomeSource` takes the lag from each reply's echoed ID (byte 0) against the ID it just sent, scales its stall grace and setpoint lead limit by it, refuses a controller more than 60 behind (or one whose reply echoes no ID of ours), and writes `lag_first` and `lag_max` into the `home_complete` and `home_failed` rows. The first real home therefore answers the question below without any extra recording. See `docs/specs/2026-10-08-vendor-home-port.md`.

**Puzzle that remains: byte 60 minus byte 0.** In every idle sample byte 60 is byte 0 plus a constant (47 in our run; 6 in the thesis's), and both bytes follow the ID through the wrap (V). A constant offset that differs per session looks like a queue depth: byte 0 would be the ID the controller is answering and byte 60 the newest ID it has received. **I, unproven:** if so, our 47 is close to the controller's `Buffers = 45` queue, which would mean the controller runs about 45 messages behind the host, so a command, and a software stop, could take effect more than a second late. The thesis's offset of 6 comes from a slower host loop. This matters for stopping safely, and it is checkable: the home and jog traces now hold the OUT ID and both IN bytes for every message, so the first lab run shows whether the offset changes when we pace slower or during a move. Related oddity: in the idle samples the ID advances only about 21 per 27 packets we count, and twice in 10 s it jumped by about 48 extra; not explained.

## Before step 5 or 6 on the real arm

- LEDs read as expected after connect and after enable; `home_switch_bits` below 32 (`docs/lab/AGENT_RUNBOOK.md`).
- An idle recording first: counts stable, error words small, impact byte 0.
- A no-motion switch check: press each microswitch by hand and see the right bit change (requirement T7).
- First jogs at speed 2 or 3, not the default 10, and a slow shoulder jog as the lag test (the SDK's speed is the largest step in counts per message; the failed home search stepped 10).
- A hand on the physical stop, the whole arm path clear, and `python scripts/home_trace_report.py` run on every legacy home.

## Keeping this honest

When a real log shows a step working or failing, change its last column and name the log. Do not move a step from "DLL only" to "Capture" unless a capture of ours (or the thesis's) shows the same bytes.
