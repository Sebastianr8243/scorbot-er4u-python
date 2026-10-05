# The ER-4U model: every number, and where it comes from

Written 2026-10-05. This is the one place that says what this project believes about the arm and why. **Nothing here is measured on our arm.** It is assembled from five sources that describe the same robot, so that a lab visit only has to confirm it. The code form is `scorbot/source_model.py`; the last column of each table says what the lab acceptance run checks.

## The sources

| Short name | What it is | How far to trust it |
|---|---|---|
| Vendor DLL | Intelitek's `USBC.dll`, read by disassembly (`docs/protocol/VENDOR_DLL_PROTOCOL.md`) | What the vendor's software computes. Two builds (2008, 2018) agree |
| Vendor INI | Intelitek's parameter files for the ER-4u (`ROB_4u.INI`, `ER4Ax1-6.ini`), shipped with the USNA toolbox | The vendor's own numbers for this arm model |
| USNA toolbox | The ScorBot Toolbox for MATLAB, US Naval Academy, public domain. Used for years of teaching on real ER-4U arms | Its limits were found by experiment on real arms; its geometry is the vendor's |
| ROS 2 project | talos-rit/scorbot_ros2: a description of the ER-4pc and ER-V for a custom controller | Gear ratios from counting teeth; geometry from a CAD model. Its own files mark most values "VERIFY". Nothing measured in motion |
| Manual | Intelitek's ER-4u user manual | Printed specifications |

## Geometry

| Quantity | Value used | Vendor INI | USNA toolbox | ROS 2 / CAD | Manual | Lab check |
|---|---|---|---|---|---|---|
| Shoulder axis above the base | 349 mm | 349 | 349 | 346 | 364 (from the base bottom) | one tape measurement |
| Shoulder axis ahead of the base axis | 16 mm | 16 | 16 | 29 | not given | same |
| Upper arm | 221 mm | 221 | 221 | 220 | 220 | none needed |
| Forearm | 221 mm | 221 | 221 | 220 | 220 | none needed |
| Wrist to tool point | 145.125 mm | 145 | 145.125 | not modelled | not given | none needed |

The vendor and the toolbox agree on all five. The CAD model's 29 mm offset is the odd one out; it is used only to place that model's meshes in the 3D view.

## Counts to angles

The vendor's formula (`scorbot/vendor_model.py`, from the DLL) with the vendor's parameters.

| Joint | Counts per degree | Vendor INI | ROS 2 (teeth counted) | Legacy code | Lab check |
|---|---|---|---|---|---|
| Base | 141.89 | 141.89 | 141.22 | 141.85 | encoder counts on a jog |
| Shoulder | 113.51 | 113.51 | 101.68 | about 115 | **phone level on a jog: the two candidates differ by 11.6%** |
| Elbow | 113.51 | 113.51 | 101.68 | about 113 | same |
| Wrist motors | 27.90 each | 27.90 | 27.90 | 33.8 (pitch) | wrist test moves |

- The shoulder and elbow scale is the one real disagreement. The vendor's file and the legacy code say about 113.5; the ROS 2 project's tooth count for an ER-4pc says 101.7. A 3 degree shoulder jog read with a phone level tells them apart (3.0 against 3.4 degrees).
- **The joints are coupled.** The elbow motor holds the forearm's angle to the floor, and the two wrist motors hold the gripper's angle to the floor. Moving the shoulder motor alone changes the elbow and pitch angles measured relative to the arm. Pitch comes from half the difference of the wrist motors and roll from half their sum. The ROS 2 project treats the wrist motors as independent joints, which this contradicts; the lab check is whether the forearm keeps its angle when only the shoulder moves.

## Home

| Fact | Value | Source | Lab check |
|---|---|---|---|
| Home pose in joint angles (base, shoulder, elbow, pitch, roll) | 0, 120.28, -95.02, -88.81, 0 degrees | Vendor formula at zero counts; the toolbox's simulator uses the same vector | photo against the 3D view |
| Tool point at home | x 169.3, y 0, z 504.3 mm; tool pitch -63.5 degrees | USNA toolbox `ScorGoHome`; this model gives the same to 0.3 mm | none needed |
| Counts at home | The vendor sets each counter to 0 at the end of homing | Vendor DLL | none (we work from the session home) |
| Our home against the vendor's | A small fixed offset per joint: the vendor backs off each switch by a set amount (shoulder -190, elbow +45, pitch +850, roll -690 counts); the legacy code runs on for twelve messages | Vendor INI, legacy code | read from the encoders: counts between the switch edge and where homing stops |

