# SCORBOT ER-4u / Controller-USB manual verification

Source-limited reading report. No robot operating procedure is supplied. Quoted vendor instructions below are documentary evidence, not directions to perform them.

## Executive summary

- **STATED [IMAGE]:** The arm has five revolute joint axes plus a servo gripper. Both wrist joint axes use motors 4 and 5. A six-motor interface is not evidence of six revolute arm joints. Arm, printed pp.4–5, PDF pp.10–11, Chapter 2 specification and joint tables.
- **STATED [IMAGE]:** Axis travel is base `310°`; shoulder `+130° / −35°`; elbow `±130°`; wrist pitch `±130°`; wrist roll `Unlimited (mechanically); ±570° (electrically)`. These are travel specifications, not documented absolute software coordinates. Arm, printed p.4, PDF p.10, specification table; coordinate origin absent under search log L2.
- **STATED [IMAGE]:** Position repeatability is `±0.18 mm (0.007") at TCP (tip of gripper)`, not the unsigned `0.18 mm` claim. Arm, printed p.4, PDF p.10, specification table.
- **STATED [TEXT]:** The controller explicitly states “On communication failure, motor power shutdown.” Neither the communication timeout duration nor watchdog reset conditions are supplied. Controller, printed p.5, PDF p.9, Figure 1-4, Safety Features; ABSENT under L4.
- **STATED [TEXT]:** Emergency stop disconnects motor power, enters COFF, aborts the program, and freezes outputs in their current state. A remote output device may continue operating. Controller, printed pp.8 and 25, PDF pp.12 and 29, Emergency Button / EMERGENCY Button and LED.
- **STATED [TEXT]:** Emergency-stop release does not itself restore Control On; the described software prompt requires a CON selection to return to it. Controller, printed p.25, PDF p.29, EMERGENCY Button and LED.
- **STATED [TEXT]/ABSENT:** COFF disables servo control; the encoder diagnostic still expects readings to change during physical movement. Home-reference retention through COFF/CON, emergency stop, or a power cycle is not specified. Arm, printed pp.18–19, PDF pp.24–25, troubleshooting items 4 and 6; L6.
- **STATED [TEXT]:** Electrical noise can cause Home to change suddenly while operation continues relative to the new Home. Controller, printed p.29, PDF p.33, troubleshooting item 9.
- **STATED [TEXT]/ABSENT:** `20` slots, two channels P0/P1, and a quarter-cycle phase shift are stated. Decode mode and counts per motor revolution are not. Counts per joint degree cannot be calculated from these manuals. Arm, printed pp.19, 24, 26–28, PDF pp.25, 30, 32–34, Figure 15 and parts tables; L1.
- **STATED [IMAGE]:** Gearing conflicts: `127.1:1` for motors 1–3 in the specifications versus `127.7:1` for S309/S310 in the parts list. Neither is selected as the verified physical ratio. Arm, printed pp.4 and 28, PDF pp.10 and 34, specification / parts tables.
- **STATED [IMAGE]:** The gripper encoder table swaps P0/P1 pad numbers relative to axes 1–5 and the D9 table: gripper P1 pad `3`, P0 pad `4`. This is a documented asymmetry, not a proven pin error. Arm, printed pp.34–35, PDF pp.40–41, wiring tables.
- **STATED [IMAGE]:** Drawings label links `220mm (8.66")` each, shoulder height `364mm (14.33")`, base height `190mm (7.5")`, and vertical envelope `1040mm (41")`. The relationship between drawn base width `230mm (9")` and mounting-text diameter `∅ 240 mm (9.49")` is unexplained. Arm, printed pp.6 and 12, PDF pp.12 and 18, Figures 6 and 13 / installation text.
- **STATED [IMAGE]/ABSENT:** Appendix A is a system connection drawing, not a component-level electrical schematic. Encoder input circuits, detailed motor output stages, and the emergency-stop switching circuit are not shown. Controller, printed p.33, PDF p.37, Appendix A; L13.
- **STATED [TEXT]/ABSENT:** Ten speed levels and a `1.5 ms control cycle parameter` are stated. The speed table, per-axis speed limits, stopping time/distance, and host-readable fault-code protocol are not supplied. Controller, printed pp.4–5, PDF pp.8–9, Figure 1-4; L7, L8, L14.
- **STATED [IMAGE]:** All 41 arm pages and 38 controller pages were visually inspected. Arm PDF pp.2, 4, 6 are blank. Controller PDF p.4 is blank; pp.10, 18, 30, 38 contain only footers. The previously missing arm specification page is fully readable. See review coverage below.

## Evidence and document conventions

`Arm` means **SCORBOT-ER 4u User Manual, Catalog #100343 Rev.B**. `Controller` means **Controller-USB User Manual, Catalog #100341, Rev. G**. Every citation gives the printed page and the 1-based PDF page. The offsets printed page + 6 (arm) and + 4 (controller) hold for the numbered body pages. Cover/copyright/contents pages are explicitly described as unnumbered or Roman-numbered; no negative printed page is invented.

**STATED** is explicit vendor content. **IMPLIED** is a deduction and is tagged **[INFERRED]** with its basis. **ABSENT** means not found in these two supplied copies, not a claim that the equipment lacks the feature. **[UNREADABLE]** means the supplied page cannot support the requested reading. **[TEXT]** identifies selectable text; **[IMAGE]** identifies a visually read drawing/table/scan. A table's source caption and evidence tag apply to **every cell** in that table unless a cell is marked `?`. `—` means the source cell is blank; it is not zero or “unused.” Added organizational tables are labelled as such.

No web search, other manual, repository inspection, or outside engineering specification was used. Repository verdicts concern only the claims supplied in the request. No outside-knowledge section is needed: no outside technical fact has been added.

## A. Document control

### A1. Identity and coverage (organizational table)

| Field | Arm | Controller |
|---|---|---|
| Title | SCORBOT-ER 4u User Manual | Controller-USB User Manual |
| Catalog / revision | `Catalog #100343 Rev.B` | `Catalog # 100341, Rev. G` |
| Published date | `(September 2001)` | `February 2007` |
| Copyright | `Copyright ©2001 Intelitek Inc.` | `Copyright © 2007 Intelitek Inc.` |
| Printed body pages present | 1–35 | 1–34 |
| PDF pages present | 41 | 38 |
| Product in main text | SCORBOT-ER 4u arm; references its Controller-USB and optional teach pendant | Controller-USB, SCORBOT arm, axes 7 and 8, teach pendant, digital/analog devices |
| Source | **STATED [IMAGE]**, cover PDF p.1; copyright unnumbered PDF p.3; printed p.1/PDF p.7, Chapter 1; last printed p.35/PDF p.41 | **STATED [IMAGE]**, cover PDF p.1 and copyright unnumbered PDF p.2; **[TEXT]**, printed pp.1–2/PDF pp.5–6, About Controller-USB; last printed p.34/PDF p.38 |

Page counts and complete numbering were **[INFERRED] by counting the rendered pages**, with the cited first/last pages, rather than represented as vendor specification numbers. File identities for reproducibility:

| Uploaded copy | SHA-256 (computed file identifier; not vendor fact) |
|---|---|
| 100343-b ER_4u(1).pdf | `76121baac03a5df98fc38c48ad2b97023858817b5fee8c541ff8a014f6cd356e` |
| 100341-g Controller-USB (0702).pdf | `a425ec0e2d5aa5af1855c7e3757bb70e004ac61dbef65b019719e14f0e5d9ce3` |

### A2. Every product-change / version statement found

**STATED [TEXT], Arm, printed p.24, PDF p.30, Chapter 7 introduction:** the drawings omit enhanced ER-4u features. The three listed changes are:

1. “Improved encoders on all motors provide greater accuracy. The encoder disk has 20 slots; the encoder housing and circuitry have also been upgraded.”
2. “Motor supports (items 34 and 35) for the shoulder and elbow axes have been improved; their dimensions have changed, and counter bearings have been added, to increase strength and stability.”
3. “Plates have been added to the robot arm frame, across the forearm and upper arm, and around the shoulder, to increase strength and stability.”

**STATED [IMAGE], Arm parts tables:** item 40 “Rear cross bar [not used in ER 4u]” (printed p.25/PDF p.31); item 113 “Spring [not used in ER 4u]” (printed p.26/PDF p.32); S234 “Nylon washer ∅ 11 x ? 4 [not used in ER 4u]” (printed p.27/PDF p.33; ambiguous unit/glyph discussed below). Figures 20–23 still depict some such parts: the p.24 qualification matters when using these illustrations.

**STATED [IMAGE], Controller, printed p.33, PDF p.37, Appendix A connection drawing:** both `#020040 SCORBOT ER-4u` and `#020030 SCORBOT ER-2u` are shown as alternatives; ASRS-36u and ASRS-36x2 are also shown. The title block says “For Robots #000413, #000418, #000420 & Accessories.” The manual does not map these title-block IDs to the model/catalog IDs in the blocks. This is not a mechanical comparison of the robots.

**STATED [TEXT], Controller, printed p.2, PDF p.6, About Controller-USB:** an RS-232 communication port is “reserved for future use.” **[TEXT], printed p.4/PDF p.8, Figure 1-4:** the teach pendant uses an integrated RS-232 channel. Figure 3-1, printed p.9/PDF p.13, separates the teach-pendant connection (5) from the reserved connector (6); these statements refer to separate interfaces.

**ABSENT:** no revision history, no identified previous encoder slot count, no explicit 4u-versus-4pc comparison, and no comparison against an older controller's interface. Whole-document visual scan: Arm PDF pp.1–41; Controller PDF pp.1–38. Selectable-text searches: `4pc`, `revision`, `version`, `older`, `previous`, `upgrade`, `improved`, `changed`, `not used`; the explicit changes above occur in Arm Chapter 7. Covers give revision identities without explaining revision differences. The drawing legends say “ESHED ROBOTEC MANUFACTURED COMPONENT”; they do not describe a product-version change. Arm, printed pp.29–32/PDF pp.35–38, Figures 20–23 [IMAGE].

## B. Kinematics and mechanics

### B1. Complete arm specification table

**STATED [IMAGE]**, Arm, printed p.4, PDF p.10, Chapter 2, **SCORBOT-ER 4u Specifications** (unnumbered table). Multi-line cells are preserved; the five axis lines remain inside their original Axis Movement cell.

| Specification cell | Value cell |
|---|---|
| Mechanical Structure | Vertical articulated |
| Number of Axes | 5 axes plus servo gripper |
| Axis Movement: Axis 1: Base rotation; Axis 2: Shoulder rotation; Axis 3: Elbow rotation; Axis 4: Wrist pitch; Axis 5: Wrist roll | Axis 1: `310°`; Axis 2: `+130° / −35°`; Axis 3: `±130°`; Axis 4: `±130°`; Axis 5: `Unlimited (mechanically); ±570° (electrically)` |
| Maximum Operating Radius | `610 mm (24.4")` |
| End Effector | DC servo gripper, with optical encoder, parallel finger motion; Measurement of object's size by means of gripper sensor and software. |
| Maximum Gripper Opening | `75 mm (3") without rubber pads`; `65 mm (2.6") with rubber pads` |
| Homing | Fixed position on each axis, found by means of microswitches |
| Feedback | Optical encoder on each axis |
| Actuators | `12 VDC servo motors` |
| Motor Capacity (axes 1–6) | `15 oz. in` Peak Torque (stall); `70 W` Power for Peak Torque |
| Gear Ratios | Motors 1, 2, 3: `127.1:1`; Motors 4, 5: `65.5:1`; Motor 6 (gripper) `19.5:1` |
| Transmission | Gears, timing belts, lead screw |
| Maximum Payload | `1 kg (2.2 lb), including gripper` |
| Position Repeatability | `±0.18 mm (0.007") at TCP (tip of gripper)` |
| Weight | `10.8 kg (23.8 lb)` |
| Maximum Path Velocity | `600 mm/sec (23.6"/sec)` |
| Ambient Operating Temperature | `2°–40°C (36°–104°F)` |

**Self-check [INFERRED]:** 17 specification rows, with five individually matched axis lines. The sign before elbow, wrist pitch, wrist electrical roll, and metric repeatability is **±**, not an absent or unsigned glyph. The base cell has a total `310°` travel and **no ± sign**. No row states a gripper force. Inch values are copied as printed, including `24.4"`; they are not repaired by conversion. The torque is printed `oz. in`, retained literally.

### B2. Complete joint-to-motor table

**STATED [IMAGE]**, Arm, printed p.5, PDF p.11, Structure, unnumbered motion table.

| Axis No. | Joint Name | Motion | Motor No. |
|---|---|---|---|
| 1 | Base | Rotates the body. | 1 |
| 2 | Shoulder | Raises and lowers the upper arm. | 2 |
| 3 | Elbow | Raises and lowers the forearm. | 3 |
| 4 | Wrist Pitch | Raises and lowers the end effector (gripper). | 4+5 |
| 5 | Wrist Roll | Rotates the end effector (gripper). | 4+5 |

**Self-check [INFERRED]:** five rows, axes 1–5 each occur once; both wrist rows intentionally repeat `4+5`. The table does not label a sixth revolute joint. The paragraph says “five revolute joints” and “With gripper attached, the robot has six degrees of freedom” [TEXT], same citation. No inference of arbitrary six-axis wrist orientation is made.

### B3. All dimension labels in arm figures (organizational table)

| Drawing / literal dimension | What its extension lines show | Source |
|---|---|---|
| Figure 5: `610mm (24")` | Radius from the base rotation center to the outside top-view envelope | **STATED [IMAGE]**, Arm printed p.6/PDF p.12, Figure 5 |
| Figure 6: `220mm (8.66")` (left link) | Shoulder-center to elbow-center link | **STATED [IMAGE]**, Arm printed p.6/PDF p.12, Figure 6 |
| Figure 6: `220mm (8.66")` (right link) | Elbow-center to wrist-center link | **STATED [IMAGE]**, same citation |
| Figure 6: `364mm (14.33")` | Vertical distance from base-bottom reference line to shoulder axis centerline | **STATED [IMAGE]**, same citation |
| Figure 6: `190mm (7.5")` | Base-bottom reference to the top of the drawn base pedestal | **STATED [IMAGE]**, same citation |
| Figure 6: `230mm (9")` | Width across the pedestal/base bottom in the side view; no diameter symbol is printed | **STATED [IMAGE]**, same citation |
| Figure 6: `1040mm (41")` | Top-to-bottom span of the illustrated side-view envelope; not height above the table | **STATED [IMAGE]**, same citation |
| Figure 12: `700 MM` | Arrow from robot base area toward surrounding safety-screen boundary | **STATED [IMAGE]**, Arm printed p.12/PDF p.18, Figure 12 |
| Figure 13: `∅207mm(8.15")` | Diameter of the six-hole pitch circle | **STATED [IMAGE]**, Arm printed p.12/PDF p.18, Figure 13 |
| Figure 13: `∅8.5mm(0.33…` | Hole-diameter callout; the right-hand imperial suffix is clipped at the image boundary | **STATED [IMAGE] / [UNREADABLE]** clipped suffix, same citation; adjacent text states `∅ 8.5 mm (0.33")` |
| Figure 14: `2 mm` and `0.80"` | Belt deflection under a drawn finger; the imperial figure label conflicts with the prose | **STATED [IMAGE]**, Arm printed p.16/PDF p.22, Figure 14 |

**Self-check [INFERRED]:** Figures 5–6 supply one radial and six side-view labels (the two link labels count separately); Figure 12 supplies one clearance label; Figure 13 supplies two diameter labels; Figure 14 supplies the metric/imperial deflection pair. Figures 1–4, 7–11, 15–26 contain no other labelled physical length, pulley diameter, or labelled rotation-angle dimension. Their part numbers, waveform levels, motor/wire identifiers, and numbered adjustment arrows are not length dimensions. All were visually scanned; waveform voltage labels are covered in D, component sizes in C's parts tables.

**IMPLIED [INFERRED]:** Figure 5's excluded wedge has **no labelled angle**. A full rotation minus the specified base travel gives `360° − 310° = 50°`. This is an inferred total excluded sector, **not a drawn angular label**, and not a verified zero direction or allocation of ±25°. Sources: Arm printed p.4/PDF p.10 specification table and printed p.6/PDF p.12 Figure 5.

**ABSENT:** wrist-center-to-TCP length, gripper weight alone, shoulder horizontal offset, explicit base coordinate frame, home angles, positive direction convention, DH parameters, and calibrated tool transform; L2/L12. The `610 mm` reach does not justify subtracting `220 + 220` to create a missing tool length because the manuals do not supply a complete geometrical chain.

### B4. Literal drawings, HOME / arrows / stops

| Figure(s) | Literal reading | Bucket / evidence / citation |
|---|---|---|
| 1 | Perspective assembled arm with exposed gears/belts and hanging jaws; no HOME, zero angle, or positive arrow | STATED [IMAGE], Arm printed p.1/PDF p.7, Figure 1 |
| 2 | Side-view arm labelled FOREARM, UPPER ARM, GRIPPER, CAM, MICROSWITCH, ENCODER, BODY, BASE; cam/microswitch callouts are near the shoulder region | STATED [IMAGE], Arm printed p.3/PDF p.9, Figure 2 |
| 2-3 | Body/base, upper arm, forearm, gripper labels on a bent arm; no numerical angle or home label | STATED [IMAGE], Arm printed p.5/PDF p.11, Figure 2-3 |
| 2-4 | BASE and WRIST ROLL have oval bidirectional rotation arrows; SHOULDER, ELBOW, WRIST PITCH have two-ended curved arrows around side-view joints | STATED [IMAGE], same page, Figure 2-4; **no arrow is marked + or −** |
| 5–6 | Top view: arm drawn toward the right with shaded nearly circular reach and a missing left wedge. Side view: bent arm inside a shaded envelope with inner exclusion and dimensions listed above | STATED [IMAGE], Arm printed p.6/PDF p.12, Figures 5–6; neither is labelled HOME |
| 7–8 | Encoder exploded housing/disk/board and motor/gear/encoder enlargement; no rotation-sign or home arrow | STATED [IMAGE], Arm printed p.7/PDF p.13, Figures 7–8 |
| 9–11 | Microswitch photograph; perspective transmissions labelled Motor 1 through Motor 6; front view of wrist/gripper differential and jaws | STATED [IMAGE], Arm printed pp.8–9/PDF pp.14–15, Figures 9–11 |
| 12–13 | Robot inside safety screen, control desk separate; six holes on a pitch circle | STATED [IMAGE], Arm printed p.12/PDF p.18, Figures 12–13 |
| 14–15 | Finger deflecting belt; encoder waveforms vs time, not joint-direction arrows | STATED [IMAGE], Arm printed pp.16 and 19/PDF pp.22 and 25, Figures 14–15 |
| 16 | Forearm side view; callout 1 points to tension fasteners with short up/down adjustment arrows | STATED [IMAGE], Arm printed p.21/PDF p.27, Figure 16 |
| 17 | Upper-arm side view and belt-stack view; callouts 2 and 3, SCREWS and BELTS; short up/down adjustment arrows | STATED [IMAGE], same page, Figure 17 |
| 18 | Two opposed motor bodies, belts/pulleys and clamping hardware; callout 4 points at lower fasteners, callout 5 at central upper fastener | STATED [IMAGE], same page, Figure 18 |
| 19 | Shoulder-side photograph with circles around cover screws; no motion arrow | STATED [IMAGE], Arm printed p.22/PDF p.28, Figure 19 |
| 20 | Exploded gripper: motor S312, encoder disk 414/board S115/housing 427, coupling S313, screw 94, shaft 105, bevel assembly 116/74 and jaw parts; leaders are part references | STATED [IMAGE], Arm printed p.29/PDF p.35, Figure 20 |
| 21 | Exploded arm links, belts/pulleys, shafts and bearings; VIEW A is an inset on pulley/miter-gear part 82. Parts 76 and 77 appear as a stopper and switch mounting plate according to the table | STATED [IMAGE], Arm printed p.30/PDF p.36, Figure 21; names from printed p.26/PDF p.32 parts table |
| 22 | Exploded base anti-backlash gearing, spring and motor S309; no tooth counts or angular dimensions on the drawing | STATED [IMAGE], Arm printed p.31/PDF p.37, Figure 22 |
| 23 | Exploded base/body with motors S309, S310, S311, encoder parts and belt drives. Rubber-stopper callout S325 is present | STATED [IMAGE], Arm printed p.32/PDF p.38, Figure 23; part name from printed p.28/PDF p.34 |
| 24 | D50 connector outline and endpoint pin labels, detailed in F | STATED [IMAGE], Arm printed p.33/PDF p.39, Figure 24 |
| 25–26 | Motor wiring labels and motor cable ending in D9; no numbered connector-face view | STATED [IMAGE], Arm printed p.35/PDF p.41, Figures 25–26 |

