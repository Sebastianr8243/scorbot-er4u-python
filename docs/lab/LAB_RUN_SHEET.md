# Lab run sheet

Purpose: the order of the day at the arm, with the number each step should give and the point at which to stop. Setup, commands and fixes for errors are in [RUN_THE_ARM.md](RUN_THE_ARM.md).

**WARNING:** the red physical stop is the only emergency stop. Someone's hand stays near it for every step that moves the arm.

Commands below assume the variables from [RUN_THE_ARM.md](RUN_THE_ARM.md), section 1, step 4 (`$py`, `$vpy`). `-m scorbot.bridge` means `& $vpy -m scorbot.bridge`, and script paths are run with `& $vpy`.

Every number below comes from a desk run against the vendor DLL and a fake controller (2026-10-25). None of it is measured on the arm. Every "Pass if" limit is an estimate until this run confirms it; if the arm disagrees, the arm is right and the limit is rewritten.

The **box** is the region of the workspace the arm has been driven in under supervision; step 6 measures it ([RUN_THE_ARM.md](RUN_THE_ARM.md), "The working box").

## Hazards for the first move

| # | What can go wrong | What limits it | What does not |
|---|---|---|---|
| H1 | The step is longer or faster than expected (the mm scale, the wrist unit and `speed` are unverified) | First move is 2 mm at speed 20. The server refuses a step over 50 mm per axis, teleop caps at 20 mm | `speed 20` returning `ok` does not prove the arm is slow: time it (step 2) |
| H2 | The arm hits the table, the mat or a hand | Your hand on the red stop; the server refuses a step that ends outside the box | **Until step 6 has measured the box there is no box check**: steps 3 and 4 are limited only by the step cap and the red stop. Wrist steps never check the box |
| H3 | The client dies or Ctrl+C is pressed mid-move | The server calls Stop when its client goes away; `Arm.send` sends `stop` on Ctrl+C | Stop is not an emergency stop. Killing the server process itself sends no Stop: what the arm does then is unknown (fault check F2) |
| H4 | A DLL call never returns | The server says `USE THE PHYSICAL STOP NOW` and stops serving | Nothing in software; use the red stop |
| H5 | The server crashes and the Home is lost | Re-Home (about 135 s on the arm) with the arm clear, before any `move_to` | Nothing: `move_by` is refused until the server is homed again |
| H6 | A wrist step is too small to see, so a wrong unit looks like a working one | Read the number, not the eye: dial indicator at the tool tip (steps 10 and 11) | A step of 200 is about 0.2 degrees if the 0.001 degree guess is right: about 0.3 mm at 100 mm from the wrist. Invisible by eye |
| H7 | A second script connects while teleop runs | The server answers a second connection with a `busy` line between commands and closes it | It does not queue: the second client must retry after the first quits |
| H8 | The red stop is pressed mid-move | The server refuses new moves while the emergency input is active | What the arm and the server do after release is unverified (fault check F1) |

## Before you touch the arm

1. Close SCORBASE. Before closing it, write down its X Y Z readout; step 1 compares against it. Teach pendant on Auto (or the bypass plug in).
2. `.\.venv\Scripts\python.exe scripts\lab_check.py --bin C:\scratch\bin`. Fix every FAIL line first. A WARN on the working box is normal before step 6.
3. Terminal 1 (32-bit): `& $py tools\usbc_server\usbc_server.py --dll-dir $bin --online`. Starting the server runs the vendor Home; wait for the `serving` line. Home takes about 135 s on the arm (8.8 s against the fake). Keep the terminal open.
4. Have ready: a ruler or calipers, a dial indicator on a stand (wrist steps), a stopwatch, the printed grid mat, a phone to film steps 5 to 8.

## Steps, in this order

"Pass if" is an estimate. "Outside check" is the thing that decides it that is not this software.

