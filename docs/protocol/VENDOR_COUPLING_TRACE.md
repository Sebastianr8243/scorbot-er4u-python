# Vendor coupling during homing, traced in `USBC.dll`

Follow-up to [VENDOR_HOMING_TRACE.md](VENDOR_HOMING_TRACE.md), which left open why the coupled-motor numbers in the homing offset move and the homing drive look inconsistent with the DLL's own counts-to-angles formula.

**Status: from disassembly, unverified on the arm.** Read as text from the decompiled 2018 build (cross-checked against the 2008 build and the 2018 assembly). The DLL was never loaded or run. Labels as in the other vendor docs: **V** read in the code, **I** our inference, **not found** when unresolved. Axes are 0-based: 0 base, 1 shoulder, 2 elbow, 3 wrist m1, 4 wrist m2. Angles are the DLL's own joint convention (`scorbot/vendor_model.py`); the toolbox convention negates shoulder, elbow and pitch.

## Answer in one paragraph

To move the shoulder motor by -190 counts while keeping the elbow, pitch and roll joint angles fixed, the count change is **(base, sh, el, m1, m2) = (0, -190, +190, +47, -47)** (exactly +46.7/-46.7). The vendor's homing offset move sends **(0, -190, +190, +95, -95)**, which changes pitch by +1.73 deg. The vendor's wrist term is a fixed integer half of the offset, with no `NoEnc90` term; the joint-preserving wrist term is the offset times `NoEnc90(wrist) / NoEnc90(shoulder)` = 0.246. So the factor-2 disagreement is real and is in the vendor's coupling, not in our model. It does not affect where the vendor's home ends up, because every coupled counter is re-homed or zeroed afterwards (I, section 4).

## 1. The offset move (`0x10009f52`), V

The function copies the current measured positions into a target vector, adds the offset `D` to the homed axis, adds coupled terms by axis, and plans one absolute point move to that vector (`0x1000d164`, speed 0.2, accelerations 0.3). The coupled terms use only the `[Gearing]` signs copied by `0x1002f69e`: `0x1008aba8` = Gearing 1 (record `+0x6C0`), `0x1008abac` = Gearing 3, `0x1008abae` = Gearing 4. With the ER-4u values (1, -1, -1, 1):

| Homed axis | Elbow | Wrist m1 | Wrist m2 | Homed axis |
|---|---|---|---|---|
| Shoulder (Gearing 1 = 1, Gearing 3 = -1) | -D | -D/2 | +D/2 | +D |
| Elbow (Gearing 3 = -1) | | +D/2 | -D/2 | +D |
| Wrist m1 (Gearing 3 = -1) | | | -D | +D |
| Wrist m2 (Gearing 4 = 1) | | +D | | +D |

- The halving is a signed integer divide by 2, truncating toward zero (V in the assembly: `CDQ`, `SUB EAX,EDX`, `SAR EAX,1`, eight times in this function).
- No `NoEnc90`, `HorizPos` or floating-point term enters the coupled targets (V: the only inputs are the offset, the gearing words and the measured positions).
- The fine edge search uses the same rules: its back-off steps go through this function and its creep step `0x1000a75c` has an identical coupling switch (V). So a 22-count shoulder back-off also moves elbow -22 and the wrist motors -11/+11; a 2-count creep moves them -2 and -1/+1.
- The 2008 build (`0x10009cc2`, `0x10007039`) has the same divides (V).

The numbers in VENDOR_HOMING_TRACE.md section 5 were read correctly: shoulder -190 gives elbow +190, m1 +95, m2 -95; elbow +45 gives m1 +22, m2 -22 (V).

## 2. The search drive (`0x100072c9`), V

Same switch shape, but the coupled values are the drive value `V` scaled:

| Homed axis drives V | Elbow | Wrist m1 | Wrist m2 |
|---|---|---|---|
| Shoulder | -V/2 | -V/4 | +V/4 |
| Elbow | | +V/2 | -V/2 |
| Wrist m1 | | | -V |
| Wrist m2 | | +V | |

