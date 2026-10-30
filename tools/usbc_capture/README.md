# Software capture of the vendor DLL's USB calls

`hook_io.js` is a [Frida](https://frida.re) script. Attached to a process that has loaded Intelitek's `USBC.dll`, it logs each 64-byte `WriteFile` and `ReadFile` the DLL makes on its driver link, one JSON line per call. It is an alternative to a USBPcap capture ([USB_CAPTURE.md](../../docs/lab/USB_CAPTURE.md)) and sees the same packets from above the driver. It needs no driver change.

It observes only. It never opens the device itself and sends nothing. The arm still moves if the vendor program is told to move it, so a real-arm run follows the usual supervised-bench rules.

## Desk test (no arm)

Done once on 2026-10-30, with `tools/usbc_fake` standing in for the controller: 5,580 writes in the log matched the fake controller's own OUT log byte for byte and in order, and every read was 64 bytes. IN bytes were not compared. This proves the hook, not the real device name.

## What is tested and what is not

- Tested: hooking `kernel32` `CreateFileA`, `WriteFile` and `ReadFile` in a 32-bit process, against the named pipe link.
- Not tested: the real device. The repo's disassembly notes say the DLL opens `\\.\ERUSBDevice0` with `CreateFileA` ([VENDOR_DLL_PROTOCOL.md](../../docs/protocol/VENDOR_DLL_PROTOCOL.md)), which the hook's name match covers. If the first real capture has no `open` line, the DLL opened the device another way; stop and look.
- Spawning the target under Frida failed for the 32-bit Python used at the desk (cause unknown). Attach to a running process instead.

## Use

See [SOFTWARE_CAPTURE.md](../../docs/lab/SOFTWARE_CAPTURE.md) for the steps and the order.

Keep captures out of the repository: they are Intelitek-protocol data and the repository is public.
