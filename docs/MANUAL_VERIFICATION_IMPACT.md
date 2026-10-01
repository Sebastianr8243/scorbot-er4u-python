# What the manual verification changes

Status: analysis, 2026-09-30. Inputs: `docs/SCORBOT_Manual_Verification.md` and
`docs/SCORBOT_Numeric_Facts.yaml` (main, e3ce4c6), `docs/DEPLOYMENT_OPTIONS.md`, and a
read-only audit of this repo. "The manual says" means the Intelitek PDFs say it. That
is still **not** hardware evidence. Nothing here was run on the arm.

## The short version

1. **The core design holds up.** The things the manual leaves unknown (counts per
   degree, zero angles, directions, home retention, timeouts) are exactly the things
   the SDK already refuses to assume.
2. **Three real gaps.**
   - The SDK cannot tell when the controller turns the motors off on its own.
   - Home can shift silently because of electrical noise.
   - Nobody knows whether the arm holds its position with the motors off.
3. **Several numbers in the repo claim more than the manual supports.** They are easy
   to fix.
4. **A hypothesis to test, not a finding.** The controller spec table lists "CP:
   Joint; Linear; Circular", "Speed or Travel time definitions" and "Software
   controlled acceleration/deceleration" (Controller p.4, Figure 1-4). It does not say
   whether the controller firmware does this or the PC software (SCORBASE) does. Our
   own code computes every setpoint on the host. S1 captures B and C can settle it.
5. **Copyright.** The verification doc copies large parts of the manual word for
   word, and it is on a public repo. You need to decide what to do about it.

## 1. What we are doing right

| Practice | Why the manual backs it | Where |
|---|---|---|
| Never assume a home survives `disable()`, faults or reconnecting | The manual does not say whether home survives COFF/CON, e-stop, reconnect or a power cycle (I-6) | `scorbot/robot.py:192, 339, 537` clear `_homed` and `_home_counts` |
| Soft-limit checks compare spans only, never zero or sign | The manual gives travel ranges but no zero position and no positive direction (I-2) | `scorbot/nominal.py:check_soft_limit_span`, `calibration.py` |
| Wrist jogs stay locked | The manual gives the 4/5 differential only qualitatively. Ratio and signs are absent (I-10) | `Scorbot.jog_joint`, HZ-14 |
| Counts per degree is measured, never derived | Decode mode is absent and the arm gear ratio conflicts (127.1 vs 127.7) (I-1, C1) | `PHYSICAL_CALIBRATION.md`, `fit_calibration.py` needs physical reference data |
| `disable()` is never called an e-stop | The e-stop cuts motor power in hardware. Software disable is queued | `CLAUDE.md`, README |
| Homing description | It matches the manual's text: "move until switch activates, then slightly until it shuts off" | `HARDWARE_REFERENCE.md:69-71` |
| An idle keepalive exists | The controller cuts power on communication failure. The legacy sync thread keeps sending idle packets | `openScorbot/libsync.py` |
| SDK `speed` 1-20 is not the controller's speed level | Traced: it is the most encoder counts added per host packet, never a packet byte. Not a bug | `openScorbot/motion_profile.py:increments_for_counts` |
| Legacy scale labelled as an assumption | Consistent with "counts per degree cannot be computed from the manual" | `motion_profile.py:9-15` |

## 2. What we need to change

### A. Safety gaps (fix before the next arm visit)

1. **The SDK cannot see motors-off.**
   - Checked: `scorbot/state.py` decodes only counts, error words and the switch byte.
     `RobotState.enabled` is just what our own code last asked for.
   - The manual says the controller cuts motor power on e-stop, over-current or a
     communication timeout of unknown length (G2, G3).
   - When that happens, `_enabled` and `_homed` stay True. The first anyone hears of it
     is probably a failed next jog (inferred).
   - Change:
     - Ask the MOTORS LED question on every arm press, not only after enable.
     - Log the controller error words between jogs.
     - Add a simulator fault, "controller dropped motors while SDK thinks enabled", and
       test that it latches.
     - State the gap in HZ-17 (`SAFETY_CASE.md:59`).
