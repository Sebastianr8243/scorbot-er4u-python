# Software capture of the vendor Home (lab steps)

Goal: get one real recording of what the vendor's `USBC.dll` sends the controller and what the controller answers during Home, then compare it with what Stack 2's port of Home sends. Everything about the arm is unverified until a bench log backs it.

This is the same recording [USB_CAPTURE.md](USB_CAPTURE.md) makes with USBPcap, taken from inside the vendor process instead. The hook needs no extra driver and no Wireshark, but it cannot be used for a real run until the Home tool can pause (see Steps). Until then use USBPcap.

## What runs the arm and what does not

| Step | Moves the arm? | Needs the person running the lab present and saying go |
|---|---|---|
| 1. Start the hook and the vendor Home | **Yes**: the vendor Home energises the motors and homes the arm | **Yes** |
| 2. Convert the log | No, reads a file | No |
| 3. Compare with the port (`replay_vendor_home.py`) | No, offline, opens no USB | No |

The hook only watches. The arm moves because the vendor tool is told to home.

## Before the arm is touched

- The red stop is in reach, the base is secured, the path is clear and the jaws are empty ([LAB_CHECKLIST.md](LAB_CHECKLIST.md) section 3, first line).
- Close SCORBASE and every vendor tool. Only one program may own the controller.
- Do not swap the driver. The vendor Home needs Intelitek's own driver, and the hook works on it.
- Install `frida-tools` outside this repository (for example `pip install frida-tools` in a separate environment). Match the bitness of the vendor process: the DLL is 32-bit, so use the 32-bit Python the vendor home tool needs.
- Make the output folder `C:\usbc_capture`. The hook writes `C:\usbc_capture\frida_io.jsonl` and fails to start if the folder is missing. Change `OUT_PATH` at the top of `tools/usbc_capture/hook_io.js` for another place.
- Keep the DLL, the INI files and every capture outside the repository.

## Steps

**Blocked until the Home tool can pause.** `tools/usbc_home/usbc_home.py --online` has no pause: it loads the DLL, initialises the controller, turns control on and starts Home in one go. If it is started first, the motors are energised and Home begins before the hook can be attached, and the hook then misses the device open. Do not run it for this capture as it is. The capture needs a tool that loads the DLL, prints its process id and waits for a keypress before Initialization. That is a change to a hardware tool and needs its own tests and review; it is not part of this page. Until then, USBPcap ([USB_CAPTURE.md](USB_CAPTURE.md)) is the way to record the vendor Home.

Once such a pause exists, the order is:

1. Start the Home tool so it stops at the pause. Note its process id (`Get-Process python`).
2. **Attach the hook while it waits**: `frida -p <pid> -l tools\usbc_capture\hook_io.js`. Only handles opened after the attach are logged.
3. Check the hook's first message says `loaded: true` and `arch: ia32`.
4. With the person running the lab present and saying go, press the key to continue. Watch the arm and keep a hand by the red stop.
5. When Home finishes or fails, wait 5 seconds, then stop the hook (Ctrl+C in the frida window).
6. Look at the log: the first line must be an `open` with a path containing `ERUSBDevice0`. If there is no `open` line, the DLL opened the device another way or the hook attached too late. Do not use the capture; run USBPcap instead ([USB_CAPTURE.md](USB_CAPTURE.md) recipe A).
7. Copy `frida_io.jsonl` to a folder outside the repository and record the DLL's size and SHA-256 next to it.

## Compare (desk, any time later)

```powershell
python scripts\hook_log_to_rows.py frida_io.jsonl rows.jsonl
python scripts\replay_vendor_home.py rows.jsonl
```

The replay hands each real reply to the port and reports the first message where the port's command differs from the vendor's. Read the neighbours before deciding which side is wrong; the replay's own output says why.

A capture of only Home is not enough to prove the port right. It shows where the port first disagrees with one real run.

## What this does not tell us

- Whether Stack 2 can send the same bytes over USB. That is the separate transport question ([LAB_CHECKLIST.md](LAB_CHECKLIST.md) section 1, `--transport auto`, which opens the vendor driver the way the DLL does and needs no swap).
- Timing between packets beyond the hook's millisecond stamps.
- Why the 2026-10-06 Stack 2 home failed. The capture narrows where to look.