**ABSENT:** no figure explicitly labelled a HOME pose; no figure labels per-joint positive rotation; no drawing dimensions a hard-stop angle/location. Stop-related part names and references to mechanical limits exist, but do not define a safe stop boundary. Complete figure scan: Arm PDF pp.7, 9, 11–15, 18, 22, 25, 27–28, 35–41; terms `home`, `positive`, `direction`, `stop`, `mechanical limit`, `cam`; L2/L3/L12. Figure captions `2-3` and `2-4` differ from prose “Figures 3 and 4”; see Contradictions.

### B5. Mounting and clearances (documented requirements, not instructions)

**STATED [TEXT]**, Arm printed p.12/PDF p.18, Chapter 4 and Figure 13: minimum `700mm` free space all around the robot; at least `3 bolts 120° apart`; robot-base `∅ 240 mm (9.49")`; pitch circle `∅ 207 mm (8.15")`; `Hole (6 off) ∅ 8.5 mm (0.33")`. The text warns that inadequate bolting can allow imbalance/toppling during motion. Printed p.13/PDF p.19, Chapter 4, puts controller and computer “well outside the robot's safety range.” Figure 12 labels SAFETY SCREEN, ROBOT WORKING AREA, CONTROL DESK [IMAGE].

**ABSENT:** mounting bolt grade, length, tightening torque, workbench load rating, overhead clearance, a numerical distance from robot to control desk, and a calculation tying `700mm` to a stopping distance. Arm printed pp.10–13/PDF pp.16–19 and Figures 5–6, 12–13 scanned; terms `bolt`, `torque`, `surface`, `clearance`, `space`, `distance`, `guard`, `screen`, `stop`; L12/L14.

## C. Transmission and gearing

### C1. Stated transmissions and ratios

| Item | Literal statement/value | Bucket / source |
|---|---|---|
| Base and shoulder | “Spur gears move the base and shoulder axes.” | STATED [TEXT], Arm printed p.8/PDF p.14, Transmissions |
| Elbow | “Pulleys and timing belts move the elbow axis.” | STATED [TEXT], same citation |
| Wrist | “Pulleys and timing belts, and a bevel gear differential unit at the end of the arm move the wrist pitch and roll axes.” | STATED [TEXT], same citation |
| Gripper | “A lead screw transmission opens and closes the gripper.” | STATED [TEXT], same citation |
| Motors 1, 2, 3 | `127.1:1` | STATED [IMAGE], Arm printed p.4/PDF p.10, specification table |
| Motors 4, 5 | `65.5:1` | STATED [IMAGE], same citation |
| Motor 6 (gripper) | `19.5:1` | STATED [IMAGE], same citation |
| S309, catalog 430901 | “Motor Gear - base; 127.7:1” | STATED [IMAGE], Arm printed p.28/PDF p.34, parts table |
| S310, catalog 430901 | “Motor Gear - shoulder/elbow; 127.7:1” | STATED [IMAGE], same citation |
| S311, catalog 430902 | “Motor Gear - pitch/wrist 65.5:1” | STATED [IMAGE], same citation |
| S312, catalog 430903 | “Motor Gear - gripper” — no ratio in this cell | STATED [IMAGE], same citation |
| Item 11, catalog 111906 | “Spur gear (120 teeth)” | STATED [IMAGE], Arm printed p.25/PDF p.31, parts table |
| Item 49, catalog 111905 | “Spur gear (72 teeth)” | STATED [IMAGE], Arm printed p.26/PDF p.32, parts table |
| Item 60, catalog 111904 | “spur gear (right – 72 teeth)” | STATED [IMAGE], same citation |
| Anti-backlash unit | “four gears”; stacked gears 22/27 and spring 23; adjustment text says “a distance of six teeth between the marked teeth” | STATED [TEXT], Arm printed p.22/PDF p.28, Adjusting Base Anti-Backlash (documentary description only) |
| Wrist differential | “Three bevel gears form a differential gear train which moves the wrist joint.” | STATED [TEXT], Arm printed p.9/PDF p.15, Gripper |
| Oldham coupling | “three parts—two metal parts fitted with bolts and an intermediate plastic part”; note gives `1.5mm to 2mm` clearance from plate 112 | STATED [TEXT], Arm printed p.23/PDF p.29, Gripper Disassembly / Note |
| Peripheral motor kit | `Motor kit (127:1), 24 V` | STATED [TEXT], Controller printed p.12/PDF p.16, Installing Peripheral Axes; not an ER-4u joint ratio |

**127.1 versus 127.7 resolution:** **UNRESOLVED contradiction**, not a rounding correction. Both are readable. The two S309/S310 entries share catalog `430901` and agree with each other; the specification groups motors 1–3 under another value. No erratum, revision-to-ratio mapping, gearhead model breakdown, or measured reduction is present. Neither can become a uniquely verified SDK constant from these PDFs. Arm printed pp.4, 24–32/PDF pp.10, 30–38, Chapter 2 / Chapter 7 [TEXT][IMAGE].

**STATED [TEXT]**, Arm printed p.9/PDF p.15, Gripper: “When motors 4 and 5 are driven in opposite directions, the wrist pitch moves up and down. When motors 4 and 5 are driven in the same direction, the wrist rolls clockwise and counterclockwise.” **ABSENT:** differential tooth counts, numerical differential ratio, motor-angle-to-joint-angle equations, which polarity/sign produces upward pitch, clockwise roll viewpoint, and whether either motor's encoder count is inverted. `65.5:1` is labelled a motor gear ratio; the manual does not label it as the complete differential/joint reduction. L10.

**ABSENT:** no pulley diameter, pitch, pulley tooth count, belt tooth count/length, lead-screw pitch, or complete output gear ratio is stated. Pulley entries 38, 47, 48, 70, 82, 86, 53/56, flange entries S300/S301 and belt entries S293–S295 supply names/catalog numbers, not sizes. Arm printed pp.8–9, 21–32/PDF pp.14–15, 27–38, transmissions, adjustment text, parts tables and Figures 16–23; searches `ratio`, `teeth`, `tooth`, `pulley`, `pitch`, `diameter`, `belt`, `lead screw`, `127`, `65.5`, `19.5`; visual check of all these drawings.

**Counts-per-degree computation withheld:** the decode multiplier, unequivocal gearing, complete post-gearhead train, wrist ratio, and axis sign conventions are not all supplied. No numeric count/degree or counts/revolution is manufactured. L1/L10.

### C2. Full parts-list transcription and checks

The full 161-row, three-column vendor parts list is reproduced in **Table supplement T1**, after J, rather than repeated here. Every cell is visually checked against Arm printed pp.25–28/PDF pp.31–34. Row counts are `44`, `44`, `44`, `29` [INFERRED by counting rows]. Known table problems are preserved: duplicate drawing number S270, reused catalog numbers with differing fastener descriptions, missing catalog cells for items 40/113, and the malformed glyph in S234. No unit is inferred for a dimension whose cell supplies none.

## D. Encoders

| Requested item | Finding | Bucket / evidence / citation |
|---|---|---|
| Sensor type/location | Electro-optical encoder attached to the shaft of the motor driving the axis; each motor has an encoder for closed-loop control | STATED [TEXT], Arm printed p.7/PDF p.13, Motors / Encoders |
| Slots | `20 slots`; explicitly said for improved encoders on **all motors** | STATED [TEXT], Arm printed p.24/PDF p.30, Chapter 7 introduction |
| Gripper disk | Item 414, catalog 105003: “Encoder disk (20 slots) - gripper” | STATED [IMAGE], Arm printed p.28/PDF p.34, parts table |
| Other disk | Item 429, catalog 105003: “Encoder disk (20 slots)” | STATED [IMAGE], same citation |
| Encoder circuitry parts | S115 and S116, catalog 45008: “Encoder circuitry (20 slots)” | STATED [IMAGE], Arm printed pp.26–27/PDF pp.32–33, parts tables; “slots” is the table's wording, not a separately stated circuit resolution |
| Board name | `PC510` | STATED [IMAGE], Arm printed p.7/PDF p.13, Figure 7; printed p.35/PDF p.41, D9 table header “Encoder (PC510) Pad #” |
| Physical elements | Figure 7 labels Disk, LEDs, Phototransistors, PC510; shows the board and split housing | STATED [IMAGE], Arm printed p.7/PDF p.13, Figure 7 |
| Channels | `P0` and `P1`, two phototransistors/two channels | STATED [TEXT], Arm printed p.19/PDF p.25, troubleshooting item 5 / Figure 15 |
| Low level tolerance | “VL (low) value should be 0.4V or less.” | STATED [TEXT], same citation; inclusive `≤ 0.4 V` is a restatement, not a new measured level |
| High level tolerance | “VH (high) value should exceed 4 V.” | STATED [TEXT], same citation; strictly `> 4 V`, not `≥ 4 V` |
| Phase | “a time shift of a quarter cycle between the two waves” | STATED [TEXT], same citation; no permitted tolerance around that phase is quantified |
| Waveforms | Three V-versus-t plots; first labels VH1/VL1, second VH2/VL2, third VH1+VH2/VL1+VL2; third represents the sum and has stepped levels | STATED [IMAGE], same citation, Figure 15 |
| Counts/direction | “The encoder reading should rise for rotation in one direction and fall for rotation in the opposite direction.” | STATED [TEXT], Arm printed p.18/PDF p.24, troubleshooting item 4 |
| Physical motion meant by that direction sentence | The preceding text concerns the axis in question being physically moved in both directions with Control Off; it supplies no named clockwise, upward, base-positive, or motor-terminal-positive direction | STATED [TEXT], same citation |
| Repeatability diagnostic tolerance | “It should be within several counts of the first reading.” | STATED [TEXT], Arm printed p.19/PDF p.25, troubleshooting item 6; `several` is not a numerical bound |
| Controller feedback type | “Incremental optical encoders for each axis” | STATED [TEXT], Controller printed p.5/PDF p.9, Figure 1-4, Position Feedback |
| Decode 1x/2x/4x | Not stated | ABSENT, L1 |
| Counts per motor revolution / joint revolution / joint degree | Not stated | ABSENT, L1; slots and waveform phase do not select an edge-counting convention |
| Encoder supply voltage/current | VLED pin names and wiring are given; a numerical VLED supply voltage/current is not specified | ABSENT, L1/L13; do not equate internal `5V` supply or `>4 V` signal-high criterion with a specified VLED rail |

**Diagnostic descriptions, not executable procedures:** Arm troubleshooting item 4 describes checking rising/falling readings and treats no change as an encoder/wiring/PCB-connection fault (printed p.18/PDF p.24 [TEXT]). Item 5 describes oscilloscope inspection of clean P0/P1 square waves, the low/high criteria above, and an incorrect phase/shape as an encoder fault (printed p.19/PDF p.25, Figure 15 [TEXT][IMAGE]). Item 6 compares readings after a return to a pencil-marked joint position, says “within several counts,” and identifies accumulating error as a replacement condition; it also associates many-axis repeatability problems with electrical noise and calls for transmission checks (same page, item 6 [TEXT]). No numerical number of repetitions, permissible drift per repetition, encoder count total, pulse frequency, or phase-error tolerance is given.

**IMPLIED [INFERRED]:** two quarter-cycle-shifted channel waveforms supply direction-sensitive information; this follows from the direction/sequence paragraph and Figure 15. It does **not** establish whether the controller counts one, two, or four edges per slot. Arm printed pp.7 and 19/PDF pp.13 and 25, Encoders / Figure 15.

## E. Home switches

### E1. Per-axis findings (organizational table)

**STATED [TEXT]**, Arm printed p.8/PDF p.14, Microswitches: “five microswitches—one on each axis.” **STATED [IMAGE]**, wiring ground/MS cells below are from Arm printed p.34/PDF p.40, SCORBOT-ER 4u Wiring.

| Joint axis | Switch location / travel end | Contacts | Wiring signal / ground | Controller level |
|---|---|---|---|---|
| 1 Base | Exact physical location and which end of travel ABSENT (L3); a base/body assembly is drawn without a usable per-axis switch-position specification | NO/NC not explicitly stated; closure-on-depression IMPLIED as explained below | MS D50 `23` brown; GND D50 `33*` white; Molex cells blank | ABSENT (L3/L13) |
| 2 Shoulder | Figure 2 labels a cam and microswitch near the shoulder region; axis number, offset and travel-end assignment are not on that callout | Same | MS D50 `7` gray; GND D50 `32*` white; Molex cells blank | ABSENT |
| 3 Elbow | Exact location/end ABSENT; Figures 21/23 and part names do not dimension the home-cam relationship | Same | MS Molex `2` white → D50 `24` orange; GND Molex `1` white → D50 `31*` white | ABSENT |
| 4 Wrist Pitch | Exact location/end ABSENT; no labelled signed home angle | Same | MS Molex `4` blue → D50 `8` green; GND Molex `3` blue → D50 `30*` white | ABSENT |
| 5 Wrist Roll | Exact location/end ABSENT; no labelled signed home angle | Same | MS Molex `6` orange → D50 `6` blue; GND Molex `5` orange → D50 `29*` white | ABSENT |
| Gripper (motor 6) | No gripper home microswitch assigned | No connection in gripper microswitch cell | Gripper rows list D50 `28*` white and `22` brown/gray under “no connection” | No switch logic specified |

Figure 2 citation: Arm printed p.3/PDF p.9 [IMAGE]. Assembly citations: Arm printed pp.30 and 32/PDF pp.36 and 38, Figures 21 and 23 [IMAGE]. All ABSENT cells refer to the specific scan/search log L3.

**IMPLIED [INFERRED], not an explicit NO label:** the diagnostic expects a depressed home microswitch to short its two poles. Combined with the signal-to-ground wiring, this implies closure to ground on actuation. However, the released contact state is never explicitly tested/described as open, the terminals are not labelled NO/NC, and no controller voltage/bit polarity is given. Calling this a fully verified normally-open, active-low digital input would exceed the evidence. Arm printed pp.17–18/PDF pp.23–24, troubleshooting item 2 [TEXT]; printed p.34/PDF p.40 wiring table [IMAGE]. General-purpose I/O LOW/HIGH thresholds belong to different terminals and cannot be assigned to these home inputs.

### E2. Homing behavior and checks

**STATED [TEXT]**, Arm printed p.8/PDF p.14, Microswitches:

> During the homing procedure, the robot joints are moved one at a time. Each axis is moved until the its home switch is activated. The axis is then moved slightly until the the switch shuts off—at that point the joint is at home.

The following paragraph calls all-joints-home the reference for operation and says “Whenever the system is turned on, the robot should be sent to this position, by means of a software homing routine.” Same citation. This is a recommendation to establish home after turn-on; it does **not** explicitly say how/where the old reference is stored or that a power cycle erases it.

**ABSENT (L3):** initial search direction, whether the slight movement reverses or continues, quantitative back-off distance, homing speed, debounce criterion, specified joint order, homing timeout, switch-level voltage, and numerical home angles. “One at a time” does not mean “1, 2, 3, 4, 5” in that order. “Slightly” is not a distance. The routine's software parameters are mentioned but not listed: Arm printed p.17/PDF p.23, troubleshooting item 2 [TEXT].

**STATED [TEXT]**, same item 2, printed pp.17–18/PDF pp.23–24: the manual describes viewing home-microswitch status in SCORBASE's Movement Information dialog, checking switch click/return, checking contact closure while depressed with an ohmmeter, and tracing continuity through the wires and D50 pins. The text supplies no contact-resistance tolerance, actuation force, switch hysteresis, repeatability, or debounce time. These are descriptions of vendor diagnostics, not instructions to execute them.

**STATED [TEXT]**, Controller printed p.28/PDF p.32, Inspection: “Robot reaches home position in all five axes, gripper and peripheral axes 7 and 8 (if connected), and Homing Complete message appears.” This broadens the described homing result beyond the five arm home switches, but gives no gripper/peripheral method. It does not create a gripper switch that the arm wiring table explicitly leaves unconnected.

### E3. Drift, shift, and retention

**STATED [TEXT]**, Controller printed p.29/PDF p.33, troubleshooting item 9:

> The Home position suddenly changes, and the robot continues operation in relation to the new Home.

> This fault may occur continually or occasionally, due to noisy electrical systems.

The cited item's remedies mention another Home routine/program reload, power-line filtering for frequent faults, and contacting the agent. There is no quantified drift threshold or promise of automatic stopping on this fault. Arm printed p.19/PDF p.25, troubleshooting item 6 [TEXT], separately associates widespread repeatability faults with environmental electrical noise and accumulating encoder error.

**ABSENT:** home retention/loss through COFF→CON, emergency stop/release, USB disconnect/reconnect, or power cycle; L6. Encoder changes being visible during COFF are evidence of continued reading during that diagnostic, not proof of home-reference retention. Arm printed pp.18–19/PDF pp.24–25 [TEXT].

## F. Wiring and connectors

### F1. Complete D50 motor table

**STATED [IMAGE]**, Arm printed p.33/PDF p.39, SCORBOT-ER 4u Wiring. The original table's grouped headers are Robot Arm Signal; Lead to Molex 12-pin Connector; Lead to D50 Connector. Every subcolumn is retained below; merged axis cells are repeated only for readability.