| # | Command | Expect | Pass if | Outside check | Stop and look if |
|---|---|---|---|---|---|
| 1 | `-m scorbot.bridge where` | x y z in mm | each of x y z within 5 mm of the SCORBASE readout you wrote down | SCORBASE's readout | no reply, or an error code |
| 2 | `-m scorbot.bridge speed 20` | `ok` | `ok`, and the 10 mm move in step 3 takes longer than the same move after `speed 40` | stopwatch on the arm, not the log | refused |
| 3 | `-m scorbot.bridge by 2 0 0` (first arm move), then `by 10 0 0` | x changes by 2.0, then 10.0 mm | the tip moved +x, and the 10 mm step moved it 10 mm within 2 mm | ruler or calipers on the tool tip | it moves the wrong way, or a different distance |
| 4 | `close`, then `open` | the gripper moves | the jaw gap changes both ways | calipers on the jaw gap, or the film | nothing moves, or only one of the two |
| 5 | `examples\arm_latency.py --speed 20` | calls per second, median and worst per call | 0 failed calls in `summarize_log.py` | the log table, read after the run | refused with "outside the box": see below |
| 6 | `examples\arm_characterise.py` (mat under the arm) | DLL units per mm near 1000 per axis | scale within 5% of 1000 on every axis, off-axis motion under 1 mm | the grid mat, read by a second person | a scale off by more than a few percent, or off-axis motion above about 1 mm |
| 7 | teach `above_a`, `at_a`, `above_b`, `at_b` | each prints the encoder | `go at_a` after moving away returns within 2 mm of the first mark | pencil mark on the mat | |
| 8 | `examples\pick_place.py --runs 10`, filmed | success rate and mean mat error | 10 of 10 runs, mean mat error under 5 mm | the film and the mat | a failed run: stop and write down which |

**Run `arm_latency.py` (step 5) before `arm_characterise.py` (step 6).** Characterise writes a box only about 20 mm tall, and `arm_latency` then refuses its 20 mm up-and-down steps. If you must run it after, use smaller `--sizes`.

## After step 8: check the desk guesses

The wrist code and the joint directions come from the vendor DLL on a fake controller ([../DESK_FINDINGS.md](../DESK_FINDINGS.md)). Check them on the arm with tiny moves, hand on the red stop, and write what you see in `logs/NOTES.md`.

| # | Command | Expect (desk) | Pass if | Outside check | Write down |
|---|---|---|---|---|---|
| 9 | `by 0 10 0` | the base turns toward +y; base count goes down by about 480 | the tip moves toward +y by 10 mm within 2 mm | ruler on the tip | which way it turned |
| 10 | `wrist 200 0` | pitch changes by about 179 units; the two wrist motors move opposite ways, about 5 counts each; the tip stays still | the dial reading changes (about 0.3 mm at 100 mm) and the tip stays within 1 mm | dial indicator on the tool tip | which way the wrist tilted, how far |
| 11 | `wrist 0 200` | roll changes by about 179 units; both wrist motors move the same way | the dial reading changes and the tip stays within 1 mm | dial indicator on the tool flange | which way it rolled |
| 12 | `teleop.py --step-mm 5 --wrist-step 200`, a few keys | each key does what steps 3, 10 and 11 did | the same readings as steps 3, 10 and 11 | ruler and dial indicator | anything that differs |

A wrist step that moves nothing, or the wrong way, means the unit or sign guess is wrong: stop and do not raise the step. If the dial shows nothing at 200, the step may just be too small to read: read the encoder change in the log before deciding, and do not raise the step on a hunch.

## Fault checks (only with the supervisor's approval; each can leave the arm needing a re-Home)

| # | Do | Expect (unverified) | Write down |
|---|---|---|---|
| F1 | Press the red stop during `by 10 0 0` at speed 20 | the arm stops; the next move is refused while the stop is active | what the server said, and whether it needed a re-Home after release |
| F2 | Close terminal 1 during `by 10 0 0` at speed 20 | unknown: the server cannot send Stop; the arm finishes its 10 mm or stops | what the arm did, and how far it went |
| F3 | Press Ctrl+C in terminal 2 during `by 0 0 20` | the arm stops; the server log shows it called Stop | whether the arm stopped at once, and how far it coasted |

Not on the sheet: unplugging the USB cable mid-move. It shows nothing that F2 does not.

## What the desk run measured (fake controller)

- A 10 mm `by` reads back as 10.0 mm. A 20 mm move reads about 1000 DLL units per mm.
- Home ended with `home_returned ok`; `quit` ended with `control_off` then `closed`.
- `ping` says `simulated: false` against a pipe fake too, so a desk run with cameras would be labelled `real`. Do not mix such episodes with arm episodes.

## After

- Read the server log as a table and paste it into `logs\NOTES.md`: `.\.venv\Scripts\python.exe scripts\summarize_log.py logs\server-<time>.jsonl` (what was asked, ok, refused with the reason, or a DLL error).
- Add a few lines to `logs\NOTES.md`: command, log file, what moved, the numbers.
- `quit` the server (`-m scorbot.bridge quit`) so Control goes off.
- Record the results in [../STATUS.md](../STATUS.md): a step that ran on the arm moves up to E4.

## Sign-off

Each "Pass if" limit above is an estimate until this sheet is filled in.

| | |
|---|---|
| Date and arm | |
| Person at the red stop (name, signature) | |
| Steps that passed | |
| Steps that failed or were skipped, and why | |
| Limits that need rewriting | |
