# Lab day: what to do, in order

The red emergency stop on the controller is the only real stop.

## Before you start

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

2. **Step T**: Measures driver read timeout with one read and no write before connecting Stack 2.
   ```powershell
   & $vpy scripts\read_timeout_check.py --online
   ```
   **Pass:** `PASS`: a `TimeoutError` that ended by itself in 4 to 6 s.
   **Stop if:** Over 10 s, a hang, or answers twice.

3. **Step S**: Checks microswitch wiring while motors are unpowered by pressing each switch by hand.
   ```powershell
   & $vpy scripts\lab_day.py --online --transport auto
   ```
   **Pass:** IN byte 5 shows base 1, shoulder 2, elbow 4, pitch 8, roll 16, one at a time.
   **Stop if:** Bit map in `vendor_home.py` is wrong for this arm; stop.

4. **Step P**: Reads 200 status replies to check timing and link stability without commanding motion.
   ```powershell
   & $vpy -m scorbot.transport.probe --transport auto --online --reads 200 --log logs\probe.jsonl
   ```
   **Pass:** 200 of 200 reads, gap median 14 to 25 ms, no `None` echo, worst echo lag at most 60, repeats at most 4.
   **Stop if:** Pacing or echo does not match desk assumptions; record and stop.

5. **Step G (moves the arm)**: Runs vendor Home and test velocity jogs only if H, T, S, and P pass and jaws are empty.
   ```powershell
   & $vpy scripts\lab_day.py --online --transport auto --from G
   ```
   **Pass:** Home completes, 3 gripper drives stall within 120 replies, joints align with tape within ~2 degrees; velocity jogs are not yet tested on the arm.
   **Stop if:** Error 574 (gripper budget exceeded), or joint off tape mark (no streaming).

6. **Bench trials (moves the arm)**: Runs supervised single-action trials to record baseline joint, stream, gripper, and policy data.
   ```powershell
   $L = "--robot-id er4u-lab --arm-label <arm sticker> --controller-label <box sticker> --driver intelitek --operator <initials> --start-pose-note <where it is>".Split(" ")
   & $vpy examples\bench_joint.py   --output logs\joint.jsonl   @L --joint base --delta 1 --acknowledge-supervised-motion
   & $vpy examples\bench_stream.py  --output logs\stream.jsonl  @L --motor base --delta 1 --hold-s 1 --acknowledge-supervised-motion
   & $vpy examples\bench_gripper.py --output logs\gripper.jsonl @L --moves open close --acknowledge-supervised-motion
   & $vpy examples\policy_loop.py   --output logs\policy.jsonl  @L --policy reach:base=2 --steps 20 --acknowledge-supervised-motion
   ```
   **Pass:** Each trial completes, stays inside travel bounds, and creates its log.
   **Stop if:** Unexpected motion, drift, or following error trips.

## After

Summarise the lab-day logs:
```powershell
scorbot lab summary <folder>\lab-day.jsonl --sheet
```
Fill [LAB_RECORD_SHEET.md](LAB_RECORD_SHEET.md).

Details for every step: [LAB_CHECKLIST.md](LAB_CHECKLIST.md).