## Joint limits

In the toolbox's sign convention (positive shoulder, elbow and pitch lift).

| Joint | Limit used | USNA toolbox (controller accepted) | Vendor INI | Manual travel |
|---|---|---|---|---|
| Base | -132 to 174 | -133.8 to 175.8 | -132 to 174 | 310 total |
| Shoulder | -28.3 to 124 | -28.3 to 126.3 | -31 to 124 | -35 to 130 |
| Elbow | -140.8 to -5.2 | -140.8 to -5.2 (elbow-up only) | -160 to 115 | -130 to 130 |
| Pitch | -109.7 to 113 | -109.7 to 134.1 | -115 to 113 | -130 to 130 |
| Roll | -360 to 360 | -360 to 360 (a guess) | -570 to 570 | -570 to 570 |

- **Home is close to the shoulder's upper limit.** The upper arm is 120 degrees up at home and the limit is 124, so there are under four degrees of travel upward, against ten the other way. The SDK refuses a jog or a stream target past it (`Scorbot._refuse_past_joint_limit`, `StreamCore` window).
- The limits are coupled in reality (shoulder, elbow and pitch near their upper ends together can fail, the toolbox notes). No source gives a formula.
- The elbow's upper limit is the toolbox refusing elbow-down, not a mechanical stop.

## Gripper

| Fact | Value | Source | Lab check |
|---|---|---|---|
| Opening | 0 (closed) to 70 mm | USNA toolbox, vendor INI | ruler |
| Scale | 5000 counts for 70 mm, about 71 counts per mm | Vendor INI | counts on an open and a close |
| Count range | -200 to 6000 | Vendor INI | same |
| Our fixed move | 2700 counts, about 38 mm | Legacy code | same |
| How the vendor moves it | A set drive in a torque mode for a time, not a position ramp | Vendor DLL (protocol doc section 12) | needs a capture before we use it |
| Detecting a grasp | Not in the toolbox. The vendor's controller has an impact threshold of 300 for this axis | Vendor INI | what the count does on a soft object |

## Motion

| Fact | Value | Source |
|---|---|---|
| Planner period | 24 ms | Vendor DLL |
| Per-motor speed limit | 6500 counts per second (our streams start at a quarter of it) | Vendor INI |
| Default move | 3 s, 30% speeding up, 40% cruising, 30% slowing | Vendor DLL and INI |
| A new target while moving | Refused by the vendor ("motion in progress") | Vendor DLL, USNA toolbox |
| Smallest move the toolbox sends | 0.25 degrees; smaller ones are skipped | USNA toolbox |
| "Has it stopped" | Readings 0.05 s apart that no longer change; the toolbox advises a further 2 s before trusting a position | USNA toolbox |

## Things other people learned the hard way

- A base angle of 180 degrees or more reports success and does not move (toolbox).
- Position readings lag the "done" flag (toolbox).
- A gripper command disturbs the speed setting in the vendor's software, so the toolbox sends the speed again afterwards.
- On the ROS 2 team's arm the original encoder wheels on axes 1 to 3 had cracked at the grub screw and slipped on the shaft: the motor turned and the count did not. A joint whose count does not follow a jog may be that.
- Limit switches sit mid-range and the arm rests on the switch after homing, so a pressed switch during motion is normal.

## What the acceptance run has to settle

In order of how much depends on each:

1. The shoulder and elbow scale: 113.5 or 101.7 counts per degree.
2. Each joint's direction for a positive count.
3. Whether the forearm keeps its angle when the shoulder moves (the coupling).
4. How far our home is from the vendor's.
5. The shoulder height and offset.
6. Whether a stream is followed, the stop works, and the gripper opens and closes the right way.