2. **Home can shift silently.**
   - Controller manual p.29, troubleshooting item 9: electrical noise can change Home
     suddenly, and the robot carries on relative to the new Home.
   - No repo file mentions this.
   - Change:
     - Add a hazard row.
     - On this branch's back-to-start and marked positions design, before BACK or GOTO,
       check that current counts minus `home_counts` agree with the logged `travel`, and
       refuse if they don't.
     - Say plainly that back-to-start is not a re-home.
3. **Holding with motors off is unknown.**
   - The manual has no brake or gravity-holding spec (I-9).
   - Since `beb4153`, every exit after enable turns the motors off. Finishing and the
     physical stop also cut power, with the shoulder or elbow possibly off home.
   - Change: add a hazard row and a `LAB_SESSION.md` line: "whether the arm holds with
     motors off is unverified; keep hands and objects clear below the arm".
4. **Recovery advice re-energises the motors.**
   - The branch spec's "start a new session and home again" runs `connect()`.
   - The handshake turns the motors on at whatever pose the arm stopped in (HZ-01).
   - Change: the guidance should say so, and require the start pose to be restored
     before connecting.
   - Also, "result 1 = stall/collision/impact" should also list e-stop, timeout and
     over-current.
5. **Stalled-but-alive sync thread** (inferred).
   - If the legacy worker blocks while still alive (a console QuickEdit pause, or
     paging on a low-RAM PC), traffic stops and the controller may time out with no
     SDK fault.
   - Change: consider a packet-age watchdog in `get_state`, and note it in HZ-08/HZ-16.

### B. Numbers that claim more than the manual supports

| Where | Problem | Fix |
|---|---|---|
| `nominal.py:69`, `HARDWARE_REFERENCE.md:52`, `tests/test_nominal.py` | `GEAR_RATIO_ARM` and `counts_per_degree` use the spec-table 127.1:1. The vendor-prior block a few lines later already uses 127.7:1. The vendor INI matches 127.7 exactly (80 x 127.7 x 5 / 360 = 141.89) | Reconcile the two in one place, mark the ratio unresolved, cite arm p.4 (127.1) and p.28 (127.7) |
| `nominal.py:counts_per_degree`, wrist `gear_ratio` | 65.5:1 is a *motor* gearbox, not the wrist joint ratio | Refuse wrist joints in the helper |
| `nominal.py:94 BASE_HEIGHT_MM = 364` | 364 mm is base-bottom to the **shoulder axis**. The base pedestal is 190 mm. This carries into `test_kinematics.py`, `test_nominal.py` and the "349 vs 364" comparison | Rename to `SHOULDER_AXIS_HEIGHT_MM`, add a 190 mm pedestal value |
| `HARDWARE_REFERENCE.md:64`, `MANUAL_AND_PRIOR_ART_FINDINGS.md:34-36` | "80 counts per motor rev in quadrature" is credited to a manual. The x4 decode is absent from both manuals | Label it inferred, from INI plus assumed x4 |
| `HARDWARE_REFERENCE.md:31` | "MOTORS off when the PC software closed". The manual says "SCORBASE closes" | Narrow it, and add "Python exit unverified" |
| `HARDWARE_REFERENCE.md:19` | "Controller has its own following-error and impact detection" | The manual only lists these as features, with no thresholds or behaviour |
| `MANUAL_AND_PRIOR_ART_FINDINGS.md:107-108` | Says no source covers communication loss or e-stop recovery | The manual covers both. Only re-homing after an e-stop is absent |
| `MANUAL_AND_PRIOR_ART_FINDINGS.md:154` | Belts-vs-gears row looks stale | The manual says spur gears for base and shoulder, belts for the elbow |
| README / jog docs | "speed 1-20" reads like the SCORBASE speed | Say "max counts per packet (about 13 ms), not SCORBASE's 10 speed levels" |
| `test_kinematics.py:68-74` | Treats 610 mm reach as a tool-chain check | 610 mm is the top-view envelope radius. Keep the tolerance and fix the comment |
| Missing | Operating temperatures | Arm 2-40 °C, controller 10-35 °C. Add to `HARDWARE_REFERENCE` and the lab checklist |

### C. Plans that are now incomplete

