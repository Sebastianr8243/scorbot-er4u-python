# ER-4U hardware reference

Facts from the Intelitek manuals for the lab's arms, with what each one means for this code. Page numbers are the manuals' own. **Manual values are nominal; calibration still needs measurement on each arm** (see [PHYSICAL_CALIBRATION.md](PHYSICAL_CALIBRATION.md)).

| Manual | Catalog | Date | Local file (not in git) |
|---|---|---|---|
| SCORBOT-ER 4u User Manual | #100343 Rev. B | Sept 2001 | `references/er4u_manual_100343-b.pdf` |
| Controller-USB User Manual | #100341 Rev. G | Feb 2007 | `references/controller_usb_manual_100341-g.pdf` |
| SCORBOT-ER 4pc User Manual (Spanish) | #100269 Rev. A | Dec 1999 | `references/manual_scorbot.pdf`: an older model with a PC-card controller; its encoder circuitry differs |

The two Intelitek PDFs are copyrighted and this repository is public, so they are kept out of git (`.gitignore`). Get copies from the lab or from Intelitek.

## Controller-USB: safety behaviour (pp. 5-6, 10-11, 25)

| Manual statement | Meaning here |
|---|---|
| "On communication failure, motor power shutdown." | The controller should cut motor power if the USB link stops, for example if the Python sync worker dies or Python crashes. **Stated, not yet observed with this code**: confirm on the bench by watching the MOTORS LED. |
| "Hardware watchdog for each axis protects against software faults." | Per-axis protection inside the controller, independent of Python. |
| "Impact, software limit protection" and "Axis position error" are controller functions. | The controller has its own following-error and impact detection. What the legacy protocol's error bytes report is still unknown. |
| Short-circuit protection; driver shutdown on overheating; motor power shutdown on failure. | More hardware protection that does not depend on this code. |
| EMERGENCY button: motor power is disconnected, all motion stops, the MOTORS LED turns off, and the controller enters COFF. After release it stays in COFF until CON. | The physical stop is authoritative, as the docs already say. After an e-stop, motors stay off until a new control-on. |

### LEDs: the only direct view of controller state (pp. 5, 10-11)

| LED | State | Meaning |
|---|---|---|
| POWER | green | Powered and communicating with the PC |
| POWER | orange | Powered, **not** communicating with the PC (the ER-4u datasheet says red; note the colour you see) |
| POWER | flashing | PC-USB communication timeout |
| MOTORS | green | Power is supplied to the motors |
| MOTORS | off | COFF, EMERGENCY pressed, **communication time-out**, over-current, or the PC software closed |
| EMERGENCY | red | Emergency stop active |

The SDK reports `enabled` from its own command history, not from the controller. **The MOTORS LED is the only independent evidence of motor power**, so the G1 observation sheet records it at each step.

### Control (p. 4)

- Real-time PID with PWM (15 kHz H-bridge drivers, 3 A standard, 7 A peak), NEC V853 microcontroller.
- "1.5 ms control cycle": the controller's internal servo loop. The host packet period (about 13 ms by the legacy sleeps) is separate; measure it in the idle capture.
- Position feedback from incremental optical encoders on each axis. The controller itself supports joint, linear and circular interpolation through SCORBASE; the legacy USB protocol used here exposes only what `openScorbot/` implements.

## SCORBOT-ER 4u arm (pp. 4-8)

| Item | Manual value | Used for |
|---|---|---|
| Axis 1, base | 310° | Upper bound for measured soft limits |
| Axis 2, shoulder | +130° / −35° | Upper bound |
| Axis 3, elbow | ±130° | Upper bound |
| Axis 4, wrist pitch | ±130° | Upper bound |
| Axis 5, wrist roll | unlimited mechanically, ±570° electrically | Upper bound |
| Maximum operating radius | 610 mm | Sanity check for `scorbot.kinematics` |
| Gear ratios | motors 1-3: 127.1:1; motors 4-5: 65.5:1; gripper: 19.5:1 | Prior for counts per degree |
| Transmission | gears, timing belts, lead screw | The 4pc manual (p. 16) has base and shoulder gear-driven, elbow on timing belts, wrist on belts plus a bevel differential. The vendor parameter files fit a final gear stage of 5:1 (base) vs 4:1 (shoulder, elbow), which explains the different counts/degree |
| Position repeatability | ±0.18 mm at the TCP (gripper tip) | **A target to test**: repeated home and jog trials |
| Maximum payload | 1 kg including gripper | Experiment limit |
| Maximum path velocity | 600 mm/s | Experiment limit |
| Weight | 10.8 kg | Handling and mounting |
| Wrist | pitch and roll both driven by motors 4 + 5 | Confirms the two-motor differential; wrist jogs stay gated |

**Not in either manual:** encoder counts per revolution, encoder zero, and joint sign convention. Counts to degrees must be measured.

**From other vendor sources (priors, not measurements; see [MANUAL_AND_PRIOR_ART_FINDINGS.md](MANUAL_AND_PRIOR_ART_FINDINGS.md)):**

- Encoder: a 20-slot disk (4pc manual p. 35), 80 counts per motor revolution in quadrature. Vendor parameter files give 141.89 counts/° base, 113.51 shoulder and elbow, 27.90 per wrist motor (`scorbot.nominal.VENDOR_COUNTS_PER_DEGREE`). The legacy pitch scale (33.8) disagrees.
- Encoder zero: SCORBASE zeroes all encoders at hard home (SCORBASE manual p. 23); its home pose is base 0, shoulder -120, elbow 95, pitch 88, roll 0 degrees (p. 28).
- Controller limits in the parameter files: base +174/-132, shoulder +31/-124, elbow +160/-115 (275° span, wider than this manual's 260°; the stricter manual bound stays enforced), pitch +115/-113.
- Geometry in the parameter files: base height 349 mm, upper arm and forearm 221 mm, gripper 145 mm (this manual's side view: 364 and 220 mm).

### Homing (p. 8)

The manual's procedure: move each axis until its switch activates, then move slightly until the switch **turns off**; that point is home. The legacy `openScorbot/setHome.py` instead continues 12 full-speed packets past the switch and stops (review finding H3). The Python home pose therefore differs from the vendor home pose, and a vendor-style back-off is the reference for fixing it after hardware traces.

The vendor parameter files home in the order shoulder, elbow, roll, pitch, base, gripper, with per-axis offsets after the switch (shoulder -190, elbow +45, pitch +850, roll -690 counts). The legacy code homes pitch before roll and does not home the gripper; SCORBASE homes the gripper with the five axes (SCORBASE manual p. 24). The captures should show the vendor sequence.

### Encoder check (maintenance section)

Mark a line on the axis, move away, return to the line: the encoder reading "should be within several counts of the first reading". A growing error means a failing encoder. This is a cheap repeatability test to run once the idle capture is understood.
