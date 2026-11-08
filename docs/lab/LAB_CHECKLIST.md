# Stack 2 lab checklist (one page)

For the first Stack 2 visit. The ladder itself is in [BRING_UP_RUNBOOK.md](BRING_UP_RUNBOOK.md). Everything about the arm here is unverified until a bench log backs it. A person is present, the red stop is in reach, nothing runs without their go.

## 1. Pick the driver path first (no swap is the default)

`--transport auto` tries `device_file` first. That opens Intelitek's own ERUSB driver the way the vendor DLL does, so **no driver change is needed**. Run the probe on the Intelitek driver. Only if it reports the device name missing do you consider WinUSB (`--transport pyusb`), and that is a swap with a cost (section 4).

What the driver package says (`Driver/ERUSB.inf` and `ERUSB.sys` from the private repo, read at the desk, not run):
- Matches `USB\VID_09F1&PID_0007`; service `ERUSB`, demand start, binary `System32\drivers\ERUSB.sys`, class "Intelitek USB robot controllers", 2009 vintage, x64.
- It is a plain WDM driver (no KMDF). Read on 2026-10-31 (`docs/protocol/LEDGER.md` PL30 to PL32): reads and writes are served inside the dispatch routine, which waits up to 5 s for the controller itself and then completes the request with `STATUS_TIMEOUT` and no bytes. Our request is never pending, so neither `CancelSynchronousIo` nor overlapped I/O with `CancelIoEx` can end it sooner. **Expect a silent controller to cost about 5 s per read**, and a stop sent from another thread to wait behind it. `device_file` raises that empty 5 s read as a timeout. Unverified on the arm: step T below measures it.

## 2. Lab-PC prep (before the arm is touched)

