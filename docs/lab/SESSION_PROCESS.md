# Session process: the vendor's routine, and where our tools stand

Written 2026-10-07 from the vendor manuals. Quotes and page numbers are from the ER-4u arm manual (100343-b, "Arm"), the Controller-USB manual (100341-g, "Controller") and the SCORBASE manual (Scorbase_USB_I). Page numbers are printed pages. Nothing here has been run on our arm.

## Why this exists

The arm is used to a routine: **every working session starts with the robot near home, and homing is run from there.** On 2026-10-06 the arm was left folded at its lowest point, the legacy home was started from there, and it failed (error code 1 after a few seconds, the shoulder rose a little, stopped, noise). We skipped the step that the vendor's routine has before homing.

## The vendor's routine

**Start of every working session** (Arm p.15, Daily Operation; Controller pp.27-28, Inspection; both say the same):

1. Before powering on: the installation meets safety standards; the robot is bolted to the work surface; all cables secure; no output wired straight to a power supply; **no people within the robot's working area.**
2. After switching on the PC and the controller: the POWER LED is orange when on and green when the software is online; the MOTORS LED is green after SCORBASE starts and Control On (CON) is selected; **no unusual noises; no unusual vibration in any axis; no obstacles in the working area.**
3. **"Bring the robot to a position near home, and activate the homing procedure."** Then check: robot movement is normal; no unusual noise when the arm moves; the robot reaches home position in every axis (all five axes and the gripper) and a "Homing Complete" message appears.

**How homing works** (Arm p.8; SCORBASE pp.23-24): each axis is homed on its own. The controller drives the axis until its micro-switch is pressed, then moves it slightly until the switch turns off; that point is home, the encoder counter is zeroed, and the next axis follows. "Whenever the system is turned on, the robot should be sent to this position, by means of a software homing routine." Homing is needed once per session.

**Getting near home first** (SCORBASE p.31): "The robot can be manipulated from the Manual Movement dialog box before it has been homed in **Joint mode only**. In fact, it is often necessary to bring the robot into a more suitable position before initiating the homing routine. However, an axis limit error message may be displayed during manipulation of a robot that has not been homed."

**Go Home** (SCORBASE p.25): after homing, "Go Home" sends the axes to the position where every encoder reads zero, at any time, without re-homing. The USNA toolbox's `ScorSafeShutdown` does the same and then disables control. Ending a session at home is what makes the next one start near home.

**What the controller does on a fault** (SCORBASE p.26): it disables control by itself on an impact condition, a trajectory error or a thermic overload during a movement. Our "error code 1" is the trajectory-error family. Noisy mains can also shift the Home position (Controller p.29): run the Home routine again.

## What this means for our tools

| Step of the routine | Our tools | Status |
|---|---|---|
| Pre-power and post-power checks | `scripts/windows_usb_check.ps1`, the LED prompts in the lab session and bench scripts | The checks exist as prompts; the "no people, no obstacles" and noise/vibration items are on the operator |
| Bring the arm near home before homing | None. `Scorbot.jog_joint` requires a home first. `home_inch` jogs before a home, but only inside its own search and only 30, 30 and 100 degrees | **Gap.** The vendor has a joint-mode manual move before homing; we do not. Today the arm has to be put near home by hand with the motors off |
| Home | Legacy `home()` (needs a start pose near home); `home_inch` (any pose within a few tens of degrees; the elbow and wrist are coupled to the shoulder and are not moved with it) | Both unproven on the arm since 2026-09-29 |
| Check movement, noise, "reaches home in every axis" | The operator's `HOME_OK` prompt and the home log | Exists |
| End of session: Go Home, then control off | The lab session has `b` (back to start), which returns the joints to where they were right after homing, one 1 degree step at a time while armed; finishing the session just switches the motors off (`_finish`), and nothing suggests going back first | **Gap.** Without it the arm is left wherever the last move ended |

## The process to follow until the gaps are closed

1. Never leave the arm folded or at an extreme. End each session with the arm back at its home pose (use `b` until the arm is where homing left it, then `x`), then motors off.
2. At the start of a session, do the pre-power and post-power checks above, aloud, with the stop operator.
3. Look at the arm. If it is not near home (upper arm up and almost vertical, forearm forward and a little up, gripper down and forward, tool tip about 169 mm out and 504 mm up), put it there by hand with the motors off and the arm supported, before enabling anything. Once SCORBASE-style joint jogging before homing exists in our tools, use that instead.
4. Home. Watch every axis; stop at the first unusual noise or motion. Record what moved, what you heard, and the final pose.
5. Do not retry a faulted home in the same session.

## Open

- Whether the pre-home joint move and the Go-Home-at-exit should be built into the lab session (they are what the vendor's routine assumes).
- Whether homing from a folded pose can ever work without moving the coupled elbow and wrist motors with the shoulder (`docs/protocol/VENDOR_COUPLING_TRACE.md`). The vendor's own routine does not try: it starts near home.
