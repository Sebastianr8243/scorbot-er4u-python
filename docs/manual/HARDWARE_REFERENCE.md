# ER-4U hardware reference

Purpose: look up manual facts (limits, LEDs, homing) and what each means for this code.

Facts from the Intelitek manuals for the lab's arms, with what each one means for this code. Page numbers are the manuals' own. **Manual values are nominal; calibration still needs measurement on each arm**.

| Manual | Catalog | Date | Local file (not in git) |
|---|---|---|---|
| SCORBOT-ER 4u User Manual | #100343 Rev. B | Sept 2001 | `references/er4u_manual_100343-b.pdf` |
| Controller-USB User Manual | #100341 Rev. G | Feb 2007 | `references/controller_usb_manual_100341-g.pdf` |
| SCORBOT-ER 4pc User Manual (Spanish) | #100269 Rev. A | Dec 1999 | `references/manual_scorbot.pdf` (local only, not in the repository since 2026-10-05): an older model with a PC-card controller; its encoder circuitry differs |

The two Intelitek PDFs are copyrighted and this repository is public, so they are kept out of git (`.gitignore`). Get copies from the lab or from Intelitek.

## Controller-USB: safety behaviour (pp. 5-6, 10-11, 25)

| Manual statement | Meaning here |
|---|---|
| "On communication failure, motor power shutdown." | The controller should cut motor power if the USB link stops. That a stopped host program or a Python crash counts as a communication failure is an inference: the manual gives no timeout, valid-packet definition or shutdown latency. **Stated, not yet observed with this code**: confirm on the bench by watching the MOTORS LED. |
| "Hardware watchdog for each axis protects against software faults." | Per-axis protection inside the controller, independent of Python. |
| "Impact, software limit protection" and "Axis position error" are controller functions. | The manual lists these functions but gives no thresholds or stop behaviour, so do not rely on them. What the legacy protocol's error bytes report is still unknown. |
| Short-circuit protection; driver shutdown on overheating; motor power shutdown on failure. | More hardware protection that does not depend on this code. |
| EMERGENCY button: motor power is disconnected, all motion stops, the MOTORS LED turns off, and the controller enters COFF. After release it stays in COFF until CON. | The physical stop is authoritative, as the docs already say. After an e-stop, motors stay off until a new control-on. |

### LEDs: the only direct view of controller state (pp. 5, 10-11)

| LED | State | Meaning |
|---|---|---|
| POWER | green | Powered and communicating with the PC |
| POWER | orange | Powered, **not** communicating with the PC (the ER-4u datasheet says red; note the colour you see) |
| POWER | flashing | PC-USB communication timeout |
| MOTORS | green | Power is supplied to the motors |
| MOTORS | off | COFF, EMERGENCY pressed, **communication time-out**, over-current, or SCORBASE closes (stated for SCORBASE only; what happens when a Python program exits is unverified) |
| EMERGENCY | red | Emergency stop active |

No decoded state byte is known to show motor power, so if the controller turns the motors off by itself (e-stop, over-current, timeout) nothing in this code notices until a later command fails. **The MOTORS LED is the only independent evidence of motor power**, so watch it at each lab step.

### Control (p. 4)

- Real-time PID with PWM (15 kHz H-bridge drivers, 3 A standard, 7 A peak), NEC V853 microcontroller.
- "1.5 ms control cycle": the controller's internal servo loop. The host packet period (about 13 ms by the legacy sleeps, not measured here) is separate.
- Position feedback from incremental optical encoders on each axis. The controller itself supports joint, linear and circular interpolation through SCORBASE; the vendor DLL calls used here are listed in `docs/API.md`.

## SCORBOT-ER 4u arm (pp. 4-8)