| Axis | Motor | Encoder | Pad # | Microsw. | Molex Color | Molex Pin# | D50 Color | D50 Pin # |
|---|---|---|---|---|---|---|---|---|
| 1 | + | — | — | — | — | — | white | 50 |
| 1 | − | — | — | — | — | — | gray/green | 17 |
| 2 | + | — | — | — | — | — | white | 49 |
| 2 | − | — | — | — | — | — | white/green | 16 |
| 3 | + | — | — | — | — | — | white | 48 |
| 3 | − | — | — | — | — | — | orange/brown | 15 |
| 4 | + | — | — | — | — | — | white | 47 |
| 4 | − | — | — | — | — | — | orange/green | 14 |
| 5 | + | — | — | — | — | — | white | 46 |
| 5 | − | — | — | — | — | — | orange/gray | 13 |
| Gripper | + | — | — | — | gray | 8 | white | 45 |
| Gripper | − | — | — | — | yellow | 7 | orange/blue | 12 |

**Self-check [INFERRED]:** 12 rows = two motor terminals for each of five body motors plus the gripper. Motor D50 pins are all distinct. Molex motor pins are only 7/8, gripper; blank cells for body motors are not an inferred Molex path.

### F2. Complete D50 encoder table

**STATED [IMAGE]**, Arm printed p.34/PDF p.40, continuation of SCORBOT-ER 4u Wiring. Source footnote on printed p.33/PDF p.39: “(* indicates two wires on same pin.)”

| Axis | Motor | Encoder | Pad # | Microsw. | Molex Color | Molex Pin# | D50 Color | D50 Pin # |
|---|---|---|---|---|---|---|---|---|
| 1 | — | GND | 1 | — | — | — | white | 33* |
| 1 | — | P1 | 4 | — | — | — | white/gray | 5 |
| 1 | — | VLED | 2 | — | — | — | yellow | 11 |
| 1 | — | P0 | 3 | — | — | — | brown | 2 |
| 2 | — | GND | 1 | — | — | — | white | 32* |
| 2 | — | P1 | 4 | — | — | — | white/orange | 21 |
| 2 | — | VLED | 2 | — | — | — | yellow | 27 |
| 2 | — | P0 | 3 | — | — | — | gray | 1 |
| 3 | — | GND | 1 | — | — | — | white | 31* |
| 3 | — | P1 | 4 | — | — | — | brown/blue | 4 |
| 3 | — | VLED | 2 | — | — | — | yellow | 10 |
| 3 | — | P0 | 3 | — | — | — | green | 36 |
| 4 | — | GND | 1 | — | — | — | white | 30* |
| 4 | — | P1 | 4 | — | — | — | green/brown | 20 |
| 4 | — | VLED | 2 | — | — | — | yellow | 26 |
| 4 | — | P0 | 3 | — | — | — | orange | 35 |
| 5 | — | GND | 1 | — | — | — | white | 29* |
| 5 | — | P1 | 4 | — | — | — | green/blue | 3 |
| 5 | — | VLED | 2 | — | — | — | yellow | 9 |
| 5 | — | P0 | 3 | — | — | — | blue | 18 |
| Gripper | — | GND | 1 | — | black | 12 | white | 28* |
| Gripper | — | P1 | 3 | — | green | 11 | gray/blue | 19 |
| Gripper | — | VLED | 2 | — | yellow | 10 | white | 25 |
| Gripper | — | P0 | 4 | — | brown | 9 | white/blue | 34 |

**Self-check [INFERRED]:** 24 rows = six groups × four signals. Each group uses pads 1–4 once. Axes 1–5 use P1=4/P0=3; gripper uses P1=3/P0=4. No encoder D50 signal pin collides with a motor-power pin. Ground repetitions with the switch table are deliberate and starred. The gripper VLED D50 wire is **white**, whereas axes 1–5 use yellow; Molex gripper VLED is yellow. These differences are retained exactly.

### F3. Complete microswitch / gripper-no-connection table

**STATED [IMAGE]**, same Arm printed p.34/PDF p.40 wiring table. The gripper's merged “no connection” cell spans its two listed leads; it is retained literally rather than assigned a switch function.

| Axis | Motor | Encoder | Pad # | Microsw. | Molex Color | Molex Pin# | D50 Color | D50 Pin # |
|---|---|---|---|---|---|---|---|---|
| 1 | — | — | — | GND | — | — | white | 33* |
| 1 | — | — | — | MS | — | — | brown | 23 |
| 2 | — | — | — | GND | — | — | white | 32* |
| 2 | — | — | — | MS | — | — | gray | 7 |
| 3 | — | — | — | GND | white | 1 | white | 31* |
| 3 | — | — | — | MS | white | 2 | orange | 24 |
| 4 | — | — | — | GND | blue | 3 | white | 30* |
| 4 | — | — | — | MS | blue | 4 | green | 8 |
| 5 | — | — | — | GND | orange | 5 | white | 29* |
| 5 | — | — | — | MS | orange | 6 | blue | 6 |
| Gripper | — | — | — | no connection | — | — | white | 28* |
| Gripper | — | — | — | no connection | — | — | brown/gray | 22 |

**Self-check [INFERRED]:** 12 rows: five actual two-lead switches, plus two gripper leads under “no connection.” The D50 table as a whole has 48 lead rows, 42 unique pin numbers: 1–36 and 45–50. Pins 28–33 each appear twice; all repetitions are ground / the gripper-no-connection row, matching the star note. No contradictory assignment to a powered motor terminal was found. Pins 37–44 are **not listed**, not proven unused. Pin 22 is expressly in a no-connection row, not an assigned sixth home-switch input.

### F4. Complete 12-pin Molex mapping (reorganized from source cells)

**STATED [IMAGE]**, Arm printed pp.33–34/PDF pp.39–40, wiring table; **[INFERRED]** ordering the entries by Molex pin number does not add a wiring claim.

| Molex pin | Robot-side color | Function | D50 pin | D50-side color |
|---|---|---|---|---|
| 1 | white | Axis 3 microswitch GND | 31* | white |
| 2 | white | Axis 3 microswitch MS | 24 | orange |
| 3 | blue | Axis 4 microswitch GND | 30* | white |
| 4 | blue | Axis 4 microswitch MS | 8 | green |
| 5 | orange | Axis 5 microswitch GND | 29* | white |
| 6 | orange | Axis 5 microswitch MS | 6 | blue |
| 7 | yellow | Gripper motor − | 12 | orange/blue |
| 8 | gray | Gripper motor + | 45 | white |
| 9 | brown | Gripper encoder P0, pad 4 | 34 | white/blue |
| 10 | yellow | Gripper encoder VLED, pad 2 | 25 | white |
| 11 | green | Gripper encoder P1, pad 3 | 19 | gray/blue |
| 12 | black | Gripper encoder GND, pad 1 | 28* | white |

**Self-check [INFERRED]:** exactly 12 rows, all Molex pins 1–12 occur once, none omitted/reused. No connector-face orientation/pin-position drawing is supplied for this Molex. Arm printed p.33/PDF p.39, Robot Wiring [TEXT], identifies a “square 12-pin Molex connector in the base” and flexible leads. Its broad “gripper motor and microswitches on the arm” prose should not override the actual cells: axes 1/2 switch Molex cells are blank; 3–5 and gripper motor/encoder pass through the listed Molex pins. This is a scope ambiguity, not evidence for rewiring the blank cells.

### F5. Complete D9 axis-7/8 table

**STATED [IMAGE]**, Arm printed p.35/PDF p.41, Single Axis Wiring, unnumbered table. The same table applies to both separate ports; it is not a serial-port pinout.

| Function | Encoder (PC510) Pad # | D9 Connector Pin # |
|---|---|---|
| Motor Power (+) | — | 1 |
| Motor Power (−) | — | 9 |
| Encoder Phototransistor (P0) | 3 | 8 |
| Encoder Phototransistor (P1) | 4 | 6 |
| Encoder LED voltage (VLED) | 2 | 3 |
| Encoder Ground (GND) | 1 | 5 + Shield |
| Microswitch Signal (MS) * | — | 4 |
| Microswitch (GND) * | — | 5 |

**Self-check [INFERRED]:** eight function rows, seven unique pin numbers {1,3,4,5,6,8,9}; pin 5 is shared encoder/switch ground. Pins 2 and 7 are not assigned in the table. Pad P0=3/P1=4 agrees with body encoders, differs from the gripper table. The heading says “(optional) microswitch”; a separate explanatory footnote for the asterisks is not printed on this page. Figure 25 labels `V LED`, `GND`, `P1`, `PO`, `MOTOR−`, `MOTOR+`; Figure 26 shows a cable ending in a D9 without pin numbers. Neither supplies a numbered connector-face orientation. Controller printed pp.10 and 12/PDF pp.14 and 16, Figure 3-2 / Installing Peripheral Axes [TEXT][IMAGE], also identifies separate AXIS 7 and AXIS 8 D9 ports.

### F6. Connector-face / panel / emergency / I/O findings

The complete vendor panel legends are reproduced in Table supplement T3. **STATED [IMAGE]**, Arm printed p.33/PDF p.39, Figure 24: D50 outline is vertical, with three staggered columns. Labels identify PIN 1 at the upper end of the right column, PIN 17 at its lower end; PIN 18 at the top of the middle column, PIN 33 at its bottom; PIN 34 at the top of the left column, PIN 50 at its bottom. The drawing does **not** state mating-face versus solder-side view. Pin-label leaders, rather than visual left/right alone, establish this description.

**STATED [IMAGE]**, Controller printed p.9/PDF p.13, Figure 3-1: rear has ROBOT D50 (9), a USB connector (8), remote emergency two-contact terminal (7), a D-type reserved connector (6), a modular teach-pendant connector (5), and mains switch/selector/socket/fuse assembly (1–4). Neither USB connector type designation nor the teach-pendant modular pin count is written. The reserved D-type drawing is separate from the front motor D9 ports. No pinout for the reserved port, USB signals, teach-pendant port, or teach-pendant bypass plug is supplied.

**STATED [TEXT]**, Controller printed pp.3, 11–13/PDF pp.7, 15–17, Figure 1-3 / installation sections: a teach-pendant emergency bypass plug is required when no pendant is connected; a **different** remote-emergency bypass cable/jumper shorts the two rear EMERGENCY terminals when no remote switch is present. The remote switch contacts are specified “normally closed (NC).” The text identifies upper square release openings and lower round wire openings, but no terminal voltage/polarity, pin numbering, contact-current rating, or circuit diagram. These two bypass accessories must not be conflated.

**STATED [IMAGE]**, Controller printed p.10/PDF p.14, Figure 3-2: digital-input terminals are numbered 1–8 with ground terminals below; relay outputs 1–4 have NC/C/NO labels; open-collector terminals 5/6 and 7/8 have ground labels; auxiliary supply has three contacts labelled `+`, `−`, and a ground symbol under `12V Power Supply`; analog input blocks are labelled 1,2,ground and 3,4,ground; analog output block is labelled 1,2,ground. The I/O LEDs occupy two eight-channel rows (outputs above inputs). The drawing does not assign physical contact pin numbers beyond channel labels or establish equivalence between the auxiliary negative contact and the ground-symbol contact.

**STATED [TEXT][IMAGE]**, Controller printed pp.15–24/PDF pp.19–28, Chapter 4, Figures 4-1–4-12: electrical ratings, input truth tables, relay truth table, sink/source configurations, and driver-based analog-output connection are given. Full table cells and jumper depiction are in T4; numeric ratings are in G1/G3. These user-I/O circuits do not reveal the robot encoder or home-switch input circuits.

### F7. Appendix A: what is actually shown

**STATED [IMAGE]**, Controller printed p.33/PDF p.37, **Appendix A, Schematic Diagram** (drawing itself is unnumbered, title “Schematic Connection Drawing”). It contains controller `#034016`, Power Board `#045108`, Display Board `#045107`, Power Supply Assy. `#035062`, flat cables `#411881 Flat Cable J2` and `#411882 Flat Cable J3`, ASRS adapter `#035074`, and teach pendant `#001709`. It labels axis 1–6, axis 7, axis 8, USB to computer, emergency switch, emergency-stop arrow to the power-board block, I/O, and `12VDC 100mA Power Supply`. Supply-to-board arrows are `9VAC`, `19VAC`, `16VAC`, `14VAC`; mains is `100-120V or 220-240V` and the power-switch text supplies `VAC`. Two linked switch-contact symbols appear on the incoming mains lines. They are not labelled an emergency-stop relay.

| Requested circuit | What can be read | Bucket / source |
|---|---|---|
| Encoder input circuits | No resistor, comparator, pull-up, optocoupler, input threshold or decoder component shown | ABSENT (L13), same Appendix A |
| Motor driver outputs | Axis interface arrows; H-bridge/PWM is stated in Figure 1-4, but no output-stage transistor circuit drawn | STATED [IMAGE] interface; component circuit ABSENT, Controller printed p.4/PDF p.8, Figure 1-4 and printed p.33/PDF p.37, Appendix A |
| Emergency-stop circuit | External arrow and diagonal emergency-stop link to Power Board; no contact/coil/pole topology, relay specification or redundancy shown | STATED [IMAGE] block connection; detailed circuit ABSENT, Appendix A |
| Isolation | No isolation barrier or rating is identified; lack of symbols is not proof of no isolation in hardware | ABSENT, L13 |
| Relays | General-purpose relay outputs 1–4 are stated and their contact behavior drawn elsewhere; no emergency-stop relay is identified | STATED [TEXT][IMAGE], Controller printed pp.21–22/PDF pp.25–26, Relay Outputs / Figure 4-9; emergency-stop relay ABSENT, L13 |

Arm has **no Appendix A** in this copy (ends Chapter 8 at printed p.35/PDF p.41); Appendix A belongs to **Controller**, despite the request's wording. Controller contents lists it as “7. Schematic Diagram,” while the page heading calls it Appendix A. Controller printed p.34/PDF p.38 is footer-only; there is no hidden second detailed schematic on that page [IMAGE].

The remaining product blocks in the same Appendix A drawing are **STATED [IMAGE]**, Controller printed p.33/PDF p.37: `#020037 ASRS-36u`, or `#0200371 and #021148 ASRS-36x2`; alternatives `#020040 SCORBOT ER-4u` or `#020030 SCORBOT ER-2u`. Each accessory arrow says “To Axis 7 or 8”: `#001009 Accessory Rotary Table`; `#001020, #001021, #001023 Accessory LSB`; `#001014 Accessory: X/Y Positioning Table`; `#001013 Accessory: Linear Positioning Table`; `#001010 Accessory: Linear Conveyor`. No other product manual was consulted to expand these abbreviations or IDs.

## G. Controller behavior

### G1. Complete Controller-USB specifications table, first page

**STATED [IMAGE]**, Controller printed p.4/PDF p.8, Specifications, table continued into Figure 1-4 on the next page. Every original cell is reproduced; spelling such as “differential” is retained.

| Item | Specification |
|---|---|
| Type of Control | Real time; Multi-tasking; PID (proportional, integral, differential); PWM (pulse width modulation). |
| Number of Servo Axes | Maximum: `8` |
| Groups of Control | `6 robot axes and 2 peripheral axes.` Axis interpolation in robot and peripherals groups. |
| Axis Drivers | PWM H-bridge drivers; `15 kHz, 3A standard; 7A peak`; `12/24V (depending on input voltage and load)` |
| Path / Trajectory Control | CP: Joint; Linear; Circular. `1.5 ms control cycle parameter.` Software controlled acceleration/deceleration. PID parameters. |
| Speed Control | Speed or Travel time definitions. Ten speed levels are available |
| Control Parameters | I/O control; Speed, velocity profile, smoothing; Axis position error; Gripper operation; Impact, software limit protection; Homing; Encoder interface; Cartesian calculations |
| Power Requirements | `110/220V AC (+15%, -10%), 50/60Hz, 180W max.` |
| Internal Power Supplies | Servo: `24V (depending on input voltage and load)`; Digital: `5V, +15V, -12V` |
| Weight | `7 kg (15.4 lb)` |
| Dimensions | `L=31.5cm; W=22.3cm; H=11.7cm`; `(L=12.4"; W=8.8"; H=4.6")` |
| Ambient Operating Temperature | `10°–35° C (50°-95° F)` |
| Microcontroller | NEC V853 |
| Communications | USB interface with PC; Integrated RS-232 channel for teach pendant |
| User power supply | `12V DC` `0.1A max.` |

### G1b. Complete specification continuation

**STATED [IMAGE]**, Controller printed p.5/PDF p.9, **Figure 1-4: Controller-USB Specifications**.

| Item | Specification |
|---|---|
| Digital Outputs | `8 digital outputs:` `1 – 4: relays 24 V (AC or DC), 1.0 A max.` `5 – 8: sink/source configurable open collectors`; `Sink: 15 VDC, 0.5 A max. for each output`; `Source: 15 VDC, 50 mA max. for all outputs combined` |
| Digital Inputs | `8 Dry Contacts: PNP/NPN` `0-24 VDC max.` `(high/low) configurable` |
| Analog Outputs | `2 analog outputs: 8-bit resolution; output voltage 0–10 VDC, 20 mA max.` |
| Analog Inputs | `4 analog inputs: (8-bit resolution)` `Input voltage 0–10 VDC` |
| Programming and Position Teaching | SCORBASE software; PC user-defined programming with C++; RoboCell 3D simulation software (optional); Teach Pendant (optional) |
| Types of Positions | Absolute; Relative; Cartesian; Joint (angle); Encoder |
| Position Feedback | Incremental optical encoders for each axis |
| Coordinate Systems | XYZ coordinates; Joint coordinates |
| LED Indicators | Main power, bicolor: green: power on and communicating with PC; orange: power on and not communicating with PC; flashing: power on and PC USB communications timeout; `8 digital inputs (green)`; `8 digital outputs (orange)`; Motors (green); Emergency (red) |
| Safety Features | Emergency cutoff switches: on Controller-USB; on Teach Pendant; optional external connection for remote switches. Short-circuit protection; On overheating, driver power shutdown; On failure, motor power shutdown; On communication failure, motor power shutdown. Impact, software limit protection. Hardware watchdog for each axis protects against software faults. |

**Self-check [INFERRED]:** 15 item rows on printed p.4, 10 on printed p.5 = 25 total. Maximum eight servo axes is consistent with six robot motors plus two peripheral axes. The eight digital outputs divide into four relays and four open collectors; adding two analog outputs gives the prose total of ten outputs (printed p.21/PDF p.25, Output Terminals and LEDs [TEXT]). Four analog inputs and eight digital inputs match front-panel channels. User supply `0.1A max.` and Appendix A `100mA` are compatible numerical expressions; no unlisted power rail is inferred.

### G2. Every LED/state statement (organizational table)

