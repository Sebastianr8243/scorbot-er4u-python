# USB capture: Intelitek software vs. this Python code

Goal: record the USB packets the **original Intelitek software** exchanges with the ER-4U controller (USB ID `09F1:0007`) and the packets this Python code sends for the same actions, then compare them offline. This is the "golden trace" from [ARCHITECTURE.md §25](ARCHITECTURE.md#25-recommended-first-implementation-task).

**Analysis is offline.** `scripts/usb_trace.py` only reads capture files that were saved earlier. It never opens USB, never talks to the controller, and never commands the arm. Only the capture sessions themselves involve the robot, and those follow the normal supervised-bench rules (emergency stop in reach, operator present).

## 1. Install Wireshark with USBPcap

1. Download Wireshark for Windows from [wireshark.org](https://www.wireshark.org/download.html).
2. In the installer, tick **Install USBPcap** (on the "Packet Capture" page). Accept its driver install. Reboot when asked; USBPcap does not work until after the reboot.
3. After rebooting, Wireshark lists interfaces named `USBPcap1`, `USBPcap2`, ... (one per root hub). Run Wireshark as Administrator if they do not appear.

USBPcap is a capture filter driver; it sits above the device driver and does not replace it. It works whichever driver the controller currently uses.

## 2. Driver caveat: capture the Intelitek session first

The Intelitek software needs its **own** USB driver for the controller. PyUSB needs **WinUSB/libusb**, which is installed with Zadig (see [START_HERE_WINDOWS.md](../START_HERE_WINDOWS.md) and [WINDOWS_BENCH_RUN.md §2](WINDOWS_BENCH_RUN.md#2-usb-driver-and-read-only-preflight)). After a Zadig swap the Intelitek software may no longer see the controller.

- **Capture the Intelitek session FIRST**, before any driver swap on that PC.
- If the driver has already been swapped, write down which driver is bound (Device Manager → controller → Properties → Driver → Driver Provider / Driver Details) in the capture notes, and restore the original driver before the Intelitek capture.
- Record the driver for every capture file (e.g. `intelitek_driver-<name>.pcapng`, `python_driver-winusb.pcapng`).
- **Only one program talks to the controller at a time.** Close the Intelitek software completely before running Python, and vice versa.

## 3. Capture recipes

For each capture: in Wireshark pick the `USBPcapN` interface for the hub the controller is on (if unsure, capture on each until `summary` shows the device), start the capture, perform the actions, stop, and **File → Save As** `.pcapng`. Plug the controller in before starting so its device number is stable. Note the start pose and anything unusual.

**A. Intelitek software** (`intelitek.pcapng`):
1. Start the capture. Launch the Intelitek software and connect to the controller.
2. Leave it idle for 10 s.
3. Home the arm.
4. One small base jog (about 1 degree, to match the Python run), noting the direction.
5. Disconnect / close the software. Stop the capture.

**B. Python** (`python.pcapng`), same steps with the repository tools:
1. Start the capture.
2. Idle: `python examples/record_raw_state.py --output idle.jsonl --robot-id ... --arm-label ... --controller-label ... --driver ... --operator ... --pose-note "..." --seconds 10 --acknowledge-connect-handshake`
3. Home and jog: `python examples/bench_joint.py --output bench.jsonl --robot-id ... --arm-label ... --controller-label ... --driver ... --operator ... --start-pose-note "..." --joint base --delta 1 --acknowledge-supervised-motion` (the script prompts before homing; `--delta` is at most 1 degree)
4. Stop the capture after the script disconnects.

Keep the JSONL logs from the Python scripts next to the capture; they give the host-side view of the same session.

## 4. Find the device, export, compare

Copy the `.pcapng` files to any machine with this repository (the analysis does not need the robot).

```
python scripts/usb_trace.py summary intelitek.pcapng
```

Lists every bus/device/endpoint with packet counts and payload sizes. The ER-4U is the device with a bulk OUT endpoint carrying 64-byte payloads (`openScorbot/libdef.py:set_msg` pads each message to 128 hex characters, which `bytes.fromhex` turns into 64 bytes; see [PROTOCOL.md](PROTOCOL.md)) and an IN endpoint returning state packets of at least 49 bytes. `--device N` limits the listing to one device. Device numbers can change after re-plugging, so run `summary` on each file.

```
python scripts/usb_trace.py export intelitek.pcapng --device 5 --out intelitek.jsonl
python scripts/usb_trace.py export python.pcapng    --device 7 --out python.jsonl
```

One JSONL row per bulk/interrupt transfer with payload: `t_s` (relative seconds), `direction`, `endpoint`, `length`, `hex`. OUT rows add `byte0`..`byte7` (byte0 is the sequence byte, bytes 1-3 are normally zero, byte4 is the command, per `openScorbot/libhex.py`). IN rows of 49+ bytes add the fields from `scorbot.state.decode_state` (encoder counts, sign bytes, signed counts, error counts, `home_switch_bits`) or `decode_error` if decoding refuses the packet. Use `--bus` only if two buses have the same device number.

```
python scripts/usb_trace.py compare intelitek.jsonl python.jsonl [--header-bytes 4] [--header-skip 1]
```

Plain-text side-by-side table. OUT "headers" are bytes `header-skip .. header-skip+header-bytes-1`; byte0 is skipped by default because the sequence byte changes on every packet.

## 5. What each comparison answers

| Question | Where to look |
| --- | --- |
| Packet semantics: does Python send the same message types? | `OUT headers` table; headers marked "only in" one file are messages the other program never sends. Inspect those rows' `hex` in the JSONL. |
| Sequence byte: does it ever send 0, and how does it wrap? | `seq byte0 min / zero_seen / wraps`. Python counts 1..255 then wraps to 1 (`libdef.countByte1`, `MAX_COUNT` 256). A `255->0` wrap or `zero_seen > 0` in the Intelitek trace means the original treats 0 as valid. `other_steps` shows repeats or jumps. |
| Encoder wrap at 0/65535: two's or ones' complement? | Export rows for the jog: watch `encoder_counts` and `encoder_sign_bytes` while the base crosses zero. `scorbot.state` assumes sign byte 128/127 and `count - 65535` (ones' complement style); if the Intelitek trace shows e.g. 65535 followed by 0 with a sign-byte change, compare against both conventions. `decode_errors` and `encoder sign bytes` flag unexpected sign values. |
| Braking after the homing switch | `home_switch_bits` values, then the OUT rows just after the bit changes in the Intelitek trace: which command bytes it sends, and how fast encoder counts settle. |
| Stop / disable packets | The last OUT rows of each file (disconnect) and any header only in one file; compare with `libhex.motorsoff` and `get_scorbotoff`. |
| Timing | `OUT->OUT interval` median/p95 (polling period) and `OUT->IN latency`. Python sleeps `WRITE`/`READ` between write and read in `libdef.check`. |

Findings belong in the lab notes with the capture file names, driver names, and start poses. Do not change the protocol code from a single capture; confirm on a second session first.
