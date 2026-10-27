# Stack 2 lab checklist (one page)

For the first Stack 2 visit. The ladder itself is in [BRING_UP_RUNBOOK.md](BRING_UP_RUNBOOK.md). Everything about the arm here is unverified until a bench log backs it. Sebastian is present, the red stop is in reach, nothing runs without his go.

## 1. Pick the driver path first (no swap is the default)

`--transport auto` tries `device_file` first. That opens Intelitek's own ERUSB driver the way the vendor DLL does, so **no driver change is needed**. Run the probe on the Intelitek driver. Only if it reports the device name missing do you consider WinUSB (`--transport pyusb`), and that is a swap with a cost (section 4).

What the driver package says (`Driver/ERUSB.inf` and `ERUSB.sys` from the private repo, read at the desk, not run):
- Matches `USB\VID_09F1&PID_0007`; service `ERUSB`, demand start, binary `System32\drivers\ERUSB.sys`, class "Intelitek USB robot controllers", 2009 vintage, x64.
- It is a plain WDM driver (no KMDF). Its imports include `IoAcquireCancelSpinLock`, `IoReleaseCancelSpinLock`, `IoCancelIrp` and `KeRemoveEntryDeviceQueue`, so it queues requests the old way and probably honours cancellation. **That is not proof.** `IoSetCancelRoutine` is an inline function in the Windows headers, so it never appears in an import list, and its absence says nothing. Whether `CancelSynchronousIo` cancels a blocked `ReadFile` is a lab question: the probe's first timeout shows it (a read that never returns means use overlapped I/O with `CancelIoEx` instead).

## 2. Lab-PC prep (before the arm is touched)

- Plug in the controller. Device Manager: note the device's driver, `Hardware Ids` (must be `VID_09F1&PID_0007`) and Details -> `Inf name` (`oemNN.inf`). Write both down.
- Close SCORBASE and every vendor tool. Only one program may own the controller.
- High-performance power plan: `powercfg /setactive SCHEME_MIN`.
- USB selective suspend off (admin): `powercfg /setacvalueindex SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 48e6b7a6-50f5-4782-a5d4-53bb8f07e226 0` then `powercfg /setactive SCHEME_CURRENT`.
- Defender exclusion for the repo and `logs` folder (a scan stall mid-stream looks like a dead arm).
- Keep the terminal in front for the whole run; do not minimise it or let the screen lock.
- Use `logs\` for real runs and `rehearsal\` for simulated ones, never mixed. Keep any 2026-10-06 logs.
- Optional: USBPcap + Wireshark. Skip it if installing is a hassle; a failure is then harder to diagnose.
- Save the output of `pnputil /enum-drivers` to a file before any driver change.

## 3. First contact order

1. Red stop within reach, base secured, arm path clear, jaws empty.
2. `probe --transport auto --online --reads 200 --log logs\probe-reads.jsonl`. Connecting briefly energises then releases the motors, so stay clear. Read the timing line: replies should be ~16 to 25 ms apart; a non-zero `None` count means replies echoed no usable ID.
3. If the probe is sane: `scripts/bring_up.py --transport auto --online --logs logs\bring-up`. Stop at the first failure; do not retry blindly.
4. Stream only after the ladder passes.

Any fault latches the session. After a fault, build a new instance; do not force-close.

## 4. If you did swap to WinUSB: get Intelitek's driver back

Do this before leaving the lab PC, or Stack 1 (the vendor tools) cannot find the controller.

1. Device Manager -> the controller -> Properties -> Driver -> **Roll Back Driver**. If it is greyed out, continue.
2. Find the WinUSB package: `pnputil /enum-drivers` and look for the entry added by Zadig (provider "libwdi" or "WinUSB"; compare with the file you saved in section 2).
3. Admin PowerShell: `pnputil /delete-driver oem<N>.inf /uninstall /force`, then unplug and replug the controller.
4. Reinstall Intelitek's: `pnputil /add-driver <folder>\ERUSB.inf /install` where the folder is Intelitek's installed driver files (not in the repo; the lab PC already has them from the vendor install).
5. Device Manager shows "USB robot controller" under "Intelitek USB robot controllers" with no warning icon. Confirm with `tools/usbc_home` that the vendor path sees the controller. Do not run Zadig again.

The same fallback as `RUN_THE_ARM.md` (uninstall the device with "remove the driver", replug, Update driver -> Have Disk -> `ERUSB.inf`) also works. The `pnputil` route is unverified on this PC.

## 5. Afterwards

Copy `logs\` off the lab PC. One dated line in `LESSONS.md` with what actually happened, including the cancel-test result and the first real gap/lag numbers.