| LED / number present | Color and state | Meaning / scope | Evidence and full source |
|---|---|---|---|
| POWER (one bicolor indicator) | Green, lit | Power on and communicating with PC; installation also says currently communicating | STATED [TEXT], Controller printed p.5/PDF p.9, Figure 1-4; printed p.10/PDF p.14, Figure 3-2 description; printed p.12/PDF p.16, installation item 11 |
| POWER | Orange, lit | Power on, not communicating with PC | STATED [TEXT], same three sources; printed p.28/PDF p.32, troubleshooting item 2: communication error and orange Power LED |
| POWER | Flashing; flashing color/frequency unspecified | PC-USB timeout. Normal for RoboCell simulation mode; may indicate USB problem online | STATED [TEXT], printed p.5/PDF p.9, Figure 1-4; printed p.10/PDF p.14, Power LED description |
| POWER | Green / flashing in troubleshooting | Item 3 asks for green; with flashing it mentions emergency switch state and CON versus COFF. It does not explicitly redefine flashing as an e-stop indicator | STATED [TEXT], printed p.28/PDF p.32, troubleshooting item 3 |
| POWER | Orange / green in inspection | “orange when the power is ON, and green when power is ON and the software is online” — the green clause qualifies the shorter orange clause | STATED [TEXT], printed p.27/PDF p.31, Inspection item 2 |
| POWER | Off | “Controller-USB power does not turn on. The power LED ... does not light up.” A symptom, not a diagnostic of a particular internal fault | STATED [TEXT], printed p.28/PDF p.32, troubleshooting item 1 |
| MOTORS (one) | Green, lit | Indicates power being supplied to all connected motors; lights after SCORBASE activation and CON | STATED [TEXT], printed p.10/PDF p.14, Figure 3-2 description; printed p.27/PDF p.31, Inspection item 2 |
| MOTORS | Off after COFF | COFF issued | STATED [TEXT], printed p.10/PDF p.14, Motors LED list |
| MOTORS | Off after emergency stop | EMERGENCY button pressed; motor power disconnected | STATED [TEXT], printed p.10/PDF p.14, Motors LED list; printed p.25/PDF p.29, EMERGENCY Button and LED |
| MOTORS | Off after timeout | Controller detects a communication time-out | STATED [TEXT], printed p.11/PDF p.15, Motors LED list continuation |
| MOTORS | Off after over-current | Controller detects an over-current error | STATED [TEXT], same citation |
| MOTORS | Off after software closes | **SCORBASE closes**; not a claim about every PC application | STATED [TEXT], same citation |
| MOTORS | Green, on after e-stop release + CON | The return-to-Control-On choice turns on the green Motors LED | STATED [TEXT], printed p.25/PDF p.29, release paragraph |
| MOTORS | Green required in troubleshooting | Controller cannot activate arm; verify green Motors LED lit | STATED [TEXT], printed p.29/PDF p.33, troubleshooting item 3 continuation; Arm printed p.17/PDF p.23, item 1 |
| EMERGENCY (one) | Red, on | Emergency button/remote stop actuated; emergency LED lights | STATED [TEXT], Controller printed pp.5, 8, 25/PDF pp.9, 12, 29, Figure 1-4 / Emergency Button / EMERGENCY Button and LED |
| EMERGENCY | Red indicator off | Button pulled out or remote emergency switch released | STATED [TEXT], printed p.25/PDF p.29, release paragraph |
| Digital INPUT LEDs 1–8 (eight) | Green, on | Corresponding digital input is on | STATED [TEXT], printed pp.5 and 15/PDF pp.9 and 19, Figure 1-4 / Input Terminals and LEDs; numbering [IMAGE] Figure 3-2, printed p.10/PDF p.14 |
| Digital OUTPUT LEDs 1–8 (eight) | Orange, on | Corresponding digital output is on | STATED [TEXT], printed pp.5 and 21/PDF pp.9 and 25, Figure 1-4 / Output Terminals and LEDs; numbering [IMAGE] Figure 3-2, printed p.10/PDF p.14 |
| Analog input/output LEDs | None specified or depicted | Analog channel counts do not imply LEDs | ABSENT, Controller printed pp.5, 10, 15–24/PDF pp.9, 14, 19–28 scanned; `LED`, `analog`, Figure 3-2 |

**Self-check [INFERRED]:** 19 described physical LEDs = POWER + MOTORS + EMERGENCY + eight input + eight output. Multiple rows above describe the same LED, not additional devices. No manual text identifies a **red POWER** state. Whole-document scan and whole-word `red`, `POWER`, `flashing`, `orange` search: Controller PDF pp.1–38 and Arm PDF pp.1–41; red is explicitly associated with Emergency in the relevant passages. Flashing/no-communication overlap is under-specified; orange and flashing are not supplied as a complete state-transition truth table. Troubleshooting p.28 is broader than the primary p.10 definition, but is not a direct conflicting color assignment.

### G3. States, faults, host behavior (organizational table)

| Event / state | Explicit text | What is implied / missing |
|---|---|---|
| Power-on | POWER LED lights and signals communication status; Motors LED comes on after SCORBASE + CON | **IMPLIED [INFERRED]:** the documented sequence requires control-on to enable motors. **ABSENT:** firmware default state register, autonomous startup behavior, transition timing. Controller printed pp.10–12, 27/PDF pp.14–16, 31, Installation / Inspection [TEXT]; L5 |
| COFF | “Control Off (to disable servo control)” in encoder diagnostic; Motors LED goes out on COFF | **IMPLIED [INFERRED]:** motor power is no longer indicated as supplied, combining LED meaning with its COFF list. **ABSENT:** whether off is coast, electrical braking, shorted winding, mechanical brake, gravity holding, or torque decay. Arm printed pp.18–19/PDF pp.24–25, items 4/6; Controller printed p.10/PDF p.14, Motors LED [TEXT]; L9 |
| CON | Lights Motors LED after software activation; returns to Control On following release | Motor power indicated on; no home-retention or command-packet semantics. Controller printed pp.10, 25/PDF pp.14, 29 [TEXT]; L6/L8 |
| E-stop press | Motor power disconnected; all motor movement stops; COFF; Emergency LED; message on pendant/SCORBASE; program aborted; outputs frozen; all SCORBASE commands including HOME and CON blocked | No stopping time/distance, stop category, braking circuit or home-validity result. Controller printed p.25/PDF p.29, EMERGENCY Button and LED [TEXT]; L6/L9/L14 |
| E-stop release | Emergency LED off; SCORBASE prompts CON to return to Control On or COFF to remain off; CON turns Motors LED on | Re-enable choice explicitly required in described software behavior; raw host protocol and home retention absent. Same citation [TEXT] |
| Communication failure | “On communication failure, motor power shutdown.” | No detection latency, timeout duration, allowed command-stream gap or recovery sequence. Controller printed p.5/PDF p.9, Figure 1-4 [TEXT]; L4 |
| Communication time-out | Motors LED goes out; POWER flashing defined for PC-USB timeout | No numerical timeout, no assertion of immediate unplug response. Controller printed pp.10–11/PDF pp.14–15 [TEXT]; L4/L5 |
| USB unplug mid-motion | No unplug-specific motion description | **IMPLIED [INFERRED]:** loss of the communication link can fall under the communication-failure/time-out shutdown provisions. The manual does not specify what happens before detection, or the exact motion/power sequence. Same sources; ABSENT L5 |
| PC software closing | Motors LED goes out when “SCORBASE closes.” | No statement about arbitrary SDK process crash, OS failure, RoboCell closing, or any other PC software. Controller printed p.11/PDF p.15 [TEXT]; L5 |
| Over-current | Motors LED goes out when controller detects an over-current error | No trip current, peak duration, filtering or re-enable policy; `3A standard; 7A peak` are driver ratings, not declared trip thresholds. Controller printed pp.4, 11/PDF pp.8, 15, Figure 1-4 / LED list [TEXT]; L4 |
| Over-temperature | “On overheating, driver power shutdown” | No temperature threshold, sensing point, hysteresis, fault code or restart behavior. Controller printed p.5/PDF p.9, Figure 1-4 [TEXT]; L4 |
| Short circuit / generic failure | “Short-circuit protection”; “On failure, motor power shutdown” | No detection mechanism, completeness definition, or failure-code list. Same citation [TEXT]; L8/L13 |
| Power loss | Power switching and mains/supply paths are drawn | **ABSENT:** brownout threshold, stop trajectory, braking/holding, retained state, restart policy. Controller printed p.33/PDF p.37, Appendix A [IMAGE]; L11 |

**Impact/limit/position error — exact scope [TEXT]:** printed p.4/PDF p.8 Figure 1-4 lists “Axis position error” and “Impact, software limit protection” under Control Parameters. Printed p.5/PDF p.9 repeats “Impact, software limit protection” under Safety Features. It does **not** define collision-force sensing, current-based impact detection, position-error threshold, following-error time allowance, per-joint soft-limit coordinates, or fault response for each of those protections. Mechanical-limit checks appear in troubleshooting (Controller printed p.29/PDF p.33, item 3; Arm printed p.17/PDF p.23, item 1). These are not a software-limit table. ABSENT under L8/L14.

**Numeric timing/rating boundaries [TEXT][IMAGE]:** `15 kHz`, `3A standard; 7A peak`, `12/24V`, servo `24V`, digital `5V, +15V, -12V`, `1.5 ms control cycle parameter`, and “Ten speed levels” are as transcribed in G1. Arm printed p.11/PDF p.17, Warnings, explicitly contrasts motors rated `12VDC nominal` with controller drivers supplying `24VDC`. This is an intentional rating-versus-supply distinction, not evidence to change motor nominal voltage to 24 V. No command-refresh period, watchdog period, acceleration value, or per-axis speed is given.

**IMPLIED [INFERRED]:** an environmental interval satisfying both published ambient ranges is their intersection: `max(2°C, 10°C) = 10°C`; `min(40°C, 35°C) = 35°C`; hence `10°C–35°C`. Arm printed p.4/PDF p.10 and Controller printed p.4/PDF p.8 specification tables. This is not a new vendor system rating or humidity specification.

### G4. USB / drivers / protocol / faults

**STATED [TEXT]**, Controller printed p.32/PDF p.36, USB Driver Installation: “During software installation, SCORBASE automatically installs a USB driver named ERUSBClass in the computer.” The page discusses a changed/corrupted driver and reinstallation through SCORBASE, Device Manager, and Windows reboot. Driver name is spelled **ERUSBClass**, not inferred as a generic serial/CDC interface. Computer requirements on printed p.11/PDF p.15 list `Windows 98/2000/XP` and a USB port; Figure 1-4 printed p.5/PDF p.9 lists PC user-defined programming with C++.

**STATED [TEXT]:** software command names CON, COFF, HOME appear in Controller printed pp.10–11 and 25/PDF pp.14–15 and 29. Input states are said to be read by SCORBASE commands (printed p.15/PDF p.19), analog values interpreted as `0` to `255` (printed p.21/PDF p.25), and the analog output word mapping is `0–255 ⇔ 0–10 V` (printed p.24/PDF p.28). Emergency/communication/axis error messages and “Homing Complete” are named generically (printed pp.25, 28–29/PDF pp.29, 32–33). None supplies a raw API operation or binary encoding.

**ABSENT, L8:** USB VID/PID, descriptors/enumeration sequence, interface class, endpoints, transfer type, packet framing, checksum, binary/ASCII command bytes, acknowledgement format, command rates, raw register map, error-code numbers, host-readable status-bit layout, fault-query command, and SDK ABI. All Controller PDF pp.1–38 and Arm PDF pp.1–41 scanned, with targeted controller printed pp.1–5, 9–12, 25, 28–34/PDF pp.5–9, 13–16, 29, 32–38. `ERUSBClass` existence and visible SCORBASE messages are not a published USB protocol.

## H. Safety: verbatim warnings, precautions, cautions and notes

These are **vendor quotations for a reading audit**, not an operating checklist. Line wrapping is normalized; spelling, numeric values, capitalization and substance are preserved. The two manuals use Warnings, Precautions, Note/Notes, bold/italic admonitions and pointing-hand callouts. No literal CAUTION-labelled block was found (all pages visually scanned; `caution` searched). All labelled warnings/notes and prominent safety admonitions are inventoried below, including nonhazard notes so that “every NOTE” is not silently narrowed to dangerous items. Ordinary procedural steps are not reclassified as warnings. Repeated notes are retained at each occurrence.

### H1. Motion, guarding, pinch/entanglement, collision and stability

**STATED [TEXT]**, Arm printed p.10/PDF p.16, Precautions (complete introductory paragraph and numbered items):

> This manual provides complete details for proper installation and operation of the SCORBOT-ER 4u. Do not install or operate the robot until you have thoroughly studied this User's Manual. Be sure you heed the safety guidelines for both the robot and the controller.

> 1. Make sure the robot base is properly and securely bolted in place.
> 2. Make sure the robot arm has ample space in which to operate freely.
> 3. Make sure a guardrail, rope or safety screen has been set up around the SCORBOT-ER 4u operating area to protect both the operator and bystanders.
> 4. Do not enter the robot's safety range or touch the robot when the system is in operation. Before approaching the robot, make sure the motor switch on the controller front panel has been shut off.
> 5. Make sure loose hair and clothing is tied back when you work with the robot.

**STATED [TEXT]**, Arm printed p.11/PDF p.17, Warnings (motion/load clauses):

> Do not overload the robot arm. The combined weight of the workload and gripper may not exceed 1kg (2.2 lb). It is recommended that the workload be grasped at its center of gravity.

> Do not use physical force to move or stop any part of the robot arm.

> Do not drive the robot arm into any object or physical obstacle.

**STATED [TEXT]**, Arm printed p.12/PDF p.18, Installation (stability admonition):

> Make sure the robot is securely bolted in place. Otherwise the robot could become unbalanced and topple over while in motion.

**STATED [TEXT]**, Controller printed p.7/PDF p.11, Warnings (peripheral-motion clause):

> Be sure to configure the peripheral axes using SCORBASE before you send a Control-ON command to the peripheral device.

**ABSENT:** no separate pinch-force limit, finger-clearance dimension, stopping-distance formula, brake/holding specification or maximum permitted human proximity is stated; L9/L12/L14. “Pinch” is an organizational hazard heading, not a vendor-supplied quantitative pinch specification.

### H2. Thermal loading, strain and environment

**STATED [TEXT]**, Arm printed p.11/PDF p.17, Warnings (complete environmental warning):

> Do not install or operate the SCORBOT-ER 4u under any of the following conditions:
> - Where the ambient temperature or humidity drops below or exceeds the specified limits.
> - Where exposed to large amounts of dust, dirt, salt, iron powder, or similar substances.
> - Where subject to vibrations or shocks.
> - Where exposed to direct sunlight.
> - Where subject to chemical, oil or water splashes.
> - Where corrosive or flammable gas is present.
> - Where the power line contains voltage spikes, or near any equipment which generates large electrical noises.

**STATED [TEXT]**, same page, remaining strain/thermal warnings:

> Do not leave a loaded arm extended for more than a few minutes.

> Do not leave any of the axes under mechanical strain for any length of time. Especially, do not leave the gripper grasping an object indefinitely.

> Since the SCORBOT-ER 4u motors are rated 12VDC nominal, while the controller motor drivers supply 24VDC, do not drive axes continuously in one direction at maximum speeds.

**STATED [TEXT]**, Controller printed p.7/PDF p.11, Warnings (complete environmental/electrical condition block):

> Do not install or operate Controller-USB under any of the following conditions:
> - Power supply is not grounded
> - Ambient temperature drops below or exceeds the specified limits
> - Exposed to large amounts of dust, dirt, salt, iron powder, or similar substances
> - Subject to vibration or shocks
> - Exposed to direct sunlight
> - Subject to chemical, oil or water splashes
> - Corrosive or flammable gas is present
> - Power line contains spikes
> - Near any equipment that generates large electrical noise.

**ABSENT:** a numeric humidity limit, numeric “few minutes,” motor duty-cycle percentage, maximum gripper hold duration, thermal trip point and derating curve. Arm printed pp.4, 11/PDF pp.10, 17 and Controller printed pp.4–8/PDF pp.8–12, plus whole-document searches `humidity`, `minute`, `duty`, `temperature`, `overheat`, `hold`; L4/L9/L12.

### H3. Electrical supply, connections, I/O and servicing

**STATED [TEXT]**, Controller printed p.7/PDF p.11, Warnings (remaining supply/connection clauses):

> Turn off Controller-USB before you connect any inputs or outputs.

> Turn off Controller-USB before you connect any peripheral devices.

> Do not plug Controller-USB into the AC power outlet before making sure that its voltage requirement (as marked at the rear panel of Controller-USB) matches your voltage supply.
>
> If the voltage setting does not match your supply, follow the instructions for changing the controller's voltage setting in Chapter 6, “Maintenance and Repair”.

**STATED [TEXT]**, Controller printed p.8/PDF p.12, Warnings continuation (all six bullets):

> Do not connect voltage source exceeding +24V to input terminals.

> Do not connect any input or output device that does not meet Controller-USB specifications.

> Never connect voltage from an external power supply directly to any open collector outputs.

> Always connect the open collector outputs to a load that meets Controller-USB specifications.

> Do not exceed current limitations for open collector outputs:
> Sink: 0.5A for each output
> Source: 50 mA for all outputs combined

> Make sure that the voltage supply of a device connected to a relay output does not exceed 24 V.

**STATED [TEXT]**, Controller printed p.11/PDF p.15, Installing Controller-USB, bold admonition:

> Do not connect Controller-USB to the AC power supply yet. Complete Steps 1-5 first.

**STATED [TEXT]**, Controller printed p.15/PDF p.19, Input Terminals and LEDs, complete Note:

> Note: Do not connect DC current from a power supply except as shown in these instructions. Connecting DC power with reversed polarity may damage the electronic circuits and components in Controller-USB, as well as any connected electronic sensors. Do not connect AC power to any input on Controller-USB.

**STATED [TEXT]**, Controller printed p.17/PDF p.21, first Notes block (two bullets):

> Any type of ordinary electrical contact switch is suitable, including a mercury switch, relay or reed switch.

> Before connecting any sensor to any input, be sure Controller-USB is switched OFF.

**STATED [TEXT]**, same page, second Notes block (three bullets):

> Any type of ordinary electrical contact switch is suitable, including a mercury switch, relay or reed switch.

> Connecting DC power with reversed polarity may damage the electronic circuits and components in Controller-USB. Do not connect AC power to any input on Controller-USB.

> Before connecting any sensor to any input, be sure Controller-USB is switched OFF.

**STATED [TEXT]**, Controller printed p.18/PDF p.22, Sink Devices (NPN), complete Notes block (eight bullets, with the associated truth table reproduced in T4):

> Before connecting any sensor to any input, be sure Controller-USB is switched OFF.

> Do not connect voltage exceeding 24 VDC to the digital inputs.

> Connecting DC power with reversed polarity may damage the electronic circuits and components in Controller-USB and external sensors. Do not connect AC power to any input on Controller-USB.

