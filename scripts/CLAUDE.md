# scripts/

Offline analysis tools and Windows setup. None of the Python scripts open USB or command the arm; keep it that way. Root rules in [../CLAUDE.md](../CLAUDE.md) apply.

| Script | Purpose | Reads | Notes |
|---|---|---|---|
| `fit_calibration.py` | Fit per-arm counts/degree from physical angle measurements | measurements CSV, limits JSON | Writes with `open("x")` (refuses to overwrite). Prints scale warnings vs legacy scale; warnings never block |
| `review_lab_logs.py` | Structural and count checks on lab JSONL | `--idle` file, `--bench` files (repeatable) | Exit 1 if any problem. Never a safety verdict (`physical_review_required` is always true) |
| `watch_lab_log.py` | Read-only live view of a running lab script from a second terminal | the run's `--output` JSONL and `<stem>.controller.jsonl` | Imports no controller code. `--once` prints one snapshot |
| `usb_trace.py` | Parse USBPcap `.pcapng`/`.pcap`; `summary`, `export`, `compare`, `setpoints` | capture files | Stdlib plus `scorbot.state` and `scorbot.calibration.signed_count_delta`. Runbook: `docs/lab/USB_CAPTURE.md` |
| `vendor_check.py` | Compare an exported trace with the layout read from the vendor DLL; one line per claim (matches, CONTRADICTED, not seen) | JSONL from `usb_trace.py export` or `from-log` | Stdlib only. The layout is from disassembly (`docs/protocol/VENDOR_DLL_PROTOCOL.md`); a match is evidence about that trace, never a safety verdict. Exit 1 on a contradiction |
| `build_bench_kit.py` | Zip the working tree for the Windows PC into `dist/` | repo files | Includes all of `references/` (no PDF exclusion) and `dist/usb-tools/zadig-2.9.exe` if present. Check `references/` before running |
| `setup_windows.ps1` | Create `.venv`, `pip install -e ".[windows,test]"`, `pip check`, run unit tests | | No USB access, no driver change |
| `windows_usb_check.ps1` | List `VID_09F1&PID_0007` in Windows and run enumeration-only preflight | | Read-only |

## fit_calibration.py

- Refuses any row whose `reference_source != "physical"` or `robot_id` mismatch. This is the vendor-display (SCORBASE) rule; do not add a "vendor" role.
- Per joint needs 3 home, 4 fit, 3 verify, 3 move_verify rows, both approach directions in every non-home group. Home spread <= 50 counts and 2 degrees; holdout and motion error <= 2 degrees; fit span >= 20 counts and 2 degrees; soft limits inside the measured range by 2.5 degrees.
- Counts go through `signed_count_delta`, so a measured region must not exceed half the counter range (ambiguity raises).
- It calls `check_soft_limit_span` so it never writes a file `scorbot.calibration.load_calibration` would reject. If you change one side, change the other and `tests/test_arm_control.py`, `tests/test_nominal.py`.
- Only base, shoulder, elbow. Wrist needs a two-motor mapping that does not exist yet.

## Log-reading tools

- `watch_lab_log.py:ALARM_EVENTS` and `review_lab_logs.py` parse row `type` and event names written by `scorbot/robot.py:_record` and `examples/*.py`. Adding or renaming an event means updating these and their tests (`tests/test_watch_lab_log.py`, `tests/test_lab_log_review.py`).
- Rows come from files being appended to. `Follower` buffers a partial last line; keep that. Quiet output does not mean the link is alive (rows are written on events, not per USB packet).
- Legacy logs may lack `data_source` (treated as `real`) or `led_prompts` (missing LED answers are then not a problem). Do not tighten this without a migration note.
- The review refuses to mix files with different `robot_id`, `motion_source_sha256`, or `data_source`, and repeats the SIMULATED banner after the report. Preserve all three.
- Differences use `signed_count_delta`; never `max - min` of signed counts (jumps 65536 at the seam; fixed once already, `test_idle_range_is_wrap_aware_at_the_zero_seam`).
- `watch_lab_log.py` writes ANSI escapes; it enables them on Windows with `os.system("")`. Respect non-TTY output when extending (`docs/design/OPERATOR_UX.md` backlog item 5).

## usb_trace.py

Standard library only; packs and offsets come from `scorbot.state` (`PACKET_MIN_LENGTH`, `decode_state`). The USBPcap header layout is `USBPCAP_HEADER`. Out-packet fields: byte 0 sequence, byte 4 command (`openScorbot/libhex.py`); this comes from code reading, not a capture. `setpoints` reads OUT bytes 12-35 as per-joint value plus sign word (`SETPOINT_REGION`, docs/protocol/PROTOCOL.md 2.3), also unverified; its echo/lead verdict is heuristic, and `--csv` opens with `"x"` (never overwrites). There are no Intelitek captures in the repo, so protocol conclusions from `compare` are unverified until someone records them.

## PowerShell

Scripts use `$ErrorActionPreference = 'Stop'`, resolve the repo root from `$PSScriptRoot`, and are run with `powershell -NoProfile -ExecutionPolicy Bypass -File`. Do not add steps that touch the USB driver (Zadig is manual and documented).

## Testing changes

`python -m unittest tests.test_lab_log_review tests.test_watch_lab_log tests.test_usb_trace tests.test_arm_control tests.test_nominal`. Tests import scripts as `scripts.<name>` (a namespace package, no `__init__.py`), so run from the repo root; renaming a function breaks them. Run the PowerShell scripts only on Windows.

## Do not

- Do not import `openScorbot` protocol modules or open a device from a script. (`fit_calibration.py` imports only `openScorbot.motion_profile`, pure.)
- Do not overwrite outputs; use exclusive create.
- Do not have a review or fit script emit "safe", "calibrated" or "validated" language beyond what its checks prove.
