# S1 lab card: USB captures

Keep this open on your phone at the robot PC. Background and analysis commands:
[USB_CAPTURE.md](USB_CAPTURE.md). The safety rules from the
[G1 card](G1_LAB_CHECKLIST.md) apply to every step that moves the arm.

**Goal:** record the controller's USB traffic while Intelitek's software moves
the arm, then while our code does the same moves. The captures answer one
question: does the controller accept target positions? That decides whether
smooth control and LeRobot policies are possible.

**Before every motion:** someone stands at the physical stop, the path is
clear, and only one program is connected to the controller. If anything moves
unexpectedly, **press the physical stop and end the session.**

## 0. Save what is already there

- [ ] Copy the whole `logs\` folder (including `sessions\` and every `.controller.jsonl`) to a USB stick or the cloud.

## 1. Set up capture (once per PC)

- [ ] Device Manager → the controller → Properties → Driver. Photograph the **Driver Provider** (probably WinUSB from Zadig).
- [ ] Install Wireshark (needs admin). On the **Packet Capture** page, tick **Install USBPcap**.
- [ ] **Reboot.**
- [ ] Open Wireshark (as Administrator if needed). Check that `USBPcap1`, `USBPcap2`, ... are listed.

## 2. Intelitek captures

- [ ] Put back the Intelitek driver: reinstall SCORBASE or roll back the driver in Device Manager. Photograph the Driver Provider again.
- [ ] Plug in the controller **before** starting Wireshark, and keep it on the same USB port for all captures.
- [ ] SCORBASE connects to the controller.

For each capture: pick the `USBPcapN` interface for the controller's hub (if unsure, try each), **start**, do the steps, **stop**, then **File → Save As** with the exact name.

| Save as | Steps in SCORBASE | Write down |
|---|---|---|
| `intelitek_A_basic.pcapng` | Connect, idle 10 s, home, jog base about 1 degree, close | Jog direction (e.g. "toward the door") |
| `intelitek_B_goto.pcapng` | Home, teach position 1, jog two or three joints a few degrees, teach position 2, then **go to** 1, **go to** 2, close | Which joints moved, did they move together |
| `intelitek_C_speeds.pcapng` | Same go-to between 1 and 2, once at a **slow** speed and once at a **fast** speed | The two speed values used |

| `intelitek_D_control.pcapng` | Arm at rest after homing: **Control Off**, wait 5 s, **Control On**, wait 5 s, Control Off, close | MOTORS LED state after each click, and how long it took to change |
| `intelitek_E_estop.pcapng` | Arm at rest: start capture, wait 5 s, **press the physical e-stop**, wait 5 s, release it per the lab procedure, note what SCORBASE shows, close | LEDs and SCORBASE messages after press and release; whether homing is needed again |

Keep go-to moves small (a few degrees per joint) and away from the table and cables.
Home runs inside capture A, so it also shows how SCORBASE homes (BACKLOG 1, 2, 38).
D looks for the real motors-off message, which a software stop needs (BACKLOG 6).
E shows what the controller sends and expects around an e-stop.
Do E only with the arm at rest, and only if the lab procedure allows pressing the stop.

- [ ] Close SCORBASE completely.

## 3. Our code capture

- [ ] Switch the driver back to WinUSB with Zadig ([START_HERE_WINDOWS.md](../START_HERE_WINDOWS.md)). Photograph the Driver Provider.
- [ ] Start a capture, then run the idle and bench commands from [USB_CAPTURE.md section 3B](USB_CAPTURE.md#3-capture-recipes) with **new** output names. Follow the script prompts as in G1.
- [ ] Stop the capture after the script disconnects. Save as `python_A_basic.pcapng`.

## 4. Take home

- [ ] Every `.pcapng` file (up to six).
- [ ] The new `logs\` files from step 3.
- [ ] Driver photos, with a note of which driver was active for each capture.
- [ ] Your notes: directions, joints that moved, speeds, anything odd (sounds, LEDs, errors).

Stop early if needed. Even capture A alone is useful.

Back at a desk (no arm needed): `export` each capture, then run `setpoints` on
ours first and the Intelitek go-to captures second
([USB_CAPTURE.md section 4](USB_CAPTURE.md#4-find-the-device-export-compare)).