> The Controller-USB inputs are factory pre-set to LOW, which is suitable for NPN sensors (open-collector or sink type).

> For NPN sensors, the voltage and input states are as follows:

> This is the same interconnection scheme as for PNP devices, but the jumper settings on Controller-USB are different.

> You can use the 12 VDC power supply on Controller-USB to power an external electronic sensor. [(17) on Figure 3-2] Be sure to observe the polarity.

> To simulate the operation of an NPN device, you can connect a Controller-USB digital output to a Controller-USB digital input. Set the output jumper to SINK and the input jumper to LOW.

**STATED [TEXT]**, Controller printed p.19/PDF p.23, Source Devices (PNP), Notes (four bullets):

> Before connecting any sensor to any input, be sure Controller-USB is switched OFF.

> Do not connect voltage exceeding +24 VDC to the digital inputs.

> Connecting DC power with reversed polarity may damage the electronic circuits and components in Controller-USB and external sensors. Do not connect AC power to any input on Controller-USB.

> The Controller-USB inputs are factory pre-set to LOW, which is suitable for NPN sensors (Open-collector or sink type). To use PNP sensors (open emitter type), change the input jumper to HIGH, as described in Chapter 6, particularly Figure 6-2: I/O Jumpers.

**STATED [TEXT]**, Controller printed p.20/PDF p.24, same Notes continuation (four bullets; associated truth table in T4):

> For PNP sensors, the voltage and input states are as follows:

> This is the same interconnection scheme as for NPN devices, but the jumper settings on Controller-USB are different.

> You can use the 12 VDC power supply on the controller to power an external electronic sensor. [(17) on Figure 3-2] Be sure to observe the polarity.

> To simulate the operation of a PNP device, you can connect a Controller-USB digital output to a Controller-USB digital input. Set the output jumper to SOURCE and the input jumper to HIGH.

**STATED [TEXT]**, Controller printed p.21/PDF p.25, Analog Inputs, complete Notes (five bullets):

> Before connecting any sensor to any input, make sure Controller-USB is switched off.

> Do not connect voltage exceeding +10 VDC to the analog inputs. Note that this is significantly lower than the limit for digital inputs.

> Connecting DC power with reversed polarity may damage the electronic circuits and components in Controller-USB and external sensors. Do not connect AC power to any input on Controller-USB.

> The voltage from 0 to 10 VDC is interpreted as an 8-bit binary number between 0 and 255.

> To power the external analog sensor, you can use the 12 VDC power supply on the controller. [(17) on Figure 3-2] Be sure to observe the polarity, and be sure to protect against accidentally providing more than 10V to the analog sensor input.

**STATED [TEXT]**, Controller printed p.23/PDF p.27, Open Collector Outputs, precaution in prose:

> When using an inductive load such as a solenoid or a relay, connect a reverse-biased protection diode across the load. See Device B as illustrated in Figure 4-10. Omitting this precaution may damage electronic circuits or components in Controller-USB.

> Never connect voltage from a power supply directly to an open collector output (terminals 5-8). The open collector outputs must always be connected to a load of the following rating:
> Power supply voltage: 15 VDC max.
> Maximum current: 0.5 A each; 2.0 A for all open collector outputs combined

**STATED [TEXT]**, same page, complete Notes (two bullets):

> The open-collector outputs are factory pre-set to SINK. To change the setting to SOURCE, refer to Chapter 6, particularly Figure 6-2: I/O Jumpers.

> Open-collector outputs may be connected to digital inputs on controller-USB.

**STATED [TEXT]**, Controller printed p.24/PDF p.28, complete Notes (three bullets):

> The open-collector outputs are factory preset to SINK. To change the setting to SOURCE, refer to Chapter 6, particularly Figure 6-2: I/O Jumpers.

> Do not connect a digital output (SOURCE) to an analog input, as the 15 V provided by the digital output exceeds the maximum 10 V permitted for an analog input.

> A digital output (SOURCE) may be connected to a digital input, as long as the digital input is set to HIGH. To change the input setting to HIGH, refer to Chapter 6, particularly Figure 6-2: I/O Jumpers.

**STATED [TEXT]**, Arm printed p.17/PDF p.23, Troubleshooting, pointing-hand admonition and servicing paragraph:

> The procedures in the section are intended only for technicians who have received proper training and certification from the manufacturer.
>
> Do not attempt to perform procedures for which you are not qualified.

> Do not open the controller. There are no user-serviceable parts inside. Do not attempt repairs for which you are not qualified. Contact your agent or an authorized technician for repairs.

**STATED [TEXT]**, Arm printed p.21/PDF p.27, Adjustments and Repairs, pointing-hand admonition:

> These procedures are to be performed only by a qualified technician who has received proper training and certification from the manufacturer.

**STATED [TEXT]**, Arm printed p.16/PDF p.22, Periodic Inspection (qualified-technician restrictions):

> Qualified Technician Only: Tighten the belts only if you are absolutely certain they are slipping or retarding the motors. For complete information, refer to the section, “Adjustments and Repairs,” later in this chapter.

> Qualified Technician Only: Check for excessive backlash in the base axis. For complete information, refer to the section, “Adjustments and Repairs,” later in this chapter.

The standalone label “Qualified Technician Only” recurs at Arm printed pp.17, 19–23/PDF pp.23, 25–29, troubleshooting items 2, 5–9 and adjustment subsections [TEXT][IMAGE]. These repeated labels restrict the described diagnostics/repairs; they do not give authorization to perform them.

**STATED [TEXT]**, Controller printed p.28/PDF p.32, Troubleshooting, bold servicing restriction:

> Do not open Controller-USB (except to change jumper setting).
>
> There are no user-serviceable parts inside. Do not attempt internal repair procedures. Contact your agent or dealer.

**STATED [TEXT]**, Controller printed p.30/PDF p.34, Opening the Controller:

> There are no user-serviceable parts inside. Do not attempt internal repair procedures. Contact your agent or dealer.

> You may open Controller-USB only when you need to change I/O jumper settings:

**STATED [TEXT]**, Controller printed p.31/PDF p.35, Changing the Voltage Setting, supply admonition:

> Make sure Controller-USB is not connected to an AC outlet while you change the voltage setting.

**STATED [TEXT]**, Controller printed p.28/PDF p.32, troubleshooting item 1, immediate-disconnection admonition:

> If the voltage supply and Controller-USB voltage setting do not match, disconnect immediately, and change the voltage setting, as described later in this chapter. (Switch 3 on Figure 3-1).

### H4. Handling, shipping and pre-use reading

**STATED [TEXT]**, Arm unnumbered copyright page/PDF p.3:

> Read this manual thoroughly before attempting to install or operate the equipment.
> If you have any problems during installation or operation, call your agent for assistance.

**STATED [TEXT]**, Arm printed p.2/PDF p.8, Acceptance Inspection:

> After removing the robot arm from its shipping cartons, examine it for signs of shipping damage. If any damage is evident, do not install or operate the system. Notify your freight carrier and begin appropriate claims procedures.

**STATED [TEXT]**, Arm printed p.3/PDF p.9, Repacking for Shipment, pointing-hand note / emphasized shipping caveat:

> The robot should be repacked in its original packaging for transport.

> Seal the carton with sealing or strapping tape. Do not use cellophane or masking tape.

**STATED [TEXT]**, same page, Handling Instructions (complete):

> Lift and carry the robot arm only by grasping the body or the base.
> See Figure 2.
> Do not lift and/or carry the robot arm by its gripper, upper arm or forearm.
> Do not touch the microswitches, cams or encoders.

**STATED [TEXT]**, Arm printed p.10/PDF p.16, pointing-hand callout:

> Read this chapter carefully before you attempt to install or use the robot system.

**STATED [TEXT]**, Controller printed p.7/PDF p.11, Handling Controller-USB (complete):

> Do not hold Controller-USB by either the front or rear panels.
> Make sure that all cables are disconnected before moving Controller-USB.

**STATED [TEXT]**, same page, Warnings, first bullet:

> Do not operate Controller-USB until you have studied this manual thoroughly.

**STATED [TEXT]**, Controller printed p.3/PDF p.7, Inspection and Acceptance:

> After removing Controller-USB from the shipping carton, examine all components for signs of shipping damage. If any damage is evident, do not install or operate Controller-USB. Notify your freight carrier and begin appropriate claims procedures.

> Be sure to check the packing list for the robot and peripherals as well.

**STATED [TEXT]**, same page, Repackaging Controller-USB, emphasized paragraph:

> Save the packing materials and shipping carton. You may need them later for shipment or storage of Controller-USB.

### H5. Emergency stop: authoritative wording and limits

**STATED [TEXT]**, Arm printed p.10/PDF p.16, pointing-hand callout:

> To immediately abort all running programs and stop all axes of motion, press the EMERGENCY STOP button on either the teach pendant or the controller.

**STATED [TEXT]**, Controller printed p.8/PDF p.12, Emergency Button:

> Pressing the Emergency button disconnects the power signals to the robot and Axes 7 and 8, and halts the SCORBASE program. The digital and analog outputs freeze their status. The red LED indicator comes ON.

**STATED [TEXT]**, Controller printed p.25/PDF p.29, complete stop-effect bullets:

> Motor power is disconnected; all motor movement stops and the Motors LED turns off.
> COFF (control off) state is activated.
> Emergency LED lights up.
> An emergency message is displayed on the teach pendant and in SCORBASE.
> Program is aborted.
> Controller-USB outputs freeze in their current state.
> All SCORBASE commands, including HOME and CON cannot be activated.

**STATED [TEXT]**, same page, complete Note:

> Pressing the Emergency button will not stop the operation of a remote output device. To stop the output device, use its own Emergency Stop button.

The named authoritative emergency mechanisms are the controller button, teach-pendant cutoff switch, and remote emergency switch. Controller Figure 1-4, printed p.5/PDF p.9, calls them “Emergency cutoff switches”; printed pp.13 and 25/PDF pp.17 and 29 says the remote emergency switch functions exactly like the controller button [TEXT]. The manuals do not claim a certified safety category, performance level, redundancy, stop-time bound or safety-rated host software. COFF is a documented software state, and timeout/overheat/failure shutdown are listed protective behaviors; these are not declared replacements for the emergency switches. L13/L14.

### H6. Nonhazard and mechanical notes, included for completeness

**STATED [TEXT]**, Arm printed p.23/PDF p.29, Gripper Disassembly, complete Note:

> Note: When tightening the coupling piece to the motor output shaft, make sure the coupling is 1.5mm to 2mm away from the plate (112).

**STATED [TEXT]**, Arm printed p.24/PDF p.30, Chapter 7 introduction: “Note that the SCORBOT-ER 4u robot arm has several enhanced features which do not appear in these drawings. They are:” followed by the three full change statements quoted in A2. This whole note is reproduced there to avoid changing the version caveat.

**STATED [TEXT]**, Arm printed p.33/PDF p.39, wiring note:

> (* indicates two wires on same pin.)

**STATED [TEXT]**, Controller printed p.31/PDF p.35, complete Note:

> Inputs are factory pre-set to LOW.
> Outputs are factory pre-set to SINK.

Inline figure-omission notes: **STATED [TEXT]**, Controller printed p.18/PDF p.22 (NPN) and p.19/PDF p.23 (PNP): “Note that the sensor input terminal is not shown, as each electronic sensor has its own design.” Printed p.20/PDF p.24 (analog): “Note that the sensor input terminal is not shown, as each electronic sensor has its own design.” Arm printed p.22/PDF p.28 includes: “Note the small unused hole on the base plate near the gears (22 and 27). It will enable you to lock the gear (22) in the next step.” These are drawing/procedure notes, not omitted circuit specifications.

**STATED [TEXT]**, Arm printed p.14/PDF p.20, emphasized nonhazard statement: “The teach pendant is an optional device.” Controller printed p.12/PDF p.16, emphasized connection note: “If you are connecting only one peripheral, use the AXIS 7 connector.” Controller printed p.11/PDF p.15, emphasized legend note: “The numbers in parentheses refer to Figures 3-1 and 3-2.” They do not provide new motion-safety limits.

## I. Known unknowns

This is an organizational table. A mixed entry keeps its explicit, inferred and absent subclaims separate. Search-log IDs specify the scope supporting absence; absence never means that a feature is absent from the hardware.

| Requested item | STATED | IMPLIED | ABSENT / evidence scope |
|---|---|---|---|
| 1. Counts per motor revolution / joint degree; decode mode | `20 slots`; two channels P0/P1; quarter-cycle phase; incremental optical feedback. Arm printed pp.19, 24, 28/PDF pp.25, 30, 34, Figure 15 / Chapter 7 / disk parts [TEXT][IMAGE]; Controller printed p.5/PDF p.9, Figure 1-4 [IMAGE] | Direction-sensitive signals, from sequence and phase; Arm printed pp.7, 19/PDF pp.13, 25 [INFERRED]. This is insufficient to choose an edge multiplier | Motor counts/revolution, 1x/2x/4x decode, joint counts/degree: ABSENT L1. Arithmetic withheld because the decoder and complete gear chain are unspecified and one reduction conflicts |
| 2. Joint zero / home pose degrees; positive directions | Axis travel signs and descriptive motion names only; Arm printed pp.4–5/PDF pp.10–11, specification and motion tables [IMAGE] | No positive-direction mapping can be deduced from bidirectional arrows | Absolute home angles, zero definition and per-joint positive physical direction: ABSENT L2. Figure 2-4's arrows have no signs |
| 3. Home search direction, back-off, switch logic, axis order | One axis at a time; switch activates, then axis moves slightly until switch shuts off. Arm printed p.8/PDF p.14, Microswitches [TEXT]. Wiring and depressed-contact test: printed pp.17–18, 34/PDF pp.23–24, 40 [TEXT][IMAGE] | Contact closure on depression and ground-referenced wiring; reasoning in E1 [INFERRED]. Neither proves an active-low controller threshold | Search direction, actual reversal, numerical back-off, speed, axis order, released NO/NC state, electrical controller threshold/logic polarity: ABSENT L3/L13 |
| 4. Communication timeout and watchdog reset | Communication-failure motor-power shutdown; hardware watchdog for each axis against software faults. Controller printed p.5/PDF p.9, Figure 1-4 [TEXT] | A communication interruption may be detected as a timeout; no timing can be derived | Duration, watchdog period, reset event, valid-packet definition, keepalive and recovery: ABSENT L4 |
| 5. USB unplug mid-motion | General communication-failure shutdown and timeout LED behavior; Controller printed pp.5, 10–11/PDF pp.9, 14–15 [TEXT] | Unplug can remove communication, making the general provisions relevant [INFERRED]; no claim of immediate shutdown | Unplug-specific sequence, motion before detection, latency, host crash/OS failure behavior: ABSENT L5 |
| 6. COFF/CON or e-stop losing home | COFF disables servo control; encoder readings can change in the COFF diagnostic. Arm printed pp.18–19/PDF pp.24–25, items 4/6 [TEXT]. E-stop enters COFF, release offers CON; Controller printed p.25/PDF p.29 [TEXT] | Continued encoder reading in that diagnostic does not establish retained reference validity | Retention/erasure through COFF/CON, e-stop, reconnect or power cycle: ABSENT L6 |
| 7. Ten-level speed table; per-axis limit | “Ten speed levels are available”; arm maximum path velocity `600 mm/sec (23.6"/sec)`. Controller printed p.4/PDF p.8, Figure 1-4; Arm printed p.4/PDF p.10, specifications [IMAGE] | Path velocity is not a per-axis angular speed | Level-to-speed mapping, per-axis angular limits and acceleration/deceleration bounds: ABSENT L7 |
| 8. Host-readable errors | Communication/axis error messages; emergency message; Homing Complete message; ERUSBClass driver; CON/COFF/HOME names. Controller printed pp.25, 28–29, 32/PDF pp.29, 32–33, 36 [TEXT] | The described software can present some fault messages; this does not define a host query protocol | Error-code numbers, status-bit layout, fault-read command, USB packets/descriptors: ABSENT L8 |
| 9. Brake / holding with motors off; can arm fall? | Motor power disconnect and movement-stop statements for e-stop. Controller printed p.25/PDF p.29 [TEXT] | No gravity or sustained holding conclusion follows from the stop sentence | Mechanical/electrical brake, coast mode, residual torque, gravity descent or holding guarantee: ABSENT L9 |
| 10. Wrist differential ratio and motor 4/5 signs | Opposite motor directions give pitch; same give roll. Motor reductions `65.5:1`. Arm printed pp.4, 9, 28/PDF pp.10, 15, 34, specifications / Gripper / S311 [TEXT][IMAGE] | Qualitative motor-direction relationship only | Differential ratio, signed motor-to-joint matrix, positive motor convention and viewing direction for CW/CCW: ABSENT L10 |
| 11. Gripper force / stroke counts / hold time | Maximum opening `75 mm (3")` without pads / `65 mm (2.6")` with pads; encoder and size measurement; motor 6 reduction `19.5:1`. Arm printed pp.4, 9/PDF pp.10, 15 [TEXT][IMAGE] | Opening is a linear dimension, not a count stroke or force specification | Force, count stroke, lead-screw pitch, grasp-force calibration, hold-time/duty-cycle limit: ABSENT L12. General thermal warning is present, but not a numerical gripper hold limit |
| 12. Controller response to power loss | Mains switch, fuses, supply rails, power LEDs; Controller printed pp.4, 9, 33/PDF pp.8, 13, 37 [IMAGE] | The drawing describes power paths, not a controlled stop sequence | Brownout threshold, power-loss stop/holding, stored-home persistence, restart policy: ABSENT L11 |

## J. Verification of supplied repository claims

No repository was supplied or inspected. These verdicts evaluate the exact claims in the request against these PDF copies. “CONFIRMED” means supported as a manual statement, not independently verified machine behavior. Long combined claims are split so that a correct number cannot conceal a missing sign or conflicting ratio.

