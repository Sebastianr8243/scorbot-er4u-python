# Guided lab session

One command runs a whole bench session: profile, checklist, connect, LED
checks, idle, home, and several small jogs. Rehearse first; the rehearsal
uses no USB and no robot.

    .\.venv\Scripts\python.exe -m scorbot.lab --simulate     # rehearsal, logs in rehearsal\
    .\.venv\Scripts\python.exe -m scorbot.lab                # real arm, logs in logs\

**Connecting energises the motors.** Someone stands at the physical stop the
whole time. Software disarm and disable are not emergency stops.

## Steps

| Step | What you do |
|---|---|
| Profile | First time: type the robot id, labels, driver, initials. Later: Enter keeps them |
| Checklist | Enter or `y` for each item; `n` ends the session, nothing moves |
| LED checks | Look at the controller: MOTORS `y` lit / `n` off / `u` unsure; POWER `g` / `o` / `f` / `u`. Answer what you see; the expected state is not shown first |
| Idle | Hands off for 5 s |
| Home | Describe the start pose, type `HOME`, answer the LEDs, watch the search, describe it, `y` if it looked right |
| Jog | Press `a`, name a landmark once, type `ARM`. Then keys below |
| Finish | `x`: motors off, LED check, summary and `LOG CHECK` line |

## Jog keys

| Key | Action |
|---|---|
| `1` / `q` | base + / - |
| `2` / `w` | shoulder + / - |
| `3` / `e` | elbow + / - |
| `s` | step size 1 or 0.5 degree |
| `a` / `d` | arm / disarm |
| `?` | help |
| `x` | finish |

One press is one step; nothing moves while a key is held. The first move of a
kind asks you to type it (for example `BASE -1`); pressing the same key again
repeats it. Unknown keys, 60 s without a key, a declined confirmation or any
error disarm. Each joint can move at most 10 degrees from home per session
(legacy scale, not measured). After each move you say which way it went and
whether anything else moved, before the numbers are shown.

## Logs and review

Logs go to `logs\<date>-<robot>-session-NN.jsonl` (never overwritten) with a
`.controller.jsonl` and an MCAP session in `logs\sessions\`. Review:

    .\.venv\Scripts\python.exe scripts\review_lab_logs.py --session logs\<file>.jsonl

`LOG CHECK: 0 problems` means the log is complete and consistent, not that
the motion was safe or accurate. The older `examples\bench_joint.py` and
`examples\record_raw_state.py` still work as the fallback procedure.
