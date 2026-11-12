# Lab day: what to do, in order

The red emergency stop on the controller is the only real stop.

## Before you start

Which moves have been compared with Intelitek's DLL: [LAB_COVERAGE.md](../protocol/LAB_COVERAGE.md).

- Keep Intelitek's driver (ERUSB); do not run Zadig.
- Close SCORBASE and vendor tools: only one program may own the controller.
- Open PowerShell in the repo root; keep the stop in reach.
- Set Python paths:
  ```powershell
  $vpy = ".\.venv\Scripts\python.exe"
  $bin = "C:\scratch\bin"
  $py = "$bin\python.exe"
  ```

## Steps

1. **Step H (moves the arm)**: Verifies arm health and vendor Home using the 32-bit vendor DLL server before any Stack 2 connect.
   Terminal 1:
   ```powershell
   & $py tools\usbc_server\usbc_server.py --dll-dir $bin --online
   ```
   Terminal 2:
   ```powershell
   & $vpy -m scorbot.bridge where
   & $vpy -m scorbot.bridge speed 20
   & $vpy -m scorbot.bridge by 2 0 0
   & $vpy -m scorbot.bridge open
   & $vpy -m scorbot.bridge close
   ```
   **Pass:** Home completes, 2 mm move reads back within 0.2 mm, both gripper moves end; put tape across each joint.
   **Stop if:** Arm or controller is not healthy; no Stack 2 at all.

2. **Steps T, S, P, G (G moves the arm)**: one command runs all four in order. It asks you to type GO before
   each step (anything else skips it) and stops at the first FAIL. Close the Stack 1 server first. Jaws empty for G.
   ```powershell
   & $vpy scripts\lab_day.py --online --transport auto
   ```
   - **T**, driver timeout, one read, no motion. On the lab PC on 2026-11-12 the expected ~5 s wait did not
     happen: the read returned at once ("10 empty reads in a row", link error), while the link itself worked
     (probe: 200 of 200 replies, median gap 16 ms). So T now prints a `FINDING` line and exits 3; that is the
     expected result, not a failure of the link. Next: run the probe (step P), then resume with
     `--from S` (order is T, S, P, G). Stop if: a hang, or a read that blocks for seconds (that would reopen
     the cancel question; record the time). Arm behaviour is unverified.
   - **S**, microswitches, arm off: press each switch with a screwdriver. Pass: IN byte 5 shows base 1,
     shoulder 2, elbow 4, pitch 8, roll 16, one at a time. Stop if: the bits are wrong.
   - **P**, 200 status reads, no motion. Pass: 200 of 200, gap median 14 to 25 ms, no `None` echo, worst echo
     lag at most 60, repeats at most 4. Stop if: any of those fail; write it down.
   - **G**, vendor Home, DLL-matched `move_joint_vendor` 1 degree out/back at `speed_percent=1`, then
     low-speed velocity jogs (moves the arm). Degree moves return to the exact starting vendor target;
     `step_motion` records speed and `total_ms`. All G motion remains unverified on the arm.
     Pass: Home completes, the 3 gripper drives stall within 120 replies, every joint lines up with its
     tape mark within about 2 degrees. Stop if: error 574 (gripper too slow; do not raise the limit), or a joint off
     its mark (no streaming).

   After you fix a stop, resume at that step, for example: `& $vpy scripts\lab_day.py --online --transport auto --from G`. After the T finding, run the probe first, then resume with `--from S`.

3. **Bench trials (moves the arm)**: Runs supervised single-action trials to record baseline joint, stream, gripper, and policy data.
   ```powershell
   $L = "--robot-id er4u-lab --arm-label <arm sticker> --controller-label <box sticker> --driver intelitek --operator <initials> --start-pose-note <where it is>".Split(" ")
   & $vpy examples\bench_joint.py   --output logs\joint.jsonl   @L --joint base --delta 1 --acknowledge-supervised-motion
   & $vpy examples\bench_stream.py  --output logs\stream.jsonl  @L --motor base --delta 1 --hold-s 1 --acknowledge-supervised-motion
   & $vpy examples\bench_gripper.py --output logs\gripper.jsonl @L --moves open close --acknowledge-supervised-motion
   & $vpy examples\policy_loop.py   --output logs\policy.jsonl  @L --policy reach:base=2 --steps 20 --acknowledge-supervised-motion
   & $vpy examples\policy_loop.py   --output logs\grip.jsonl    @L --policy reach:base=2 --steps 20 --grip-at 10 --acknowledge-supervised-motion   # stop, jaw open, resume (#209)
   ```
   **Pass:** Each trial completes, stays inside travel bounds, and creates its log. `bench_joint.py` is the first arm test of Stack 2's own jog (`jog_joint`), which has no DLL equivalent; run it after G passes. For the last one, the
   `gripper_resume` row in `logs\grip.jsonl` has `within_drift: true`.
   **Stop if:** Unexpected motion, drift, or following error trips.

## After

Summarise the lab-day logs (`<folder>` is the `logs\lab-<time>` folder step 2 printed):
```powershell
& $vpy scripts\lab_summary.py <folder>\lab-day.jsonl --sheet
```
Fill [LAB_RECORD_SHEET.md](LAB_RECORD_SHEET.md).

Details for every step: [LAB_CHECKLIST.md](LAB_CHECKLIST.md).
