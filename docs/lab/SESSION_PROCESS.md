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
| Bring the arm near home before homing | `Scorbot.pre_home_jog` and the keyboard session's pre-home step (`python -m scorbot.lab --inch-home`): typed joint moves of at most 2 degrees, the coupled motors following | Built 2026-10-07, **never run on the arm**. Until it has been, put the arm near home by hand with the motors off |
| Home | Legacy `home()` (needs a start pose near home); `home_inch` (any pose within a few tens of degrees; the elbow and wrist are coupled to the shoulder and are not moved with it) | Both unproven on the arm since 2026-09-29 |
| Check movement, noise, "reaches home in every axis" | The operator's `HOME_OK` prompt and the home log | Exists |
| End of session: Go Home, then control off | `Scorbot.park_at_home` and the exit prompt of `--inch-home` ("return the arm to its home pose before the motors go off?"); `b` still returns to the session's home while armed | Built 2026-10-07, **never run on the arm** |

## The process to follow until the gaps are closed

1. Never leave the arm folded or at an extreme. End each session with the arm back at its home pose (use `b` until the arm is where homing left it, then `x`), then motors off.
2. At the start of a session, do the pre-power and post-power checks above, aloud, with the stop operator.
3. Look at the arm. If it is not near home (upper arm up and almost vertical, forearm forward and a little up, gripper down and forward, tool tip about 169 mm out and 504 mm up), put it there by hand with the motors off and the arm supported, before enabling anything. Once SCORBASE-style joint jogging before homing exists in our tools, use that instead.
4. Home. Watch every axis; stop at the first unusual noise or motion. Record what moved, what you heard, and the final pose.
5. Do not retry a faulted home in the same session.

## Open

- Whether the coupled moves are right on the arm. The vectors and signs come from the vendor's own joint formulas and the simulator and the tests are built on the same formulas, so they cannot show a wrong wrist sign: only the trial below can.
- The wrist roll and the gripper are not homed; nothing jogs the roll.

## First trial of the coupled moves (before any homing)

Supervised, hand at the physical stop, arm supported near its home pose, wrist set by eye. `python -m scorbot.lab --inch-home`, answer `y` to "Bring the arm near home first?", and:

1. **Elbow direction check.** Type `ELBOW +2` five times, watching the gripper against the forearm: with the coupling right, the gripper turns with the forearm and the angle between them does not change (a phone level on each link helps). Then `ELBOW -2` five times. If the gripper tilts relative to the forearm, about twice as far as the elbow moved, the wrist sign is wrong: press the stop, end the session, send the log.
2. **Shoulder check.** `SHOULDER +2` three times: the whole arm should lift with the angle at the elbow unchanged. If the elbow bends, the elbow sign is wrong: stop.
3. **Base check.** `BASE +2` twice and back.
4. Only then `HOME`, then at the end `PARK`.

Any unusual noise, strain or motion: stop, do not retry in the same session, and keep the log (`logs\`).
