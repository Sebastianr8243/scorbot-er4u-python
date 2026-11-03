# Lab record sheet

Numbers the desk cannot settle. None of these needs its own test: each comes from a command on the
[checklist](LAB_CHECKLIST.md) you are running anyway. Write the value, the log file and the time.
Until a dated line is filled in here, the matching claim stays unverified.

| # | Unknown | Desk value (source) | How to read it at the lab | Value seen | Log |
|---|---|---|---|---|---|
| 1 | Parameter folder the lab PC loads | `ER4u\$Default`, and `$CURRENT` is the same bytes (ER4CONF.INI `CurrentParFolder`) | Open `ER4CONF.INI` beside USBC.dll on the lab PC; copy `CurrentParFolder`. Different folder: compare its `EncLimit_1`/`EncLimit_2` with `scorbot/nominal.py` `VENDOR_ENCODER_SOFT_LIMITS` before any Stack 2 move | | |
| 2 | Counts read 0 at the vendor Home pose | 0 on every axis: vendor Home ends with set-position `48` per axis (`scorbot/vendor_home.py`) | `bring_up` log, first state after Home | | |
| 3 | Physical pose at zero | shoulder -120.28, elbow 95.02, pitch 88.81 degrees (vendor INI `HorizPos`); the shoulder is 3.7 degrees inside its -124 limit | Photo or protractor at Home | | |
| 4 | Wrist pitch counts per degree | 27.9 per motor (`NoEnc90 = 2511`, `$Default/ER4Ax4.ini:39`). The legacy 33.8 (676/20) has no vendor source | After Home, jog pitch a known angle; counts moved / degrees | | |
| 5 | Base counts per degree (gear ratio) | 141.9 (`NoEnc90 = -12770`, gear 127.7:1; the manual's 127.1 does not reproduce the INI) | Jog base 90 degrees against a mark; counts moved / 90 | | |
| 6 | Reply count sign and zero match the DLL's | Same (desk emulation, USNA cross-check; reply parse not traced) | Hand-over step X: Stack 1 pose, then Stack 2 probe; the counts must agree | | |
| 7 | Idle creep with motors on | None expected; the legacy idle message sends the measured position (about 1 count low), where the DLL holds its commanded setpoint | Motors on after Home, no command, 60 s probe log; drift of any axis in counts | | |
| 8 | Gripper stroke | `RangeEnc = 5000` counts for `RangeMM = 70` mm (ROB_4u.INI) | `bench_gripper` log: counts from closed to open | | |
| 9 | Driver read timeout | 5 s, not interruptible (ledger PL31) | Step T, `scripts/read_timeout_check.py` | | |