| Supplied claim | Verdict | Quote / literal value and citation |
|---|---|---|
| Controller: “On communication failure, motor power shutdown.” | **CONFIRMED** | Exact sentence. STATED [TEXT], Controller printed p.5/PDF p.9, Figure 1-4, Safety Features |
| MOTORS off on COFF, emergency stop, timeout, over-current, or PC software closing | **PARTLY** | “COFF (control off) command is issued”; “EMERGENCY button is pressed”; “Controller-USB detects a communication time-out”; “Controller-USB detects an over-current error”; “SCORBASE closes.” First four confirmed; last is specifically SCORBASE, not every PC program. STATED [TEXT], Controller printed pp.10–11/PDF pp.14–15, Motors LED list |
| POWER green = powered/communicating; orange = powered/not communicating; flashing = PC-USB timeout | **CONFIRMED** | “Green: The controller is turned on and is currently communicating with the PC”; “Orange: ... currently not communicating”; “Flashing: ... PC-USB timeout.” STATED [TEXT], Controller printed p.10/PDF p.14, Power LED description; p.5/PDF p.9, Figure 1-4. Flash color/frequency is not specified |
| After e-stop release, motors remain off until new control-on | **CONFIRMED** for described SCORBASE behavior | “A message appears on the SCORBASE screen, prompting you select CON (control on) to return to the Control On state, or COFF (control off) to remain in the Control Off state.” STATED [TEXT], Controller printed p.25/PDF p.29, release paragraph. No raw packet sequence supplied |
| Arm base `310 deg` | **CONFIRMED** | `310°`, no ±. STATED [IMAGE], Arm printed p.4/PDF p.10, Axis Movement cell |
| Shoulder `+130/-35` | **CONFIRMED** | `+130° / −35°`, same source |
| Elbow `+/-130` | **CONFIRMED** | `±130°`, same source |
| Wrist pitch `+/-130` | **CONFIRMED** | `±130°`, same source |
| Roll unlimited mechanically, `+/-570 deg` electrically | **CONFIRMED** | `Unlimited (mechanically); ±570° (electrically)`, same source |
| Radius `610 mm` | **CONFIRMED** metric | `610 mm (24.4")` in table; `610mm (24")` in Figure 5. STATED [IMAGE], Arm printed pp.4, 6/PDF pp.10, 12; imperial values conflict |
| Payload `1 kg` including gripper | **CONFIRMED** | `1 kg (2.2 lb), including gripper`. STATED [IMAGE], Arm printed p.4/PDF p.10, Maximum Payload |
| Repeatability `0.18 mm` | **PARTLY** | `±0.18 mm (0.007") at TCP (tip of gripper)`. Sign and location qualification are missing from the claim. STATED [IMAGE], same table |
| Weight `10.8 kg` | **CONFIRMED** | `10.8 kg (23.8 lb)`, same table |
| Path velocity `600 mm/s` | **CONFIRMED** | Original notation `600 mm/sec (23.6"/sec)`, same table; `mm/s` is an explicitly equivalent spelling, not a changed magnitude |
| Motors 1–3 gear ratio `127.1` | **PARTLY** / unresolved | `127.1:1` in specification; S309/S310 `127.7:1` in parts list. Both readable. STATED [IMAGE], Arm printed pp.4, 28/PDF pp.10, 34, Gear Ratios / parts table |
| Motors 4–5 ratio `65.5` | **CONFIRMED** | `65.5:1`, table and S311 agree. STATED [IMAGE], same pages |
| Gripper ratio `19.5` | **CONFIRMED** as motor-6 specification | `Motor 6 (gripper) 19.5:1`. STATED [IMAGE], Arm printed p.4/PDF p.10. S312 parts row gives no ratio; complete jaw linear conversion remains absent |
| Links `220 mm` each; shoulder axis `364 mm` above base bottom | **CONFIRMED** | Two `220mm (8.66")` labels; `364mm (14.33")` extension from base-bottom datum to shoulder center. STATED [IMAGE], Arm printed p.6/PDF p.12, Figure 6 |
| Each axis activates its home switch then moves slightly until it turns off, defining home | **CONFIRMED** | “Each axis is moved until the its home switch is activated. The axis is then moved slightly until the the switch shuts off—at that point the joint is at home.” STATED [TEXT], Arm printed p.8/PDF p.14, Microswitches. Source typos preserved; no reversal, distance or order is stated |
| Wrist opposite motors 4/5 = pitch; same = roll | **CONFIRMED** | “When motors 4 and 5 are driven in opposite directions, the wrist pitch moves up and down. When motors 4 and 5 are driven in the same direction, the wrist rolls clockwise and counterclockwise.” STATED [TEXT], Arm printed p.9/PDF p.15, Gripper |
| Encoder disk has `20` slots; neither manual gives counts/revolution | **CONFIRMED** within these supplied copies | “The encoder disk has 20 slots”; disk 414/429 both “(20 slots)”. STATED [TEXT][IMAGE], Arm printed pp.24, 28/PDF pp.30, 34, Chapter 7 / parts table. Counts/revolution **NOT FOUND**, L1; full visual and text audit finds no decoder multiplier or numerical revolution count. The suspicion that the second half is wrong is not supported by these PDFs |

**Self-check [INFERRED]:** all supplied claim groups are represented. No unsigned arm limit has been silently converted to a signed home coordinate; no slots-to-counts multiplier was invented. A NOT FOUND result for a numerical count is not a verdict that the device does not count.

## Contradictions

This organizational table distinguishes a direct contradiction from an unexplained difference, a version caveat, or a contextual qualification. Those distinctions prevent an apparent discrepancy from becoming an unjustified engineering correction.

| Item / classification | First statement | Second statement | Resolution / implication |
|---|---|---|---|
| Arm motor reduction — **direct numeric conflict** | `127.1:1`, motors 1–3; Arm printed p.4/PDF p.10, specifications [IMAGE] | `127.7:1`, S309 base and S310 shoulder/elbow; Arm printed p.28/PDF p.34, parts [IMAGE] | Unresolved. No authoritative correction or version mapping; neither is a uniquely verified physical constant |
| Reach imperial equivalent — **direct numeric conflict** | `610 mm (24.4")`; Arm printed p.4/PDF p.10, table [IMAGE] | `610mm (24")`; Arm printed p.6/PDF p.12, Figure 5 [IMAGE] | Metric agrees. Preserve both imperial values; no silent conversion |
| Belt deflection — **direct numeric conflict** | `2 mm (0.08")`; Arm printed p.16/PDF p.22, Periodic Inspection and p.21/PDF p.27, Adjusting Timing Belts [TEXT] | `2 mm` / `0.80"`; Arm printed p.16/PDF p.22, Figure 14 [IMAGE] | Figure's inch number disagrees with both prose occurrences; magnitude selection unresolved as documentary evidence, even though the metric repeats |
| Base dimension — **unexplained difference** | `230mm (9")` side-view base width; Arm printed p.6/PDF p.12, Figure 6 [IMAGE] | `∅ 240 mm (9.49")` base diameter; Arm printed p.12/PDF p.18, Installation [TEXT] | No drawing explains whether different features are dimensioned. Do not treat as a proven same-feature conflict or infer a corrected footprint |
| Encoder pad mapping — **asymmetry** | Body P1 pad4/P0 pad3; D9 P1 pad4/P0 pad3; Arm printed pp.34–35/PDF p.40–41, wiring [IMAGE] | Gripper P1 pad3/P0 pad4; Arm printed p.34/PDF p.40, gripper encoder rows [IMAGE] | Different assembly may explain it; supplied drawings do not. Needs connector/board verification, not speculative rewiring |
| Gripper VLED lead color — **asymmetry** | Axes1–5 D50 VLED yellow; Arm printed p.34/PDF p.40, table [IMAGE] | Gripper D50 VLED white; Molex lead yellow, same table [IMAGE] | Explicit different lead segments/colors; no pin collision |
| Motors switch location — **cross-manual naming mismatch** | “MOTORS switch on the controller front panel”; Arm printed p.10/PDF p.16, Precautions [TEXT] | Controller front shows MOTORS LED, Emergency button and I/O; no separately labelled MOTORS switch. Controller printed p.10/PDF p.14, Figure 3-2 [IMAGE] | A distinct front motor switch is not documented in the controller manual; do not equate LED with switch |
| Motors LED after power-on — **cross-manual sequence ambiguity** | Daily item1: after power, “The power and motor LEDs light up”; Arm printed p.15/PDF p.21, Daily Operation [TEXT] | Motors LED lights after SCORBASE activation and CON; Controller printed p.10/PDF p.14 and p.27/PDF p.31 [TEXT] | Arm's abbreviated daily statement does not establish the controller's raw startup state |
| No force on axes / encoder check — **contextual tension** | “Do not use physical force to move or stop any part of the robot arm”; Arm printed p.11/PDF p.17, Warnings [TEXT] | Diagnostic physical movement under COFF; Arm printed pp.18–19/PDF p.24–25, items4/6 [TEXT] | Qualified-maintenance context is present; no quantified permissible force. This report does not turn the diagnostic into instructions |
| Home coverage — **missing mechanism** | Five arm switches, one per axis; gripper switch cells “no connection”; Arm printed p.8/PDF p.14 and p.34/PDF p.40 [TEXT][IMAGE] | Inspection says all five axes, gripper and peripherals home; Controller printed p.28/PDF p.32 [TEXT] | Broad result versus documented switch wiring; gripper/peripheral home method remains absent |
| POWER flashing — **troubleshooting context** | Flashing defined as PC-USB timeout; Controller printed p.10/PDF p.14 [TEXT] | Flashing remedy mentions emergency switch and CON/COFF; Controller printed p.28/PDF p.32, item3 [TEXT] | No direct new flashing-color assignment. Trouble chart does not define a full state machine |
| Motor nominal / driver supply — **explicit distinction, not conflict** | `12VDC nominal` motors; Arm printed p.11/PDF p.17 [TEXT] | Same paragraph says drivers `24VDC`; Controller printed p.4/PDF p.8 gives `12/24V` drivers and `24V` servo, depending on input/load [IMAGE] | Ratings are for different things with explicit qualifiers; no continuous-current or duty-cycle conclusion |
| Ambient temperatures — **different component ratings** | Arm `2°–40°C`; Arm printed p.4/PDF p.10 [IMAGE] | Controller `10°–35°C`; Controller printed p.4/PDF p.8 [IMAGE] | Not contradiction. Interval satisfying both is inferred in G3, not a published combined rating |
| Safety chapter reference — **wrong reference** | Installation refers to safety instructions in “Chapter 1”; Arm printed p.12/PDF p.18 [TEXT] | Safety is Chapter3 pp.10–11/PDF p.16–17; Chapter1 is general information [TEXT][IMAGE] | Preserve incorrect reference; actual safety material located |
| Belt figure reference — **wrong / nonexistent figure number** | “Figure 8-1”; Arm printed p.21/PDF p.27, belt text [TEXT] | Deflection drawing is Figure14 at p.16/PDF p.22; no Figure8-1 in this copy [IMAGE] | Broken reference; do not invent a missing drawing |
| Structure figure numbering — **reference mismatch** | Prose Figures3 and4; Arm printed p.5/PDF p.11 [TEXT] | Captions Figure 2-3 andFigure 2-4, same page [IMAGE] | Same visible illustrations, inconsistent numbering |
| Anti-backlash part S25 — **part-reference conflict** | Text calls a spur gear “S25”; Arm printed p.22/PDF p.28 [TEXT] | Parts S25 = “Ball bearing”; spur gear item28 and Figure22 labels differ. Arm printed p.25/PDF p.31 table / p.31/PDF p.37 Figure22 [IMAGE] | Text part ID cannot be trusted as a gear identifier |
| Duplicate S270 — **parts-table conflict** | S270 catalog320704 “Needle bearing ∅ 15 x ∅ 21 x 12”; Arm printed p.27/PDF p.33 [IMAGE] | S270 catalog320705 “Bushing for #320704”, same table [IMAGE] | One drawing number maps to two catalog parts/descriptions; not resolved |
| Catalog306602 — **parts-table conflict** | S26: `#1/4-20 x 1`; Arm printed p.25/PDF p.31 [IMAGE] | S27: `#1/4-20 x 5/8`, same table [IMAGE] | Same catalog ID with different dimensions; exact final fastener needs vendor verification |
| Catalog306201 — **parts-table conflict** | S6 socket head `#6-32 X 1/4`; Arm printed p.25/PDF p.31 [IMAGE] | S81 flat head `#8-32 x 3/8`; Arm printed p.26/PDF p.32 [IMAGE] | Same catalog ID with different screw type and size |
| Older parts still drawn — **explicit version caveat** | Items40/113/S234 “not used in ER4u”; Arm printed pp.25–27/PDF p.31–33 [IMAGE] | Figures20–23 still depict older components; Arm printed pp.29–32/PDF p.35–38 [IMAGE] | Chapter7 p.24/PDF p.30 explicitly says enhanced features not reflected in drawings; parts presence in drawing is not proof of installed hardware |
| Unlisted drawing callouts — **incomplete cross-reference** | Examples39 (Figure23),65 (Figure21),S241 (Figure22); Arm printed pp.30–32/PDF p.36–38 [IMAGE] | No corresponding Dwg# row in parts tables pp.25–28/PDF p.31–34 [IMAGE] | Identifiable callouts lack list entries; cannot infer catalog or dimensions |
| Controller schematic heading — **index mismatch** | Contents “7. Schematic Diagram”; Controller unnumbered PDF p.3 [TEXT] | Actual heading “Appendix A Schematic Diagram”; p.33/PDF p.37 [IMAGE] | One drawing, inconsistent organizational label |
| Controller robot identifiers — **unmapped identifiers** | Title “For Robots #000413, #000418, #000420”; Controller printed p.33/PDF p.37, Appendix A [IMAGE] | Blocks #020040 ER4u, #020030 ER2u, same drawing [IMAGE] | No map between ID systems; not proven inconsistent products |
| General input-voltage warning — **specific limit qualifies general wording** | General controller warning says no voltage above24V to inputs; p.8/PDF p.12 [TEXT] | Analog inputs explicitly limited10V; pp.5,20–21/PDF p.9,24–25 [TEXT][IMAGE] | General warning must not be treated as permission for24V on analog inputs |

No red-POWER statement, a conflicting D50 powered-pin assignment, or a second numerical counts/revolution specification was found. Search logs L1, L8 and L13 and G2 give the scope of these negative findings.

## Needs a human to look at the page

These are concrete unresolved readings or hardware/drawing relationships. A higher-quality original or vendor answer may be needed; a human view of the same supplied page cannot recover a circuit that is absent.

| Manual; printed page; PDF page | Figure/table | What to check | Why not resolved here |
|---|---|---|---|
| Arm printed p.27; PDF p.33 | Parts table, S234 | `Nylon washer ∅ 11 x ? 4` and unit/glyph after x | The embedded glyph before4 is malformed/ambiguous in both selectable text and rendered image. [UNREADABLE] for that glyph only; row is marked `?` |
| Arm printed p.12; PDF p.18 | Figure13 | Complete imperial suffix on hole diameter | Right-edge figure clips the suffix. [UNREADABLE] in drawing; prose independently reads `∅8.5mm (0.33")` |
| Arm printed p.4/p.28; PDF p.10/34 | Specs / S309–S310 | Physical installed reduction:127.1 or127.7 | Both clear; documentary contradiction requires model-specific vendor or component evidence |
| Arm printed p.6/p.12; PDF p.12/18 | Figure6 / installation | Whether230mm width and240mm diameter dimension distinct base features | No common datum/detail explains relationship |
| Arm printed pp.34–35; PDF p.40–41 | Gripper / D9 encoder tables | PC510 gripper P0/P1 pad numbering | Both tables readable, but board/assembly orientation and pad variant are not documented |
| Arm printed p.33; PDF p.39 | Figure24 | Mating-face versus solder-side D50 orientation | View designation absent; mirror interpretation cannot be resolved from outline |
| Arm printed p.35; PDF p.41 | Figures25–26 / D9 table | D9 physical orientation and optional-switch star notation | No numbered face view; no separate explanatory star footnote on page |
| Arm printed pp.25–32; PDF p.31–38 | Parts / Figures20–23 | S270, catalog306602/306201, missing callout entries and installed enhanced parts | Identifiers conflict or are absent; p.24 warns drawings omit changes |
| Controller printed p.33; PDF p.37 | Appendix A | Board-level encoder, motor and emergency-stop circuits | This is only a block connection drawing; component circuits are absent, not unreadable |
| Controller printed p.10/p.28; PDF p.14/32 | POWER description / trouble item3 | Exact flashing color/state transitions and why e-stop/CON is mentioned | Wording does not supply an exhaustive LED truth table |

The formerly blank extraction pages do **not** require a human to recover hidden data: Arm PDF p.10 is readable; Controller PDF p.4 is genuinely blank and PDF p.10/18/30/38 contain only footers. These were inspected as images.

## Complete table supplements

### T1. Every arm parts-list cell

Each table below is **STATED [IMAGE]**, Arm, Chapter 7 Parts Lists, at the printed/PDF page in its caption. `—` denotes an empty source cell. Only S234 contains a deliberately uncertain glyph; no unit is supplied for most hardware dimensions. The full descriptions retain their original units or lack of units.

#### T1-25. Arm printed p.25, PDF p.31

| Dwg# | Cat# | Description |
|---|---|---|
| 1 | 113012 | Bearing housing cover (plastic) |
| 2 | 111401 | Main shaft base |
| S 2 | 306003 | Socket head cap screw #4-40 X 1/4 |
| S 3 | 306004 | Socket head cap screw #4-40 X 3/8 |
| 4 | 113004 | Base plate |
| 5 | 113001 | Base |
| S 6 | 306201 | Socket head cap screw #6-32 X 1/4 |
| S 8 | 306002 | Socket head cap screw #2-56 x 3/8 |
| 11 | 111906 | Spur gear (120 teeth) |
| S 11 | 306204 | Socket head cap screw #8-32 x 1/4 |
| 12 | 112103 | Bottom Plate - shoulder |
| S 12 | 301205 | Socket head cap screw #8-32 x 3/8 |
| S 13 | 306206 | Socket head cap screw #8-32 x 1/2 |
| S 14 | 306207 | Socket head cap screw #8-32 x 5/8 |
| 15 | 112401 | Support base - motors 4+5 |
| 16 | 112403 | Support clamp - motors 4+5 |
| 17 | 110205 | Right side plate - shoulder |
| 18 | 110210 | Left side plate - shoulder |
| S 18 | 306401 | Socket head cap screw #10-32 x 3/8 |
| S 19 | 306402 | Socket head cap screw #10-32 x 1/2 |
| 20 | 111901 | Anti-backlash spur gear (transfer) |
| S 20 | 306404 | Socket head cap screw #10-32 x 3/4 |
| S 21 | 306405 | Socket head cap screw #10-32 x 7/8 |
| 22 | 111902 | Anti-backlash spur gear (upper) |
| S 22 | 306407 | Socket head cap screw #10-32 x 1/4 |
| 23 | 113501 | Anti-backlash spring |
| S 23 | 306403 | Socket head cap screw #10-32 x 5/8 |
| 24 | 107003 | Washer |
| S 24 | 306408 | Socket head cap screw #10-32 x 1 1/2 |
| S 25 | 321001 | Ball bearing (motor 1 gear) |
| S 26 | 306602 | Socket head cap screw #1/4-20 x 1 |
| 27 | 111903 | Anti-backlash spur gear (base) |
| S 27 | 306602 | Socket head cap screw #1/4-20 x 5/8 |
| 28 | 111907 | Spur gear (base motor) |
| S 31 | 306414 | Socket head cap screw #10-32 x 3/4 x 1/4 shoulder |
| 32 | 319404 | Spur gear (motors 2+3) |
| 34 | 112412 | Motor support (motor 2) |
| 35 | 112412 | Motor support (motor 3) |
| 37 | 112402 | Motor support (motors 4+5) |
| 38 | 319406 | Timing belt pulley (motors 4+5) |
| 40 | — | Rear cross bar [not used in ER 4u] |
| 46 | 111402 | Main shoulder shaft |
| 47 | 111909 | Timing belt pulley |
| 48 | 111911 | Timing belt pulley |

