# S1 addendum: captures for the questions the desk work left open

Written 2026-10-07. Do these in the same session as the [S1 card](S1_CAPTURE_LAB_CARD.md), with the same setup (Intelitek driver back, USBPcap on, one program connected, someone at the physical stop, the arm near home). They are SCORBASE captures only: nothing here runs our code on the arm. Analysis is offline, as in [USB_CAPTURE.md](USB_CAPTURE.md).

Why these four: the desk traces ([VENDOR_MANUAL_MOVE_TRACE.md](../protocol/VENDOR_MANUAL_MOVE_TRACE.md), [VENDOR_HOMING_TRACE.md](../protocol/VENDOR_HOMING_TRACE.md)) say what the DLL would send. They cannot say what SCORBASE's front end asks of the DLL, or what the controller does with it. The first trial's biggest unknown, whether the wrist moves the way our coupled move assumes, is also the thing a capture of SCORBASE's own joint jog can show without moving the arm ourselves.

| Save as | Steps in SCORBASE | Write down | Settles |
|---|---|---|---|
| `intelitek_I_prehome_joint.pcapng` | Fresh start: connect, **Control On**, do **not** home. Open Manual Movement in **Joint mode**. Jog **shoulder** about 1 second (small), release, wait 3 s. **Elbow** about 1 second, release, wait 3 s. **Base** about 1 second, release, wait 3 s. Close | Which direction each key moved the arm; what the forearm and gripper did while only the shoulder key was held (did the gripper keep pointing the same way in space?); any limit message | Whether SCORBASE's joint jog moves **one motor** (the DLL says so) or the coupled motors too; the model's prediction that the forearm and gripper keep their direction in space |
| `intelitek_J_gohome.pcapng` | Home. Jog two joints a few degrees (as in capture B). Then **Go Home**. Close | Whether the arm moved in one smooth move or axis by axis; how long it took; where it ended against the home you recorded | Whether Go Home is one controller command (BACKLOG 38) or a stream of setpoints, and what it does with the coupled motors |
| `intelitek_K_sethome.pcapng` | **Only if the menus have a Set Home or Define Home item** (the DLL exports `SetHome`; the front end may not use it). Home, jog the base a few degrees, run it, then read the position SCORBASE shows. If there is no such item, skip this capture and write that down | The exact menu path and the position shown before and after | Whether `SetHome` zeroes the counters with `48` and marks the arm homed (`PROTOCOL.md` says it does not mark homed; the code read says it does) |
| `intelitek_L_home_from_off.pcapng` | Place the arm **a few degrees away from its home pose by hand** with Control Off, then Control On and **Home**. Close | The pose before; what moved during homing and in what order; any error | Whether the vendor home tolerates an off-home start, and in which order the axes and the coupled motors move (the trace says shoulder, elbow, wrist, base) |

Keep every move small and away from the table and cables. Stop at the first surprise and keep what you have.

## What to look for at the desk

Export and compare as in [USB_CAPTURE.md section 4](USB_CAPTURE.md#4-find-the-device-export-compare). Start with capture I, because it answers the question that decided the pre-home design:

1. Find the packets sent while a key was held. In each, see **which of the five motor setpoint or velocity slots change**. If holding SHOULDER only changes the shoulder slot, the front end drives one motor and the DLL trace is the whole story. If the elbow and wrist slots change too, SCORBASE couples them and our default single-motor move is *less* than the vendor's.
2. Compare the sizes of the changes with the signs in [VENDOR_COUPLING_TRACE.md](../protocol/VENDOR_COUPLING_TRACE.md). A wrist change that is **opposite in sign** or **twice** the predicted size is the wrong-sign result the first trial is meant to catch, found without moving the arm ourselves.
3. In `J`, count the packets between the first and last setpoint change and note the commanded values. One message with a target means a controller-side move; a ramp of setpoints means the PC drives it.
4. In `K` and `L`, look for a `48` message (set position, value 0). Write its payload down; a byte the legacy code never sends is what the project rule says needs a capture before it goes into our code.
5. In the connect or homing packets of any capture, find the reply to `72` (controller version, reply bytes 11-14); it decides the version-8 branches in the homing trace. Record the number.

## After the visit

Record each verdict in `docs/protocol/VENDOR_MANUAL_MOVE_TRACE.md` and `VENDOR_HOMING_TRACE.md` as V (seen on the wire) or contradicted, correct `PROTOCOL.md` line 381 about `SetHome`, and tell the owner before changing any packet code. Nothing in `openScorbot/` changes without these captures.
