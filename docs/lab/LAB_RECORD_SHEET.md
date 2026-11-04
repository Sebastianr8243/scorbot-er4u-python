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
| 6 | Reply count sign and zero match the DLL's | **Desk, seen 2026-11-03:** same sign, scale and zero. The lab DLL reads count = (u32 at IN byte 19+5N & 0xFFFFFF) - 0x7FFFFF (decompile `FUN_1000b130`) and uses it unchanged for `GetCurrentPosition`, counts to joints and the 906/907 EncLimit compare. `vendor_home.decode_reply` equals it on all 27 rows of `tests/fixtures/dll_lab_xyz.json` (`tests/test_reply_decode_matches_dll.py`); after each `48` the DLL's next setpoint is 0 for raw `7FFFFF` (PL51 recording, 23 of 23). Known gap: the legacy decode PolicyArm and the stream pass to the limit check reads positive counts 1 low | Confirmation only: hand-over step X, Stack 1 pose then Stack 2 probe; the counts must agree | | |
| 7 | Idle creep with motors on | None expected; since PL98 the idle message holds a fixed setpoint (here the measured snapshot taken at enable), as the DLL holds its commanded setpoint | Motors on after Home, no command, 60 s probe log; drift of any axis in counts | | |
| 8 | Gripper stroke | `RangeEnc = 5000` counts for `RangeMM = 70` mm (ROB_4u.INI) | `bench_gripper` log: counts from closed to open | | |
| 9 | Driver read timeout | 5 s, not interruptible (ledger PL31) | Step T, `scripts/read_timeout_check.py` | | |
| 10 | Largest following error in a normal move | Under 70 counts per arm axis expected: the impact stop trips above `ImpactDetect` = 70 (vendor `ER4AxN.ini`, PL98), up from our old 40 | Every jog, vendor move and stream log: the largest abs error word per axis in moves with no contact. Within about 15 counts of 70 in a clean move: raise it before going faster | | |