| Item | Manual value | Used for |
|---|---|---|
| Axis 1, base | 310° | Upper bound for measured soft limits |
| Axis 2, shoulder | +130° / −35° | Upper bound |
| Axis 3, elbow | ±130° | Upper bound |
| Axis 4, wrist pitch | ±130° | Upper bound |
| Axis 5, wrist roll | unlimited mechanically, ±570° electrically | Upper bound |
| Maximum operating radius | 610 mm (top-view envelope) | Rough sanity check for a reach limit (kinematics code is on the `stack2` branch); the manual does not say which point it is measured to |
| Heights (side view, p. 6) | base bottom to shoulder axis 364 mm; base pedestal 190 mm | Prior for kinematics (the `stack2` branch holds the constants) |
| Gear ratios | motors 1-3: 127.1:1 in the specification table (p. 4) but 127.7:1 for S309/S310 in the parts list (p. 28), unresolved; motors 4-5: 65.5:1; gripper: 19.5:1 | Prior for counts per degree (arm axes only: 65.5:1 is the wrist *motor* gearbox, the differential ratio is not given). Measuring counts per degree directly makes the conflict moot |
| Transmission | gears, timing belts, lead screw | The 4pc manual (p. 16) has base and shoulder gear-driven, elbow on timing belts, wrist on belts plus a bevel differential. The vendor parameter files fit a final gear stage of 5:1 (base) vs 4:1 (shoulder, elbow), which explains the different counts/degree |
| Position repeatability | ±0.18 mm at the TCP (gripper tip) | **A target to test**, at the tool tip: encoder-count repeatability from home and jog trials does not test it |
| Maximum payload | 1 kg including gripper | Experiment limit |
| Maximum path velocity | 600 mm/s | Experiment limit |
| Weight | 10.8 kg | Handling and mounting |
| Ambient temperature | arm 2-40 °C; controller 10-35 °C (Controller manual p. 4) | Run only where both hold, i.e. 10-35 °C (inferred combined window) |
| Wrist | pitch and roll both driven by motors 4 + 5 | Confirms the two-motor differential; wrist jogs stay gated |

**Not in either manual:** encoder counts per revolution, encoder zero, and joint sign convention. Counts to degrees must be measured.

**From other vendor sources (priors, not measurements):**

- Encoder: a 20-slot disk with two channels a quarter cycle apart (ER-4u manual pp. 19, 24, 28; 4pc manual p. 35). 80 counts per motor revolution assumes x4 quadrature decoding, which **neither manual states** (inferred from the INI `CountsPerRound=20`). Vendor parameter files give 141.89 counts/° base, 113.51 shoulder and elbow, 27.90 per wrist motor; a desk run through the vendor DLL reproduced them ([../DESK_FINDINGS.md](../DESK_FINDINGS.md)). The legacy pitch scale (33.8) disagrees.
- Encoder zero: SCORBASE zeroes all encoders at hard home (SCORBASE manual p. 23); its home pose is base 0, shoulder -120, elbow 95, pitch 88, roll 0 degrees (p. 28).
- Controller limits in the parameter files: base +174/-132, shoulder +31/-124, elbow +160/-115 (275° span, wider than this manual's 260°; the stricter manual bound stays enforced), pitch +115/-113.
- Geometry in the parameter files: base height 349 mm (probably the DH offset to the shoulder axis, inferred), upper arm and forearm 221 mm, gripper 145 mm (this manual's side view: shoulder axis 364 mm, links 220 mm).

### Homing (p. 8)

The manual's procedure: move each axis until its switch activates, then move slightly until the switch **turns off**; that point is home. The manual gives no search direction, axis order, back-off distance or speed. The vendor DLL's `Home` follows this procedure, so the arm API inherits it.

The vendor parameter files home in the order shoulder, elbow, roll, pitch, base, gripper, with per-axis offsets after the switch (shoulder -190, elbow +45, pitch +850, roll -690 counts). The legacy code homes pitch before roll and does not home the gripper; SCORBASE homes the gripper with the five axes (SCORBASE manual p. 24). The captures should show the vendor sequence.

The Controller-USB manual (p. 29, troubleshooting item 9) says electrical noise can make the Home position change suddenly, with the robot carrying on relative to the new Home. The manual also does not say whether home survives COFF/CON, an e-stop, a USB reconnect or a power cycle. Treat Home as lost after any e-stop, disable, fault or disconnect: re-home before the next move, and check any stored encoder counts against logged travel before reusing them.

### Encoder check (maintenance section)

Mark a line on the axis, move away, return to the line: the encoder reading "should be within several counts of the first reading". A growing error means a failing encoder. This is a cheap repeatability test to run once the idle capture is understood.
