# Stack 2 bring-up ladder

Stack 2 is our own USB protocol and has never moved the arm. Hardware behaviour is unverified.
The red physical stop is the only emergency stop. Software stop/disable needs a responsive link.
Connecting energises then releases the motors briefly; the operator must be present and clear of the arm.

## Desk rehearsal (no USB or device access)

Run from the repo root in PowerShell with the shared venv:

```powershell
$env:PYTHONPATH = $PWD
..\openScorbot\.venv\Scripts\python.exe -m scorbot.transport.probe --simulate --latency-ms 1
..\openScorbot\.venv\Scripts\python.exe scripts/bring_up.py --simulate --logs rehearsal\bring-up
..\openScorbot\.venv\Scripts\python.exe scripts/bring_up.py --simulate --stream --logs rehearsal\bring-up
..\openScorbot\.venv\Scripts\python.exe scripts/bring_up.py --simulate --legacy-home --logs rehearsal\bring-up
```

Every simulated line and JSONL row says SIMULATED. DryLink uses the existing Plant's real
64-byte packet replies, including plain status polls. DryScorbot overrides `_open_link()`;
the real `connect()`, handshake and legacy workers run unchanged. The motion ladder then
uses SimulatedScorbot with the same SDK gates. This does not verify hardware acceptance.
`--latency-ms` models dry-link reply availability only, default 0; it does not pace the motion simulator.
Fake latency is never evidence about the controller. The thesis mean of 16.9 ms is one idle capture.

## Lab commands (operator present, supervised only)

Close SCORBASE and vendor tools; only one program may own the controller. Secure the base,
start near home, clear the entire arm/wrist path, empty the jaws and put a hand at the red stop.
Keep real runs under `logs`, separate from `rehearsal`. Record a USBPcap capture alongside the log.

```powershell
..\openScorbot\.venv\Scripts\python.exe -m scorbot.transport.probe --transport auto --online
..\openScorbot\.venv\Scripts\python.exe scripts/bring_up.py --transport auto --online --logs logs\bring-up
# Later, supervised streaming trial:
..\openScorbot\.venv\Scripts\python.exe scripts/bring_up.py --transport auto --online --stream --logs logs\bring-up
```

Real runs need `--online`, like the vendor tools; without it they refuse. No other typed gate.
Vendor Home requires exact `EMPTY`, `CLEAR`, `HOME` at its step. A mismatch aborts.
`--transport device_file` selects Intelitek's ERUSB driver; `pyusb` selects WinUSB/libusb;
`auto` tries device_file and falls back only when the device name is missing.

## Order and PASS

1. Probe: full `Scorbot.connect()` handshake (motor-on then motor-off), one decoded snapshot.
   Standalone probe prints raw hex, counts and switch bits, then closes. Ladder retains its connection.
   `probe --online --reads 200 --log logs\probe-reads.jsonl` also times 200 idle replies (gap min/median/p95/max in ms,
   echo-lag histogram; `None` means the reply echoed no usable ID). It sends nothing beyond the handshake. First real
   timing numbers; unverified until run.
2. Enable motors: command succeeds. Check and write down the actual MOTORS LED; SDK enabled is history.
3. HOME: default `vendor_home`, the vendor's recorded sequence through our SDK, including wrist/gripper.
   A successful SDK Home establishes this session's home. Controller acceptance is unverified.
   `--legacy-home` selects `home(start_position_confirmed=True)` (the SDK's actual keyword);
   legacy Home failed on the real arm on 2026-10-06.
4. One +1 degree jog of `--joint base` (default; shoulder/elbow also available), speed 1, then -1 back.
   This order overrides the earlier jog-before-home idea: `jog_joint` requires a homed session.
   Vendor Home leaves counts on the legacy 0/65535 seam; the first move stays a 1 degree jog.
5. Optional `--stream`: one 1 degree sine over 3 seconds on the chosen joint, then host-side close.
   Adaptive pacing is off unless `--adaptive-pacing`; none of its timing is verified on the arm.
6. Disable motors and disconnect. Check the MOTORS LED independently.

Each step prints PASS/FAIL and before/after encoder counts (joint order base, shoulder, elbow,
wrist motor 1, wrist motor 2, gripper). Probe's unavailable before count is explicit.
PASS means the SDK returned without faults, unexpected switch bits, collision or position-error
counts; jog deltas and return must be within the existing 20-count drift band using 65535 wrap.
It is a desk/procedure check, never a safety or physical-angle verdict.

## Abort and record

Any exception, refusal, corrupt/stale state, unexpected counts, collision or interrupt aborts the
ladder at once, latches the session, and attempts existing disconnect cleanup. No later move runs.
Use the physical stop for unexpected motion, LED disagreement or a blocked link. Do not retry in
the same session. Inspect the log and capture before starting a new instance. Never raise limits.
For every step write operator initials, arm/controller/driver identity, starting pose, log and
capture paths, before/after counts, switches, LEDs, physical direction/angle, PASS/FAIL and surprise.
JSONL is exclusively created and flushed before action/status output; the final line gives its path.
If a vendor Home packet trace exists, the runner prints how to export it with `usb_trace.py from-log`.
Export the vendor capture with `usb_trace.py export capture.pcapng --device N --out vendor-trace.jsonl`,
then `usb_trace.py compare vendor-trace.jsonl home-trace.jsonl`. Compare only the same data source.
Never mix real and SIMULATED sessions or compare their timing.

## Open unknowns

- Does ERUSB honour `CancelSynchronousIo`? DeviceLink's timeout depends on it; no bench answer exists.
- What happens when the stream stops? No controller timeout figure exists. Stop is host-side;
  never depend on a controller watchdog to cut power.
- Zadig/WinUSB breaks the Intelitek vendor path until its driver is restored. Prefer ERUSB first.
