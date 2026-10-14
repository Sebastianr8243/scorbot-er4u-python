# Manual and prior-art findings (2026-09-29)

Research notes from three desk surveys: the SCORBASE USB manual, other ER-4u
sources (vendor controller parameter files, datasheet, the local 4pc manual,
third-party projects) and robot operator-UX prior art. **Nothing here is
measured on our arms.** Vendor values are priors, never calibration
(`scorbot/nominal.py` rule). Facts are paraphrased with page numbers; the
manuals themselves are copyrighted and are not in this repository.

## Sources

| Short name | Source |
|---|---|
| SCORBASE | Intelitek SCORBASE for USB manual #100342 Rev. I, March 2016, <https://downloads.intelitek.com/Manuals/Robotics/ER-4u/Scorbase_USB_I.pdf>. Page numbers are the printed ones (PDF page minus 5) |
| Vendor INI | Intelitek ER-4u controller parameter files (`ER4Ax1-6.ini`, `ROB_4u.INI`, `ER4CONF.INI`; sets `$Default`, `$CURRENT`, `$2kg`, `$3kg`, `$MaxSpeed`), dated 2001-2003, bundled in <https://github.com/kutzer/ScorBotToolbox> under `ScorBotToolboxSupport/Par/er4u/` |
| Datasheet | Intelitek ER-4u datasheet 35-1005-8600 Rev K |
| 4pc manual | `references/manual_scorbot.pdf` (local only, not in the repository; Spanish, ER-4pc, older controller; mechanics likely shared, electronics differ) |
| Kutzer | USNA ScorBot Toolbox (MATLAB, via Intelitek's USBC DLL): DH table `ScorDHtable.m`, error codes `ScorParseErrorCode.m` |
| ESPE 2023 | Guilcazo, Nunez, Ortiz and Andaluz, "Robot manipulator applied to bottle filling processes: an approach in the teaching-learning process", Universidad de las Fuerzas Armadas ESPE, Latacunga, 22 Aug 2023 (degree-project article, 18 PDF pages), <https://repositorio.espe.edu.ec/server/api/core/bitstreams/857043ad-c59c-4866-8e12-b8aa33e46481/content>. Page numbers are PDF pages. The PDF itself stays out of the repository. |

## Calibration priors (S2)

`NoEnc90` in the vendor INI is the encoder count for 90 degrees of joint travel.

| Joint | NoEnc90 | Counts per degree | Legacy `COUNTS_PER_DEGREE` | Agrees? |
|---|---|---|---|---|
| base | -12770 | 141.89 | 141.85 | yes |
| shoulder | -10216 | 113.51 | 115.0 | close |
| elbow | +10216 | 113.51 | 112.6 | close |
| wrist pitch (motor) | 2511 | 27.90 | 33.8 | **no, unresolved** |
| wrist roll (motor) | 2511 | 27.90 | 27.9 | yes |
| gripper | 6075; `RangeEnc` 5000 counts = `RangeMM` 70 mm | | | |

- The signs are Intelitek's direction convention (unverified on our arms).
- One mechanical model fits every axis (inference): 20-slot encoder disk (4pc
  manual p. 35, INI `CountsPerRound=20`) x 4 quadrature (assumed: no manual
  states the decode mode) = 80 counts per motor
  revolution, x 127.7:1 motor gearbox, x a final stage (base 5:1 from 120:24,
  shoulder and elbow 4:1, wrist 23:12). This replaces the "~402 counts per
  motor revolution" hypothesis in `nominal.py`. Both manuals contradict
  themselves: 127.7:1 in the parts list (ER-4u p. 28; 4pc p. 40) and 127.1:1 in
  the spec table (ER-4u p. 4; 4pc p. 11). The INI fits 127.7. `nominal.py` keeps
  both (`GEAR_RATIO_ARM`, `GEAR_RATIO_ARM_PARTS_LIST`).
- The legacy pitch scale (33.8) disagrees with the INI (27.9). Wrist jogs stay
  disabled; resolve this with a measurement before any wrist motion.

Geometry (vendor `ROB_4u.INI`, matches Kutzer's DH table): base height 349 mm,
joint-1 offset 16 mm, upper arm 221 mm, forearm 221 mm, gripper 145 mm.
`nominal.py` has 364 mm (base bottom to shoulder axis, `SHOULDER_AXIS_HEIGHT_MM`)
and 220 mm; see "Contradictions" below.

Controller angle limits (vendor INI): base +174/-132 (306 degree span), shoulder
+31/-124 (155), elbow +160/-115 (275), pitch +115/-113 (228), roll +/-570.
Encoder soft limits: base -25000..20000, shoulder -18000..1500, elbow
-25000..20000, pitch +/-15000.

Home pose:
- SCORBASE joint values at home: base 0, shoulder -120, elbow 95, pitch 88,
  roll 0 degrees; XYZ 169, 0, 503 mm, pitch -63 (SCORBASE p. 28-29; origin at
  the base centre, table level, p. 17).
- All encoders are zeroed at hard home, "0 or close to zero" (SCORBASE p. 23, 39).
- INI horizontal pose: shoulder -13653 counts, elbow -10786, pitch -1773.
- INI home offsets after the switch: shoulder -190, elbow +45, pitch +850,
  roll -690 counts.
- Homing order (INI): shoulder, elbow, roll, pitch, base, gripper. The legacy
  `setHome.py` homes pitch before roll. SCORBASE homes the gripper with the
  five axes (p. 24).

Datasheet: effective speeds base 20 deg/s, shoulder and elbow 26.3, pitch 83,
roll 106. Payload up to 2.5 kg at reduced velocity. Gripper 75 mm open (65 with
pads); the SCORBASE Jaw command is accurate only for 5-65 mm (p. 48).

## Controller and protocol (S1)

- Parameter `ER4CONF.INI`: host period `PCPeriod=16` ms, `USBCPeriod=1.5` ms.
  This fits our estimated ~13 ms idle cycle.
- Per-axis impact detection (`ImpactDetect=70` on axes 1-5, 300 on the gripper),
  a thermal model per motor, homing `MaxTime` 55-110 s per axis.
- The controller turns control off by itself after an impact, a trajectory
  error or a thermal overload during a move; later moves give an error until
  control is turned on again (SCORBASE p. 26). Matches our fault latch.
- Error codes (Kutzer, from Eshed `Error.h`): 201/500/900 position error or
  impact, 202 thermal overload, 300/301 emergency on/off, 561 homing time
  elapsed, 563 home switch not found, 564/565 plus/minus limit, 901 home not
  done, 903 control disabled.
- SCORBASE Movement Information shows per-axis position error in encoder
  counts, home-switch bits (1 = pressed) and one axis's PWM (p. 17, 19, 97).
  This supports reading our unknown "error word" as position error (unverified).
- F9 Stop "is sent directly to the device" on an ER-4u (p. 80): a real stop
  command probably exists. Capture it (BACKLOG 6).
- Go to Position is point-to-point: all axes move independently (p. 46).
  Wording such as "the controller records the current position" (p. 49)
  hints that positions may be stored in the controller; captures B and C
  should show whether position tables are uploaded.
- Distinct actions worth capturing: Search Home vs Go Home (Go Home drives all
  encoders to zero without re-homing, p. 23, 25), Control On/Off (F5), gripper
  Open/Close vs Jaw (mm), Set Axis to zero (p. 49), digital and analog outputs,
  F9 Stop during a move.
- Speed scales: Teach Positions 1-10, default 5 (p. 33); Go to Position 1-99 %,
  default 50, or a duration (p. 46). Record which one a capture used.

## Safety

- Keep 70 cm clearance and a barrier; switch motors off before approaching
  (4pc manual p. 19). Do not leave a loaded arm extended for minutes; do not run
  an axis continuously at full speed (p. 20).
- If an axis runs away: press and release EMERGENCY, then re-home (4pc p. 28).
- The teach pendant's Teach/Manual switch on Teach blocks motion from the PC
  (SCORBASE p. 40): check the pendant is on Auto or absent before motion.
- F9 Stop is for emergencies, F10 Pause finishes the current move; the physical
  EMERGENCY button is the real stop (SCORBASE p. 14, 80).
- The Controller-USB manual covers communication loss ("On communication
  failure, motor power shutdown", p. 5, no timeout given) and e-stop release
  (stays in COFF until a new CON, p. 25). Not covered by any source: whether
  home survives an e-stop, COFF/CON, reconnect or power cycle, and so whether
  re-homing is required.

## Operator UX (guided session design)

From SCORBASE:
- Controller powered before connecting; SCORBASE silently runs Off-Line when no
  controller answers; only one copy may run (p. 6-7, 25).
- Enabling control is a confirmed prompt; Control On/Off is always shown in the
  status bar (p. 25-26).
- Joint-mode jogging is allowed before homing, to reach a start pose (p. 31).
  Our SDK requires home before any jog; changing that is a separate safety decision.
- Homing shows per-axis progress with a tick per axis and can be aborted (p. 24).
  Homing is needed once per session (p. 24).
- Jog keys 1/Q base, 2/W shoulder, 3/E elbow, 4/R pitch, 5/T roll, 6/Y gripper;
  continuous while held (p. 30-32). Our design keeps step-per-press.
- Experience levels 1, 2 and Pro hide commands (p. 27, 33-35, 96).

From other tools:
- LeRobot: profile keyed by robot id; "Enter keeps the saved value, a letter
  redoes it"; letter aliases for every key; avoid global key hooks, which fail on
  Windows and catch keys when the terminal is not focused.
- UR PolyScope, FANUC, ABB: a permanent status line (mode, step, speed, armed,
  fault); an explicit enabled/armed state; visible step size; incremental jog.
  Do not call a keypress an enabling device or hold-to-run: our commands are
  queued and cannot be interrupted.
- Dobot, xArm: preset step list, separate joint step setting.
- teleop_twist_keyboard: key map printed on entry and on `?`; unknown keys do
  nothing; idle timeout.
- clig.dev: confirm severe actions by typing the target; tell the user when
  state changes; respond to Ctrl-C at once. Degani and Wiener: do-list
  checklists for novices, do-confirm for experienced operators.
- Terminal library: standard library only (`input`, `msvcrt.getwch` behind a
  small key-source interface); prompt_toolkit crashes in Git Bash and on
  redirected output; Textual is a candidate for the later student window app.

## Vision-loop control of a real ER-4U (ESPE 2023, read 2026-10-13)

Read in full (18 PDF pages). It is a camera-guided bottle-packing task on a real
ScorBot-ER-4U. It says nothing about the USB protocol, packets, homing or
limits, so it adds no constants to the code. Its value is as evidence of what
loop rate and control style have worked on this arm.

**What they built (PDF pp. 2-5, 15).**

- A fixed stereo camera (ZED 2) looks at the work area beside the arm
  ("eye-to-hand"; the text calls it "handheld camera" in several places, which
  is a wording slip, since a handheld camera would sit on the arm).
- Object detection is an SSD network on a MobileNet backbone, run in Python with
  the camera's SDK. It gives the bottle and the gripper position in camera
  coordinates. Those are sent to MATLAB over TCP/IP.
- The control law runs in MATLAB. A Unity3D scene (a CAD model of the arm from
  SolidWorks, a conveyor and bottles) shares memory with MATLAB through a DLL, so
  the same controller drives the simulation first and the real arm after.
- The arm is described as 5 degrees of freedom plus a gripper, with a Jacobian
  kinematic model.

**The control law (PDF pp. 9-11).**

- It outputs joint velocity references: a pseudo-inverse of the Jacobian times a
  saturating (tanh) function of the position error, plus a null-space term that
  pulls the joints toward a preferred posture without disturbing the end point.
- The stability proof (Lyapunov) assumes "perfect speed tracking", meaning the
  joints are taken to follow the velocity reference exactly. The paper does not
  test or measure that assumption on the arm.
- Their numbers: error gains 0.8 on x, y and z; saturation gains 0.5; null-space
  weights 0.95 on all five joints; preferred posture `[0, 1.047, 0.524, 0,
  0.262]` rad; sampling period `T0 = 0.2 s` (PDF p. 15).
- Their "HOME" pose is `[0, 0.785, 0.698, 0.873, 0]` rad (PDF p. 15). It is a
  starting pose in their model, not the controller's homed position, and must
  not be mixed up with vendor homing or with `nominal.py`.

**How they reached the arm (PDF p. 5).** One sentence: "toolbox libraries for
movement of each joint". No library is named. (I) It is probably the USNA
MATLAB toolbox in the Kutzer row above, which goes through Intelitek's USBC DLL,
but the paper does not say so.

**Results (PDF pp. 12-16).** Plots show the end-point error shrinking toward
zero in simulation, and photos of a six-bottle packing run at 10 to 65 s on the
real arm. There is no table of errors, no latency, no measured loop jitter, no
tracking error of the joints and no mention of faults or stops.

**What it supports for us.**

| Question | What the paper gives | Strength |
|---|---|---|
| Can this arm be run in a vision loop at about 5 Hz? | Yes: `T0 = 0.2 s` with a camera, a neural detector and MATLAB in the loop | Moderate. One group, one task, no timing data |
| Is a velocity-reference style control usable? | They used one; the vendor DLL's `MoveManual(axis, velocity)` is the same kind of call | Weak. They assume the joints track perfectly and never measure it |
| Does it show the controller follows streamed setpoints at 10-50 Hz? | No. 5 Hz only | None. The streaming risk in `LAB_PLATFORM_VISION.md` stays open |
| Does it say what a slow policy plus a fast follower needs? | No | None |

**What it does not settle.** Joint tracking error at 5 Hz, controller latency,
how a stop behaves mid-motion, units of the velocity command, and which software
sat between MATLAB and the DLL. The Visit A captures and the vendor-DLL jog
tool are what answer these.

**Quality note.** A degree-project article with a self-described "handheld"
mislabel and a proof that rests on an unmeasured assumption. Use it as a pointer
that this has been done, not as a source of numbers. The thesis it cites for the
kinematic model (Ipiales et al., *Electronics* 2023, 12(4), 974) is the better
place to look if we need their model details.

## Contradictions with our docs (verdicts recorded)

| Topic | Our docs | New source | Verdict (2026-10-01, `nominal.CONTRADICTIONS`) |
|---|---|---|---|
| Encoder counts per revolution | "not in either manual" (HARDWARE_REFERENCE) | 20-slot disk (manuals); 80 counts/motor rev is INI plus an assumed x4 decode, not stated in any manual | both kept: 80 is an inference, recorded in `nominal.py` comments; a fit decides |
| Encoder zero | not given | zero at hard home (SCORBASE p. 23) | documented: vendor zero is hard home; whether ours matches is what `vendor_limit_report` logs evidence for |
| Shoulder axis height, link length | 364 mm, 220 mm (`nominal.py`, side view) | 349 mm, 221 mm (INI, Kutzer) | measure (height); both kept (links) |
| Elbow span | bound 260 degrees (`check_soft_limit_span`) | controller limits span 275 | adopted 260: the manual's tighter bound stays the calibration gate; 275 stored as a vendor prior |
| Shoulder range | 165 degrees | 158 (datasheet), 155 (INI limits) | both kept: gate stays 165; set soft limits inside 155 |
| Path velocity | 600 mm/s | 700 mm/s (datasheet) | both kept: unused by any check |
| Pitch counts per degree | 33.8 (legacy) | 27.9 (INI) | measure: wrist stays disabled |
| POWER LED with no PC link | orange | red (datasheet) | capture: note the colour at the next visit (not a constant) |
| Homing back-off | ER-4u manual: back off until the switch releases | SCORBASE text is vague; captures must settle it | capture: S1 captures settle it (not a constant) |

## Third-party projects

- <https://github.com/kutzer/ScorBotToolbox>: MATLAB toolbox; vendor INI files, DH table, error codes.
- <https://github.com/talos-rit/esp-driver>: ESP32 replacement controller; gear-stage comments.
- <https://github.com/steveturbek/scorbot_controller>: Arduino replacement; home switches stay pressed for about 200 encoder steps.
- <https://github.com/baijuch/sboter4u>: ROS MoveIt configuration.
- <https://github.com/tidus747/openScorbot>: upstream of our legacy code.
