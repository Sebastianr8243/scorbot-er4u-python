# S1 lab card: USB captures

Keep this open on your phone at the robot PC. Background and analysis commands:
[USB_CAPTURE.md](USB_CAPTURE.md). The safety rules from the
[G1 card](G1_LAB_CHECKLIST.md) apply to every step that moves the arm.

**Goal:** record the controller's USB traffic while Intelitek's software moves
the arm, then while our code does the same moves. The captures answer one
question: does the controller accept target positions? That decides whether
smooth control and LeRobot policies are possible. They also answer open
questions the manuals leave blank (see
[MANUAL_VERIFICATION_IMPACT.md](../manual/MANUAL_VERIFICATION_IMPACT.md)): how homing
works, whether home survives Control Off and e-stop, the communication
time-out, and how speed levels are sent.

The same captures confirm or contradict what was read from the vendor DLL.
Which capture settles which claim, and the order to do them in if time is
short, is in [VENDOR_PROTOCOL_LAB_PLAN.md](VENDOR_PROTOCOL_LAB_PLAN.md). Two
extra items for step 0 from that plan: copy `USBC.dll` itself (never commit
it) and write down the SCORBASE version. Start capture A **before** SCORBASE
connects.

**Before SCORBASE starts:** with the controller just powered on, photograph the
POWER and MOTORS LEDs (the manuals disagree on the start-up state).

**Before every motion:** someone stands at the physical stop, the path is
clear, and only one program is connected to the controller. If anything moves
unexpectedly, **press the physical stop and end the session.**

## 0. Save what is already there

- [ ] Copy the whole `logs\` folder (including `sessions\` and every `.controller.jsonl`) to a USB stick or the cloud.
- [ ] In the SCORBASE install folder, copy `USBC.INI`, `ER4CONF.INI`, any `.h` or `.txt` files next to `USBC.dll`, and the whole `PAR\` folder. Search the whole SCORBASE folder for `usbc.h`, `usbcdef.h`, `error.h` and `usbc.lib` (the vendor SDK files; finding them is a big win). Write down the size and date of `USBC.dll`. Take them home; do not commit them (vendor files). Compare with the public copies in [PROTOCOL.md section 12](../protocol/PROTOCOL.md#12-evidence-from-inteliteks-own-software-stack): a different `PCPeriod` or `Buffers` changes what to look for.

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
| `intelitek_A_basic.pcapng` | Connect, **idle 60 s with motors on**, home, write down the position SCORBASE shows, **home a second time**, jog base about 1 degree, close | Jog direction (e.g. "toward the door"); the shown position after each home (same both times?); whether the gripper moved during homing; the order the axes homed in |
| `intelitek_B_goto.pcapng` | Home, teach position 1, jog two or three joints a few degrees, teach position 2, then **go to** 1, **go to** 2. Then one more **go to** 1 at the **slowest speed or a long travel time (3-5 s)**, close | Which joints moved, did they move together; the slow move's duration by stopwatch. The slow move shows whether SCORBASE streams a new setpoint about every 16 ms or sends one target (PROTOCOL.md unknown 15) |
| `intelitek_C_speeds.pcapng` | Same go-to between 1 and 2 at **three or more speeds**: the lowest, a middle and the highest. If SCORBASE offers a travel-time setting instead of speed, do one go-to with it | Every speed value exactly as set, and which scale (1-10 or 1-99 %) |
| `intelitek_D_control.pcapng` | Arm at rest after homing: write down the shown position, **Control Off**, wait 5 s, **Control On**, wait 5 s, write down the shown position, try a small **go to** 1, Control Off, close | MOTORS LED after each click and how long it took; the shown position before and after; whether SCORBASE asked for homing before the go-to; with motors off, did any joint move or sag (hands clear below the arm) |
| `intelitek_E_estop.pcapng` | Arm at rest: write down the shown position, start capture, wait 5 s, **press the physical e-stop**, wait 5 s, release it per the lab procedure, note what SCORBASE shows, Control On, write down the shown position, close | LEDs (photograph the POWER LED: colour and whether it blinks) and SCORBASE messages after press and release; the shown position before and after; whether homing is needed again; did any joint sag with motors off |
| `intelitek_F_stop.pcapng` | Home, then start a small go-to move and press **F9 Stop** while it moves; close | Did the arm stop at once? Did control stay on? (SCORBASE says F9 is sent to the controller, so this may reveal a real stop command) |
| `intelitek_H_manual.pcapng` | Home, then with SCORBASE's manual jog (keyboard axis keys, or the pendant if the lab procedure allows) **hold one base key about 2 s**, release, wait 3 s, close | Key used, how far the base moved, did it keep moving after release. Shows whether manual jog is a velocity message different from go-to (PROTOCOL.md unknown 16) |
| `intelitek_G_close.pcapng` | Arm at rest after homing, **Control On**: start a stopwatch and close SCORBASE completely; keep capturing 30 s | When the MOTORS LED went off and what the POWER LED did, with times from closing. Shows whether SCORBASE sends a motors-off message or the controller times out (and roughly after how long) |

Keep go-to moves small (a few degrees per joint) and away from the table and cables.
Home runs inside capture A, so it also shows how SCORBASE homes (BACKLOG 1, 2, 38).
D looks for the real motors-off message and F for the stop command, which a software stop needs (BACKLOG 6).
For every capture, note the teach-pendant switch position (Auto, Teach or unplugged) and which speed scale you set (1-10 in Teach Positions, 1-99 % in Go to Position).
E shows what the controller sends and expects around an e-stop. D and E also
show whether home survives Control Off and an e-stop; G shows the communication
time-out. A and C give SCORBASE's idle packet rate and how speed levels are sent.
Do E only with the arm at rest, and only if the lab procedure allows pressing the stop.

- [ ] Close SCORBASE completely.

## 3. Our code capture

- [ ] Switch the driver back to WinUSB with Zadig ([START_HERE_WINDOWS.md](../../START_HERE_WINDOWS.md)). Photograph the Driver Provider.
- [ ] Start a capture, then run the idle and bench commands from [USB_CAPTURE.md section 3B](USB_CAPTURE.md#3-capture-recipes) with **new** output names. Follow the script prompts as in G1.
- [ ] Stop the capture after the script disconnects. Save as `python_A_basic.pcapng`.

If Wireshark is not available, still run the jogs: every jog now logs its own packets (`motion_trace` in the `.controller.jsonl`, see [USB_CAPTURE.md](USB_CAPTURE.md)).

## 4. Take home

- [ ] Every `.pcapng` file (up to nine).
- [ ] The new `logs\` files from step 3.
- [ ] Driver photos, with a note of which driver was active for each capture.
- [ ] Your notes: directions, joints that moved, speeds, shown positions, LED photos and times, any sag with motors off, anything odd (sounds, errors).

Stop early if needed. Even capture A alone is useful.

Back at a desk (no arm needed): `export` each capture, then run `setpoints` on
ours first and the Intelitek go-to captures second
([USB_CAPTURE.md section 4](USB_CAPTURE.md#4-find-the-device-export-compare)).
