# Lab run sheet

One page for the day at the arm. Commands and fixes for errors are in [RUN_THE_ARM.md](RUN_THE_ARM.md). **The red physical stop is the only emergency stop.** Someone's hand stays near it for every step that moves the arm.

Every number below is from a desk run against the vendor DLL and a fake controller (2026-10-25). None of it is measured on the arm yet.

## Before you touch the arm

1. Close SCORBASE. Teach pendant on Auto (or the bypass plug in).
2. `.\.venv\Scripts\python.exe scripts\lab_check.py --bin C:\scratch\bin`. Fix every FAIL line first. A WARN on the working box is normal before step 5.
3. Terminal 1: start the server (`--online`). Wait for the `serving` line. Home takes about 135 s on the arm (8.8 s against the fake).

## Steps, in this order

| # | Command | Expect | Stop and look if |
|---|---|---|---|
| 1 | `-m scorbot.bridge where` | x y z in mm | no reply, or an error code |
| 2 | `-m scorbot.bridge speed 20` | `ok` | refused |
| 3 | `-m scorbot.bridge by 10 0 0` | x changes by 10.0 mm | it moves the wrong way, or a different distance |
| 4 | `close`, then `open` | the gripper moves | nothing moves, or only one of the two |
| 5 | `examples\arm_latency.py --speed 20` | calls per second, median and worst per call | refused with "outside the box": see below |
| 6 | `examples\arm_characterise.py` (mat under the arm) | DLL units per mm near 1000 per axis | a scale off by more than a few percent, or off-axis motion above about 1 mm |
| 7 | teach `above_a`, `at_a`, `above_b`, `at_b` | each prints the encoder | |
| 8 | `examples\pick_place.py --runs 10`, filmed | success rate and mean mat error | a failed run: stop and write down which |

**Run `arm_latency.py` (step 5) before `arm_characterise.py` (step 6).** Characterise writes a box only about 20 mm tall, and `arm_latency` then refuses its 20 mm up-and-down steps. If you must run it after, use smaller `--sizes`.

## After step 8: check the desk guesses

The wrist code and the joint directions come from the vendor DLL on a fake controller ([../DESK_FINDINGS.md](../DESK_FINDINGS.md)). Check them on the arm with tiny moves, hand on the red stop, and write what you see in `logs/NOTES.md`.

| # | Command | Expect (desk) | Write down |
|---|---|---|---|
| 9 | `by 0 10 0` | the base turns toward +y; base count goes down by about 480 | which way it turned |
| 10 | `wrist 200 0` | pitch changes by about 179 units; the two wrist motors move opposite ways, about 5 counts each; the tip stays still | which way the wrist tilted, how far |
| 11 | `wrist 0 200` | roll changes by about 179 units; both wrist motors move the same way | which way it rolled |
| 12 | `teleop.py --step-mm 5 --wrist-step 200`, a few keys | each key does what steps 3, 10 and 11 did | anything that differs |

A wrist step that moves nothing, or the wrong way, means the unit or sign guess is wrong: stop and do not raise the step.

## What the desk run measured (fake controller)

- A 10 mm `by` reads back as 10.0 mm. A 20 mm move reads about 1000 DLL units per mm.
- Home ended with `home_returned ok`; `quit` ended with `control_off` then `closed`.
- `ping` says `simulated: false` against a pipe fake too, so a desk run with cameras would be labelled `real`. Do not mix such episodes with arm episodes.

## After

- Read the server log as a table and paste it into `logs\NOTES.md`: `.venv\Scripts\python.exe scripts\summarize_log.py logs\server-<time>.jsonl` (what was asked, ok, refused with the reason, or a DLL error).
- Add a few lines to `logs\NOTES.md`: command, log file, what moved, the numbers.
- `quit` the server (`-m scorbot.bridge quit`) so Control goes off.
- Record the results in [../STATUS.md](../STATUS.md): a step that ran on the arm moves up to E4.
