# Desk findings: what the vendor DLL asks for (2026-10-25)

Purpose: results of running the vendor DLL against a fake controller, so the first lab run starts from stated expectations.

Answers to the unknowns in [STATUS.md](STATUS.md) that can be read without the arm. **None of this was measured on the arm.** The numbers come from running the vendor `USBC.dll` against a fake controller that follows the DLL's own setpoints exactly (`tools/usbc_fake/fake_oracle.py`, 44 small moves from three poses), so they show what the DLL asks the motors to do, not how the real arm responds. The fake's switch model is a guess, so the Home pose below is the DLL's own bookkeeping after Home, not a measured pose.

## What the DLL's arrays are

| Array | Index | Unit (from the moves below) |
|---|---|---|
| `encoder` | 0 base, 1 shoulder, 2 elbow, 3 wrist m1, 4 wrist m2, 5 gripper | motor counts |
| `joint` | base, shoulder, elbow, wrist pitch, wrist roll | thousandths of a degree |
| `xyz` | x, y, z, pitch, roll | x, y, z in micrometres; pitch, roll in thousandths of a degree |

After the vendor Home the DLL reports joints `[0, -120.278, 95.021, 88.805, 0]` degrees and xyz `(169.0, 0, 504.3)` mm, which matches the SCORBASE home pose in the hardware reference (base 0, shoulder -120, elbow 95, pitch 88, roll 0).

## What the moves showed

| Unknown | Result | Evidence in the run |
|---|---|---|
| Axis numbering | Motor index 0 base, 1 shoulder, 2 elbow, 3 and 4 the two wrist motors, 5 gripper (0-based) | At the home pose a +10 mm x move changed only indices 1 and 2 and a +10 mm y move mainly index 0. Away from the centre line (pose C, y = 80 mm) an x move also moves the base (+80 counts) and a y move also moves the shoulder (-89): normal for an arm that swings about the base |
| Direction | Positive y is a positive base angle but **negative** base counts (-480 counts for +10 mm y). Positive x lowers the shoulder count (-268) and raises the elbow count (+144) | same moves |
| Counts per degree | Base 141.9 and shoulder 113.5 (ratio of `encoder` to `joint` change, 113.4 to 113.5 over nine moves). Each wrist motor 27.96 to 28.2 (2 or 13 whole counts, so coarse). The **elbow joint angle follows the elbow count plus the shoulder count** (113.5 counts per degree of shoulder + elbow), not the elbow count alone | ratios computed from the raw results file, checked against the vendor parameter files |
| Wrist units | **Probably 0.001 degree per pitch or roll unit** (consistent, not proven). A request of 100 gave 71 (2 counts) and 500 gave 465 (13 counts): at 27.9 counts per degree those would be 2.79 and 13.95 counts, and the DLL drops the fraction | roll moves at all three poses; the data fit this unit but cannot fix it alone |
| Wrist coupling | Roll: both wrist motors move the same way. Pitch: they move in opposite ways (+100 gave m1 -2, m2 +2), and the shoulder and elbow shift a few counts so the tool tip moves less than 0.08 mm per 100 units | pitch and roll moves |
| Does `move_by` hold the wrist? | Yes. Relative x, y, z moves never moved a wrist motor and left xyz pitch and roll unchanged | 18 moves at 3 poses |
| mm scale | The `xyz` array is in micrometres. A 10 mm request gave 9.99 to 10.05 mm along the axis; worst case 0.47 percent (z at pose B), and up to 0.5 percent of sideways drift (pose C, y). The DLL's own accuracy, not the arm's | all 18 position moves; errors recomputed from the raw results |

The smallest wrist step is one count of one motor: 1/27.9 degree, about 0.036 degree. Requests are cut down to whole counts, so a small request moves less than asked (100 units gave 71).

Every move and its reverse returned all arrays to the exact starting values (nine +/-10 mm pairs, twelve pitch and roll pairs). That says the DLL's own maths is consistent, not that the arm is.

**Cross-check:** every figure above was recomputed from the raw results by a second pass. Where the first reading differed, this page uses the recomputed value.

## What the disassembly notes add (stack2 branch)

These come from the disassembly notes on the `stack2` branch. They were not re-checked against the disassembly; treat them as unconfirmed.

- DLL axis indices are 0-based and match the ones above (0 base to 5 gripper). The `ER4Ax1..6.ini` files are 1-based.
- Wrist: the notes give the DLL's formula `pitch = -shoulder - elbow + (m1 - m2)/2` and `roll = (m1 + m2)/2` (in each motor's own scaled units), so opposite wrist motion pitches and equal motion rolls. This agrees with the run.
- Jog: `MoveManual` takes an axis and a velocity percent; a negative velocity picks the `Manual_1` speed and a non-negative one `Manual_2` (the signs in the vendor ini files). Its wrist branch is untraced.
- Message cadence: while Control is on, packets must keep flowing. The notes give pacing numbers (DLL waits about 8 ms between packets, queue limits 45 normal, 4 manual) but **no comms-loss timeout**.
- The notes cover the 2008 and 2018 DLL builds; the lab DLL is a different build.

## What this does not settle

- Latency, how far the arm coasts after a stop, and the controller's reaction to a lost USB link. Only the arm shows these.
- Whether the real arm turns the way the DLL asks: signs, backlash and the Home pose must be checked at the lab (one tiny move per axis, read the log).
- The lab DLL build is a different build from the ones the disassembly notes cover; the run above used the DLL in `C:/scratch/bin-fake`.
- Everything about `MoveManual` (the DLL's jog call): it takes an axis and a velocity percent, and its wrist branch is untraced in the notes.

## Rerun

`tools/usbc_fake/README.md` has the setup. Start `pipe_server.py` first and wait for its `listening` line (its arm-presence check can take 10 seconds; a DLL that starts earlier never connects), then run `fake_oracle.py` with the 32-bit Python.