**Self-check [INFERRED]:** 44 rows; three cells per row. Description blanks are not introduced.

#### T1-26. Arm printed p.26, PDF p.32

| Dwg# | Cat# | Description |
|---|---|---|
| 49 | 111905 | Spur gear (72 teeth) |
| 52 | 111405 | First tension shaft |
| 53 | 113013 | Tension wheel |
| 55 | 111406 | Second tension shaft |
| 56 | 113014 | Tension pulley |
| 57 | 112406 | Clamp – lower arm – left side plate |
| 58 | 110215 | Upper arm – right side plate |
| 60 | 111904 | spur gear (right – 72 teeth) |
| 61 | 110220 | Upper arm – left side plate |
| 63 | 112407 | Clamp – lower arm – left side plate |
| 64 | 111403 | Middle shaft |
| 67 | 107001 | Aluminum spacer |
| 70 | 111910 | Timing belt pulley |
| S 70 | 306007 | Flat head socket screw #4-40 x 1/4 |
| 72 | 111407 | Third tension shaft |
| 74 | 111404 | Gripper axis |
| 76 | 112439 | Stopper (motors 4+5) |
| 77 | 110705 | Base plate limit switch |
| S 81 | 306201 | Flat head socket screw #8-32 x 3/8 |
| 82 | 113008 | Timing belt pulley + miter gear |
| S 82 | 306211 | Flat head socket screw #8-32 x 1/2 |
| 84 | 110228 | Forearm left side plate |
| 86 | 111912 | Timing belt pulley |
| 87 | 112114 | Flange |
| S 87 | 306410 | Flat head socket screw #10-32 x 1/2 |
| 88 | 110223 | Forearm – right side plate |
| 91 | 112408 | Gripper gear motor support |
| S 91 | 306412 | Flat head socket screw #10-32 x 1/4 |
| 94 | 113801 | Lead screw |
| 96 | 112117 | Gripper bridge |
| 97 | 112118 | Gripper finger (inner) |
| 98 | 112119 | Gripper finger (outer) |
| 99 | 112120 | Gripper finger (short) |
| 100 | 112113 | Gripper clamp |
| 101 | 110703 | Mounting plate – gripper |
| 102 | 113201 | Rubber pad – gripper |
| 103 | 111409 | Pivot pin |
| 105 | 111408 | Main shaft – gripper |
| 107 | 113802 | Lead nut – gripper |
| 108 | 112115 | Bearing housing |
| 109 | 112116 | Bearing housing cover |
| 112 | 110229 | Gripper motor base plate |
| 113 | — | Spring [not used in ER 4u] |
| S 115 | 45008 | Encoder circuitry (20 slots) |

**Self-check [INFERRED]:** 44 rows; three cells per row. Description blanks are not introduced.

#### T1-27. Arm printed p.27, PDF p.33