- **DEPLOYMENT_OPTIONS / LAB_PLATFORM_VISION risk framing (hypothesis).** The
  controller spec lists joint, linear and circular path control with speed or
  travel-time definitions (G1). It does not say whether the firmware or SCORBASE does
  the interpolation.
  - If the firmware does it, a waypoint-plus-speed command, like SCORBASE go-to, sent
    at 1-5 Hz could carry ACT action chunks. The S1 question would then have three
    answers: streaming, waypoints, or 1 Hz jogs.
  - The capture of a SCORBASE go-to (S1 B/C) answers this: does it send one target, or
    a stream of setpoints?
- **Server-to-lab-PC action link.**
  - Network loss never trips the controller timeout, because only the lab PC touches
    USB. The lab PC needs its own staleness deadline: stop accepting actions, then latch.
  - Do **not** run inference in the same process as the sync thread. GIL and GC pauses
    could starve the keepalive and cause a shutdown mid-motion (inferred).
- **LeRobot plugin and exporter features.**
  - Observation: six raw motor counts with motor-space names. Never label motors 4/5
    as pitch and roll.
  - Action: start with three joints (base, shoulder, elbow), matching the gates.
  - Gate the wrist in joint space later, through a versioned 2x2 calibration.
  - The gripper has no home switch, so gripper counts cannot be compared across
    sessions without its own reference.
  - The controller's speed level (1-10) is a categorical setting. Fix it per dataset or
    put it in the action.
- **S1 capture card.** Add:
  - Positions shown before and after COFF/CON and before and after e-stop. Does
    SCORBASE demand a re-home?
  - A new capture: close SCORBASE while in Control On, and record the LED timing and
    the final packets. This shows whether there is an explicit motors-off packet, or
    gives a timeout estimate.
  - Three or more speed levels, including both ends, with the exact values written
    down.
  - At least 60 s of SCORBASE idle traffic (its keepalive cadence).
  - Home twice, and check whether counts reset to a fixed value.
  - LED state at power-on before SCORBASE starts.

### D. Copyright (decided 2026-09-30: keep the doc as is)

- `SCORBOT_Manual_Verification.md` reproduces roughly 30% or more of the manuals near
  word for word:
  - all warnings (section H, about 13 KB);
  - full parts tables (T1, 167 rows);
  - wiring tables (F, about 14 KB);
  - panel legends and I/O tables (T2-T6).
- `CLAUDE.md` bans committing the manuals for exactly this reason, and the repo is
  public.
- Option: keep the findings, verdicts and page citations. Move H, F and T1-T6 to a
  gitignored local file.
- The text stays in git history unless history is rewritten. Not legal advice.

## 3. What we can do now, without the arm

In priority order:

1. ~~Decide the copyright question.~~ Decided: keep the doc as is.
2. **Docs-and-numbers fix** (section B). Done on `docs/manual-verification-fixes`, together with the item 3 hazards. Small, offline and testable. It removes
   overclaims before anyone builds on them.
3. **Safety case update.** Add hazards for motors-off you can't see, home shift from
   noise, holding with motors off unknown, and a stalled sync thread. Note the HZ-17
   gap.
4. **Propose spec changes to this branch's design before building it** (needs your
   review).
   - Add the drift check before BACK/GOTO.
   - Add the MOTORS LED prompt on every arm press.
   - Correct the recovery guidance.

   This answers the open question "finish this branch or start camera work": finish
   this branch first, with these changes.
5. **Simulator fault plus test** for "controller dropped motors, SDK still thinks
   enabled".
6. **S1 capture card additions** (section C), ready for the next visit.
7. **Update DEPLOYMENT_OPTIONS.**
   - Add the waypoint option.
   - Add the lab-PC staleness deadline.
   - Say inference never runs in the sync process.
   - Treat speed level as an action parameter.
   - Record the plugin and exporter feature decisions.
8. **Then camera capture (S3)**, as planned.

Needs the arm or vendor, not now:

- installed gear ratio;
- decode multiplier;
- zero angles and signs;
- timeout length;
- holding behaviour;
- gripper force;
- e-stop circuit topology.

Measuring counts per degree directly (G2) makes the 127.1 vs 127.7 question moot.