- Shoulder: elbow is `-V/2` (C integer divide); the wrist values are `V/4` by an arithmetic shift with the round-toward-zero fix-up, so truncation toward zero (V). The section 3 table of VENDOR_HOMING_TRACE.md is correct.
- Again only gearing signs and fixed shifts; no `NoEnc90` (V).
- Small difference (V): for wrist m2 the drive function branches on Gearing 3, the offset move on Gearing 4. With the ER-4u values both give m1 and m2 the same value, so nothing changes on this arm.
- Relative to the offset move, the shoulder's coupled drive values are exactly half the offset ratios (elbow 1/2 instead of 1, wrist 1/4 instead of 1/2); the elbow's coupled drive equals its offset ratio (1/2) (V for the numbers).

## 3. What the DLL's own formulas say

`counts_to_joints` (`0x100303db`; V for the structure, scales checked below; matches `vendor_model.counts_to_joints`):

- shoulder = (e1 - HorizPos1) k1
- elbow = (e2 + e1 - HorizPos2) k2 (Gearing 1 = 1 adds the shoulder encoder)
- pitch = -shoulder + ((e3 - e4) / 2 - HorizPos3) k3 - elbow (Gearing 3 = -1 takes **half** the wrist difference; Gearing 2 = -1 subtracts the elbow)
- roll = ((e3 + e4) / 2 - HorizPos4) k4

with k = (pi/2) / NoEnc90 and NoEnc90 = (-12770, -10216, 10216, 2511, 2511). The halving in pitch is an integer `/ 2` in the code (V).

**The scales, checked (V).** The pitch term reads its scale from `0x1008ab08`, which is slot 3 of the per-axis table at `0x1008aaf0`. That table is written only by `0x100068ff`, called from the axis-file loader `0x100043b2` with each axis's own `NoEnc90` (record `+0xDC`) and `HorizPos` (`+0xE0`). In the assembly it computes counts per unit = `NoEnc90 / 1.5707963267948966` (or `/ 90.0` when a per-axis flag bit 0 at planner `+0x2A4` is set, a degrees mode), then radians per count = `1.0 / that`, and stores `-HorizPos` at `0x1008aad0 + 4 n`. The three doubles were read from the 2018 DLL file with `peread.py` (`0x10065cc0` = pi/2, `0x10065cd0` = 90.0, `0x10065cd8` = 1.0). The same code runs for every axis, so there is no extra x2 or /2 on the wrist scales. Under either branch the ratio wrist/shoulder scale is the same, so the verdict below does not depend on the flag. No source independent of the DLL confirms the wrist scale (the toolbox's 0.3 mm home match goes through the same DLL, so it is circular).

`joints_to_counts` (`0x10030ed2`, V for the structure; the operands of each `__ftol` are hidden in the decompile and were read in the earlier asm and emulation work, VENDOR_DLL_PROTOCOL.md section 10): elbow motor = elbow term minus shoulder counts; pitch counts p come from (shoulder + elbow + pitch); m1 = p + r, m2 = r - p. Its structure confirms the half: m1 +x, m2 -x is x pitch counts.

Running the model (`scorbot.vendor_model.counts_to_joints`, DLL convention, deg):

| Count change | shoulder | elbow | pitch | roll |
|---|---|---|---|---|
| sh +1000 | -8.81 | +8.81 | 0 | 0 |
| el +1000 | 0 | +8.81 | -8.81 | 0 |
| m1 +1000 | 0 | 0 | +17.92 | +17.92 |
| m2 +1000 | 0 | 0 | -17.92 | +17.92 |
| vendor shoulder offset (0, -190, +190, +95, -95) | +1.674 | 0 | **+1.731** | 0 |
| joint-preserving (0, -190, +190, +47, -47) | +1.674 | 0 | +0.011 | 0 |
| shoulder and elbow only (0, -190, +190, 0, 0) | +1.674 | 0 | -1.674 | 0 |
| vendor elbow offset (0, 0, +45, +22, -22) | 0 | +0.396 | **+0.392** | 0 |
| joint-preserving (0, 0, +45, +11, -11) | 0 | +0.396 | -0.002 | 0 |