| Dwg# | Cat# | Description |
|---|---|---|
| 116 | 113009 | Miter gear (bottom) |
| S 116 | 45008 | Encoder circuitry (20 slots) |
| 127 | 107009 | Spacer washer (for base bearing) |
| S 139 | 306008 | Socket head set screw #4-40 x 1/8 |
| S 145 | 306213 | Socket head set screw #8-32 x 3/16 |
| S 151 | 306413 | Socket head set screw #10-32 x 3/16 |
| S 153 | 306214 | Socket head set screw #8-32 x 1/4 (without head) |
| S 187 | 302002 | Socket binding head screw M2 x 10 (limit switch) |
| S 188 | 302001 | Slotted binding head screw M2 x 8 (limit switch) |
| S 189 | 302006 | Slotted binding head screw M2x20 (encoder housing) |
| S 206 | 313001 | Washer (for screw #4-40) |
| S 207 | 107012 | Washer (black); internal; for plastic cover ∅ 12.5 x ∅ 5.5 x 0.6 |
| S 208 | 313004 | Washer for screw #10-32 |
| S 209 | 313005 | Washer for screw ∅1/4 |
| S 212 | 314508 | Washer lock; black; external ∅ 5 |
| S 215 | 314002 | Spring washer (for screw #4-40) |
| S 216 | 314003 | Spring washer (for screw #6-32) |
| S 217 | 314004 | Spring washer (for screw #8-32) |
| S 218 | 314005 | Spring washer (for screw #10-32) |
| S 219 | 314006 | Spring washer (for screw ∅ 1/4) |
| S 225 | 314503 | Lock washer M2 |
| S 227 | 313003 | Washer (for screw #8-32) |
| S 232 | 107008 | Teflon washer ∅ 1/4" x ∅ 3/8" x 0.6mm |
| S 233 | 107007 | Teflon washer ∅ 1/4" x ∅ 1/2" x 0.6mm |
| S 234 | 113016 | Nylon washer ∅ 11 x ? 4 [not used in ER 4u] |
| S 240 | 310001 | Hexagonal nut M2 |
| S 253 | 316006 | E-Ring ∅ 1/8 DIN 6799 |
| S 254 | 316003 | Retaining ring ∅ 10 DIN 471 |
| S 255 | 316004 | Retaining ring ∅ 12 DIN 471 |
| S 257 | 316302 | Retaining ring ∅ 25 DIN 471 |
| S 260 | 320005 | Ball bearing ∅ 8 x ∅ 22 x 7 |
| S 261 | 320004 | Ball bearing ∅ 10 x ∅ 19 x 5 |
| S 262 | 320006 | Ball bearing ∅ 10 x ∅ 26 x 8 |
| S 263 | 320203 | Ball bearing ∅ 25 x ∅ 47 x 8 |
| S 268 | 320701 | Needle bearing ∅ 12 x ∅ 16 x 10 |
| S 269 | 320702 | Needle bearing ∅ 12 x ∅ 19 x 16 |
| S 270 | 320704 | Needle bearing ∅ 15 x ∅ 21 x 12 |
| S 270 | 320705 | Bushing for #320704 |
| S 275 | 320501 | Thrust bearing ∅ 10 x ∅ 24 x 2 |
| S 276 | 320502 | Thrust washer ∅ 10 x ∅ 24 x 1 |
| S 277 | 320503 | Thrust washer ∅ 10 x ∅ 24 x 2.5 |
| S 278 | 320504 | Thrust bearing ∅ 12 x ∅ 26 x 2 |
| S 279 | 320505 | Thrust washer ∅ 12 x ∅ 26 x 1 |
| S 283 | 314501 | Lock washer |

**Self-check [INFERRED]:** 44 rows; three cells per row. Description blanks are not introduced.

#### T1-28. Arm printed p.28, PDF p.34

| Dwg# | Cat# | Description |
|---|---|---|
| S 285 | 310401 | Lock nut – gripper |
| S 286 | 310402 | Lock nut – base KM 5 |
| S 288 | 100706 | Washer ∅ 10.5 x ∅ 20 x 0.5 |
| S 289 | 100705 | Washer ∅ 12.5 x ∅ 22 x 0.5 |
| S 293 | 319201 | Timing belt |
| S 294 | 319202 | Timing belt |
| S 295 | 319203 | Timing belt |
| S 300 | 315202 | Flange – timing belt pulley |
| S 301 | 315201 | Flange – timing belt pulley |
| S 308 | 317501 | Pivot pin ∅ 1/8" x 3/8" |
| S 309 | 430901 | Motor Gear - base; 127.7:1 |
| S 310 | 430901 | Motor Gear - shoulder/elbow; 127.7:1 |
| S 311 | 430902 | Motor Gear - pitch/wrist 65.5:1 |
| S 312 | 430903 | Motor Gear - gripper |
| S 313 | 319001 | Coupling |
| S 315 | 410802 | Limit switch |
| S 316 | 310802 | Nut for harness |
| S 317 | 300006 | Harness clamp |
| S 318 | 113006 | Rubber plug (base) |
| S 319 | 300007 | Harness clamp |
| S 320 | 314007 | Conical washer |
| S 322 | 113203 | Rubber grommet |
| S 324 | 113202 | O-ring (rubber) |
| S 325 | 113204 | Rubber stopper |
| S 350 | 317801 | Roll pin ∅ 1/8 x 1 1/4 |
| S 351 | 317502 | Ball bearing ∅ - 3.5 mm |
| 414 | 105003 | Encoder disk (20 slots) - gripper |
| 427 | 113005 | Encoder housing (plastic) |
| 429 | 105003 | Encoder disk (20 slots) |

**Self-check [INFERRED]:** 29 rows; three cells per row. Description blanks are not introduced.

**Cross-page self-check [INFERRED]:** `44 + 44 + 44 + 29 = 161` rows. Only drawing ID S270 is repeated; it has two catalog entries. The list is not a consecutive integer sequence: S-prefixed fasteners are separate IDs, and missing numerical IDs cannot be filled. Items40 and113 have blank catalog cells. Catalogs reused across rows are:
| Catalog | Rows / full printed descriptions |
|---|---|
| 306201 | S 6: Socket head cap screw #6-32 X 1/4; S 81: Flat head socket screw #8-32 x 3/8 |
| 306602 | S 26: Socket head cap screw #1/4-20 x 1; S 27: Socket head cap screw #1/4-20 x 5/8 |
| 112412 | 34: Motor support (motor 2); 35: Motor support (motor 3) |
| 45008 | S 115: Encoder circuitry (20 slots); S 116: Encoder circuitry (20 slots) |
| 430901 | S 309: Motor Gear - base; 127.7:1; S 310: Motor Gear - shoulder/elbow; 127.7:1 |
| 105003 | 414: Encoder disk (20 slots) - gripper; 429: Encoder disk (20 slots) |

The S309/S310 shared catalog is consistent with their identical printed ratio. Other repeated catalog IDs are preserved; equal catalog IDs do not authorize merging different descriptions. All catalogue repetition is inventoried above, rather than selecting only conspicuous conflicts.

### T2. Controller packing-list table

**STATED [IMAGE]**, Controller printed p.3/PDF p.7, Figure 1-3: Packing List. Six numbered source entries, including their internal lists.

| Item | Contents |
|---|---|
| 1 | Controller-USB 110/220 VAC |
| 2 | Cables: Power Cable 110/220 VAC; USB Cable; Remote emergency switch bypass cable. |
| 3 | Emergency Bypass Plug (required when the Teach Pendant is not connected) |
| 4 | on CD-ROM: SCORBASE software; RoboCell software |
| 5 | Documentation: Controller-USB User Manual; SCORBASE User Manual; RoboCell User Manual |
| 6 | Teach Pendant (TP) (Optional and supplied only when ordered): Teach Pendant; Mounting fixture; User Manual |

**Self-check [INFERRED]:** six entries, numbered1–6 once. Remote bypass cable and pendant bypass plug are different entries. Listing another manual as a packing item is not use of that manual as an audit source.

### T3. Complete panel legends

**STATED [IMAGE]**, Controller printed p.9/PDF p.13, Figure 3-1: rear-panel legend.

| Number | Description |
|---|---|
| 1 | Power On/Off Switch |
| 2 | Power Line 110/220 VAC socket |
| 3 | Line Voltage Selector |
| 4 | AC Power Fuse Drawer - 110V 2A; 220V 1A |
| 5 | TEACH PENDANT connection |
| 6 | Reserved for future use |
| 7 | Remote EMERGENCY switch 2-pin connector |
| 8 | USB connector (to PC) |
| 9 | Robot 50-pin D-type connector |

**STATED [IMAGE]**, Controller printed p.10/PDF p.14, Figure 3-2: front-panel legend.

| Number | Description |
|---|---|
| 10 | Digital Input / Output terminals |
| 11 | Analog Input / Output terminals |
| 12 | Emergency Button and LED indicator |
| 13 | Digital Input / Output LED indicators |
| 14 | Power LED indicator. See below. |
| 15 | Motors LED indicator (lit when control on). See below. |
| 16 | Axes7 and8 driver D9 connectors (separate for each device) |
| 17 | Auxiliary12 VDC power supply -0.1 Amp max. |

**Self-check [INFERRED]:** rear9 rows, front8 rows; callouts1–17 each appear once. Callout16 represents two separate motor ports. A MOTORS LED is present; a separately named motor-power switch is absent in this figure.

### T4. Every I/O truth/configuration table

**STATED [IMAGE]**, Controller printed p.15/PDF p.19, Input Terminals and LEDs, unnumbered sensor/jumper table.

| Digital Sensor Type | Jumper Setting |
|---|---|
| Dry-contact (unpowered) | LOW |
| Dry-contact (powered) | HIGH |
| Semiconductor (sink) | LOW |
| Semiconductor (source) | HIGH |

**STATED [IMAGE]**, Controller printed p.16/PDF p.20, Digital Inputs, unnumbered truth table.

| Jumper | Unconnected | 0-2.0 V | 2.5-24 V |
|---|---|---|---|
| LOW | OFF | ON | OFF |
| HIGH | OFF | OFF | ON |

**STATED [IMAGE]**, Controller printed p.18/PDF p.22, NPN Notes truth table.

| Jumper | Unconnected | 0-2.0 V | 2.5-24 V |
|---|---|---|---|
| LOW | OFF | ON | OFF |

**STATED [IMAGE]**, Controller printed p.20/PDF p.24, PNP Notes truth table.

| Jumper | Unconnected | 0-2.0 V | 2.5-24 V |
|---|---|---|---|
| HIGH | OFF | OFF | ON |

**STATED [IMAGE]**, Controller printed p.22/PDF p.26, Figure 4-9-associated output relay truth table.

| SCORBASE Output state | Device A (NC) | Device B (NO) |
|---|---|---|
| OFF | ON | OFF |
| ON | OFF | ON |

**Self-check [INFERRED]:** sensor table4 rows; general digital truth table2 rows; NPN1 row; PNP1 row; relay2 rows. LOW/HIGH are symmetric in the asserted ON voltage interval but both unconnected cells are OFF. The `2.0 V` to `2.5 V` intervening region is explicitly unpredictable in prose; it is not an extra OFF interval. These thresholds describe user digital inputs, not robot home or encoder inputs. Relay NC/NO states exchange at ON/OFF; they are not an emergency-stop relay truth table.

The following is an **organizational transcription of a drawing**, not an original vendor table: **STATED [IMAGE]**, Controller printed p.31/PDF p.35, Figure 6-2: I/O Jumpers. The source depicts shunts on the upper pair for factory LOW/SINK. The lower pair is the HIGH/SOURCE alternative.

| Group | Channels shown | Upper pair label / shown setting | Lower pair label |
|---|---|---|---|
| INPUTS | 1,2,3,4,5,6,7,8 | LOW; each shown on upper pair | HIGH |
| OUTPUTS | 5,6,7,8 | SINK; each shown on upper pair | SOURCE |

**Self-check [INFERRED]:** eight input jumpers, four output jumpers; no jumpers are depicted for relay outputs1–4. Figure 4-4, printed p.19/PDF p.23, shows input1 LOW/output5 SINK. Figure 4-6, printed p.20/PDF p.24, shows input1 HIGH/output5 SOURCE. Their depicted jumper positions agree with Figure 6-2.

### T5. Figure legends on the exploded parts drawings

**STATED [IMAGE]**, Arm printed pp.29–32/PDF pp.35–38, Figures20–23. Each of the four drawings repeats the same two-entry key. Both symbols are circular part-callout outlines.

| Symbol | Legend text |
|---|---|
| Circle containing XXX with a small S above | STANDARD COMPONENT |
| Circle containing XXX without S | ESHED ROBOTEC MANUFACTURED COMPONENT |

**Self-check [INFERRED]:** two legend entries per drawing, four occurrences. XXX is an example part-number placeholder, not a part assigned XXX. Drawn item numbers are cross-references to T1, not additional numeric dimensions.

### T6. Contents tables (navigation data)

These are reproduced because the request asks for every table. Chapter/section numbering and destination-page columns are navigation data, not physical limits. The same page offsets can be applied to their body destinations; the contents page itself is cited below. Dot leaders and indentation are normalized, preserving every title and destination.

#### T6-Arm

**STATED [TEXT]**, Arm Roman printed p.v/PDF p.5, Table of Contents; visually checked [IMAGE].

| Title / numbering | Printed destination page |
|---|---|
| CHAPTER 1     General Information | 1 |
| About SCORBOT-ER 4u | 1 |
| Acceptance Inspection | 2 |
| Repacking for Shipment | 3 |
| Handling Instructions | 3 |
| CHAPTER 2      Specifications | 4 |
| Structure | 5 |
| Work Envelope | 6 |
| Motors | 7 |
| Encoders | 7 |
| Microswitches | 8 |
| Transmissions | 8 |
| Gripper | 9 |
| CHAPTER 3      Safety | 10 |
| Precautions | 10 |
| Warnings | 11 |
| CHAPTER 4      Installation | 12 |
| CHAPTER 5     Operating Methods | 14 |
| SCORBASE Software | 14 |
| Teach Pendant | 14 |
| CHAPTER 6     Maintenance | 15 |
| Maintenance | 15 |
| Daily Operation | 15 |
| Periodic Inspection | 16 |
| Troubleshooting | 17 |
| Adjustments and Repairs | 21 |
| Adjusting the Timing Belts | 21 |
| Adjusting Base Anti-Backlash | 22 |
| Tightening the Oldham Coupling in Gripper | 23 |
| Gripper Disassembly | 23 |
| Gripper Reassembly | 23 |
| CHAPTER 7      Parts Lists | 24 |
| CHAPTER 8     Wiring | 33 |
| Robot Wiring | 33 |
| Single Axis Wiring | 35 |

**Self-check [INFERRED]:** 35 contents entries reproduced; destination pages are within this manual. Controller entry “7. Schematic Diagram” differs from the actual Appendix A heading, as recorded in Contradictions.

#### T6-Controller

**STATED [TEXT]**, Controller unnumbered PDF p.3, Table of Contents; visually checked [IMAGE].

| Title / numbering | Printed destination page |
|---|---|

**Self-check [INFERRED]:** 0 contents entries reproduced; destination pages are within this manual. Controller entry “7. Schematic Diagram” differs from the actual Appendix A heading, as recorded in Contradictions.

## Absence search log

All 79 rendered pages were visually inspected, including image-only tables, rotated drawings and footer-only pages. Selectable text was extracted page by page and searched as a cross-check; a failed text search alone was never treated as sufficient evidence of absence. Every log row below therefore includes the whole-document scan **Arm PDF p.1–41 / printed body1–35 and front matter; Controller PDF p.1–38 / printed body1–34 and front matter**, plus the focused pages shown. Terms are case-insensitive; punctuation variants and contextual readings were checked. A hit on a related word is not a found specification. These are audit search scopes, not additional vendor facts.

| Log | Question / focused pages visually read | Terms searched / conclusion |
|---|---|---|
| L1 | Counts and decoder: Arm printed pp.4,7,18–19,24–28,33–35/PDF p.10,13,24–25,30–34,39–41; Controller printed pp.4–5,28–29,33/PDF p.8–9,32–33,37 | `count`, `counts`, `revolution`, `rev`, `pulse`, `slot`, `20`, `80`, `decode`, `quadrature`, `resolution`, `P0`, `P1`, `PC510`, `encoder`. Slots/channels/phase and general count readings found; no numeric counts/revolution, joint counts/degree or edge multiplier |
| L2 | Coordinates/home pose/direction: Arm printed pp.3–9,24–35/PDF p.9–15,30–41, Figures2–11,20–26; Controller printed pp.4–5,25,28–29/PDF p.8–9,29,32–33 | `home`, `zero`, `degree`, `angle`, `positive`, `negative`, `clockwise`, `counterclockwise`, `direction`, `coordinate`. Travel and CW/CCW descriptions found; no absolute home angle or signed per-joint convention |
| L3 | Homing algorithm/switch logic: Arm printed pp.7–8,17–19,33–35/PDF p.13–14,23–25,39–41; Controller printed pp.25,28–29,33/PDF p.29,32–33,37 | `home`, `homing`, `microswitch`, `switch`, `normally`, `NC`, `NO`, `logic`, `back`, `order`, `sequence`, `slightly`, `active`, `level`. One-at-a-time and activate/turn-off text found; no search order/direction/back-off value or controller home-input level. NC applies to remote e-stop or relay contacts, not explicitly arm home switches |
| L4 | Timeout/watchdog/trip thresholds: Controller printed pp.1–5,10–12,25,27–34/PDF p.5–9,14–16,29,31–38; Arm printed pp.4,11,17–20/PDF p.10,17,23–26 | `time`, `timeout`, `time-out`, `time out`, `watchdog`, `reset`, `current`, `over`, `heat`, `ms`, `second`, `cycle`, `refresh`. General shutdown/watchdog and1.5ms control-cycle parameter found; no timeout/watchdog duration, reset event, over-current trip setting or over-temperature threshold |
| L5 | Startup, disconnect/host closing: Controller printed pp.9–13,25,27–34/PDF p.13–17,29,31–38; Arm printed pp.15–20/PDF p.21–26 | `USB`, `unplug`, `disconnect`, `close`, `SCORBASE`, `power`, `start`, `default`, `CON`, `COFF`, `communication`. SCORBASE-closing and generic timeout provisions found; no unplug-specific motion sequence or raw firmware startup state |
| L6 | Home retention: Arm printed pp.8,15,17–20/PDF p.14,21,23–26; Controller printed pp.10–12,25,27–29,32–34/PDF p.14–16,29,31–33,36–38 | `retain`, `reset`, `erase`, `home`, `reference`, `COFF`, `CON`, `power`, `off`, `emergency`, `memory`. A warning to check home parameters and changing Home found; no transition-specific retained-home guarantee/erasure rule |
| L7 | Speed levels and axes: Arm printed pp.4,11,14–20/PDF p.10,17,20–26; Controller printed pp.4–5,27–29/PDF p.8–9,31–33 | `speed`, `level`, `ten`, `velocity`, `acceleration`, `deceleration`, `degree`, `rpm`, `travel time`, `600`. Ten levels and maximum path velocity found; no numeric ten-level mapping or per-axis angular-speed table |
| L8 | USB/host protocol and fault codes: Controller printed pp.1–5,9–12,15–25,28–34/PDF p.5–9,13–16,19–29,32–38; Arm printed pp.14–20,33–35/PDF p.20–26,39–41 | `USB`, `ERUSBClass`, `driver`, `enumer`, `descriptor`, `VID`, `PID`, `endpoint`, `packet`, `command`, `error`, `code`, `status`, `register`, `C++`, `checksum`, `ack`. Driver/name, generic messages and software command names found; no USB protocol or host fault-code map. PID hits are control-law text, not a USB product ID |
| L9 | Holding/brakes/gravity: Arm printed pp.4,8–11,15–23/PDF p.10,14–17,21–29; Controller printed pp.4–8,10–11,25,28–29,33/PDF p.8–12,14–15,29,32–33,37 | `brake`, `hold`, `holding`, `fall`, `gravity`, `coast`, `motor`, `off`, `stop`, `strain`, `load`. Stop/strain warnings found; no brake or sustained-off holding/gravity statement |
| L10 | Wrist matrix/differential: Arm printed pp.4–9,21–32/PDF p.10–15,27–38; Controller printed pp.4–5,33/PDF p.8–9,37 | `differential`, `bevel`, `miter`, `ratio`, `4`, `5`, `opposite`, `same`, `pitch`, `roll`, `direction`, `sign`. Qualitative opposite/same relationship found; no differential numerical ratio or signed motor matrix |
| L11 | Power loss/brownout: Controller printed pp.4–13,25,27–34/PDF p.8–17,29,31–38; Arm printed pp.10–20/PDF p.16–26 | `power`, `loss`, `fail`, `brown`, `restart`, `off`, `memory`, `retain`, `shutdown`, `supply`. Power paths and generic failure shutdown found; no explicit power-loss control/holding/restart statement |
| L12 | Gripper, dimensions and force: Arm printed pp.3–9,11–13,18–32/PDF p.9–15,17–19,24–38; Controller printed pp.4–5,28–29/PDF p.8–9,32–33 | `force`, `grip`, `stroke`, `count`, `hold`, `lead`, `pitch`, `length`, `diameter`, `humidity`, `minute`, `duty`. Opening, encoder and indefinite-hold warning found; no gripper force, screw pitch, count stroke, numerical hold time, humidity limit, TCP extension or complete tool transform |
| L13 | Detailed circuits and connector orientations: Arm printed pp.7,24–35/PDF p.13,30–41; Controller printed pp.4–5,9–13,15–25,30–34/PDF p.8–9,13–17,19–29,34–38 | `circuit`, `schematic`, `relay`, `isol`, `opto`, `pin`, `ground`, `VLED`, `driver`, `emergency`, `mating`, `solder`, `pull`. D50/D9/Molex tables and system schematic found; no encoder/home input circuit, isolation rating, motor transistor circuit, detailed e-stop topology or connector-face designation |
| L14 | Stop and protection bounds: Arm printed pp.4,10–11,17–20/PDF p.10,16–17,23–26; Controller printed pp.4–8,25,28–29,33/PDF p.8–12,29,32–33,37 | `impact`, `limit`, `error`, `position`, `stop`, `distance`, `time`, `category`, `safety`, `threshold`, `force`, `redundan`. Protection names found; no trip equations, software-limit coordinate table, stop-time/distance, safety category, performance level or redundancy claim |

## Review coverage and formerly blank extraction pages

The detailed page inventory below records all rendered pages. Its topics are navigation labels used by this audit, not new facts. Every page was viewed as an image; text was also read where present. No unreadable page was omitted. [IMAGE] identifies the visual review; individual findings retain their own tags above. Footer-only pages are distinguished from completely blank ones.

| Manual | Printed page | PDF page | Visual review / section or figure |
|---|---|---|---|
| Arm | unnumbered | 1 | [IMAGE] Cover / identity |
| Arm | unnumbered | 2 | [IMAGE] Genuinely blank |
| Arm | unnumbered | 3 | [IMAGE] Copyright / revision / date / reading admonition |
| Arm | unnumbered | 4 | [IMAGE] Genuinely blank |
| Arm | v | 5 | [IMAGE] Complete contents table |
| Arm | unnumbered | 6 | [IMAGE] Genuinely blank |
| Arm | 1 | 7 | [IMAGE] About SCORBOT-ER4u / Figure1 |
| Arm | 2 | 8 | [IMAGE] Acceptance inspection / package list |
| Arm | 3 | 9 | [IMAGE] Repacking and handling / Figure2 |
| Arm | 4 | 10 | [IMAGE] Complete specification table, signs visually resolved |
| Arm | 5 | 11 | [IMAGE] Structure / motor mapping / Figures2-3,2-4 |
| Arm | 6 | 12 | [IMAGE] Work envelope / Figures5,6 |
| Arm | 7 | 13 | [IMAGE] Motors and encoders / Figures7,8 |
| Arm | 8 | 14 | [IMAGE] Microswitches and transmissions / Figures9,10 |
| Arm | 9 | 15 | [IMAGE] Gripper and wrist differential / Figure11 |
| Arm | 10 | 16 | [IMAGE] Safety precautions / stop callouts |
| Arm | 11 | 17 | [IMAGE] All arm warnings |
| Arm | 12 | 18 | [IMAGE] Installation / mounting / Figures12,13 |
| Arm | 13 | 19 | [IMAGE] Installation continuation / controller location |
| Arm | 14 | 20 | [IMAGE] Software and optional pendant descriptions |
| Arm | 15 | 21 | [IMAGE] Maintenance / daily operation |
| Arm | 16 | 22 | [IMAGE] Periodic inspection / Figure14 |
| Arm | 17 | 23 | [IMAGE] Troubleshooting items1,2 / switch checks / qualification |
| Arm | 18 | 24 | [IMAGE] Switch-check continuation / items3,4 / encoder direction |
| Arm | 19 | 25 | [IMAGE] Encoder waveform and repeatability checks / Figure15 |
| Arm | 20 | 26 | [IMAGE] Troubleshooting items7–9 / backlash / noise |
| Arm | 21 | 27 | [IMAGE] Timing-belt adjustments / Figures16–18 |
| Arm | 22 | 28 | [IMAGE] Base anti-backlash / Figure19 |
| Arm | 23 | 29 | [IMAGE] Oldham coupling / clearance note |
| Arm | 24 | 30 | [IMAGE] Enhanced-product changes not reflected in drawings |
| Arm | 25 | 31 | [IMAGE] Parts list,44 rows |
| Arm | 26 | 32 | [IMAGE] Parts list,44 rows |
| Arm | 27 | 33 | [IMAGE] Parts list,44 rows; S234 ambiguous glyph |
| Arm | 28 | 34 | [IMAGE] Parts list,29 rows; ratios/20-slot disks |
| Arm | 29 | 35 | [IMAGE] Rotated exploded gripper / Figure20 |
| Arm | 30 | 36 | [IMAGE] Rotated exploded arm / Figure21 |
| Arm | 31 | 37 | [IMAGE] Rotated base anti-backlash / Figure22 |
| Arm | 32 | 38 | [IMAGE] Rotated base assembly / Figure23 |
| Arm | 33 | 39 | [IMAGE] D50 motor cells and connector face / Figure24 |
| Arm | 34 | 40 | [IMAGE] All encoder and microswitch wiring cells / Molex mapping |
| Arm | 35 | 41 | [IMAGE] D9 peripheral wiring / Figures25,26 |
| Controller | unnumbered | 1 | [IMAGE] Cover / identity |
| Controller | unnumbered | 2 | [IMAGE] Copyright / revision / date |
| Controller | unnumbered | 3 | [IMAGE] Complete contents table |
| Controller | unnumbered | 4 | [IMAGE] Genuinely blank |
| Controller | 1 | 5 | [IMAGE] About Controller-USB / Figure 1-1 |
| Controller | 2 | 6 | [IMAGE] Controller overview / Figure 1-2 |
| Controller | 3 | 7 | [IMAGE] Acceptance / six-item packing list / Figure 1-3 |
| Controller | 4 | 8 | [IMAGE] Specifications first page,15 rows |
| Controller | 5 | 9 | [IMAGE] Specifications continuation,10 rows / Figure 1-4 |
| Controller | 6 | 10 | [IMAGE] Footer only; no body content |
| Controller | 7 | 11 | [IMAGE] Handling and warnings |
| Controller | 8 | 12 | [IMAGE] Warnings continuation / emergency button |
| Controller | 9 | 13 | [IMAGE] Rear-panel figure and all legend cells / Figure 3-1 |
| Controller | 10 | 14 | [IMAGE] Front-panel figure, LEDs and legend / Figure 3-2 |
| Controller | 11 | 15 | [IMAGE] Motors LED continuation / PC requirements / installation |
| Controller | 12 | 16 | [IMAGE] Installation continuation / peripheral kit and bypass cable |
| Controller | 13 | 17 | [IMAGE] Remote emergency NC switch / connector openings |
| Controller | 14 | 18 | [IMAGE] Footer only; no body content |
| Controller | 15 | 19 | [IMAGE] Input LEDs / sensor-jumper table / Note |
| Controller | 16 | 20 | [IMAGE] Digital-input voltage truth table / Figure 4-1 |
| Controller | 17 | 21 | [IMAGE] Powered dry contacts / Figure 4-2 / two Notes blocks |
| Controller | 18 | 22 | [IMAGE] NPN wiring / Figure 4-3 / notes and truth table |
| Controller | 19 | 23 | [IMAGE] NPN loopback / Figure 4-4 / PNP Figure 4-5 and notes |
| Controller | 20 | 24 | [IMAGE] PNP truth table / Figure 4-6 / analog Figure 4-7 |
| Controller | 21 | 25 | [IMAGE] Analog Figure 4-8 / Notes / relay-output introduction |
| Controller | 22 | 26 | [IMAGE] Relay contact truth table / Figure 4-9 |
| Controller | 23 | 27 | [IMAGE] Sink outputs / Figure 4-10 / diode precaution and Notes |
| Controller | 24 | 28 | [IMAGE] Source / Figure 4-11 / Notes / analog Figure 4-12 |
| Controller | 25 | 29 | [IMAGE] Complete e-stop effects, output-device limitation and release behavior |
| Controller | 26 | 30 | [IMAGE] Footer only; no body content |
| Controller | 27 | 31 | [IMAGE] Inspection / LED statements |
| Controller | 28 | 32 | [IMAGE] Inspection home completion / troubleshooting items1–3 |
| Controller | 29 | 33 | [IMAGE] Troubleshooting continuation, current and noisy Home shift |
| Controller | 30 | 34 | [IMAGE] Opening / jumper restrictions / Figure 6-1 |
| Controller | 31 | 35 | [IMAGE] Figure 6-2 jumpers / voltage-selection and fuse ratings |
| Controller | 32 | 36 | [IMAGE] ERUSBClass driver installation text |
| Controller | 33 | 37 | [IMAGE] Rotated Appendix A system schematic, all labels read |
| Controller | 34 | 38 | [IMAGE] Footer only; no body content |

**Self-check [INFERRED]:** 41 arm +38 controller =79 pages. Arm blank pages2/4/6; Controller blank page4 and footer-only PDF p.10/18/30/38 were included. Printed/PDF mappings were checked against visible page footers.


## Machine-readable numeric appendix

The accompanying [SCORBOT_Numeric_Facts.yaml](SCORBOT_Numeric_Facts.yaml) contains one entry per recorded numeric fact with the requested keys: `name`, `value`, `unit`, `manual`, `printed_page`, `pdf_page`, `figure_or_table`, `evidence_tag`, `confidence`, `notes`.

Values are stored as strings so that signs, strict bounds, ranges, fractions, catalog leading zeros and conflicting printed values survive parsing. Paired metric/imperial values have separate entries; source spelling is retained in notes. Identifier entries are explicitly marked as identifiers. Part dimensions without stated units retain `unit: not stated`. Composite screw/thread designations are kept as designations, rather than assuming their subnumbers are independent physical measurements. Reference labels, ordinary instruction step numbers and navigation-page numbers are not represented as engineering quantities.

`confidence: high` means the value is clearly read, **not** that the installed hardware has been independently verified. Conflicting clearly read ratios therefore remain high-confidence transcriptions with conflict notes. An unreadable glyph has low confidence and is not repaired. ABSENT facts are not encoded as zero or assigned a guessed value. Inferred numeric checks include explicit arithmetic in notes.

## Questions I could not answer

These are suggested evidence sources, not robot operating instructions. This report supplies no motion or test procedure.

1. What reduction is actually installed in motors1–3:127.1:1 or127.7:1? **Resolve:** vendor confirmation tied to gearhead/robot serial number; component identification or qualified reduction measurement. C1, L10.
2. What is the encoder decoder multiplier and reported count per motor revolution/joint degree? **Resolve:** vendor firmware/encoder-interface specification; qualified comparison of encoder transitions with reported counter data. D, L1.
3. What are the signed joint/motor coordinates, home angles and wrist differential matrix? **Resolve:** vendor kinematic definition and differential drawings; independently controlled coordinate characterization if documentation remains unavailable. B4/C1/I2/I10.
4. What are the home search direction, axis order, back-off amount and input logic thresholds? **Resolve:** vendor homing algorithm plus board-level home-input circuit; software/USB trace of an existing authorized homing session. E, L3/L13.
5. Is home valid after COFF/CON, e-stop, USB reconnect or power cycle? **Resolve:** vendor state-retention specification, or a qualified state-transition test with separate safety controls. I6/L6.
6. What is the communication timeout/watchdog period and what resets it? **Resolve:** vendor timing contract/firmware documentation; USB capture and qualified timing characterization. I4/L4.
7. What happens between USB unplug or host-process failure and motor shutdown? **Resolve:** vendor failure-state specification; approved instrumented fault testing. Generic communication-failure text is insufficient to bound latency. I5/L5.
8. Are motors electrically braked, mechanically held or allowed to move under gravity when off or on power loss? **Resolve:** vendor driver/brake schematic and holding specification; qualified non-motion electrical/mechanical inspection or approved bench characterization. I9/I12/L9/L11.
9. What are the ten speed levels, per-axis angular rates and acceleration limits? **Resolve:** vendor control-software/firmware specification; captured commanded parameters from a separately authorized software session. I7/L7.
10. What packets, acknowledgements and error/status queries can a Python host use? **Resolve:** vendor SDK/protocol documentation; USB capture of known SCORBASE exchanges. Driver ERUSBClass alone does not answer this. G4/L8.
11. What are impact/position-error/over-current/temperature thresholds and stop latency? **Resolve:** vendor protection specification and test evidence; a qualified independently safeguarded validation program. G3/L4/L14.
12. What is gripper force, screw pitch, stroke in counts and permissible hold duration? **Resolve:** vendor gripper/motor duty and force specifications; approved bench force/displacement characterization. I11/L12.
13. Is the gripper pad swap intentional, and what is each connector's mating-face orientation? **Resolve:** vendor harness/PC510 revision drawing; qualified continuity/board-marking inspection. F2/F5/L13.
14. Which base dimension applies to the mounting footprint, and what is the missing wedge's datum orientation? **Resolve:** vendor controlled dimensioned assembly drawing; qualified dimensional survey. B3/B5.
15. What is the actual emergency-stop switching topology, isolation and claimed safety performance? **Resolve:** vendor component schematics, safety architecture and validation evidence. A system connection diagram cannot settle it. F7/H5/L13/L14.
16. How should S270, catalog306201/306602, S234's malformed glyph and omitted drawing callouts be resolved? **Resolve:** a corrected vendor parts list/original CAD and confirmation of the installed enhanced ER4u assembly. A2/T1/Contradictions.

No outside-knowledge facts were required or added.
