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
| 4pc manual | `references/manual_scorbot.pdf` (Spanish, ER-4pc, older controller; mechanics likely shared, electronics differ) |
| Kutzer | USNA ScorBot Toolbox (MATLAB, via Intelitek's USBC DLL): DH table `ScorDHtable.m`, error codes `ScorParseErrorCode.m` |

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
  manual p. 35, INI `CountsPerRound=20`) x 4 quadrature = 80 counts per motor
  revolution, x 127.7:1 motor gearbox, x a final stage (base 5:1 from 120:24,
  shoulder and elbow 4:1, wrist 23:12). This replaces the "~402 counts per
  motor revolution" hypothesis in `nominal.py`. The 4pc manual gives 127.7:1 in
  the parts list (p. 40) and 127.1:1 in the spec table (p. 11); the INI fits 127.7.
- The legacy pitch scale (33.8) disagrees with the INI (27.9). Wrist jogs stay
  disabled; resolve this with a measurement before any wrist motion.

Geometry (vendor `ROB_4u.INI`, matches Kutzer's DH table): base height 349 mm,
joint-1 offset 16 mm, upper arm 221 mm, forearm 221 mm, gripper 145 mm.
`nominal.py` has 364 mm and 220 mm; see "Contradictions" below.

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
- Not covered by any source: behaviour on communication loss, e-stop recovery
  details, whether re-homing is required after an e-stop.

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

## Contradictions with our docs (to resolve)

| Topic | Our docs | New source |
|---|---|---|
| Encoder counts per revolution | "not in either manual" (HARDWARE_REFERENCE) | 20-slot disk, 80 counts/motor rev (4pc manual, INI) |
| Encoder zero | not given | zero at hard home (SCORBASE p. 23) |
| Base height, link length | 364 mm, 220 mm (`nominal.py`) | 349 mm, 221 mm (INI, Kutzer) |
| Elbow span | bound 260 degrees (`check_soft_limit_span`) | controller limits span 275 |
| Shoulder range | 165 degrees | 158 (datasheet), 155 (INI limits) |
| Path velocity | 600 mm/s | 700 mm/s (datasheet) |
| Pitch counts per degree | 33.8 (legacy) | 27.9 (INI) |
| Shoulder/elbow scale difference | timing belts (PHYSICAL_CALIBRATION) | gears; 4:1 final stage vs base 5:1 (4pc p. 16) |
| POWER LED with no PC link | orange | red (datasheet) |
| Homing back-off | ER-4u manual: back off until the switch releases | SCORBASE text is vague; captures must settle it |

## Third-party projects

- <https://github.com/kutzer/ScorBotToolbox>: MATLAB toolbox; vendor INI files, DH table, error codes.
- <https://github.com/talos-rit/esp-driver>: ESP32 replacement controller; gear-stage comments.
- <https://github.com/steveturbek/scorbot_controller>: Arduino replacement; home switches stay pressed for about 200 encoder steps.
- <https://github.com/baijuch/sboter4u>: ROS MoveIt configuration.
- <https://github.com/tidus747/openScorbot>: upstream of our legacy code.