(The caller's figures are the toolbox convention, with shoulder, elbow and pitch negated; the magnitudes agree.)

## 4. Verdict on the factor of 2

**The joint-only count vectors** (inverse of `0x100303db`, per count of the moved motor, I derived from V formulas, checked with `joints_to_counts`):

| Move only | base | sh | el | m1 | m2 |
|---|---|---|---|---|---|
| Shoulder, by Ds counts | 0 | Ds | -Ds | -0.2458 Ds | +0.2458 Ds |
| Elbow, by De counts | 0 | 0 | De | +0.2458 De | -0.2458 De |
| Pitch, by p counts | 0 | 0 | 0 | +p | -p |
| Roll, by r counts | 0 | 0 | 0 | +r | +r |

0.2458 = NoEnc90(wrist) / |NoEnc90(shoulder or elbow)| = 2511 / 10216.

- The vendor's offset coupling has the right signs and the right elbow term, but its wrist term is D/2, about **2.03 times** the joint-preserving 0.246 D (V for the code, I for the comparison). It equals neither the relative-pitch-preserving vector (+-47) nor the gripper-to-floor-preserving vector (wrist 0, since moving the shoulder and elbow motors alone already keeps the gripper's absolute pitch, per `vendor_model.absolute_angles`). It overshoots the first by as much as the second undershoots.
- Nothing in the code suggests a deliberate geometric choice: the coupling switch is generic, driven only by gearing signs, and is shared with other robot types (I). The fixed `/2` and `/4` read as hand-picked integer approximations, not a formula from `NoEnc90` (I).
- **It does not matter for the vendor's final home** (I, from VENDOR_HOMING_TRACE.md sections 1 and 5): after the shoulder's offset the DLL zeroes elbow, m1, m2 and shoulder with `48`; the elbow, roll and pitch are then homed to their own switches and offsets and zeroed again; the end-of-group sweep zeroes all six. The coupling only changes the transient pose during homing (about 1.7 deg of pitch for the shoulder offset). This holds for `Home('&')` and `Home('A')`. A single-axis `Home(1)` or `Home(2)` also runs the common tail, so it zeroes the elbow and wrist counters with `48` at the pose the coupled offset left, not at their switches (V for the calls, I for the consequence).
- **The drive values are not position ratios at all.** The search runs in Mode T (`4F <mask> 54`, sent to the axis and its coupled motors by `0x100079cd`, V), and the exported `MoveTorque` uses the same Mode T plus the same `4D` builder and logs its value as **"PWM"** (V, its log string). So the `4D` value is a motor drive (PWM) command, open loop at the PC (I). The coupled fractions are feed-forward drive levels to make the coupled motors roughly follow, and a ratio of PWM levels is not a ratio of counts (I). That is why they need not match the offset vectors.

## 5. Units of the homing `Velocity` (`4D` value)

- V: the `4D` value is called PWM in `MoveTorque`'s log message, and `MoveTorque` puts the axis in Mode T first. A per-axis `TorquePWM` parameter (index 9, value 1) is downloaded at connect.
- Not found: the PWM scale (duty fraction per unit, or full scale). The homing values 90-165 and the controller's mapping to motor voltage are not in the DLL. A capture during a SCORBASE home plus the observed speed would bound it.

## 6. For our SDK

- A joint move "shoulder only" in the DLL's joint sense is (0, Ds, -Ds, -0.246 Ds, +0.246 Ds). For Ds = -190: **(0, -190, +190, +47, -47)**.
- A faithful copy of the vendor's homing offset is (0, -190, +190, +95, -95) and leaves pitch about 1.73 deg off the pose the joint-preserving vector gives. Either is followed by homing of the elbow and wrist, so the choice only affects the transient pose; the joint-preserving one is the one that agrees with `source_model` (I).
- None of this is verified on the arm. The model's scales are vendor priors, not measurements (`docs/manual/ER4U_MODEL.md`).

## Unresolved

- The PWM scale of `4D` (not found).
- Whether the controller itself adds any coupling in Mode T or Mode H (not found; it is not in the DLL).
- Why the vendor chose `/2` for the wrist in the offset move (not found; no comment or alternative branch in either build).
