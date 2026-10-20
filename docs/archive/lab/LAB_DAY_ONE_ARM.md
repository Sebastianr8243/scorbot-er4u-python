# Lab day, one arm: one page to print

Nothing here has run on the arm. Someone has a hand on the **physical stop** for every step after 1. Ctrl-C and the software stop are not emergency stops. Unexpected motion, a LED that disagrees with the software, or a fault: **press the stop, end the session, keep `logs\`, do not retry.** Longer version: [LAB_DAY_CARD.md](LAB_DAY_CARD.md).

## Bring

Phone with a level app, tape or marker, a USB stick (copy `logs\` to it first), the lab PC with SCORBASE **closed**. Arm resting near its home pose, motors off.

## Run

```powershell
git pull                                    # on main
.\scripts\lab_day.ps1 -DryRun               # prints every command; check the labels
$env:SCORBOT_HARDWARE = "1"                # operator only: the hardware switch; close this window to turn it off
.\scripts\lab_day.ps1 -Arm ARM-1 -Operator SR
```

Add `-InchHome` if the legacy home fails (it did on 2026-10-06). If a step fails: stop, then `.\scripts\lab_day.ps1 -From <next step>` only after you understand why. Before the arm: `.\scripts\lab_day.ps1 -Simulate` rehearses the whole flow with no arm (answer the prompts the same way).

| # | Step | You type | Look at |
|---|---|---|---|
| 1 | USB check | nothing | `PASS USB 09F1:0007`. Anything else: stop |
| 2 | Idle, 10 s | `GO`, then LED answers | Counts sit still |
| 3 | Jog base +1 deg | `GO`, `HOME`, `HOME_OK`, `MOVE` | Which way it turned. Phone level before and after |
| 4 | Jog shoulder +1 deg | same | A refusal is normal (under 4 deg of travel above home) |
| 5 | Jog elbow +1 deg | same | Does the wrist move too? |
| 6 | Stream, base 1 deg out and back | `GO`, `HOME`, `HOME_OK`, `STREAM` | Goes out and comes back |
| 7 | Policy run, base reach 1 deg | `GO`, `HOME`, `HOME_OK`, `POLICY` | Only if 3 and 6 went well |
| 8 | Export dataset | nothing | `reloaded and matched the source rows` |

**LED prompts** (look at the controller front panel): MOTORS lit? `y` / `n` / `u`. POWER `g`reen / `o`range / `f`lashing / `u`nsure. Answer what you see, not what you expect: a mismatch is the point.

At each "describe" prompt write plain words: which way it moved and roughly how far, whether anything else moved, any noise.

## After

```powershell
.\.venv\Scripts\python.exe scripts\lab_summary.py logs\*.jsonl --phone base=<deg you read> --phone shoulder=<deg> --phone elbow=<deg>
```

Write down, per joint, the phone-level angle before and after the +1 jog. Copy `logs\` and `datasets\` to the USB stick and bring them back. The summary says which way the counts went for `+1` and counts per degree; those are **leads to check against what you saw, not calibration**.

## Not covered

Camera, wrist, gripper, other arms. All scales and directions are vendor priors, not measured on this arm.