- Plug in the controller. Device Manager: note the device's driver, `Hardware Ids` (must be `VID_09F1&PID_0007`) and Details -> `Inf name` (`oemNN.inf`). Write both down.
- Close SCORBASE and every vendor tool. Only one program may own the controller.
- Keep the terminal in front for the whole run; do not minimise it or let the screen lock.
- Use `logs\` for real runs and `rehearsal\` for simulated ones, never mixed. Keep any 2026-10-06 logs.
- Save the output of `pnputil /enum-drivers` to a file before any driver change.

## 3. First contact order

1. Red stop within reach, base secured, arm path clear, jaws empty.
2. `probe --transport auto --online --reads 200 --log logs\probe-reads.jsonl`. Connecting briefly energises then releases the motors, so stay clear. Read the timing line: replies should be ~16 to 25 ms apart; a non-zero `None` count means replies echoed no usable ID.
3. If the probe is sane: `scripts/bring_up.py --transport auto --online --logs logs\bring-up`. Stop at the first failure; do not retry blindly.
4. Stream only after the ladder passes and vendor Home establishes the encoder zero. Travel uses the vendor limit (USBC.dll EncLimit / ROB_4u.INI), not measured on our arm; lab, stream and policy examples default to no extra cap. `--travel-cap-deg` can add a positive bound without an imposed ceiling. The 5 degree jog ceiling, stream lead/step and speed limits still apply.

Any fault latches the session. After a fault, build a new instance; do not force-close.

## 3a. Stack 2 command menu (run any, in any order)

The ladder below is advice, not a gate: every command here runs on its own. From the repo root on the lab PC,
in PowerShell, with `$vpy` the venv's python (`.\.venv\Scripts\python.exe`). Use `--transport pyusb`
instead of `auto` after a Zadig swap. Every command that moves the arm asks for typed words first; the red
physical stop is the only emergency stop (Ctrl+C is not). Add `--simulate` (and drop `--online`) to rehearse any of them.

No motion:

```powershell
& $vpy scripts\read_timeout_check.py --online                                          # T: one read, no write
& $vpy -m scorbot.transport.probe --transport auto --online --reads 200 --log logs\probe.jsonl   # P: 200 reads
& $vpy scripts\follow_report.py logs\<run>.controller.jsonl                            # read a run's controller log
```

The whole ladder, or part of it:

```powershell
& $vpy scripts\lab_day.py --online --transport auto            # T, S, P, then G; stops at the first FAIL
& $vpy scripts\lab_day.py --online --transport auto --from G   # straight to G (skips T, S, P)
& $vpy scripts\lab_day.py --online --transport auto --x        # P also judges X (after Stack 1 left the arm off home)
```

Stack 2 vendor Home and first motion (`bring_up.py`, vendor Home by default):

```powershell
& $vpy scripts\bring_up.py --transport auto --online --logs logs\bring-up                      # vendor Home, 1 degree jog, stop
& $vpy scripts\bring_up.py --transport auto --online --logs logs\bring-up --joint shoulder     # jog another joint
& $vpy scripts\bring_up.py --transport auto --online --logs logs\bring-up --stream --speed-pct 25 --stream-seconds 5
```

Bench trials (each records a session; `bench_joint`/`bench_stream`/`policy_loop` home with the vendor Home;
the same eight labels every time):

```powershell
$L = "--robot-id er4u-lab --arm-label <arm sticker> --controller-label <box sticker> --driver intelitek --operator <initials> --start-pose-note <where it is>".Split(" ")
& $vpy examples\bench_joint.py   --output logs\joint.jsonl   @L --joint base --delta 1 --acknowledge-supervised-motion
& $vpy examples\bench_stream.py  --output logs\stream.jsonl  @L --motor base --delta 1 --hold-s 1 --acknowledge-supervised-motion
& $vpy examples\bench_gripper.py --output logs\gripper.jsonl @L --moves open close --acknowledge-supervised-motion
& $vpy examples\policy_loop.py   --output logs\policy.jsonl  @L --policy reach:base=2 --steps 20 --acknowledge-supervised-motion
& $vpy examples\policy_loop.py   --output logs\grip.jsonl    @L --policy reach:base=2 --steps 20 --grip-at 10 --acknowledge-supervised-motion   # stop, jaw open, resume (#209)
```

Labels with spaces need quotes, so for those type the flags out instead of using `$L`.

## 3b. Pass and fail, written before the visit

`python scripts/lab_day.py --online --transport <auto|pyusb>` (or `scorbot lab day --online`) runs T, S, P (add `--x` for X) and G in this order (T first, as its row says) and checks each against the line below; rehearse it first with `--simulate`. H stays by hand. On the WinUSB plan run T on Intelitek's driver before Zadig (`--transport auto`; decline GO for S, P and G to skip them), because the runner skips T on `pyusb`.

Declining any GO skips that step and continues; `lab-day.jsonl` records it as skipped. A FAIL still stops immediately. All logs go in one dated folder, `logs/lab-<UTC time>/`, with `lab-day.jsonl` indexing the probe and bring-up logs. Inapplicable T/S checks record `n/a` without changing the exit code; exit is 1 on failure, otherwise 3 for a declined GO or a jog skipped inside G, otherwise 0. Ctrl+C or EOF records an interrupted end with exit 130 before propagating the interruption. Afterwards run `python scripts/lab_summary.py <folder>/lab-day.jsonl --sheet` for the record sheet, step results and velocity-jog counts.

Each step has its pass line fixed now, so the result cannot be argued afterwards. Stop at the first fail and write it down. The rows marked "no motion" command nothing; the rest move the arm and need a go each time.

| Step | What | Pass | Fail means |
|---|---|---|---|
| H | Stack 1 health: start the arm server in terminal 1 (`tools/usbc_server`, it homes once), then in terminal 2 run the bridge commands `where`, `speed 20`, `by 2 0 0`, `open`, `close` (moves; see [RUN_THE_ARM.md](RUN_THE_ARM.md#python-api-arm-server) section "Python API (arm server)"). After the Home, put a tape mark across each joint | Home OK; the 2 mm move reads back within 0.2 mm; both gripper moves end | The arm or controller is not healthy today; no Stack 2 at all |
| S | Microswitches: press each with a screwdriver, arm off (no motion) | IN byte 5 shows base 1, shoulder 2, elbow 4, pitch 8, roll 16, one at a time | The bit map in `vendor_home.py` is wrong for this arm; stop |
| T | Timeout, before any Stack 2 connect: `python scripts/read_timeout_check.py --online`, one read with no write (no motion) | `PASS`: a `TimeoutError` that ended by itself in 4 to 6 s (ledger PL31) | Over 10 s or a hang: no Stack 2 motion this visit. "answered": run T once more; a second answer means the controller sends unasked, record it and stop |
| P | Stack 2 probe, 200 reads (step 2 above; connect pulses the motors) | 200 of 200 reads; gap median 14 to 25 ms; no `None` echo; worst echo lag at most 60; `repeats` longest at most 4 (write it down: the old 10-in-a-row gripper stop was withdrawn in #72, ledger PL27, so nothing stops on it now). Expect `repeats` count near a quarter of the replies and the lag to climb to about 46 and drop back: the 2026-10-06 log at this same pace shows both (ledger PL06, PL07), so neither is a fail. Write both numbers down | Pacing or echo is not what the desk assumes; record and stop |
| X | Hand-over: after H, move with Stack 1 to a pose off home (`by 20 0 0`, a go), close Stack 1, run P with `--log` (the probe itself sends no motion) | After Stack 2's handshake some joint still reads hundreds of counts from 0: its reset (`5A`) kept the vendor's zero | Every count near 0 after the handshake: the reset re-zeroes the counts, so every Stack 2 connect loses the vendor's Home. Record the counts |
| G | Only if H, S, T and P pass and the operator says go, jaws empty: `bring_up.py --velocity-jog` (vendor Home, 1 degree jog/back, then one velocity jog each for base, shoulder and elbow at manual speed 5, 0.3 s out and 0.3 s back). Type JOG before each axis; declining skips it. Velocity jogs are unverified on the arm | Home completes; each of the three gripper drives ends on a stall within 120 replies; every joint lines up with H's tape mark within about 2 degrees (eye or phone level). Write down the three gripper travels from the log: they set the per-arm minimum stroke (ledger PL29) | Note the error number. 574 "did not stall within the vendor's gripper budget" means the real gripper is slower than the vendor's 120 replies: stop, do not raise the limit at the bench. A joint off its mark: Stack 2's zero is not the vendor's; no streaming |

## 3c. Numbers to write down for LeRobot

These fill the priors in `docs/LEROBOT_CHAIN.md` section 5. Each comes from a step above or a supervised trial after G; the ones marked (moves) need a go each time.

| Number | How |
|---|---|
| Streaming period (the LeRobot frame budget) | P gives the idle gap; the streaming period comes from the `host_monotonic_ns` of the step records in the `bench_stream.py` log below |
| Counts per degree and direction, per joint | After G: `examples/bench_joint.py`, a 1 degree jog per joint read with a phone level, then `scripts/lab_summary.py --phone base=<deg>` (moves) |
| Stream lag, overshoot, settle | After G: one `bench_stream.py` trial per motor, 1 degree out and back at speed 25, then `scripts/follow_report.py` on its log (moves) |
| Repeatability (proxy) | The same `bench_stream.py` trial three times; compare the settled counts at the end of each leg, and the tape marks from H (moves) |
| Gripper move time and travel | From G's log (`bring_up.py --logs`): time from first setpoint to stall, and counts travelled |
| Stack 1 action rate | After H: `examples/arm_latency.py --speed 20`, then `--speed 50` (moves) |

## 4. If you did swap to WinUSB: get Intelitek's driver back

Do this before leaving the lab PC, or Stack 1 (the vendor tools) cannot find the controller.

1. Device Manager -> the controller -> Properties -> Driver -> **Roll Back Driver**. If it is greyed out, continue.
2. Find the WinUSB package: `pnputil /enum-drivers` and look for the entry added by Zadig (provider "libwdi" or "WinUSB"; compare with the file you saved in section 2).
3. Admin PowerShell: `pnputil /delete-driver oem<N>.inf /uninstall /force`, then unplug and replug the controller.
4. Reinstall Intelitek's: `pnputil /add-driver <folder>\ERUSB.inf /install` where the folder is Intelitek's installed driver files (not in the repo; the lab PC already has them from the vendor install).
5. Device Manager shows "USB robot controller" under "Intelitek USB robot controllers" with no warning icon. Confirm with `tools/usbc_home` that the vendor path sees the controller. Do not run Zadig again.

The same fallback as `RUN_THE_ARM.md` (uninstall the device with "remove the driver", replug, Update driver -> Have Disk -> `ERUSB.inf`) also works. The `pnputil` route is unverified on this PC.

## 5. Afterwards

Copy `logs\` off the lab PC. Fill the [record sheet](LAB_RECORD_SHEET.md) from `python scripts/lab_summary.py logs\*.jsonl --sheet --phone base=<deg>`. Write what actually happened, including the cancel-test result and the first real gap/lag numbers, as dated values with their log file in the record sheet, and update the rows it changes in [`STATUS.md`](../STATUS.md).
